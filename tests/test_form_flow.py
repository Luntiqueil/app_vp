import asyncio

from photoapp import form_filler
from photoapp.form_flow import (
    HELD,
    NO_PHOTOS,
    NO_TICKS,
    SUBMITTED,
    UNCONFIRMED,
    FormRequest,
    hold_reasons,
    run_form,
    wanted_ticks,
)

STICKER = "Отклеены наклейки на сумке"


def request_for(photo, tmp_path, bag="PY1234", ticks=(STICKER,)):
    return FormRequest(
        bag=bag,
        service=None,
        ticks=tuple(ticks),
        courier_id=None,
        phone=None,
        photos=(str(photo),),
        is_screen=(False,),
        screenshot=tmp_path / "form.png",
    )


def run(request, may_submit=True, force=False):
    calls = []

    async def on_start():
        calls.append(1)

    async def scenario():
        try:
            return await run_form(request, may_submit, force, on_start)
        finally:
            await form_filler.close_browser()

    return asyncio.run(scenario()), calls


def test_hold_reasons_require_a_tick_and_attached_photos():
    assert hold_reasons([], ticked=1, attached=1) == []
    assert hold_reasons(["сервис"], ticked=1, attached=1) == ["сервис"]
    assert hold_reasons([], ticked=0, attached=0) == [NO_TICKS, NO_PHOTOS]


def test_wanted_ticks_removes_repeats_so_a_box_is_not_unticked():
    assert wanted_ticks(None, ["наклейки на сумке", "Отклеены наклейки на сумке"]) == ["наклейки на сумке"]


def test_complete_report_is_submitted_after_uploads_finish(form_server, photo, tmp_path):
    outcome, calls = run(request_for(photo, tmp_path))
    assert outcome == type(outcome)(SUBMITTED, ())
    assert form_server == ["/submit?pending=0"]  # отправлено, когда фото уже загрузились
    assert calls == [1]
    assert (tmp_path / "form.png").exists()


def test_without_any_tick_form_is_held_and_not_submitted(form_server, photo, tmp_path):
    outcome, calls = run(request_for(photo, tmp_path, ticks=()))
    assert outcome.state == HELD
    assert NO_TICKS in outcome.problems and NO_PHOTOS in outcome.problems
    assert form_server == [] and calls == []


def test_unknown_tick_holds_the_form(form_server, photo, tmp_path):
    outcome, _ = run(request_for(photo, tmp_path, ticks=(STICKER, "Такого пункта нет")))
    assert outcome.state == HELD
    assert form_server == []


def test_missing_bag_is_never_submitted_even_when_forced(form_server, photo, tmp_path):
    outcome, _ = run(request_for(photo, tmp_path, bag=None), force=True)
    assert outcome.state == HELD
    assert form_server == []


def test_auto_submit_off_fills_but_does_not_submit(form_server, photo, tmp_path):
    outcome, calls = run(request_for(photo, tmp_path), may_submit=False)
    assert outcome == type(outcome)(HELD, ())
    assert form_server == [] and calls == []


def test_person_can_force_a_report_without_photo_question(form_server, photo, tmp_path):
    outcome, _ = run(request_for(photo, tmp_path, ticks=("Отказался устранять замечания",)), force=True)
    assert outcome.state == SUBMITTED
    assert NO_PHOTOS in outcome.problems
    assert len(form_server) == 1


def test_unconfirmed_when_form_keeps_showing_submit_button(form_server, photo, tmp_path, monkeypatch):
    monkeypatch.setattr(form_filler, "FORM_URL", form_filler.FORM_URL + "?stuck=1")
    monkeypatch.setattr("photoapp.form_flow.SUBMIT_TIMEOUT_MS", 1000)
    outcome, calls = run(request_for(photo, tmp_path))
    assert outcome.state == UNCONFIRMED
    assert calls == [1]


def test_chosen_service_wins_over_bag_prefix(form_server, photo, tmp_path):
    async def scenario():
        try:
            _, page = await form_filler.fill("PY1234", None, chosen_service="Лавка")
            return await page.get_by_label("Лавка").is_checked()
        finally:
            await form_filler.close_browser()

    assert asyncio.run(scenario()) is True
