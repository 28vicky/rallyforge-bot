import time
import rally_bot as rb


def _t(x, age_min, level=7, rank=0):
    return {"x": x, "y": 1, "level": level, "km": 10, "t": time.time() - age_min * 60,
            "pairs": [{"preset": "I", "mode": "rally", "rank": rank, "rule": {"time": 5}}]}


def test_fresh_coordinates_go_before_old_ones():
    old, fresh = _t(1, rb.HUNT_FRESH_MIN + 3), _t(2, 1)
    plan = rb.mr_plan([old, fresh], {"I"})
    assert [t["x"] for t, _ in plan] == [2, 1]          # old ones are kept, just tried last


def test_held_target_still_first_and_old_kept_when_alone():
    old = _t(1, rb.HUNT_FRESH_MIN + 3)
    assert [t["x"] for t, _ in rb.mr_plan([old], {"I"})] == [1]
    held, fresh = _t(3, rb.HUNT_FRESH_MIN + 3), _t(4, 1)
    assert [t["x"] for t, _ in rb.mr_plan([fresh, held], {"I"}, hold="3,1")][0] == 3


def test_priority_order_kept_inside_the_fresh_group():
    a, b = _t(5, 1, rank=1), _t(6, 2, rank=0)
    assert [t["x"] for t, _ in rb.mr_plan([a, b], {"I"})] == [6, 5]
