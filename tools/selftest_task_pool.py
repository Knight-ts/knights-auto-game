"""离线自检：任务分类（采购/配送按描述排除，不接取）与任务池快照。

用法：
    python tools/selftest_task_pool.py
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agent"))
os.chdir(ROOT)

from action.zshg.task_hud_recognizer import HudTaskInfo, TaskHudRecognizer  # noqa: E402

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


def make(name: str, desc: str, level: str = "10", task_type: str = "讨伐任务") -> HudTaskInfo:
    task = HudTaskInfo(
        task_type=task_type,
        task_name=name,
        task_level=level,
        task_description=desc,
        action_text="接受",
        accept_button_box=[10, 10, 10, 10],
    )
    return task


def test_evaluate() -> None:
    print("[任务分类] evaluate_task")
    recognizer = TaskHudRecognizer()
    cases = [
        ("采购", "前往杂货铺购买一批物资", "购买"),
        ("配送", "把货物送往邻镇", "送往"),
        ("杂货", "替杂货铺跑腿", "杂货铺"),
        ("送到", "把信送到城堡", "送到"),
    ]
    for label, desc, keyword in cases:
        ok, reason = recognizer.evaluate_task(make(label, desc), min_level=0, max_level=120)
        check(f"{label} 被识别为非战斗", ok is False and keyword in reason, f"{ok} {reason}")

    ok, reason = recognizer.evaluate_task(make("讨伐", "讨伐盘踞在山谷的哥布林"))
    check("战斗任务可接", ok is True, reason)

    ok, reason = recognizer.evaluate_task(
        make("保护", "保护商队", task_type="保护任务")
    )
    check("类型黑名单排除", ok is False and "类型排除" in reason, reason)

    ok, reason = recognizer.evaluate_task(
        make("黑名单任务", "讨伐", level="10"), blacklist={"黑名单任务"}
    )
    check("名字黑名单排除", ok is False and "黑名单" in reason, reason)

    ok, reason = recognizer.evaluate_task(make("低级", "讨伐", level="5"), min_level=20)
    check("低于等级排除", ok is False, reason)

    ok, reason = recognizer.evaluate_task(make("高级", "讨伐", level="90"), max_level=50)
    check("高于等级排除", ok is False, reason)


def test_scan_pool() -> None:
    print("[任务分类] scan_pool 快照与候选")
    recognizer = TaskHudRecognizer()
    tasks = [
        make("采购A", "前往杂货铺购买物资"),
        make("配送B", "把货物送往邻镇"),
        make("讨伐C", "讨伐哥布林", level="30"),
    ]
    recognizer.recognize_tasks = lambda context, screenshot: tasks  # type: ignore[assignment]

    items, eligible = recognizer.scan_pool(
        None, None, min_level=0, max_level=120, city="暮角城", blacklist=set()
    )

    check("快照包含 3 条", len(items) == 3, str(items))
    check("采购标记 non_combat", items[0]["non_combat"] is True and not items[0]["eligible"], str(items[0]))
    check("配送标记 non_combat", items[1]["non_combat"] is True and not items[1]["eligible"], str(items[1]))
    check("只有战斗任务进候选", [t.task_name for t in eligible] == ["讨伐C"], str([t.task_name for t in eligible]))
    check("分类原因可读", "非战斗任务" in items[0]["reason"], items[0]["reason"])


def main() -> int:
    test_evaluate()
    test_scan_pool()
    print(f"\n自检完成: 通过 {PASSED}，失败 {FAILED}")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
