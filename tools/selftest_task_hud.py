"""离线自检：任务板 HUD 识别在「识别节点没启动」时不能崩。

对应报错 20261023-215353-f8bdb5（AttributeError: 'NoneType' object has no
attribute 'hit'）：任务卡滚到屏幕底部时，接受按钮的 ROI 落到画面外，MAA 的
识别流程启动失败返回 None，旧代码直接读 ``reco.hit`` 把整轮月度循环打断。

不需要连接设备。用法：

    python tools/selftest_task_hud.py
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agent"))
os.chdir(ROOT)

from action.zshg.task_hud_recognizer import (  # noqa: E402
    HudTaskInfo,
    TaskHudRecognizer,
)

PASSED = 0
FAILED = 0

SCREEN_W = 720
SCREEN_H = 1280


class FakeScreenshot:
    """只提供 shape，识别逻辑只用到截图尺寸。"""

    shape = (SCREEN_H, SCREEN_W, 3)


class FakeBestResult:
    def __init__(self, text="", box=None):
        self.text = text
        self.box = box if box is not None else [0, 0, 10, 10]


class FakeReco:
    def __init__(self, hit=True, best=None, all_results=None):
        self.hit = hit
        self.best_result = best
        self.all_results = all_results or []


class NoneContext:
    """模拟识别节点没启动：run_recognition 返回 None。"""

    def __init__(self):
        self.calls = []

    def run_recognition(self, entry, image, pipeline_override=None):
        self.calls.append((entry, pipeline_override))
        return None


class RaisingContext:
    """模拟 MAA 对非法 ROI 直接抛异常。"""

    def run_recognition(self, entry, image, pipeline_override=None):
        raise ValueError(f"{entry}: invalid roi")


class StrictContext:
    """模拟 MAA 的严格 ROI 校验：矩形越界就返回 None。"""

    def __init__(self, icon_box):
        self.icon_box = icon_box
        self.ocr_rois = []

    def run_recognition(self, entry, image, pipeline_override=None):
        if entry == "Hud_TaskIconMatch":
            return FakeReco(best=FakeBestResult(box=list(self.icon_box)))
        roi = ((pipeline_override or {}).get(entry) or {}).get("roi")
        if roi is None:
            return None
        x, y, w, h = roi[:4]
        if w <= 0 or h <= 0 or x < 0 or y < 0:
            return None
        if x + w > SCREEN_W or y + h > SCREEN_H:
            return None  # 与 MAA 行为一致：ROI 越界 → 节点未启动
        self.ocr_rois.append((entry, list(roi)))
        if entry == "Hud_TaskNameOcr":
            return FakeReco(best=FakeBestResult("村庄撤离"))
        if entry == "Hud_TaskLevelOcr":
            return FakeReco(best=FakeBestResult("敌人等级：55"))
        if entry == "Hud_TaskDescOcr":
            return FakeReco(
                best=FakeBestResult("描述"),
                all_results=[FakeBestResult("近期天气越来越冷，"), FakeBestResult("村民集中过冬。")],
            )
        if entry == "Hud_AcceptButtonOcr":
            return FakeReco(best=FakeBestResult("接受", box=[520, 1150, 120, 60]))
        return None


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name} {detail}")


def test_clamp_roi(recognizer: TaskHudRecognizer) -> None:
    print("[ROI] 裁剪到画面内")
    shape = (SCREEN_H, SCREEN_W, 3)
    check("完全在画面外 → None", recognizer._clamp_roi([520, 1330, 180, 120], shape) is None)
    check("部分越界 → 裁掉越界部分", recognizer._clamp_roi([520, 1200, 180, 120], shape) == [520, 1200, 180, 80])
    check("完全在画面内 → 原样返回", recognizer._clamp_roi([10, 20, 100, 50], shape) == [10, 20, 100, 50])
    check("左边越界 → x 归零", recognizer._clamp_roi([-30, 20, 100, 50], shape) == [0, 20, 70, 50])
    check("没有截图尺寸时不做裁剪", recognizer._clamp_roi([520, 1330, 180, 120], None) == [520, 1330, 180, 120])

    icon_box = [40, 1080, 70, 70]
    accept_roi = recognizer._calc_dynamic_roi(
        icon_box, recognizer.TASK_VALUE_OFFSETS["accept_button"], shape
    )
    check("底部任务卡接受按钮 ROI 完全越界 → None", accept_roi is None, str(accept_roi))
    desc_roi = recognizer._calc_dynamic_roi(
        icon_box, recognizer.TASK_VALUE_OFFSETS["task_description"], shape
    )
    check("底部任务卡描述 ROI 被裁剪进画面", desc_roi == [120, 1180, 570, 100], str(desc_roi))


def test_none_recognition(recognizer: TaskHudRecognizer) -> None:
    print("[崩溃复现] 识别节点返回 None")
    ctx = NoneContext()
    try:
        info = recognizer.recognize_task_info_by_icon(
            ctx, FakeScreenshot(), "混战任务", [40, 1080, 70, 70]
        )
    except AttributeError as exc:  # 旧代码在这里炸
        check("识别返回 None 时不抛 AttributeError", False, str(exc))
        return
    check("识别返回 None 时不抛 AttributeError", True)
    check("降级为空任务信息", isinstance(info, HudTaskInfo) and info.task_name == "")
    check("四个区域都尝试过识别", len(ctx.calls) == 4, str([c[0] for c in ctx.calls]))

    try:
        tasks = recognizer.recognize_tasks(ctx, FakeScreenshot())
    except AttributeError as exc:
        check("recognize_tasks 同样不抛 AttributeError", False, str(exc))
        return
    check("recognize_tasks 同样不抛 AttributeError", True)
    check("没有图标匹配时返回空列表", tasks == [], str(tasks))


def test_raising_recognition(recognizer: TaskHudRecognizer) -> None:
    print("[容错] 识别直接抛异常")
    try:
        info = recognizer.recognize_task_info_by_icon(
            RaisingContext(), FakeScreenshot(), "斩杀任务", [40, 300, 70, 70]
        )
    except Exception as exc:  # noqa: BLE001
        check("识别抛异常时被吞掉", False, repr(exc))
        return
    check("识别抛异常时被吞掉", True)
    check("仍然返回任务对象", isinstance(info, HudTaskInfo))


def test_bottom_card(recognizer: TaskHudRecognizer) -> None:
    print("[回归] 屏幕底部任务卡的完整扫描")
    ctx = StrictContext([40, 1080, 70, 70])
    try:
        items, eligible = recognizer.scan_pool(
            ctx, FakeScreenshot(), min_level=0, max_level=85, city="暮角城", blacklist=set()
        )
    except AttributeError as exc:
        check("scan_pool 不抛 AttributeError", False, str(exc))
        return
    check("scan_pool 不抛 AttributeError", True)
    check("识别到 1 条任务", len(items) == 1, str(items))
    if not items:
        return
    item = items[0]
    check("任务名/等级/描述都读到", item["name"] == "村庄撤离" and item["level"] == "敌人等级：55", str(item))
    check("描述多行已合并", item["description"] == "近期天气越来越冷，村民集中过冬。", item["description"])
    check("底部卡片接受按钮没读到", eligible[0].accept_button_box is None, str(eligible))
    check(
        "所有 OCR ROI 都在画面内",
        all(0 <= r[0] and 0 <= r[1] for _, r in ctx.ocr_rois)
        and all(r[0] + r[2] <= SCREEN_W and r[1] + r[3] <= SCREEN_H for _, r in ctx.ocr_rois),
        str(ctx.ocr_rois),
    )
    check("没有接受按钮时不产生可点击框", all(t.accept_button_box is None for t in eligible))


def test_middle_card(recognizer: TaskHudRecognizer) -> None:
    print("[正常] 屏幕中部任务卡")
    ctx = StrictContext([40, 600, 70, 70])
    items, eligible = recognizer.scan_pool(
        ctx, FakeScreenshot(), min_level=0, max_level=85, city="暮角城", blacklist=set()
    )
    check("识别到 1 条任务", len(items) == 1, str(items))
    check("中部卡片可接", len(eligible) == 1, str(items))
    if eligible:
        check("接取按钮位置已读到", eligible[0].accept_button_box is not None)


def main() -> int:
    recognizer = TaskHudRecognizer()
    test_clamp_roi(recognizer)
    test_none_recognition(recognizer)
    test_raising_recognition(recognizer)
    test_bottom_card(recognizer)
    test_middle_card(recognizer)
    print(f"\n通过 {PASSED}，失败 {FAILED}")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
