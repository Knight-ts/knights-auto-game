"""离线自检：海盗事件的自动/手动分支（不连设备，用假 Context）。

用法：
    python tools/selftest_pirate_event.py
"""

from __future__ import annotations

import os
import sys
import types

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agent"))
os.chdir(ROOT)

from action.fight.fight_processor import detect_and_manage_event  # noqa: E402

PASSED = 0
FAILED = 0
MARKER = os.path.join(ROOT, "temp", "pirate_alert.json")


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name} {detail}")


class FakeResult:
    def __init__(self, hit: bool, text: str = ""):
        self.hit = hit
        self.best_result = types.SimpleNamespace(text=text, box=[0, 0, 10, 10]) if hit else None
        self.all_results = [types.SimpleNamespace(text=text)] if text else []


class FakeController:
    def post_screencap(self):
        job = types.SimpleNamespace(wait=lambda: job, get=lambda: np.zeros((1280, 720, 3), dtype=np.uint8))
        return job


class FakeContext:
    def __init__(self, mode: str | None = "auto"):
        self.mode = mode
        self.calls: list[str] = []
        self.stopped = False
        self.controller = FakeController()
        self.tasker = types.SimpleNamespace(
            controller=self.controller,
            stopping=False,
            post_stop=self._stop,
        )

    def _stop(self):
        self.stopped = True

    def run_task(self, entry, pipeline_override=None):
        self.calls.append(entry)
        return types.SimpleNamespace(status=types.SimpleNamespace(succeeded=True))

    def run_recognition(self, node, image, pipeline_override=None):
        return FakeResult(node == "Event_PirateRaid", "海盗正在袭扰你的庄园")

    def get_node_data(self, name):
        if name == "PirateSelect" and self.mode is not None:
            return {"recognition": {"param": {"expected": [self.mode]}}}
        return None


def cleanup() -> None:
    if os.path.exists(MARKER):
        os.remove(MARKER)


def test_manual() -> None:
    print("[海盗事件] 手动处理")
    cleanup()
    context = FakeContext("manual")
    event = detect_and_manage_event(context, None)
    check("识别为手动分支", event == "pirate_manual", str(event))
    check("没有自动开战", "Event_PirateRaid_Dispatch" not in context.calls, str(context.calls))
    check("暂停了任务", context.stopped)
    check("留下手动标记", os.path.exists(MARKER))


def test_auto() -> None:
    print("[海盗事件] 自动战斗")
    cleanup()
    context = FakeContext("auto")
    event = detect_and_manage_event(context, None)
    check("识别为自动分支", event == "pirate_raid", str(event))
    check("调用了海盗任务", "Event_PirateRaid_Dispatch" in context.calls, str(context.calls))
    check("没有暂停", not context.stopped)
    check("没有手动标记", not os.path.exists(MARKER))


def test_default_auto() -> None:
    print("[海盗事件] 没配选项时默认自动")
    cleanup()
    context = FakeContext(None)
    event = detect_and_manage_event(context, None)
    check("默认走自动", event == "pirate_raid", str(event))
    check("调用了海盗任务", "Event_PirateRaid_Dispatch" in context.calls, str(context.calls))


def main() -> int:
    test_manual()
    test_auto()
    test_default_auto()
    cleanup()
    print(f"\n自检完成: 通过 {PASSED}，失败 {FAILED}")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
