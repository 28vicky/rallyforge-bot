import time
import rally_bot as rb


def _mem(monkeypatch):
    st = {}
    monkeypatch.setattr(rb, "load_duty_state", lambda: st)
    monkeypatch.setattr(rb, "save_duty_state", lambda d: None)
    return st


def test_cut_off_task_goes_first_after_restart(monkeypatch):
    _mem(monkeypatch)
    logs = []
    rb.task_running_set("s1", "duty:champions")
    rb.run_now_set("s1", ["duty:wheel"])
    rb.resume_interrupted_task("s1", logs.append)
    assert rb.run_now_list("s1") == ["duty:champions", "duty:wheel"] and "cut off" in logs[0]
    rb.resume_interrupted_task("s1", logs.append)           # picked up once only
    assert len(logs) == 1


def test_mapping_is_never_resumed(monkeypatch):
    _mem(monkeypatch)
    rb.task_running_set("s1", "duty:map_city")
    rb.resume_interrupted_task("s1", lambda *a: None)
    assert rb.run_now_list("s1") == []


def test_old_cut_off_task_is_dropped(monkeypatch):
    st = _mem(monkeypatch)
    st["_running"] = {"s1": {"name": "duty:wheel", "at": time.time() - 7200}}
    rb.resume_interrupted_task("s1", lambda *a: None)
    assert rb.run_now_list("s1") == []
