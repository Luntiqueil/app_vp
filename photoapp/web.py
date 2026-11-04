"""Веб-часть: страница загрузки и API. Annotations здесь обычные: FastAPI читает их при запуске (Python 3.9)."""
import asyncio
import contextlib
import hashlib
import hmac
import logging
import shutil
from dataclasses import asdict
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import form_filler
from . import jobs as j
from .config import MAX_NOTE_CHARS, MAX_PHOTOS, MAX_UPLOAD_BYTES, Settings
from .images import NotAnImage, save_as_jpeg
from .jobs import Job, JobStore
from .pipeline import SCREENSHOT_NAME
from .worker import Worker

log = logging.getLogger("photo-app")
STATIC = Path(__file__).with_name("static")
COOKIE = "app_vp_key"
COOKIE_AGE = 365 * 24 * 3600
BAD_KEY_DELAY_S = 1
OPEN_PATHS = ("/login", "/static/", "/manifest.webmanifest")
RETRY_ALLOWED = (j.REVIEW, j.FAILED)


def _public(job: Job) -> dict:
    data = asdict(job)
    for private in ("digest", "bag_override", "force"):
        data.pop(private)
    data["photo_count"] = len(data.pop("photos"))
    return data


async def _read_limited(upload: UploadFile) -> bytes:
    data = await upload.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Фото слишком большое.")
    return data


def create_app(settings: Settings, store: JobStore, worker: Worker) -> FastAPI:
    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        worker.start()
        for job_id in store.recover():
            worker.enqueue(job_id)
        yield
        await worker.stop()
        await form_filler.close_browser()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    def key_ok(value: Optional[str]) -> bool:
        return bool(value) and hmac.compare_digest(value.encode(), settings.app_key.encode())

    def with_cookie(response):
        response.set_cookie(COOKIE, settings.app_key, max_age=COOKIE_AGE, httponly=True, samesite="lax")
        return response

    @app.middleware("http")
    async def require_key(request: Request, call_next):
        path = request.url.path
        if key_ok(request.cookies.get(COOKIE)) or path.startswith(OPEN_PATHS):
            return await call_next(request)
        if key_ok(request.query_params.get("key")):
            return with_cookie(RedirectResponse(path, status_code=303))
        if path.startswith("/api/"):
            return JSONResponse({"detail": "Нужен ключ."}, status_code=401)
        return RedirectResponse("/login", status_code=303)

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/manifest.webmanifest")
    async def manifest():
        return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")

    @app.get("/login", response_class=HTMLResponse)
    async def login_page():
        return FileResponse(STATIC / "login.html")

    @app.post("/login")
    async def login(key: str = Form("")):
        if not key_ok(key.strip()):
            await asyncio.sleep(BAD_KEY_DELAY_S)
            return RedirectResponse("/login?bad=1", status_code=303)
        return with_cookie(RedirectResponse("/", status_code=303))

    @app.get("/api/jobs")
    async def list_jobs():
        return {"jobs": [_public(job) for job in store.recent()], "limits": {"photos": MAX_PHOTOS}}

    @app.post("/api/jobs")
    async def create_job(photos: List[UploadFile] = File(...), note: str = Form("")):
        if len(photos) > MAX_PHOTOS:
            raise HTTPException(400, f"Не больше {MAX_PHOTOS} фото в одном отчёте.")
        contents = [await _read_limited(photo) for photo in photos]
        digest = hashlib.sha256(b"".join(sorted(hashlib.sha256(c).digest() for c in contents))).hexdigest()
        duplicate = store.by_digest(digest)
        if duplicate:
            return {"job": _public(duplicate), "duplicate": True}
        job_id = store.new_id()
        folder = store.folder(job_id)
        folder.mkdir(parents=True)
        names = tuple(f"photo_{n}.jpg" for n in range(1, len(contents) + 1))
        try:
            for name, content in zip(names, contents):
                await asyncio.to_thread(save_as_jpeg, content, folder / name)
        except NotAnImage:
            shutil.rmtree(folder, ignore_errors=True)
            raise HTTPException(400, "Один из файлов не похож на фото.")
        job = store.create(job_id, note.strip()[:MAX_NOTE_CHARS], names, digest)
        worker.enqueue(job.id)
        return {"job": _public(job), "duplicate": False}

    def job_or_404(job_id: str) -> Job:
        job = store.get(job_id)
        if not job:
            raise HTTPException(404, "Отчёт не найден.")
        return job

    @app.post("/api/jobs/{job_id}/retry")
    async def retry(job_id: str, note: Optional[str] = Form(None), bag: Optional[str] = Form(None)):
        """Человек проверил отчёт и просит отправить. Сделанные шаги не повторяются."""
        job = job_or_404(job_id)
        if job.status not in RETRY_ALLOWED:
            raise HTTPException(409, "Этот отчёт сейчас нельзя повторить.")
        bag_override = job.bag_override
        if bag and bag.strip():
            bag_override = form_filler.pick_bag([bag])
            if not bag_override:
                raise HTTPException(400, "Номер сумки: 2 латинские буквы и 4 цифры, например PY1234.")
        new_note = job.note if note is None else note.strip()[:MAX_NOTE_CHARS]
        updated = store.update(
            job_id, status=j.QUEUED, note=new_note, bag_override=bag_override, force=True, message=""
        )
        worker.enqueue(job_id)
        return {"job": _public(updated)}

    @app.get("/api/jobs/{job_id}/photos/{number}")
    async def photo(job_id: str, number: int):
        job = job_or_404(job_id)
        if not 1 <= number <= len(job.photos):
            raise HTTPException(404, "Фото не найдено.")
        return FileResponse(store.folder(job_id) / job.photos[number - 1], media_type="image/jpeg")

    @app.get("/api/jobs/{job_id}/screenshot")
    async def screenshot(job_id: str):
        job_or_404(job_id)
        path = store.folder(job_id) / SCREENSHOT_NAME
        if not path.exists():
            raise HTTPException(404, "Снимка формы нет.")
        return FileResponse(path, media_type="image/png")

    return app
