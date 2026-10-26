"""离线自检：接取任务以大地图左下角 HUD 为准（不连设备，用猴子补丁）。

用法：
    python tools/selftest_accept_hud.py
"""

from __future__ import annotations

import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agent"))
os.chdir(ROOT)

import action.fight.fight_utils as fu  # noqa: E402

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name} {detail}")


class FakeRecords:
    def __init__(self):
        self.pending = {}
        self.blacklist = []

    def set_pending(self, city="", task_name=""):
        self.pending = {"city": city, "task": task_name}

    def get_pending(self):
        return self.pending

    def add_blacklist(self, name):
        self.blacklist.append(name)


def patch(
    *,
    hud="",
    accepted=False,
    board_name="",
    retry=False,
    fail_mode="retry",
    abandon=None,
):
    calls = {"ensure": 0, "panel": 0, "accept": 0, "abandon": 0, "dismiss": 0}
    records = FakeRecords()

    fu.ensure_at_bigmap = lambda context: True
    fu._read_map_task_name = lambda context: hud
    fu._read_accepted_task_name = lambda context: board_name
    fu._current_city = lambda context: "暮角城"
    fu.task_records = records

    def ensure_task_accepted(context):
        calls["ensure"] += 1
        return accepted

    def open_city_task_panel(context):
        calls["panel"] += 1
        return True

    def accept_new_task(context):
        calls["accept"] += 1
        return True

    fu.ensure_task_accepted = ensure_task_accepted
    fu.open_city_task_panel = open_city_task_panel
    fu._accept_new_task = accept_new_task

    fu.dismiss_battle_result = lambda context: calls.update(dismiss=calls["dismiss"] + 1)
    fu._task_fail_mode = lambda context: fail_mode
    fu._retry_failed_task = lambda context, name="": retry

    def abandon_task(context, max_swipes=5):
        calls["abandon"] += 1
        return True

    fu.abandon_accepted_task = abandon_task
    return calls, records


class FakeContext:
    tasker = types.SimpleNamespace(stopping=False)


def test_hud_has_task() -> None:
    print("[接任务] 左下角已有任务")
    calls, records = patch(hud="远征任务·暮角城")
    ok = fu._preprocess_accept_task(FakeContext())
    check("直接继续", ok is True)
    check("没有打开任务板", calls["panel"] == 0 and calls["ensure"] == 0, str(calls))
    check("记录了 HUD 任务名", records.pending.get("task") == "远征任务·暮角城", str(records.pending))


def test_hud_empty_but_board_accepted() -> None:
    print("[接任务] HUD 没读到、任务板已接取")
    calls, records = patch(hud="", accepted=True, board_name="任务板任务")
    ok = fu._preprocess_accept_task(FakeContext())
    check("继续已接取任务", ok is True)
    check("用任务板名字兜底", records.pending.get("task") == "任务板任务", str(records.pending))
    check("没有接新任务", calls["accept"] == 0, str(calls))


def test_no_task_accept() -> None:
    print("[接任务] 没有任务才接")
    calls, _ = patch(hud="", accepted=False)
    ok = fu._preprocess_accept_task(FakeContext())
    check("接取流程成功", ok is True)
    check("打开任务板并接取", calls["panel"] == 1 and calls["accept"] == 1, str(calls))


def test_fail_retry_keeps_task() -> None:
    print("[失败处理] 继续任务：任务没结束不放弃")
    calls, _ = patch(hud="还在的任务", retry=False, fail_mode="retry")
    ok = fu._handle_task_failure(FakeContext())
    check("返回可继续", ok is True)
    check("没有放弃任务", calls["abandon"] == 0, str(calls))


def test_fail_retry_task_ended() -> None:
    print("[失败处理] 继续任务：任务已结束")
    calls, _ = patch(hud="", retry=False, fail_mode="retry")
    ok = fu._handle_task_failure(FakeContext())
    check("返回可继续", ok is True)
    check("没有放弃任务", calls["abandon"] == 0, str(calls))


def test_fail_switch_still_abandons() -> None:
    print("[失败处理] 换任务：仍然放弃")
    calls, _ = patch(hud="打不过的任务", retry=False, fail_mode="switch")
    ok = fu._handle_task_failure(FakeContext())
    check("返回可继续", ok is True)
    check("放弃了任务", calls["abandon"] == 1, str(calls))


def main() -> int:
    test_hud_has_task()
    test_hud_empty_but_board_accepted()
    test_no_task_accept()
    test_fail_retry_keeps_task()
    test_fail_retry_task_ended()
    test_fail_switch_still_abandons()
    print(f"\n自检完成: 通过 {PASSED}，失败 {FAILED}")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
