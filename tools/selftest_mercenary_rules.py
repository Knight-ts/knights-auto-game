# -*- coding: utf-8 -*-
"""离线自检：佣兵招募规则（名字 / 爵位 / 橙色特性 / 好娃组合 / 属性门槛）与弹窗名字识别。

不需要连接设备。用法：

    python tools/selftest_mercenary_rules.py
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agent"))
os.chdir(ROOT)

from utils import (  # noqa: E402
    mercenary,
    mercenary_admit,
    mercenary_profile,
    mercenary_rules,
)

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


def rule(**kwargs) -> dict:
    config = dict(mercenary_rules.DEFAULT)
    config.update(kwargs)
    return mercenary_rules._normalize(config)


# ------------------------------------------------------------------ 爵位


def test_parse_title() -> None:
    print("[爵位] 名字里的爵位")
    check("机器人起名 末尾“骑”", mercenary_rules.parse_title("感S技A骑") == "骑士")
    check("机器人起名 末尾“公”", mercenary_rules.parse_title("感ss敏s公") == "公爵")
    check("单字名 = 无爵位", mercenary_rules.parse_title("宾") == "无")
    check("游戏显示名带称号", mercenary_rules.parse_title("希利赋女骑士") == "骑士")
    check("空名字 = 未知", mercenary_rules.parse_title("") is None)
    check("面板文字兜底", mercenary_rules.parse_title("无名", "慧根女公爵") == "公爵")
    check("爵位等级排序", mercenary_rules.title_rank("公爵") > mercenary_rules.title_rank("伯爵"))


# ------------------------------------------------------------------ 橙色特性


def test_orange() -> None:
    print("[特性] 橙色血统特性计数")
    count, names = mercenary_profile.count_orange(["欧里克的神力"])
    check("普通橙特性计数", count == 1 and names == ["欧里克的神力"], str(count))
    count, _ = mercenary_profile.count_orange(["虎牙"])
    check("蓝特性不算橙色", count == 0)
    count, _ = mercenary_profile.count_orange(["佩尔弗因王族圣痕", "虎牙", "欧里克的神力"])
    check("多个橙色特性", count == 2, str(count))
    count, _ = mercenary_profile.count_orange(["不存在的特性"])
    check("表里没有的特性不算", count == 0)


# ------------------------------------------------------------------ 判定


def test_evaluate() -> None:
    print("[判定] 名字 / 爵位 / 特性 / 组合")
    whitelist = rule(mode="whitelist", names=["上"])
    check(
        "白名单命中",
        mercenary_rules.evaluate({"name": "上专"}, whitelist)["accept"] is True,
    )
    check(
        "白名单未命中",
        mercenary_rules.evaluate({"name": "卡尔"}, whitelist)["accept"] is False,
    )
    blacklist = rule(mode="blacklist", names=["死"])
    check(
        "黑名单命中则拒绝",
        mercenary_rules.evaluate({"name": "死灵"}, blacklist)["accept"] is False,
    )
    check(
        "黑名单未命中则接受",
        mercenary_rules.evaluate({"name": "卡尔"}, blacklist)["accept"] is True,
    )

    orange_rule = rule(names=["上"], min_orange=2)
    check(
        "橙色特性够",
        mercenary_rules.evaluate({"name": "上专", "orange": 2}, orange_rule)["accept"] is True,
    )
    check(
        "橙色特性不够",
        mercenary_rules.evaluate({"name": "上专", "orange": 1}, orange_rule)["accept"] is False,
    )
    skip_verdict = mercenary_rules.evaluate({"name": "上专", "orange": None}, orange_rule)
    check("特性没读到就跳过该条件", skip_verdict["accept"] is True, str(skip_verdict))
    check("备注写明跳过", "跳过" in skip_verdict["note"], skip_verdict["note"])

    title_rule = rule(names=["上"], title_min="公爵")
    check(
        "爵位不足",
        mercenary_rules.evaluate({"name": "上专", "title": "伯爵"}, title_rule)["accept"] is False,
    )
    check(
        "爵位达标",
        mercenary_rules.evaluate({"name": "上专", "title": "公爵"}, title_rule)["accept"] is True,
    )

    any_rule = rule(names=["上"], min_orange=2, match="any")
    check(
        "任一满足：名字不中但特性够",
        mercenary_rules.evaluate({"name": "卡尔", "orange": 3}, any_rule)["accept"] is True,
    )
    check(
        "任一满足：都不满足",
        mercenary_rules.evaluate({"name": "卡尔", "orange": 1}, any_rule)["accept"] is False,
    )

    all_rule = rule(names=["上"], title_min="公爵", match="all")
    check(
        "全部满足：名字中但爵位不够",
        mercenary_rules.evaluate({"name": "上专", "title": "骑士"}, all_rule)["accept"] is False,
    )

    check("规则摘要 - 名字", mercenary_rules.describe(rule()) == "whitelist")
    check(
        "规则摘要 - 多条件",
        mercenary_rules.describe(rule(min_orange=2, title_min="公爵")) == "whitelist/橙≥2/公爵+",
    )
    check(
        "规则摘要 - 任一",
        mercenary_rules.describe(rule(min_orange=2, match="any")) == "any(whitelist/橙≥2)",
    )
    check("空白名单会全拒", mercenary_rules.evaluate({"name": "卡尔"}, rule())["accept"] is False)


# ------------------------------------------------------------------ 入队组合 / 属性


def test_admit_helpers() -> None:
    print("[组合] 潜力等级 + 特性 + 好娃组合")
    check("等级映射 SS", mercenary_admit.grade_of(0.95) == "SS")
    check("等级映射 S", mercenary_admit.grade_of(0.80) == "S")
    check("等级映射 A", mercenary_admit.grade_of(0.60) == "A")
    check("等级映射 E", mercenary_admit.grade_of(0.0) == "E")
    check(
        "属性一行文本",
        mercenary_admit.grades_text({"力量": 0.8, "技巧": 0.95}) == "力S 体E 技SS 感E 敏E 意E",
        mercenary_admit.grades_text({"力量": 0.8, "技巧": 0.95}),
    )

    strong = {"力量": 0.80, "体质": 0.40, "技巧": 0.95, "感知": 0.40, "敏捷": 0.10, "意志": 0.35}
    weak = {"力量": 0.80, "体质": 0.40, "技巧": 0.60, "感知": 0.40, "敏捷": 0.10, "意志": 0.10}

    conditions = [
        {
            "id": "koneita",
            "name": "科内塔之怒组合",
            "features": ["科内塔之怒"],
            "potential": {
                "力量": {"grade": "S"},
                "技巧": {"grade": "SS"},
                "or_attributes": [{"意志": {"min_value": 0.3}}, {"敏捷": {"min_value": 0.3}}],
            },
        },
        {"id": "shang_zi_zhuan", "name": "上自专", "features": ["上古灵性", "自然共感", "专注之眸"]},
    ]

    ok, _ = mercenary_admit.check_potential(strong, conditions[0])
    check("属性门槛通过（技巧SS + 力量S + 意志0.35）", ok is True)
    ok, _ = mercenary_admit.check_potential(weak, conditions[0])
    check("属性门槛不过（技巧只有A）", ok is False)

    ok, found = mercenary_admit.check_features(["科内塔之怒"], conditions[0])
    check("必需特性命中", ok is True and found == ["科内塔之怒"], str(found))
    ok, _ = mercenary_admit.check_features(["虎牙"], conditions[0])
    check("必需特性缺失", ok is False)
    ok, _ = mercenary_admit.check_features(["上古灵性", "自然共感", "专注之眸", "虎牙"], conditions[1])
    check("多特性全部命中（可以多带别的）", ok is True)

    result, name, detail = mercenary_admit.match_combos(["科内塔之怒组合"], strong, ["科内塔之怒"], conditions)
    check("组合命中", result is True and name == "科内塔之怒组合", f"{result} {name} {detail}")
    result, _, detail = mercenary_admit.match_combos(["科内塔之怒组合"], weak, ["科内塔之怒"], conditions)
    check("组合不命中并说明原因", result is False and "属性不够" in detail, detail)
    result, name, _ = mercenary_admit.match_combos(
        ["科内塔之怒组合", "上自专"], weak, ["上古灵性", "自然共感", "专注之眸"], conditions
    )
    check("多个组合满足任一", result is True and name == "上自专", f"{result} {name}")
    result, _, _ = mercenary_admit.match_combos([], strong, [], conditions)
    check("没选组合 = 条件未启用", result is None)

    ok, detail = mercenary_admit.check_potential_min(strong, {"技巧": "SS", "力量": "S"})
    check("单独属性门槛通过", ok is True, detail)
    ok, detail = mercenary_admit.check_potential_min(weak, {"技巧": "SS"})
    check("单独属性门槛不过", ok is False and "技巧A" in detail, detail)


def test_evaluate_detail() -> None:
    print("[判定] 详情面板条件（组合 / 必需特性 / 属性门槛）")
    combos_rule = rule(names=[], combos=["科内塔之怒组合"], match="any")
    profile = {
        "name": "卡尔",
        "title": "无",
        "detail_ok": True,
        "features": ["科内塔之怒"],
        "potential": {"力量": 0.80, "技巧": 0.95, "意志": 0.35},
    }
    check("组合命中就算收", mercenary_rules.evaluate(profile, combos_rule)["accept"] is True)
    weak = dict(profile, potential={"力量": 0.80, "技巧": 0.60, "意志": 0.10})
    check("组合不命中就拒", mercenary_rules.evaluate(weak, combos_rule)["accept"] is False)

    skipped = mercenary_rules.evaluate({"name": "卡尔", "detail_ok": False}, combos_rule)
    check("面板没读到 → 组合条件跳过", skipped["accept"] is False and "跳过" in skipped["note"], skipped["note"])

    feature_rule = rule(names=[], features=["科内塔之怒"])
    check(
        "必需特性命中",
        mercenary_rules.evaluate(dict(profile, detail_ok=True), feature_rule)["accept"] is True,
    )
    check(
        "必需特性缺失",
        mercenary_rules.evaluate(dict(profile, features=["虎牙"]), feature_rule)["accept"] is False,
    )

    potential_rule = rule(names=[], potential_min={"技巧": "SS"})
    check("属性门槛通过", mercenary_rules.evaluate(profile, potential_rule)["accept"] is True)
    check("属性门槛不过", mercenary_rules.evaluate(weak, potential_rule)["accept"] is False)

    check(
        "规则摘要 - 组合",
        mercenary_rules.describe(rule(combos=["a", "b"], match="any")) == "any(whitelist/组合×2)",
        mercenary_rules.describe(rule(combos=["a", "b"], match="any")),
    )
    check(
        "属性门槛会规范化",
        mercenary_rules._normalize(rule(potential_min={"技巧": "ss", "乱写": "S", "力量": "X"}))[
            "potential_min"
        ]
        == {"技巧": "SS"},
    )


# ------------------------------------------------------------------ 名字识别


class _Result:
    def __init__(self, text, box):
        self.text = text
        self.box = box


class _Detail:
    def __init__(self, rows):
        self.hit = bool(rows)
        self.filtered_results = [_Result(t, b) for t, b in rows]
        self.best_result = self.filtered_results[0] if rows else None


class _FakeContext:
    def __init__(self, rows):
        self.rows = rows

    def run_recognition(self, node, img, pipeline_override=None):
        return _Detail(self.rows)


def test_name_recognition() -> None:
    print("[名字] 弹窗里的名字行")
    config = rule()
    dialog = [
        ("弗吉尔和上专的孩子成年了！可以加入佣兵团", [40, 640, 600, 36]),
        ("希望加入佣兵团", [300, 385, 280, 34]),
        ("感s技a骑", [420, 469, 200, 40]),
        ("年龄15", [430, 538, 120, 34]),
        ("银币", [200, 745, 80, 30]),
        ("14/1805533", [280, 745, 200, 30]),
        ("取消", [180, 855, 120, 40]),
        ("招募", [470, 855, 120, 40]),
    ]
    name, _ = mercenary._recognize_name(_FakeContext(dialog), object(), config)
    check("带字母的名字能读出来", name == "感s技a骑", name)

    single = list(dialog)
    single[2] = ("宾", [430, 469, 80, 40])
    name, _ = mercenary._recognize_name(_FakeContext(single), object(), config)
    check("单字名字能读出来", name == "宾", name)

    banner_only = [
        ("祈灵日到来", [210, 250, 300, 34]),
        ("巴萨罗穆和布瑞娜的孩子成年了！可以加入佣兵团", [40, 640, 600, 36]),
        ("14/118170", [40, 745, 200, 30]),
        ("取消", [180, 855, 120, 40]),
    ]
    name, _ = mercenary._recognize_name(_FakeContext(banner_only), object(), config)
    check("只有横幅时不写假名字", name == "", name)


def main() -> int:
    test_parse_title()
    test_orange()
    test_evaluate()
    test_admit_helpers()
    test_evaluate_detail()
    test_name_recognition()
    print(f"\n通过 {PASSED} 项，失败 {FAILED} 项")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
