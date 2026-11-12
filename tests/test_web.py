import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from photoapp import jobs as j
from photoapp.config import MAX_PHOTOS, Settings
from photoapp.jobs import JobStore
from photoapp.web import COOKIE, create_app
from photoapp.worker import Worker

KEY = "test-key"


def jpeg(color=(10, 20, 30)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 48), color).save(buffer, "JPEG")
    return buffer.getvalue()


@pytest.fixture
def env(tmp_path):
    processed = []

    async def process(job_id):
        processed.append(job_id)

    store = JobStore(tmp_path)
    settings = Settings(app_key=KEY, telegram_token="t", telegram_chat_id="1", data_dir=tmp_path)
    app = create_app(settings, store, Worker(process))
    with TestClient(app, follow_redirects=False) as client:
        yield client, store, processed


@pytest.fixture
def authed(env):
    client, store, processed = env
    client.cookies.set(COOKIE, KEY)
    return client, store, processed


def upload(client, *contents, note="наклейки"):
    files = [("photos", (f"{n}.jpg", content, "image/jpeg")) for n, content in enumerate(contents)]
    return client.post("/api/jobs", files=files, data={"note": note})


def test_api_requires_key(env):
    client, _, _ = env
    assert client.get("/api/jobs").status_code == 401
    assert client.get("/").headers["location"] == "/login"


def test_wrong_key_does_not_log_in(env):
    client, _, _ = env
    assert client.get("/?key=wrong").headers["location"] == "/login"
    response = client.post("/login", data={"key": "wrong"})
    assert response.headers["location"] == "/login?bad=1"
    assert COOKIE not in response.cookies


def test_link_with_key_sets_cookie_and_hides_key(env):
    client, _, _ = env
    response = client.get(f"/?key={KEY}")
    assert response.status_code == 303 and response.headers["location"] == "/"
    assert "httponly" in response.headers["set-cookie"].lower()
    assert client.get("/").status_code == 200


def test_login_form_sets_cookie(env):
    client, _, _ = env
    assert client.post("/login", data={"key": KEY}).headers["location"] == "/"
    assert client.get("/api/jobs").status_code == 200


def test_upload_creates_job_and_queues_it(authed):
    client, store, processed = authed
    response = upload(client, jpeg(), jpeg((1, 2, 3)))
    assert response.status_code == 200
    job = store.get(response.json()["job"]["id"])
    assert job.note == "наклейки" and len(job.photos) == 2
    assert (store.folder(job.id) / "photo_2.jpg").exists()
    client.get("/api/jobs")
    assert client.get(f"/api/jobs/{job.id}/photos/1").headers["content-type"] == "image/jpeg"
    assert client.get(f"/api/jobs/{job.id}/photos/3").status_code == 404


def test_same_photos_are_not_processed_twice(authed):
    client, store, _ = authed
    first = upload(client, jpeg(), jpeg((1, 2, 3))).json()
    second = upload(client, jpeg((1, 2, 3)), jpeg()).json()
    assert second["duplicate"] is True and second["job"]["id"] == first["job"]["id"]
    assert len(store.recent()) == 1


def test_non_image_is_rejected_and_leaves_nothing(authed):
    client, store, _ = authed
    response = upload(client, b"not an image")
    assert response.status_code == 400
    assert store.recent() == [] and not any(store.root.iterdir())


def test_too_many_photos_rejected(authed):
    client, _, _ = authed
    assert upload(client, *[jpeg((n, 0, 0)) for n in range(MAX_PHOTOS + 1)]).status_code == 400


def test_public_job_hides_internals(authed):
    client, _, _ = authed
    job = upload(client, jpeg()).json()["job"]
    assert "digest" not in job and "force" not in job and job["photo_count"] == 1


def test_retry_only_from_review_or_failed(authed):
    client, store, _ = authed
    job_id = upload(client, jpeg()).json()["job"]["id"]
    assert client.post(f"/api/jobs/{job_id}/retry").status_code == 409
    store.update(job_id, status=j.DONE, form=j.SUBMITTED)
    assert client.post(f"/api/jobs/{job_id}/retry").status_code == 409


def test_retry_applies_corrections_and_grants_one_time_permission(authed):
    client, store, _ = authed
    job_id = upload(client, jpeg()).json()["job"]["id"]
    store.update(job_id, status=j.REVIEW, form=j.HELD)
    response = client.post(f"/api/jobs/{job_id}/retry", data={"bag": "py1234", "note": "грязная"})
    assert response.status_code == 200
    job = store.get(job_id)
    assert (job.bag_override, job.note, job.force, job.status) == ("PY1234", "грязная", True, j.QUEUED)


def test_retry_rejects_bad_bag(authed):
    client, store, _ = authed
    job_id = upload(client, jpeg()).json()["job"]["id"]
    store.update(job_id, status=j.REVIEW)
    assert client.post(f"/api/jobs/{job_id}/retry", data={"bag": "12"}).status_code == 400
    assert store.get(job_id).status == j.REVIEW


def test_unknown_job_is_404(authed):
    client, _, _ = authed
    assert client.post("/api/jobs/../../etc/retry").status_code == 404
    assert client.get("/api/jobs/nope/screenshot").status_code == 404


def test_form_items_are_served(authed):
    client, _, _ = authed
    data = client.get("/api/form-items").json()
    assert "Лавка" in data["services"]
    assert any(section["title"] == "Внешний вид термосумки" for section in data["sections"])


def test_upload_keeps_chosen_items_service_and_bag(authed):
    client, store, _ = authed
    files = [("photos", ("0.jpg", jpeg(), "image/jpeg"))]
    data = {"note": "", "ticks": ["Угрожал и агрессировал", "Отклеены наклейки на сумке"],
            "service": "Еда", "bag": "py1234"}
    job = store.get(client.post("/api/jobs", files=files, data=data).json()["job"]["id"])
    assert job.ticks == ("Отклеены наклейки на сумке", "Угрожал и агрессировал")
    assert (job.service_choice, job.bag_override) == ("Еда", "PY1234")


def test_upload_rejects_item_missing_from_the_form(authed):
    client, store, _ = authed
    files = [("photos", ("0.jpg", jpeg(), "image/jpeg"))]
    response = client.post("/api/jobs", files=files, data={"ticks": ["Отклеены наклейки на сумке"], "service": "Лавка"})
    assert response.status_code == 400
    assert store.recent() == []


def test_retry_replaces_chosen_items(authed):
    client, store, _ = authed
    job_id = upload(client, jpeg()).json()["job"]["id"]
    store.update(job_id, status=j.REVIEW, form=j.HELD)
    data = {"ticks": ["Выдан номер"], "ticks_sent": "1"}
    assert client.post(f"/api/jobs/{job_id}/retry", data=data).status_code == 200
    assert store.get(job_id).ticks == ("Выдан номер",)


def test_page_and_scripts_are_revalidated_so_updates_show_up(authed):
    client, _, _ = authed
    for path in ("/", "/static/app.js", "/static/choices.js"):
        assert client.get(path).headers["cache-control"] == "no-cache"
