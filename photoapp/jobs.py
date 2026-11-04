"""Отчёты и их хранение на диске: перезапуск приложения не теряет фото и не отправляет форму дважды."""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

QUEUED, PROCESSING, DONE, REVIEW, FAILED = "queued", "processing", "done", "review", "failed"
# Telegram: pending → sending → sent | failed | unknown (ответа нет, сообщение могло уйти)
# Форма: pending → filling → held | failed | submitting → submitted | unconfirmed
SENDING, SENT, UNKNOWN, PENDING = "sending", "sent", "unknown", "pending"
FILLING, HELD, SUBMITTING, SUBMITTED, UNCONFIRMED = "filling", "held", "submitting", "submitted", "unconfirmed"


@dataclass(frozen=True)
class Job:
    id: str
    created_at: str
    note: str
    photos: Tuple[str, ...]
    digest: str
    status: str = QUEUED
    telegram: str = PENDING
    form: str = PENDING
    message: str = ""
    problems: Tuple[str, ...] = ()
    bag: Optional[str] = None
    service: Optional[str] = None
    bag_override: Optional[str] = None
    force: bool = False  # человек проверил отчёт и сам попросил отправить
    has_screenshot: bool = False


def _from_dict(raw: dict) -> Job:
    return Job(**{**raw, "photos": tuple(raw["photos"]), "problems": tuple(raw["problems"])})


class JobStore:
    def __init__(self, data_dir: Path):
        self.root = Path(data_dir) / "jobs"
        self.root.mkdir(parents=True, exist_ok=True)
        self._jobs: Dict[str, Job] = {}
        for path in self.root.glob("*/job.json"):
            job = _from_dict(json.loads(path.read_text(encoding="utf-8")))
            self._jobs[job.id] = job

    def folder(self, job_id: str) -> Path:
        return self.root / job_id

    def new_id(self) -> str:
        return uuid.uuid4().hex

    def create(self, job_id: str, note: str, photos: Tuple[str, ...], digest: str) -> Job:
        job = Job(
            id=job_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            note=note,
            photos=photos,
            digest=digest,
        )
        self._save(job)
        return job

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def by_digest(self, digest: str) -> Optional[Job]:
        return next((j for j in self._jobs.values() if j.digest == digest), None)

    def recent(self, limit: int = 30) -> List[Job]:
        return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)[:limit]

    def update(self, job_id: str, **changes) -> Job:
        job = replace(self._jobs[job_id], **changes)
        self._save(job)
        return job

    def recover(self) -> List[str]:
        """После сбоя: незавершённое — снова в очередь, кроме шагов, которые могли успеть
        выполниться. Такие шаги не повторяем сами, а просим человека проверить."""
        requeue = []
        for job in sorted(self._jobs.values(), key=lambda j: j.created_at):
            if job.status not in (QUEUED, PROCESSING):
                continue
            telegram = UNKNOWN if job.telegram == SENDING else job.telegram
            if job.form == SUBMITTING:
                self.update(
                    job.id,
                    status=REVIEW,
                    telegram=telegram,
                    form=UNCONFIRMED,
                    message="Приложение перезапускалось во время отправки. Проверьте, дошла ли форма.",
                )
                continue
            form = job.form if job.form in (SUBMITTED, UNCONFIRMED) else PENDING
            self.update(job.id, status=QUEUED, telegram=telegram, form=form)
            requeue.append(job.id)
        return requeue

    def _save(self, job: Job) -> None:
        folder = self.folder(job.id)
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / "job.json.tmp"
        tmp.write_text(json.dumps(asdict(job), ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, folder / "job.json")
        self._jobs[job.id] = job
