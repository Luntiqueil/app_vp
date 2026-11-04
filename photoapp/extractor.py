"""Бесплатное распознавание: Tesseract OCR + поиск по шаблонам из fields.json."""
import json
import re
from collections import Counter
from pathlib import Path

import pytesseract
from PIL import Image, ImageOps


def load_config(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _variants(img: Image.Image):
    """Несколько вариантов картинки — разные фото лучше читаются по-разному."""
    img = ImageOps.exif_transpose(img).convert("L")
    if max(img.size) < 1600:  # мелкий текст читается лучше после увеличения
        k = 1600 / max(img.size)
        img = img.resize((int(img.width * k), int(img.height * k)), Image.LANCZOS)
    yield ImageOps.autocontrast(img)
    yield ImageOps.autocontrast(img).point(lambda p: 255 if p > 150 else 0)


def ocr_text(img: Image.Image, lang: str) -> str:
    chunks = []
    for v in _variants(img):
        for psm in (6, 11):  # 6 — блок текста, 11 — разрозненный текст (бирки)
            chunks.append(
                pytesseract.image_to_string(v, lang=lang, config=f"--psm {psm}")
            )
    return "\n".join(chunks)


def normalize_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits[0] in "78":
        return "7" + digits[1:]
    if len(digits) == 10:
        return "7" + digits
    return raw.strip()


# кириллические буквы, похожие на латинские (OCR часто путает их)
_LATIN = str.maketrans("АВЕКМНОРСТХУавекмнорстху", "ABEKMHOPCTXYABEKMHOPCTXY")


def find_field_ranked(field: dict, text: str):
    """Ищем по шаблонам по порядку (раньше в списке = важнее). Возвращаем
    (значение, номер шаблона). Из нескольких распознаваний берём самое частое."""
    if "options" in field:
        return find_option(field, text)
    patterns = field["patterns"]
    offset = 0
    inv = field.get("inv_if")
    if inv and re.search(inv, text, re.IGNORECASE):
        patterns = field["inv_patterns"]  # экран «Инвентарь»: ищем по подписям плиток
    else:
        skip = field.get("skip_if")
        if skip and re.search(skip, text, re.IGNORECASE):
            return None, None  # это не то фото (например, другой скриншот приложения)
        offset = len(field.get("inv_patterns", []))  # физическая сумка — после инвентаря
    for rank, pattern in enumerate(patterns, offset if patterns is field["patterns"] else 0):
        found = []
        for m in re.finditer(pattern, text, re.IGNORECASE | re.MULTILINE):
            value = (m.group(1) if m.groups() else m.group(0)).strip()
            if field.get("normalize") == "phone":
                value = normalize_phone(value)
            if field.get("compact"):
                value = re.sub(r"\s+", "", value)
            if field.get("latin"):
                value = value.translate(_LATIN)
            if field.get("upper"):
                value = value.upper()
            if value:
                found.append(value)
        if found:
            return Counter(found).most_common(1)[0][0], rank
    return None, None


def find_option(field: dict, text: str):
    """Поле-выбор (например, сервис по надписи на термосумке): считаем, сколько раз
    встретились слова каждого варианта, побеждает вариант с наибольшим числом совпадений."""
    skip = field.get("skip_if")
    if skip and re.search(skip, text, re.IGNORECASE):
        return None, None
    hits = Counter()
    for name, patterns in field["options"].items():
        for pattern in patterns:
            hits[name] += len(re.findall(pattern, text, re.IGNORECASE))
    best = [(n, k) for n, k in hits.most_common() if k]
    return (best[0][0], 0) if best else (None, None)


def find_field(field: dict, text: str):
    return find_field_ranked(field, text)[0]


def vision_rows(img: Image.Image):
    """Строки текста от Apple Vision: [(текст, уверенность, (x, y, w, h))].
    Координаты нормированы, начало — снизу слева. None, если Vision недоступен."""
    try:
        from ocrmac import ocrmac
    except Exception:
        return None
    try:
        rgb = ImageOps.exif_transpose(img).convert("RGB")
        result = ocrmac.OCR(
            rgb,
            language_preference=["ru-RU", "en-US"],
            recognition_level="accurate",
        ).recognize()
    except Exception:
        return None
    # сверху вниз = по убыванию y, внутри строки — слева направо
    return sorted(result, key=lambda r: (-round(r[2][1] + r[2][3] / 2, 2), r[2][0]))


def vision_text(img: Image.Image):
    rows = vision_rows(img)
    return None if rows is None else "\n".join(r[0] for r in rows)


# --- коды на плитках «Инвентарь»: код стоит НАД подписью («Термобокс на Байке») ---
_TO_LETTER = {"0": "O", "1": "I", "5": "S", "8": "B", "6": "G", "2": "Z"}
_TO_DIGIT = {"O": "0", "I": "1", "L": "1", "S": "5", "B": "8", "G": "6", "Z": "2", "/": "7"}
# (слово в подписи, название для текста, форма кода: L — буква, D — цифра)
_TILES = [("термобокс", "Термобокс", "LLDDDL"), ("термосумк", "Термосумка", "LLDDDD")]
_ALNUM = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


def _fix_code(raw: str, shape: str):
    """Приводим прочитанное к форме кода: на местах букв — буквы, на местах цифр — цифры."""
    t = re.sub(r"[^A-Za-zА-Яа-я0-9/]", "", raw).upper().translate(_LATIN)
    if len(t) != len(shape):
        return None
    out = []
    for ch, kind in zip(t, shape):
        if kind == "L":
            ch = _TO_LETTER.get(ch, ch)
            if not ch.isalpha():
                return None
        else:
            ch = _TO_DIGIT.get(ch, ch)
            if not ch.isdigit():
                return None
        out.append(ch)
    return "".join(out)


def _reread(img: Image.Image, bbox):
    """Перечитываем маленький кусок с кодом в увеличенном виде разными способами."""
    W, H = img.size
    x, y, w, h = bbox
    px, py = 0.015, 0.006
    box = (max(0, int((x - px) * W)), max(0, int((1 - (y + h) - py) * H)),
           min(W, int((x + w + px) * W)), min(H, int((1 - y + py) * H)))
    crop = ImageOps.exif_transpose(img).convert("RGB").crop(box)
    k = max(3, int(700 / max(crop.width, 1)))
    crop = crop.resize((crop.width * k, crop.height * k), Image.LANCZOS)
    crop = ImageOps.expand(crop, border=30, fill="white")
    gray = ImageOps.autocontrast(crop.convert("L"))
    cands = []
    for psm in (7, 8):
        try:
            cands.append(pytesseract.image_to_string(
                gray, lang="eng",
                config=f"--psm {psm} -c tessedit_char_whitelist={_ALNUM}"))
        except Exception:
            pass
    try:
        from ocrmac import ocrmac
        res = ocrmac.OCR(crop, language_preference=["en-US"],
                         recognition_level="accurate").recognize()
        cands.append(" ".join(r[0] for r in res))
    except Exception:
        pass
    return cands


def tile_codes(img: Image.Image, rows):
    """Для подписей «Термобокс»/«Термосумка» находим код прямо над ними и
    перечитываем его крупно. Возвращает [(слово, код, кандидаты)]."""
    found = []
    for key, word, shape in _TILES:
        for text, _, (cx, cy, cw, ch) in rows:
            if key not in text.lower():
                continue
            best = None
            for t2, _, (x, y, w, h) in rows:
                gap = (y + h / 2) - (cy + ch)
                overlap = min(x + w, cx + cw) - max(x, cx)
                if t2 != text and 0 <= gap <= 0.09 and overlap > 0 \
                        and any(c.isdigit() for c in t2) and 4 <= len(t2.strip()) <= 9:
                    if best is None or gap < best[0]:
                        best = (gap, t2, (x, y, w, h))
            if not best:
                continue
            cands = [best[1]] + _reread(img, best[2])
            fixed = [c for c in (_fix_code(c, shape) for c in cands) if c]
            if fixed:
                found.append((word, Counter(fixed).most_common(1)[0][0], cands))
    return found


def extract(img: Image.Image, cfg: dict) -> tuple[dict, str]:
    """Сначала Apple Vision (если есть), недостающие поля добираем через Tesseract.
    В values["_ranks"] лежат приоритеты найденных значений (меньше = важнее)."""
    engine = cfg.get("engine", "auto")  # auto | vision | tesseract
    values = {f["key"]: None for f in cfg["fields"]}
    ranks = {}
    raw_parts = []

    def fill(text, only_missing):
        for f in cfg["fields"]:
            if only_missing and values[f["key"]] is not None:
                continue
            v, r = find_field_ranked(f, text)
            if v is not None:
                values[f["key"]], ranks[f["key"]] = v, r

    if engine in ("auto", "vision"):
        rows = vision_rows(img)
        if rows:
            text = "\n".join(r[0] for r in rows)
            try:
                for word, code, cands in tile_codes(img, rows):
                    text += f"\n{word} {code}"
                    raw_parts.append(f"[Код у подписи] {word} {code}  (варианты: {cands})")
            except Exception:
                pass
            raw_parts.insert(0, "[Vision] " + text)
            fill(text, only_missing=False)

    if engine in ("auto", "tesseract") and any(v is None for v in values.values()):
        text = ocr_text(img, cfg.get("ocr_lang", "rus+eng"))
        raw_parts.append("[Tesseract] " + text)
        fill(text, only_missing=True)

    values["_ranks"] = ranks
    return values, "\n".join(raw_parts)
