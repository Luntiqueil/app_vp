"""Распознавание фото и текст подписи. Логика merge и подписи взята из bot.py в telegram-photo-bot."""
from __future__ import annotations

import html
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from PIL import Image

from . import form_filler
from .extractor import _LATIN, extract, load_config

FIELDS_FILE = form_filler.RULES_DIR / "fields.json"
CAPTION_KEYS = ("phone", "id", "bag_number")  # что пишем под фото в Telegram
TICK_RE = re.compile(r"^\s*отметь\s+(.+)$", re.IGNORECASE | re.MULTILINE)
BAG_IN_NOTE_RE = re.compile(r"(?<![A-ZА-Я0-9])([A-ZА-Я]{2})\s?(\d{4})(?![A-ZА-Я0-9])")


@dataclass(frozen=True)
class Recognition:
    bag: Optional[str]
    service: Optional[str]
    courier_id: Optional[str]
    phone: Optional[str]
    is_screen: Tuple[bool, ...]  # скриншоты КИСАРТ: фото, где нашлись ID или телефон
    caption: str


def merge(cfg: dict, results: Sequence[Tuple[dict, str]]) -> dict:
    """Значения со всех фото: побеждает самое приоритетное совпадение
    (термобокс важнее термосумки), равные по приоритету — через запятую."""
    merged = {}
    for field in cfg["fields"]:
        key = field["key"]
        cands = [(values.get("_ranks", {}).get(key, 0), values[key]) for values, _ in results if values.get(key)]
        best = min((rank for rank, _ in cands), default=None)
        top = [value for rank, value in cands if rank == best]
        if field.get("pick") == "most_common" and top:
            merged[key] = Counter(top).most_common(1)[0][0]
        else:
            merged[key] = ", ".join(dict.fromkeys(top))
    return merged


def bag_from_note(note: str) -> Optional[str]:
    """Номер сумки, написанный человеком, важнее распознанного."""
    found = BAG_IN_NOTE_RE.search(note.upper().translate(_LATIN))
    return found.group(1) + found.group(2) if found else None


def ticks_from_note(note: str) -> List[str]:
    """Пункты формы из пометки: «отметь …» и короткие слова («наклейки», «грязная»…)."""
    return TICK_RE.findall(note) + form_filler.shortcuts_for(TICK_RE.sub("", note))


def visible_note(note: str) -> str:
    """Пометка без команд для формы — она идёт в подпись Telegram."""
    lines = [
        line for line in note.split("\n")
        if not TICK_RE.match(line) and not form_filler.shortcuts_for(line)
    ]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def build_caption(cfg: dict, merged: dict, note: str) -> str:
    fields = [f for f in cfg["fields"] if f["key"] in CAPTION_KEYS]
    filled = [f for f in fields if merged.get(f["key"])]
    # нашёлся только номер сумки — остальные поля не показываем
    only_bag = bool(filled) and all(f["key"] == "bag_number" for f in filled)
    shown = filled if only_bag else (fields if filled else [])
    lines = [f"<b>{f['label']}:</b> {html.escape(merged.get(f['key']) or '—')}" for f in shown]
    parts = [html.escape(visible_note(note)), "\n".join(lines)]
    return "\n\n".join(part for part in parts if part)


def analyze(paths: Sequence[Path], note: str, bag_override: Optional[str] = None) -> Recognition:
    cfg = load_config(FIELDS_FILE)
    results = []
    for path in paths:
        with Image.open(path) as img:
            results.append(extract(img, cfg))
    merged = merge(cfg, results)
    candidates = [bag_override, bag_from_note(note), merged.get("bag_number")]
    candidates += [values.get("bag_number") for values, _ in results]
    bag = form_filler.pick_bag(candidates)
    # номер, который человек исправил или дописал, показываем и в подписи
    shown = {**merged, "bag_number": bag} if bag and (bag_override or bag_from_note(note)) else merged
    return Recognition(
        bag=bag,
        service=merged.get("service") or None,
        courier_id=merged.get("id") or None,
        phone=merged.get("phone") or None,
        is_screen=tuple(bool(values.get("id") or values.get("phone")) for values, _ in results),
        caption=build_caption(cfg, shown, note),
    )
