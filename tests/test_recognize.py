import pytest

from photoapp import form_filler, recognize
from photoapp.extractor import load_config
from photoapp.recognize import (
    analyze,
    bag_from_note,
    build_caption,
    merge,
    ticks_from_note,
    visible_note,
)

CFG = load_config(recognize.FIELDS_FILE)


@pytest.mark.parametrize("bag,service", [
    ("AA1234", "Лавка"), ("ab1234", "Лавка"), ("TA1234", "Лавка"), ("PY1234", "Еда"), (None, None),
])
def test_service_rules(bag, service):
    assert form_filler.service_for(bag) == service


def test_merge_prefers_higher_priority_value():
    results = [({"bag_number": "OB2593", "_ranks": {"bag_number": 1}}, ""),
               ({"bag_number": "XE678H", "_ranks": {"bag_number": 0}}, "")]
    assert merge(CFG, results)["bag_number"] == "XE678H"


def test_merge_joins_equal_ids():
    results = [({"id": "111"}, ""), ({"id": "222"}, ""), ({"id": "111"}, "")]
    assert merge(CFG, results)["id"] == "111, 222"


@pytest.mark.parametrize("note,bag", [
    ("py 1234", "PY1234"), ("сумка РY1234 грязная", "PY1234"), ("наклейки", None), ("79991234567", None),
])
def test_bag_from_note(note, bag):
    assert bag_from_note(note) == bag


def test_ticks_from_note_uses_shortcuts_and_commands():
    ticks = ticks_from_note("наклейки\nотметь Выдан номер")
    assert ticks == ["Выдан номер", "Отклеены наклейки на сумке"]


def test_visible_note_hides_form_commands():
    assert visible_note("наклейки\nкурьер спорил\nотметь Выдан номер") == "курьер спорил"


def test_caption_with_only_bag_shows_one_line():
    assert build_caption(CFG, {"bag_number": "PY1234"}, "") == "<b>Номер сумки:</b> PY1234"


def test_caption_shows_all_fields_when_more_found_and_escapes_note():
    caption = build_caption(CFG, {"phone": "79990000000"}, "a<b")
    assert caption.startswith("a&lt;b\n\n")
    assert "<b>Номер сумки:</b> —" in caption


def test_analyze_prefers_override_then_note_then_photo(photo, monkeypatch):
    def fake_extract(_img, _cfg):
        return ({"bag_number": "PY1111", "service": "Лавка", "_ranks": {}}, "")

    monkeypatch.setattr(recognize, "extract", fake_extract)
    assert analyze([photo], "").bag == "PY1111"
    assert analyze([photo], "сумка PY2222").bag == "PY2222"
    result = analyze([photo], "сумка PY2222", "PY3333")
    assert result.bag == "PY3333"
    assert "PY3333" in result.caption
    assert result.is_screen == (False,)
