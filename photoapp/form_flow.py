"""Полный проход по форме: заполнить, проверить и, только если всё в порядке, отправить.

Отправленную форму нельзя отозвать, поэтому при любом сомнении отчёт остаётся у человека.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, List, Optional, Sequence, Tuple

from playwright.async_api import TimeoutError as PlaywrightTimeout

from . import form_filler

log = logging.getLogger("photo-app")
UPLOAD_TIMEOUT_MS = 90_000
SUBMIT_TIMEOUT_MS = 30_000
POLL_MS = 300
REVEAL_DELAY_S = 0.5  # форме нужно время, чтобы показать вопросы, открывшиеся после галочек
SUBMIT_NAME = re.compile(r"^\s*Отправить\s*$")
NO_TICKS = "не указано, что сделано: допишите слово, например «наклейки» или «грязная»"
NO_PHOTOS = "фото не прикрепились к форме"
SUBMITTED, HELD, UNCONFIRMED = "submitted", "held", "unconfirmed"


@dataclass(frozen=True)
class FormRequest:
    bag: Optional[str]
    service: Optional[str]
    ticks: Tuple[str, ...]
    courier_id: Optional[str]
    phone: Optional[str]
    photos: Tuple[str, ...]
    is_screen: Tuple[bool, ...]
    screenshot: Path


@dataclass(frozen=True)
class FormOutcome:
    state: str
    problems: Tuple[str, ...]


def wanted_ticks(bag: Optional[str], ticks: Sequence[str]) -> List[str]:
    """Без повторов: второй клик по той же галочке снял бы её."""
    wanted: List[str] = []
    for tick in [*form_filler.marks_for(bag), *ticks]:
        low = tick.lower()
        if not any(low in w.lower() or w.lower() in low for w in wanted):
            wanted.append(tick)
    return wanted


def hold_reasons(problems: Sequence[str], ticked: int, attached: int) -> List[str]:
    """Причины не отправлять. Обязательные поля формы не проверяют, есть ли нарушение и фото,
    поэтому это проверяем сами."""
    return [
        *problems,
        *([NO_TICKS] if not ticked else []),
        *([NO_PHOTOS] if not attached else []),
    ]


async def _apply_ticks(page, wanted: Sequence[str]) -> Tuple[int, List[str]]:
    ticked, problems = 0, []
    for tick in wanted:
        if "::" in tick:  # выпадающий список: «Вопрос :: вариант»
            done = await form_filler.choose(*(part.strip() for part in tick.split("::", 1)), page)
        else:
            done = await form_filler.tick(tick, page)
        if done:
            ticked += 1
        else:
            problems.append(f"«{tick}» (нет в форме)")
    return ticked, problems


async def _wait_uploads(page) -> List[str]:
    """Файл уходит в форму не сразу: пока у него крутится индикатор, отправлять нельзя."""
    loaders = page.locator(".FileBadge-Loader")
    waited = 0
    while await loaders.count():
        if waited >= UPLOAD_TIMEOUT_MS:
            return ["фото не успели загрузиться"]
        await page.wait_for_timeout(POLL_MS)
        waited += POLL_MS
    errors = await page.locator(".FileQuestion .QuestionLayout-ErrorContainer").all_inner_texts()
    return [text.strip() for text in errors if text.strip()]


async def _fill_page(page, request: FormRequest, base_problems: Sequence[str]) -> Tuple[List[str], int, int]:
    ticked, tick_problems = await _apply_ticks(page, wanted_ticks(request.bag, request.ticks))
    await asyncio.sleep(REVEAL_DELAY_S)
    photos = list(request.photos)
    screens = [p for p, is_screen in zip(photos, request.is_screen) if is_screen]
    problems = [
        *base_problems,
        *tick_problems,
        *await form_filler.fill_kisart(page, request.courier_id, request.phone),
        *await form_filler.upload_kisart(page, screens, photos),
        *await form_filler.upload_photos(page, photos),
    ]
    problems += await _wait_uploads(page)
    attached = await page.locator(".FileQuestion .FileBadge").count()
    return problems, ticked, attached


async def _click_submit(page) -> bool:
    """True, когда форма приняла ответ: кнопка «Отправить» исчезла вместе с вопросами."""
    button = page.get_by_role("button", name=SUBMIT_NAME)
    await button.click(timeout=form_filler.TIMEOUT)
    try:
        await button.wait_for(state="hidden", timeout=SUBMIT_TIMEOUT_MS)
        return True
    except PlaywrightTimeout:
        return False


async def run_form(
    request: FormRequest,
    may_submit: bool,
    force: bool,
    on_submit_start: Callable[[], Awaitable[None]],
) -> FormOutcome:
    base_problems, page = await form_filler.fill(request.bag, request.service)
    try:
        problems, ticked, attached = await _fill_page(page, request, base_problems)
        reasons = hold_reasons(problems, ticked, attached)
        await page.screenshot(path=str(request.screenshot), full_page=True)
        # номер сумки обязателен в самой форме: без него не отправить даже по просьбе человека
        if not request.bag or not may_submit or (reasons and not force):
            return FormOutcome(HELD, tuple(reasons))
        await on_submit_start()
        accepted = await _click_submit(page)
        await page.screenshot(path=str(request.screenshot), full_page=True)
        return FormOutcome(SUBMITTED if accepted else UNCONFIRMED, tuple(reasons))
    finally:
        await page.close()
