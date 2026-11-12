"""Пункты формы, которые человек отмечает сам на экране приложения (rules/form_items.json)."""
from __future__ import annotations

import json
from functools import lru_cache
from typing import Optional, Sequence, Tuple

from .form_filler import RULES_DIR

ITEMS_FILE = RULES_DIR / "form_items.json"


class InvalidChoice(ValueError):
    """Пункт, которого нет в форме для этого сервиса, или два ответа в одном списке."""


@lru_cache(maxsize=1)
def _load() -> dict:
    return json.loads(ITEMS_FILE.read_text(encoding="utf-8"))


def _shown(section: dict, service: Optional[str]) -> bool:
    return not service or service in section.get("services", [service])


def catalog(service: Optional[str] = None) -> dict:
    """Сервисы и разделы с пунктами; с сервисом — только то, что для него есть в форме."""
    data = _load()
    sections = [
        {"title": s["title"], "choice": bool(s.get("choice")), "services": s.get("services"), "items": s["items"]}
        for s in data["sections"]
        if _shown(s, service)
    ]
    return {"services": data["services"], "sections": sections}


def validate(chosen: Sequence[str], service: Optional[str]) -> Tuple[str, ...]:
    """Выбранные пункты в порядке формы, без повторов. Неизвестное — ошибка, а не пропуск:
    молча потерянный пункт ушёл бы в форму неотмеченным."""
    if service and service not in _load()["services"]:
        raise InvalidChoice(f"Нет такого сервиса: {service}")
    wanted = set(chosen)
    result = []
    for section in catalog(service)["sections"]:
        picked = [item["value"] for item in section["items"] if item["value"] in wanted]
        if section["choice"] and len(picked) > 1:
            raise InvalidChoice(f"В разделе «{section['title']}» можно выбрать только один вариант.")
        result.extend(picked)
    unknown = wanted - set(result)
    if unknown:
        raise InvalidChoice(f"Этого пункта нет в форме: {sorted(unknown)[0]}")
    return tuple(result)
