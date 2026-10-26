"""离线自检：城堡被攻打事件处理器（不连设备，用假 Context 走完整流程）。

用法：
    python tools/selftest_castle_event.py
"""

from __future__ import annotations

import os
import sys
import types

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agent"))
os.chdir(ROOT)

from action.fight.castle_defense_processor import CastleDefenseProcessor  # noqa: E402

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


class FakeResult:
    def __init__(self, hit: bool, text: str = ""):
        self.hit = hit
        self.best_result = types.SimpleNamespace(text=text, box=[0, 0, 10, 10]) if hit else None
        self.all_results = [types.SimpleNamespace(text=text)] if text else []


class FakeJob:
    succeeded = True

    def wait(self):
        return self

    def get(self):
        return np.zeros((1280, 720, 3), dtype=np.uint8)


class FakeController:
    def __init__(self, owner=None):
        self.owner = owner

    def post_screencap(self):
        return FakeJob()

    def post_press_key(self, key):
        if self.owner is not None:
            self.owner.pressed_keys.append(key)
        return FakeJob()


class FakeContext:
    """按节点名推进状态，模拟：地图横幅 → 城堡列表 → 防守窗口 → 战斗 → 胜利。"""

    def __init__(self, policy: str = "defend"):
        self.state = "map"
        self.policy = policy
        self.calls: list[str] = []
        self.pressed_keys: list[int] = []
        self.controller = FakeController(self)
        self.tasker = types.SimpleNamespace(
            controller=self.controller, stopping=False
        )

    # 记录任务调用并推进场景
    def run_task(self, entry, pipeline_override=None):
        self.calls.append(entry)
        if entry == "Event_CastleAttackBannerClick":
            self.state = "list"
        elif entry == "Event_CastleListGo":
            self.state = "defend"
        elif entry == "Event_CastleDefendButton":
            self.state = "battle"
        elif entry == "AutoFight_Start":
            self.state = "victory"
        elif entry in ("FightVictory", "FightResult_ReturnBigMap"):
            self.state = "bigmap"
        elif entry in ("UI_ReturnBigMap", "BackButton_500ms"):
            self.state = "bigmap"
        return types.SimpleNamespace(status=types.SimpleNamespace(succeeded=True))

    def run_recognition(self, node, image):
        hit = False
        text = ""
        if node == "Event_CastleAttacked":
            hit, text = self.state == "map", "1座城堡被攻打中。"
        elif node == "Event_CastleListWindow":
            hit, text = self.state == "list", "下列城堡正在被攻打中"
        elif node == "Event_CastleDefendWindow":
            hit, text = self.state == "defend", "防守要塞"
        elif node == "FightEndRound":
            hit = self.state == "battle"
        elif node in ("FightVictory", "FightFail"):
            hit, text = self.state == "victory", "战斗胜利"
        elif node == "UI_MainWindows":
            hit = self.state in ("map", "bigmap")
        elif node == "Event_CastleAttackText":
            return FakeResult(True, "1座城堡被攻打中。")
        return FakeResult(hit, text)

    def get_node_data(self, name):
        if name == "CastleDefenseSelect":
            return {"recognition": {"param": {"expected": [self.policy]}}}
        return None


def run_case(policy: str) -> FakeContext:
    context = FakeContext(policy)
    result = CastleDefenseProcessor().run(context, None)
    check(f"[{policy}] 处理器返回成功", bool(result.success), str(result))
    return context


def test_defend_flow() -> None:
    print("[城堡被攻打] 前往防守流程")
    context = run_case("defend")
    check("点击顶部横幅", "Event_CastleAttackBannerClick" in context.calls, str(context.calls))
    check("列表点前去", "Event_CastleListGo" in context.calls, str(context.calls))
    check("点防守要塞", "Event_CastleDefendButton" in context.calls, str(context.calls))
    check("进入主动战斗", "AutoFight_Start" in context.calls, str(context.calls))
    check("没有按返回忽略", not context.pressed_keys)


def test_ignore_flow() -> None:
    print("[城堡被攻打] 忽略流程")
    context = FakeContext("ignore")
    result = CastleDefenseProcessor().run(context, None)
    check("处理器返回成功", bool(result.success), str(result))
    check("不点前去", "Event_CastleListGo" not in context.calls, str(context.calls))
    check("不点防守要塞", "Event_CastleDefendButton" not in context.calls, str(context.calls))
    check("不进入战斗", "AutoFight_Start" not in context.calls, str(context.calls))
    check("按了返回键", len(context.pressed_keys) >= 1, str(context.pressed_keys))


def test_not_on_event() -> None:
    print("[城堡被攻打] 非事件画面")

    class IdleContext(FakeContext):
        def run_recognition(self, node, image):
            return FakeResult(node == "UI_MainWindows", "大地图")

    result = CastleDefenseProcessor().run(IdleContext(), None)
    check("非事件画面返回失败", result.success is False, str(result))


def main() -> int:
    test_defend_flow()
    test_ignore_flow()
    test_not_on_event()
    print(f"\n自检完成: 通过 {PASSED}，失败 {FAILED}")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
