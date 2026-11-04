"""Заполнение Яндекс Формы в браузере без окон (Playwright).

Копия form_filler.py из Luntiqueil/telegram-photo-bot (коммит d9d5689). Отличия описаны в README.
Открывает форму, выбирает город, велопомощника, вписывает номер сумки
и отмечает сервис (сумки AA…/AB…/TA… — Лавка, остальные — Еда) и «Вело».
Кнопку «Отправить» этот модуль не нажимает: это делает form_flow после всех проверок.
Ссылка на форму и велопомощник задаются в .env (FORM_URL, FORM_HELPER).
"""
from __future__ import annotations

import asyncio
import logging
import os
import json
import re
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger("photo-bot")

FORM_URL = os.getenv("FORM_URL", "")
CITY_LABEL = os.getenv("FORM_CITY_LABEL", "Выбери город")
CITY = os.getenv("FORM_CITY", "Москва")
HELPER_LABEL = os.getenv("FORM_HELPER_LABEL", "Велопомощник Москва")
HELPER = os.getenv("FORM_HELPER", "")
BAG_LABEL = os.getenv("FORM_BAG_LABEL", "Номер сумки курьера")
SERVICE_LABEL = os.getenv("FORM_SERVICE_LABEL", "Сервис курьера с нарушением (определите по термосумке)")
# сумки Лавки начинаются с этих букв, остальные считаем Едой
LAVKA_PREFIXES = tuple(p.strip().upper() for p in os.getenv("FORM_LAVKA_PREFIXES", "AA,AB,TA").split(","))
DEFAULT_SERVICE = os.getenv("FORM_DEFAULT_SERVICE", "Еда")
PHOTO_LABEL = os.getenv("FORM_PHOTO_LABEL", "Фото до устранения нарушения внешнего вида")
TRANSPORT_LABEL = os.getenv("FORM_TRANSPORT_LABEL", "Способ передвижения курьера с нарушением")
TRANSPORT = os.getenv("FORM_TRANSPORT", "Вело")
BAG_RE = re.compile(r"^[A-Z]{2}\d{4}$")  # форма ждёт 2 буквы и 4 цифры
TIMEOUT = 15_000  # мс
RULES_DIR = Path(__file__).with_name("rules")
# что отмечать для конкретных сумок: {"PY0000": ["Сумка утратила внешний вид"]}
MARKS_FILE = Path(os.getenv("FORM_MARKS_FILE", RULES_DIR / "marks.json"))

_pw = None
_browser = None
_last_page = None  # последняя открытая форма — ей можно управлять командами из Telegram
_lock = asyncio.Lock()


def pick_bag(values: list[str]) -> str | None:
    """Первый номер в формате «2 буквы + 4 цифры» (сначала — тот, что в подписи)."""
    for v in values:
        for part in (v or "").split(","):
            part = part.strip().upper()
            if BAG_RE.match(part):
                return part
    return None


SHORTCUTS_FILE = Path(os.getenv("FORM_SHORTCUTS_FILE", RULES_DIR / "shortcuts.json"))


def shortcuts_for(caption: str | None) -> list[str]:
    """Пункты формы по коротким словам в подписи к фото («наклейки», «грязь», «рваная»…)."""
    if not caption or not SHORTCUTS_FILE.exists():
        return []
    try:
        rules = json.loads(SHORTCUTS_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("Не удалось прочитать %s (%s)", SHORTCUTS_FILE, e)
        return []
    text = caption.replace("ё", "е")
    return [item for pattern, item in rules.items()
            if pattern != "_" and re.search(pattern, text, re.IGNORECASE)]


def marks_for(bag: str | None) -> list[str]:
    """Пункты, которые для этой сумки нужно отметить всегда (marks.json)."""
    if not bag or not MARKS_FILE.exists():
        return []
    try:
        return list(json.loads(MARKS_FILE.read_text(encoding="utf-8")).get(bag.upper(), []))
    except Exception as e:
        log.warning("Не удалось прочитать %s (%s)", MARKS_FILE, e)
        return []


def service_for(bag: str | None) -> str | None:
    """Сервис по номеру сумки: Лавка по префиксу, иначе сервис по умолчанию."""
    if not bag:
        return None
    return "Лавка" if bag.upper().startswith(LAVKA_PREFIXES) else DEFAULT_SERVICE


async def _get_browser():
    global _pw, _browser
    if _browser and _browser.is_connected():
        return _browser
    from playwright.async_api import async_playwright

    if _pw is None:
        _pw = await async_playwright().start()
    path = os.getenv("FORM_BROWSER_PATH")  # необязательно: свой Chrome/Chromium
    _browser = await _pw.chromium.launch(headless=True, executable_path=path or None)
    return _browser


async def close_browser() -> None:
    """Закрывает браузер и Playwright, чтобы приложение завершалось без висящих процессов."""
    global _pw, _browser
    if _browser:
        await _browser.close()
    if _pw:
        await _pw.stop()
    _pw = _browser = None


def _question(page, label: str):
    """Блок вопроса: ближайший предок подписи, в котором есть поле ввода или кнопка."""
    lab = page.get_by_text(label, exact=True).first
    return lab, lab.locator(
        "xpath=ancestor::*[.//input or .//textarea or .//button or .//*[@role='combobox']][1]"
    )


async def _choose(page, label: str, option: str):
    lab, q = _question(page, label)
    await lab.wait_for(timeout=TIMEOUT)
    await q.locator("[role=combobox], button, input").first.click()
    opt = page.get_by_role("option").filter(has_text=option).first
    if not await opt.count():
        # длинный список может подгружаться при вводе — набираем текст поиска
        await page.keyboard.type(option)
        await page.wait_for_timeout(500)
        opt = page.get_by_role("option").filter(has_text=option).first
    await opt.click(timeout=TIMEOUT)


async def _type(page, label: str, text: str):
    lab, q = _question(page, label)
    await lab.wait_for(timeout=TIMEOUT)
    await q.locator("input, textarea").first.fill(text)


async def _pick(page, label: str, option: str):
    """Радиокнопка или галочка с текстом option в вопросе label."""
    lab, q = _question(page, label)
    await lab.wait_for(timeout=TIMEOUT)
    await q.get_by_text(option, exact=True).first.click(timeout=TIMEOUT)


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", t.replace("ё", "е")).strip().lower()


async def choose(question: str, option: str, page=None) -> str | None:
    """Выбирает вариант option в выпадающем списке вопроса question."""
    page = page or _last_page
    if page is None or page.is_closed():
        return None
    try:
        await _choose(page, question, option)
        return f"{question}: {option}"
    except Exception as e:
        log.warning("Форма: не удалось выбрать «%s» в «%s» (%s)", option, question, e)
        return None


async def tick(text: str, page=None) -> str | None:
    """Отмечает в форме (по умолчанию — в последней открытой) вариант, в подписи которого
    есть text (галочка, кнопка-вариант). Повторная команда снимает галочку.
    Возвращает подпись варианта."""
    page = page or _last_page
    if page is None or page.is_closed():
        return None
    want = _norm(text)
    if not want or re.search(r"отправ|далее|назад", want):
        return None  # кнопки формы словами из подписи не нажимаем
    labels = page.locator("label")
    for i in range(await labels.count()):
        lab = labels.nth(i)
        caption = (await lab.inner_text()).strip()
        if caption and want in _norm(caption) and await lab.locator("input[type=checkbox], input[type=radio]").count():
            await lab.click(timeout=TIMEOUT)
            await page.bring_to_front()
            return caption
    return None


async def _upload(page, label: str, paths: list[str]):
    """Загружает файлы в вопрос с кнопкой «Загрузить»."""
    lab = page.get_by_text(label, exact=True).first
    await lab.wait_for(timeout=TIMEOUT)
    q = lab.locator("xpath=ancestor::*[.//input[@type='file']][1]")
    await q.locator("input[type=file]").first.set_input_files(paths)


KISART_ID_LABEL = os.getenv("FORM_KISART_ID_LABEL", "Номер ID в системе")
KISART_PHONE_LABEL = os.getenv("FORM_KISART_PHONE_LABEL", "Номер телефона из сайта")


async def type_if_shown(page, label_part: str, text: str) -> bool | None:
    """Вписывает text в поле, подпись которого содержит label_part, если поле есть в форме.
    None — поля нет (вопрос не открыт), True/False — получилось ли."""
    if page is None or page.is_closed():
        return None
    lab = page.get_by_text(re.compile(re.escape(label_part))).first
    if not await lab.count():
        return None
    try:
        q = lab.locator("xpath=ancestor::*[.//input[not(@type) or @type='text' or @type='tel'] or .//textarea][1]")
        await q.locator("input:not([type]), input[type=text], input[type=tel], textarea").first.fill(text)
        return True
    except Exception as e:
        log.warning("Форма: не удалось вписать «%s» (%s)", label_part, e)
        return False


async def fill_kisart(page, courier_id: str | None, phone: str | None) -> list[str]:
    """Поля КИСАРТ (появляются после пунктов регистрации, привязки и АТК): ID и телефон.
    Без ID форма просит поставить « - » и обязательно указать телефон."""
    problems = []
    # с альбома значения могут прийти через запятую — берём первое
    courier_id = (courier_id or "").split(",")[0].strip() or None
    phone = (phone or "").split(",")[0].strip() or None
    if await type_if_shown(page, KISART_ID_LABEL, courier_id or "-") is False:
        problems.append("ID КИСАРТ")
    shown = await type_if_shown(page, KISART_PHONE_LABEL, phone or "")
    if shown is False or (shown and not phone):
        problems.append("телефон КИСАРТ" + ("" if phone else " (не найден на фото)"))
    if shown and not courier_id:
        problems.append("ID КИСАРТ не найден на фото — поставил « - »")
    return problems


# файловые вопросы КИСАРТ: (часть подписи, какие скриншоты туда; обязателен ли)
KISART_UPLOADS = [
    ("Скриншот о успешной регистрации", "all", True),
    ("Фото с ПРО до привязки", "first", True),
    ("Фото с ПРО после привязки", "last", True),
    ("Фото ознакомления с АТК", "all", False),
]


async def _upload_if_shown(page, label_part: str, paths: list[str]) -> bool | None:
    lab = page.get_by_text(re.compile(re.escape(label_part))).first
    if not await lab.count():
        return None
    try:
        q = lab.locator("xpath=ancestor::*[.//input[@type='file']][1]")
        await q.locator("input[type=file]").first.set_input_files(list(paths)[:20])
        return True
    except Exception as e:
        log.warning("Форма: не удалось загрузить фото в «%s» (%s)", label_part, e)
        return False


async def upload_kisart(page, screens: list[str], photos: list[str] = ()) -> list[str]:
    """Фото альбома — в файловые вопросы КИСАРТ: в регистрацию и АТК — все фото,
    в привязку — первый скриншот (фото, где бот прочитал ID или телефон) «до», последний «после»."""
    problems = []
    if page is None or page.is_closed():
        return problems
    for label, which, required in KISART_UPLOADS:
        if which == "first":
            paths = screens[:1] if len(screens) >= 2 else []
        elif which == "last":
            paths = screens[-1:]
        else:
            paths = list(photos) or screens
        if not paths:
            if required and await page.get_by_text(re.compile(re.escape(label))).count():
                problems.append(f"«{label}» (нет скриншота)")
            continue
        if await _upload_if_shown(page, label, paths) is False:
            problems.append(f"«{label}»")
    return problems


AFTER_LABEL = os.getenv("FORM_AFTER_LABEL", "Фото после устранения нарушения внешнего вида")


async def _shown(page, label: str) -> bool:
    return bool(label) and bool(await page.get_by_text(label, exact=True).count())


async def upload_photos(page, paths: list[str]) -> list[str]:
    """Фото альбома — в «Фото до устранения…», а если в форме есть «Фото после устранения…»
    и фото больше одного — последнее фото туда (сначала снимают грязную сумку, потом чистую).
    Эти вопросы появляются только после галочки нарушения, поэтому вызывать после галочек.
    Возвращает, что не загрузилось."""
    paths = list(paths)
    if not paths or page is None or page.is_closed() or not await _shown(page, PHOTO_LABEL):
        return []
    before, after = paths, []
    if len(paths) > 1 and await _shown(page, AFTER_LABEL):
        before, after = paths[:-1], paths[-1:]
    problems = []
    for label, files, name in ((PHOTO_LABEL, before, "фото до устранения"),
                               (AFTER_LABEL, after, "фото после устранения")):
        if not files:
            continue
        try:
            await _upload(page, label, files[:20])  # форма берёт до 20 файлов
        except Exception as e:
            log.warning("Форма: не удалось загрузить %s (%s)", name, e)
            problems.append(name)
    return problems


async def fill(bag: str | None, photo_service: str | None = None):
    """Открывает форму в новой вкладке и заполняет что может.
    Возвращает (список проблем, вкладка формы)."""
    problems = []
    async with _lock:
        browser = await _get_browser()
        page = await browser.new_page()
        global _last_page
        _last_page = page
        await page.goto(FORM_URL)
        await page.bring_to_front()

        if CITY:
            try:
                await _choose(page, CITY_LABEL, CITY)
                nxt = page.get_by_role("button", name=re.compile(r"^\s*Далее\s*$"))
                await nxt.click(timeout=TIMEOUT)
            except Exception as e:
                log.warning("Форма: не удалось выбрать город (%s)", e)
                problems.append("город / переход на вторую страницу")
                return problems, page  # без второй страницы дальше заполнять нечего

        if HELPER:
            try:
                await _choose(page, HELPER_LABEL, HELPER)
            except Exception as e:
                log.warning("Форма: не удалось выбрать велопомощника (%s)", e)
                problems.append("велопомощник")

        if bag:
            try:
                await _type(page, BAG_LABEL, bag)
            except Exception as e:
                log.warning("Форма: не удалось вписать номер сумки (%s)", e)
                problems.append("номер сумки")
        else:
            problems.append("номер сумки (не найден на фото)")

        if TRANSPORT:
            try:
                await _pick(page, TRANSPORT_LABEL, TRANSPORT)
            except Exception as e:
                log.warning("Форма: не удалось выбрать способ передвижения (%s)", e)
                problems.append("способ передвижения")

        # сервис по номеру сумки; без номера — по надписи на термосумке (если узнали)
        service = service_for(bag) or photo_service
        if service:
            try:
                await _pick(page, SERVICE_LABEL, service)
            except Exception as e:
                log.warning("Форма: не удалось выбрать сервис (%s)", e)
                problems.append("сервис")
        else:
            problems.append("сервис Еда/Лавка")
    return problems, page
