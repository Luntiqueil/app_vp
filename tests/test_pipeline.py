import asyncio
import shutil

import pytest

from photoapp import jobs as j
from photoapp.form_flow import HELD, SUBMITTED, UNCONFIRMED, FormOutcome
from photoapp.jobs import JobStore
from photoapp.pipeline import Pipeline
from photoapp.recognize import Recognition
from photoapp.telegram_sender import TelegramError, TelegramUnknown

REC = Recognition(bag="PY1234", service=None, courier_id=None, phone=None, is_screen=(False,), caption="подпись")


class Sender:
    def __init__(self, error=None):
        self.error = error
        self.sent = []

    async def send(self, photos, caption):
        if self.error:
            raise self.error
        self.sent.append((list(photos), caption))


class Runner:
    def __init__(self, outcome=None, error=None):
        self.outcome = outcome or FormOutcome(SUBMITTED, ())
        self.error = error
        self.calls = []

    async def __call__(self, request, may_submit, force, on_start):
        self.calls.append((request, may_submit, force))
        if self.error:
            await on_start()
            raise self.error
        if self.outcome.state == SUBMITTED:
            await on_start()
        return self.outcome


@pytest.fixture
def store(tmp_path, photo):
    jobs = JobStore(tmp_path / "data")
    folder = jobs.folder("a")
    folder.mkdir(parents=True)
    shutil.copy(photo, folder / "photo_1.jpg")
    jobs.create("a", "наклейки", ("photo_1.jpg",), "digest")
    return jobs


def pipeline(store, sender, runner, auto_submit=True):
    return Pipeline(store, sender, runner, auto_submit, recognize=lambda *_args: REC)


def process(pipe):
    return asyncio.run(pipe.process("a"))


def test_full_success_sends_to_telegram_and_form(store):
    sender, runner = Sender(), Runner()
    job = process(pipeline(store, sender, runner))
    assert (job.status, job.telegram, job.form) == (j.DONE, j.SENT, j.SUBMITTED)
    assert job.bag == "PY1234" and job.service == "Еда"
    assert len(sender.sent) == 1
    assert runner.calls[0][0].ticks == ("Отклеены наклейки на сумке",)


def test_telegram_failure_does_not_block_the_form(store):
    job = process(pipeline(store, Sender(TelegramError("нет")), Runner()))
    assert (job.status, job.telegram, job.form) == (j.FAILED, j.FAILED, j.SUBMITTED)
    assert "Telegram" in job.message


def test_retry_after_telegram_failure_does_not_submit_form_again(store):
    runner = Runner()
    process(pipeline(store, Sender(TelegramError("нет")), runner))
    store.update("a", force=True)
    sender = Sender()
    job = process(pipeline(store, sender, runner))
    assert job.status == j.DONE
    assert len(runner.calls) == 1 and len(sender.sent) == 1


def test_form_crash_does_not_resend_telegram_on_retry(store):
    sender = Sender()
    process(pipeline(store, sender, Runner(outcome=FormOutcome(HELD, ("x",)))))
    store.update("a", force=True)
    runner = Runner()
    job = process(pipeline(store, sender, runner))
    assert job.status == j.DONE
    assert len(sender.sent) == 1
    assert runner.calls[0][2] is True
    assert job.force is False  # разрешение действует только на один проход


def test_held_form_needs_review_and_says_so(store):
    job = process(pipeline(store, Sender(), Runner(outcome=FormOutcome(HELD, ("нет фото",)))))
    assert (job.status, job.form) == (j.REVIEW, j.HELD)
    assert job.problems == ("нет фото",)
    assert "не отправлена" in job.message


def test_auto_submit_off_passes_no_permission(store):
    runner = Runner(outcome=FormOutcome(HELD, ()))
    process(pipeline(store, Sender(), runner, auto_submit=False))
    assert runner.calls[0][1] is False


def test_person_confirming_allows_submit_when_auto_submit_off(store):
    store.update("a", force=True)
    runner = Runner()
    process(pipeline(store, Sender(), runner, auto_submit=False))
    assert runner.calls[0][1] is True


def test_crash_after_click_is_unconfirmed_and_not_repeated(store):
    runner = Runner(error=RuntimeError("упал браузер"))
    job = process(pipeline(store, Sender(), runner))
    assert job.form == j.UNCONFIRMED and job.status == j.REVIEW
    process(pipeline(store, Sender(), runner))
    assert len(runner.calls) == 1


def test_unconfirmed_outcome_is_reported(store):
    job = process(pipeline(store, Sender(), Runner(outcome=FormOutcome(UNCONFIRMED, ()))))
    assert job.status == j.REVIEW
    assert "не подтвердила" in job.message


def test_telegram_unknown_is_not_resent_without_person(store):
    sender = Sender(TelegramUnknown("ReadTimeout"))
    job = process(pipeline(store, sender, Runner()))
    assert job.telegram == j.UNKNOWN and job.status == j.REVIEW
    ok = Sender()
    process(pipeline(store, ok, Runner()))
    assert ok.sent == []


def test_recognition_failure_marks_job_failed(store):
    def broken(*_args):
        raise ValueError("плохое фото")

    job = asyncio.run(Pipeline(store, Sender(), Runner(), recognize=broken).process("a"))
    assert job.status == j.FAILED


def test_recover_requeues_unfinished_but_not_a_possible_submit(tmp_path):
    jobs = JobStore(tmp_path)
    for name in ("queued", "filling", "submitting", "submitted", "done"):
        jobs.create(name, "", (), name)
    jobs.update("filling", status=j.PROCESSING, form=j.FILLING, telegram=j.SENDING)
    jobs.update("submitting", status=j.PROCESSING, form=j.SUBMITTING)
    jobs.update("submitted", status=j.PROCESSING, form=j.SUBMITTED)
    jobs.update("done", status=j.DONE, form=j.SUBMITTED)

    reloaded = JobStore(tmp_path)
    requeued = reloaded.recover()

    assert set(requeued) == {"queued", "filling", "submitted"}
    assert reloaded.get("filling").form == j.PENDING
    assert reloaded.get("filling").telegram == j.UNKNOWN
    assert reloaded.get("submitted").form == j.SUBMITTED
    assert reloaded.get("submitting").form == j.UNCONFIRMED
    assert reloaded.get("submitting").status == j.REVIEW
