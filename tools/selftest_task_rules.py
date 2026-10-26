"""离线自检：任务接取规则（城市等级区间 / 城市黑名单 / 任务板快照）。

不需要连接设备。用法：

    python tools/selftest_task_rules.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agent"))
os.chdir(ROOT)

from action.zshg.task_hud_recognizer import (  # noqa: E402
    HudTaskInfo,
    TaskHudRecognizer,
)
from utils import task_rules  # noqa: E402

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


def reset(temp_dir: str) -> None:
    task_rules.TABLE_DIR = temp_dir
    task_rules._rules_cache = None
    task_rules._rules_mtime = -1.0
    task_rules._pool_cache = None
    task_rules._pool_mtime = -1.0


def test_rules(temp_dir: str) -> None:
    print("[规则] 等级区间")
    check("默认区间 0-120", task_rules.city_rule("雄月城")["min_level"] == 0)
    task_rules.set_city_levels("暮角城", 40, 90)
    rule = task_rules.city_rule("暮角城")
    check("城市区间保存", rule["min_level"] == 40 and rule["max_level"] == 90, str(rule))
    check("其他城市仍用默认", task_rules.city_rule("雄月城")["max_level"] == 120)
    check("城市规则标记 explicit", rule["explicit"] is True)

    task_rules.set_city_levels("暮角城", 120, 30)
    rule = task_rules.city_rule("暮角城")
    check("区间自动纠正顺序", rule["min_level"] == 30 and rule["max_level"] == 120, str(rule))

    task_rules.set_city_levels("", 10, 80)
    check("默认区间保存", task_rules.load_rules()["defaults"]["min_level"] == 10)
    check("默认区间影响未配置城市", task_rules.city_rule("银风堡")["min_level"] == 10)


def test_blacklist() -> None:
    print("[规则] 黑名单")
    task_rules.save_global_blacklist(["危机四伏", "观察者"])
    task_rules.add_city_blacklist("暮角城", "逃亡")
    merged = set(task_rules.effective_blacklist("暮角城"))
    check("全局+城市合并", merged == {"危机四伏", "观察者", "逃亡"}, str(merged))
    other = set(task_rules.effective_blacklist("雄月城"))
    check("其他城市不含城市名单", other == {"危机四伏", "观察者"}, str(other))

    task_rules.remove_city_blacklist("暮角城", "逃亡")
    check("城市名单可移除", "逃亡" not in task_rules.city_rule("暮角城")["blacklist"])

    task_rules.add_global_blacklist("外来者")
    check("全局名单可追加", "外来者" in task_rules.global_blacklist())
    task_rules.remove_global_blacklist("外来者")
    check("全局名单可移除", "外来者" not in task_rules.global_blacklist())

    # 旧格式兼容（网页旧接口直接写 {"blacklist": [...]}）
    with open(task_rules.blacklist_path(), "w", encoding="utf-8") as handle:
        json.dump({"blacklist": ["旧任务"]}, handle, ensure_ascii=False)
    check("旧格式兼容", task_rules.global_blacklist() == ["旧任务"])


def test_evaluate() -> None:
    print("[规则] 单条任务判定")
    recognizer = TaskHudRecognizer()
    blacklist = {"观察者"}

    ok, reason = recognizer.evaluate_task(
        HudTaskInfo(task_type="斩杀任务", task_name="普通任务", task_level="敌人等级：60"),
        min_level=40,
        max_level=90,
        blacklist=blacklist,
    )
    check("区间内可接", ok and reason == "可接取", reason)

    ok, reason = recognizer.evaluate_task(
        HudTaskInfo(task_type="斩杀任务", task_name="高级任务", task_level="敌人等级：95"),
        min_level=40,
        max_level=90,
        blacklist=blacklist,
    )
    check("超上限拒绝", not ok and "90" in reason, reason)

    ok, reason = recognizer.evaluate_task(
        HudTaskInfo(task_type="斩杀任务", task_name="新手任务", task_level="敌人等级：10"),
        min_level=40,
        max_level=90,
        blacklist=blacklist,
    )
    check("低于下限拒绝", not ok and "40" in reason, reason)

    ok, reason = recognizer.evaluate_task(
        HudTaskInfo(task_type="斩杀任务", task_name="观察者", task_level="敌人等级：50"),
        min_level=0,
        max_level=120,
        blacklist=blacklist,
    )
    check("黑名单拒绝", not ok and reason == "黑名单", reason)

    ok, reason = recognizer.evaluate_task(
        HudTaskInfo(task_type="保护任务", task_name="保护", task_level="敌人等级：50"),
        min_level=0,
        max_level=120,
        blacklist=set(),
    )
    check("保护任务类型排除", not ok and "类型" in reason, reason)

    ok, reason = recognizer.evaluate_task(
        HudTaskInfo(
            task_type="混战任务",
            task_name="采购",
            task_level="敌人等级：50",
            task_description="前往杂货铺购买物资",
        ),
        min_level=0,
        max_level=120,
        blacklist=set(),
    )
    check("非战斗描述排除", not ok and "非战斗" in reason, reason)

    check(
        "空任务名拒绝",
        recognizer.evaluate_task(HudTaskInfo(task_name=""), blacklist=set())[0] is False,
    )


def test_pool(temp_dir: str) -> None:
    print("[任务板] 快照落盘与去重")
    items = [
        {"name": "任务A", "type": "斩杀任务", "level": "50", "eligible": True, "reason": "可接取"},
        {"name": "任务B", "type": "生存任务", "level": "95", "eligible": False, "reason": "高于最高等级 90"},
    ]
    payload = task_rules.save_pool("暮角城", items)
    check("快照城市", payload["city"] == "暮角城")
    check("快照条目数", len(payload["tasks"]) == 2)

    path = task_rules.pool_path()
    first_mtime = os.path.getmtime(path)
    time.sleep(0.05)
    task_rules.save_pool("暮角城", items)
    check("相同内容不重复写盘", os.path.getmtime(path) == first_mtime)

    task_rules.save_pool("暮角城", items[:1])
    check("内容变化会更新", len(task_rules.load_pool()["tasks"]) == 1)
    check("城市列表包含快照城市", "暮角城" in task_rules.city_names())


def main() -> int:
    with tempfile.TemporaryDirectory() as temp_dir:
        reset(temp_dir)
        test_rules(temp_dir)
        test_blacklist()
        test_evaluate()
        test_pool(temp_dir)
    print(f"\n自检完成: 通过 {PASSED}，失败 {FAILED}")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
