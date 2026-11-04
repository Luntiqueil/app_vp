"""Очередь отчётов: обрабатываем строго по одному, форма и распознавание тяжёлые."""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Optional

log = logging.getLogger("photo-app")


class Worker:
    def __init__(self, process: Callable[[str], Awaitable[object]]):
        self._process = process
        self._queue: Optional[asyncio.Queue] = None
        self._task: Optional[asyncio.Task] = None

    def start(self) -> None:
        # в Python 3.9 очередь привязывается к текущему циклу, поэтому создаём её уже внутри него
        self._queue = asyncio.Queue()
        self._task = asyncio.create_task(self._run())

    def enqueue(self, job_id: str) -> None:
        self._queue.put_nowait(job_id)

    async def join(self) -> None:
        await self._queue.join()

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _run(self) -> None:
        while True:
            job_id = await self._queue.get()
            try:
                await self._process(job_id)
            except Exception:
                log.exception("Отчёт %s: непредвиденная ошибка", job_id)
            finally:
                self._queue.task_done()
