"""离线自检：任务失败处理（retry 真正重试 + 回退继续循环）。

用法：
    python tools/selftest_task_failure.py
"""

from __future__ import annotations

import os
import sys
import types
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agent"))
os.chdir(ROOT)

from action.fight import fight_utils  # noqa: E402

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


class FakeContext:
    def __init__(self):
        self.tasker = types.SimpleNamespace(stopping=False)

    def get_node_data(self, name):
        return None


def test_retry_succeeds() -> None:
    print("[失败处理] retry 第二次成功")
    context = FakeContext()
    calls = {"fight": 0, "abandon": 0, "dismiss": 0}

    def fake_fight(_context):
        calls["fight"] += 1
        return calls["fight"] >= 2

    with (
        patch.object(fight_utils, "_task_fail_mode", return_value="retry"),
        patch.object(fight_utils.task_records, "get_pending", return_value={"task": "永恒愿望", "city": "暮角城"}),
        patch.object(fight_utils, "_read_map_task_name", return_value=""),
        patch.object(fight_utils, "dismiss_battle_result", side_effect=lambda _c: calls.__setitem__("dismiss", calls["dismiss"] + 1)),
        patch.object(fight_utils, "ensure_at_bigmap", return_value=True),
        patch.object(fight_utils, "_process_fight", side_effect=fake_fight),
        patch.object(fight_utils, "finalize_task_record"),
        patch.object(fight_utils, "abandon_accepted_task", side_effect=lambda _c: calls.__setitem__("abandon", calls["abandon"] + 1) or True),
        patch.object(fight_utils.task_records, "set_pending"),
        patch.object(fight_utils, "_current_city", return_value="暮角城"),
    ):
        result = fight_utils._handle_task_failure(context)

    check("返回 True", result is True, str(result))
    check("共尝试 2 次战斗", calls["fight"] == 2, str(calls))
    check("成功后不放弃任务", calls["abandon"] == 0, str(calls))


def test_retry_falls_back() -> None:
    print("[失败处理] retry 全失败后放弃并继续")
    context = FakeContext()
    calls = {"fight": 0, "abandon": 0}

    def fake_fight(_context):
        calls["fight"] += 1
        return False

    with (
        patch.object(fight_utils, "_task_fail_mode", return_value="retry"),
        patch.object(fight_utils.task_records, "get_pending", return_value={"task": "永恒愿望", "city": "暮角城"}),
        patch.object(fight_utils, "_read_map_task_name", return_value=""),
        patch.object(fight_utils, "dismiss_battle_result"),
        patch.object(fight_utils, "ensure_at_bigmap", return_value=True),
        patch.object(fight_utils, "_process_fight", side_effect=fake_fight),
        patch.object(fight_utils, "finalize_task_record"),
        patch.object(fight_utils, "abandon_accepted_task", side_effect=lambda _c: calls.__setitem__("abandon", calls["abandon"] + 1) or True),
        patch.object(fight_utils.task_records, "set_pending"),
        patch.object(fight_utils, "_current_city", return_value="暮角城"),
    ):
        result = fight_utils._handle_task_failure(context)

    check("返回 True（不停止月度循环）", result is True, str(result))
    check("重试次数有界", calls["fight"] == fight_utils.TASK_FAILURE_RETRY_ATTEMPTS, str(calls))
    check("最终放弃任务", calls["abandon"] == 1, str(calls))


def test_switch_mode() -> None:
    print("[失败处理] 换任务模式")
    context = FakeContext()
    calls = {"abandon": 0}
    with (
        patch.object(fight_utils, "_task_fail_mode", return_value="switch"),
        patch.object(fight_utils.task_records, "get_pending", return_value={"task": "任务A"}),
        patch.object(fight_utils, "_read_map_task_name", return_value=""),
        patch.object(fight_utils, "dismiss_battle_result"),
        patch.object(fight_utils, "abandon_accepted_task", side_effect=lambda _c: calls.__setitem__("abandon", calls["abandon"] + 1) or True),
        patch.object(fight_utils, "_process_fight") as fight,
    ):
        result = fight_utils._handle_task_failure(context)
    check("返回 True", result is True)
    check("直接放弃", calls["abandon"] == 1, str(calls))
    check("不重试战斗", not fight.called)


def main() -> int:
    test_retry_succeeds()
    test_retry_falls_back()
    test_switch_mode()
    print(f"\n自检完成: 通过 {PASSED}，失败 {FAILED}")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
