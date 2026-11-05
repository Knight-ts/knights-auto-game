"""离线自检：接任务只看当前任务板一屏，不滑动、不刷新（不消耗水晶）。

用法：
    python tools/selftest_accept_no_refresh.py
"""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agent"))
os.chdir(ROOT)

import action.fight.fight_utils as fu  # noqa: E402
from action.zshg.task_hud_recognizer import HudTaskInfo  # noqa: E402

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


class FakeJob:
    succeeded = True

    def wait(self):
        return self

    def get(self):
        return None


class FakeController:
    def __init__(self):
        self.clicks = []

    def post_screencap(self):
        return FakeJob()

    def post_click(self, x, y):
        self.clicks.append((x, y))
        return FakeJob()


class FakeContext:
    def __init__(self):
        self.calls = []
        self.controller = FakeController()
        self.tasker = types.SimpleNamespace(controller=self.controller)

    def run_task(self, entry, pipeline_override=None):
        self.calls.append(entry)
        return types.SimpleNamespace(status=types.SimpleNamespace(succeeded=True))

    def get_node_data(self, name):
        return None


class FakeRecognizer:
    def __init__(self):
        self.items = []
        self.eligible = []

    def scan_pool(self, context, screenshot, **kwargs):
        return self.items, self.eligible

    def pick_best(self, eligible):
        return eligible[0] if eligible else None


def make_task(name: str) -> HudTaskInfo:
    return HudTaskInfo(
        task_type="讨伐任务",
        task_name=name,
        task_level="10",
        task_description="讨伐哥布林",
        action_text="接受",
        accept_button_box=[10, 10, 10, 10],
    )


def patch(recognizer: FakeRecognizer) -> FakeContext:
    fu._current_city = lambda context: "暮角城"
    fu.task_rules = types.SimpleNamespace(
        city_rule=lambda city: {"min_level": 0, "max_level": 120},
        effective_blacklist=lambda city: [],
        save_pool=lambda city, items: None,
    )
    fu.battle_memory = types.SimpleNamespace(
        memory=lambda: types.SimpleNamespace(rank_tasks=lambda *args: [])
    )
    fu.task_records = types.SimpleNamespace(set_pending=lambda **kwargs: None)
    fu.TaskHudRecognizer = lambda: recognizer
    return FakeContext()


def test_accept_from_screen() -> None:
    print("[接任务] 当前屏有可接任务")
    recognizer = FakeRecognizer()
    recognizer.items = [{"name": "讨伐A", "type": "讨伐任务", "level": "10", "eligible": True, "reason": "可接取"}]
    recognizer.eligible = [make_task("讨伐A")]
    context = patch(recognizer)

    ok = fu._accept_new_task(context)
    check("接取成功", ok is True)
    check("点了接受按钮", len(context.controller.clicks) == 1, str(context.controller.clicks))
    check("没有任何滑动/刷新", not any("Swipe" in call or "Refresh" in call for call in context.calls), str(context.calls))


def test_no_refresh_when_empty() -> None:
    print("[接任务] 当前屏没有可接任务")
    recognizer = FakeRecognizer()
    recognizer.items = [{"name": "配送B", "type": "配送任务", "level": "10", "eligible": False, "reason": "非战斗任务（送往）"}]
    recognizer.eligible = []
    context = patch(recognizer)

    ok = fu._accept_new_task(context)
    check("不接任务", ok is False)
    check("没有点接受按钮", not context.controller.clicks, str(context.controller.clicks))
    check("没有滑动/刷新", not any("Swipe" in call or "Refresh" in call for call in context.calls), str(context.calls))


def test_source_has_no_refresh() -> None:
    print("[接任务] 源码检查")
    source = (Path(ROOT) / "agent" / "action" / "fight" / "fight_utils.py").read_text(encoding="utf-8")
    start = source.index("def _accept_new_task(")
    end = source.index("def _pool_reason_summary(")
    body = source[start:end]
    check("没有 FindCityTask_Refresh", "FindCityTask_Refresh" not in body)
    check("没有 SwipeUp/SwipeDown", "SwipeUp" not in body and "SwipeDown" not in body)


def main() -> int:
    test_accept_from_screen()
    test_no_refresh_when_empty()
    test_source_has_no_refresh()
    print(f"\n自检完成: 通过 {PASSED}，失败 {FAILED}")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
