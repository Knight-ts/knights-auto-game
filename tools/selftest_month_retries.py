"""离线自检：「任务失败继续轮数」解析（0 = 不限，不再卡 10）。"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agent"))
os.chdir(ROOT)

from action.fight.fight_processor import YEARLY_MONTH_RETRIES, _yearly_month_retries  # noqa: E402

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
    def __init__(self, expected: str):
        self.expected = expected

    def get_node_data(self, name):
        if name == "YearlyMonthRetries":
            return {"recognition": {"param": {"expected": [self.expected]}}}
        return None


def main() -> int:
    cases = [
        ("3", 3, "默认值"),
        ("10", 10, "原来的上限仍然有效"),
        ("20", 20, "超过 10 不再被截断"),
        ("0", 0, "0 = 不限"),
        ("999", 999, "三位数上限"),
        ("1000", 0, "超过 999 按不限（旧配置写超大数的写法）"),
        ("10000000", 0, "超大数 = 不限"),
        ("abc", YEARLY_MONTH_RETRIES, "脏数据回默认"),
        ("-5", YEARLY_MONTH_RETRIES, "负数回默认而不是变成不限"),
    ]
    for raw, expected, label in cases:
        value = _yearly_month_retries(FakeContext(raw))
        check(f"{label}（{raw}）", value == expected, f"{value} != {expected}")

    print(f"\n自检完成: 通过 {PASSED}，失败 {FAILED}")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
