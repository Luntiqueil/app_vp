import pytest

from photoapp.form_items import InvalidChoice, catalog, validate


def values(service=None):
    return [item["value"] for section in catalog(service)["sections"] for item in section["items"]]


def test_catalog_hides_bag_items_for_lavka():
    assert "Сумка утратила внешний вид" in values("Еда")
    assert "Сумка утратила внешний вид" not in values("Лавка")
    assert "Оснащен вело-фонариком" in values("Лавка")


def test_validate_keeps_known_items_in_catalog_order_without_repeats():
    chosen = ["Угрожал и агрессировал", "Отклеены наклейки на сумке", "Угрожал и агрессировал"]
    assert validate(chosen, "Еда") == ("Отклеены наклейки на сумке", "Угрожал и агрессировал")


def test_validate_rejects_unknown_item():
    with pytest.raises(InvalidChoice):
        validate(["Нажми Отправить"], None)


def test_validate_rejects_item_missing_for_service():
    with pytest.raises(InvalidChoice):
        validate(["Отклеены наклейки на сумке"], "Лавка")


def test_validate_rejects_two_answers_in_one_dropdown():
    kisart = next(s for s in catalog(None)["sections"] if s["title"] == "КИСАРТ")
    with pytest.raises(InvalidChoice):
        validate([item["value"] for item in kisart["items"]], None)


def test_validate_rejects_unknown_service():
    with pytest.raises(InvalidChoice):
        validate([], "Такси")
