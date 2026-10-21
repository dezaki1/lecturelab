"""Keep-awake: what LectureLab asks Windows for (a fake instead of the real system call)."""

from __future__ import annotations

from backend.core import awake


def test_asks_for_the_right_thing_and_gives_it_back() -> None:
    calls: list[int] = []
    state = {"recording": False, "busy": False}
    k = awake.KeepAwake(
        lambda: state["recording"],
        set_state=calls.append,
        is_busy=lambda: state["busy"],
    )
    k.check()
    assert calls == []  # nothing going on: Windows' rules untouched

    state["busy"] = True
    k.check()
    k.check()  # unchanged: no second call
    assert calls == [awake.ES_CONTINUOUS | awake.ES_SYSTEM_REQUIRED]

    state["recording"] = True  # slide capture needs the screen on
    k.check()
    assert calls[-1] & awake.ES_DISPLAY_REQUIRED

    state.update(recording=False, busy=False)
    k.check()
    assert calls[-1] == awake.ES_CONTINUOUS


def test_stopping_releases_the_request() -> None:
    calls: list[int] = []
    k = awake.KeepAwake(lambda: True, check_s=0.01, set_state=calls.append, is_busy=lambda: True)
    k.start()
    k.stop()
    assert calls[0] & awake.ES_SYSTEM_REQUIRED and calls[-1] == awake.ES_CONTINUOUS


def test_pending_jobs_count_as_busy(client) -> None:  # type: ignore[no-untyped-def]
    from backend.core.db import session_scope
    from backend.core.models import Job

    assert not awake.jobs_pending()
    with session_scope() as s:
        s.add(Job(kind="subject_hub"))
        s.commit()
    assert awake.jobs_pending()
