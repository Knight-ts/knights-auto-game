# -*- coding: utf-8 -*-
"""离线自检：未知界面处理 + 返回路线 + 源码定位。

用法：
    python tools/selftest_scene_return.py

不需要模拟器：用假的截图 / 点击 / 识别结果把「点几次 → 画面有没有变化 →
记录 / 套用已保存的返回方式」整条链路跑一遍，最后检查源码定位能找到
真实函数和 pipeline 节点。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agent"))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from action.fight import scene_return  # noqa: E402
from utils import unknown_store  # noqa: E402

PASSED = 0
FAILED = 0
WIDTH, HEIGHT = 720, 1280


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name} {detail}")


class FakeJob:
    def __init__(self, image=None):
        self.succeeded = True
        self._image = image

    def wait(self):
        return self

    def get(self):
        return None if self._image is None else self._image.copy()


class FakeController:
    def __init__(self, screen):
        self.screen = screen
        self.clicks: list[tuple] = []
        self.on_click = None

    def post_screencap(self):
        return FakeJob(self.screen)

    def post_click(self, x, y):
        self.clicks.append(("click", x, y))
        if self.on_click is not None:
            self.screen = self.on_click(self.screen)
        return FakeJob()

    def post_click_key(self, keycode):
        self.clicks.append(("key", keycode))
        if self.on_click is not None:
            self.screen = self.on_click(self.screen)
        return FakeJob()


class FakeTasker:
    def __init__(self, screen):
        self.stopping = False
        self.controller = FakeController(screen)


class FakeContext:
    def __init__(self, screen):
        self.tasker = FakeTasker(screen)
        self.tasks: list[str] = []

    def run_task(self, name):
        self.tasks.append(name)
        return type("Result", (), {"status": type("Status", (), {"succeeded": True})})

    def run_recognition(self, node, image):
        return type("Detail", (), {"hit": False, "best_result": None})


def base_screen(color=0) -> np.ndarray:
    return np.full((HEIGHT, WIDTH, 3), color, dtype=np.uint8)


def with_block(screen, color=200) -> np.ndarray:
    copy = screen.copy()
    copy[560:900, 80:640] = color
    return copy


def test_signature() -> None:
    print("\n[1] 画面指纹 / 变化判断")
    screen = base_screen()
    check("同一画面指纹一致", scene_return.signature(screen) == scene_return.signature(base_screen()))
    changed, diff = scene_return.frame_changed(screen, base_screen())
    check("同一画面不算变化", changed is False and diff == 0.0, f"diff={diff}")
    changed, diff = scene_return.frame_changed(screen, with_block(screen))
    check("明显变化能认出来", changed is True and diff > 2.0, f"diff={diff}")
    noise = screen.copy()
    noise[10:12, 10:12] = 30
    changed, diff = scene_return.frame_changed(screen, noise, threshold=2.0)
    check("小噪声不超过阈值", changed is False, f"diff={diff}")


def build_store() -> tuple[unknown_store.Store, Path]:
    root = Path(tempfile.mkdtemp(prefix="knights-unknown-"))
    (root / "table").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(
        Path(ROOT) / "table" / "return_routes.json",
        root / "table" / "return_routes.json",
    )
    return unknown_store.Store(root), root


def test_record_flow() -> None:
    print("\n[2] 未知画面：点几次 → 没变化 → 记录")
    store, root = build_store()
    old_store, old_root = scene_return.STORE, scene_return.ROOT
    scene_return.STORE = store
    scene_return.ROOT = root
    try:
        ctx = FakeContext(base_screen())
        result = scene_return.handle_unknown(
            ctx, base_screen(), source="selftest:record", reason="自检"
        )
        check("状态是 recorded", result["status"] == "recorded", str(result["status"]))
        check("默认点 3 次", len(result["attempts"]) == 3, str(result["attempts"]))
        check(
            "点了下方 / 返回键 / 中心",
            [step["step"] for step in result["attempts"]]
            == ["click_bottom", "back_key", "click_center"],
            str(result["attempts"]),
        )
        check("三步都没变化", all(step["changed"] is False for step in result["attempts"]))
        record = result["record"]
        check("记录带截图", bool(record and record["has_image"]))
        check("记录带调用栈", bool(record and record["trace"]), str((record or {}).get("trace")))
        check("调用栈指向自检文件", "selftest_scene_return" in (record or {}).get("trace", [{}])[0].get("file", ""))
        check("记录写进 index.json", (root / "debug" / "unknown" / "index.json").exists())
        check("生成 REPORT.md", (root / "debug" / "unknown" / "REPORT.md").exists())

        # 同一画面再次出现：不新增记录，只累加命中次数
        again = scene_return.handle_unknown(ctx, base_screen(), source="selftest:again")
        check("第二次命中旧记录", again["status"] == "stuck", str(again["status"]))
        check("命中次数累加", (again.get("record") or {}).get("hits") == 2, str(again.get("record")))
        check("记录只有一条", len(store.items()) == 1, str(len(store.items())))
    finally:
        scene_return.STORE, scene_return.ROOT = old_store, old_root
        shutil.rmtree(root, ignore_errors=True)


def test_changed_and_route() -> None:
    print("\n[3] 画面一变就停 / 套用保存的返回方式")
    store, root = build_store()
    old_store, old_root = scene_return.STORE, scene_return.ROOT
    scene_return.STORE = store
    scene_return.ROOT = root
    try:
        # 第一次点就换个画面：不应该记录
        ctx = FakeContext(base_screen())
        ctx.tasker.controller.on_click = lambda screen: with_block(screen, 180)
        result = scene_return.handle_unknown(ctx, base_screen(), source="selftest:changed")
        check("状态是 changed", result["status"] == "changed", str(result["status"]))
        check("只点了 1 次就停", len(result["attempts"]) == 1, str(result["attempts"]))
        check("没有留下记录", len(store.items()) == 0, str(len(store.items())))

        # 先记一条静态未知画面，再给它保存返回方式
        stuck_ctx = FakeContext(base_screen())
        recorded = scene_return.handle_unknown(stuck_ctx, base_screen(), source="selftest:stuck")
        record = recorded["record"]
        route_id = f"unknown:{record['key']}"
        store.save_route(route_id, ["task:BackButton_500ms", "task:PopUpWindowTip"], label="自检路线")
        store.touch(record["id"], route_id="popup_tip", route_steps=["task:BackButton_500ms"], route_label="自检路线", route_saved=True)

        # 再遇到同一画面：应该走保存的路线（两次 run_task），而不是默认点击
        ctx2 = FakeContext(base_screen())
        result2 = scene_return.handle_unknown(ctx2, base_screen(), source="selftest:route")
        check("命中旧记录", result2["status"] == "stuck", str(result2["status"]))
        check("用的是保存的路线", result2["route"] == "自检路线", str(result2.get("route")))
        check(
            "执行了保存的两步",
            ctx2.tasks == ["BackButton_500ms", "PopUpWindowTip"],
            str(ctx2.tasks),
        )
        check("没有默认点击", not ctx2.tasker.controller.clicks, str(ctx2.tasker.controller.clicks))
    finally:
        scene_return.STORE, scene_return.ROOT = old_store, old_root
        shutil.rmtree(root, ignore_errors=True)


def test_route_adoption() -> None:
    print("\n[4] 删掉记录后重新遇到同一画面：自动接回已保存的返回方式")
    store, root = build_store()
    old_store, old_root = scene_return.STORE, scene_return.ROOT
    scene_return.STORE = store
    scene_return.ROOT = root
    try:
        ctx = FakeContext(base_screen())
        record = scene_return.handle_unknown(ctx, base_screen(), source="selftest:first")["record"]
        route_id = f"unknown:{record['key']}"
        # 用和预设 popup_tip 一样的步骤，页面据此回显到对应预设
        store.save_route(route_id, ["popup"], label="提示弹窗：点「确定」关闭")
        store.remove(record["id"])
        check("记录已删除", len(store.items()) == 0)

        again = scene_return.handle_unknown(ctx, base_screen(), source="selftest:again2")
        adopted = again.get("record") or {}
        check("重新记录后带返回方式", adopted.get("route_saved") is True, str(adopted))
        check("回显到预设 popup_tip", adopted.get("route_id") == "popup_tip", str(adopted.get("route_id")))
        check("路线步骤接回来", adopted.get("route_steps") == ["popup"], str(adopted.get("route_steps")))
    finally:
        scene_return.STORE, scene_return.ROOT = old_store, old_root
        shutil.rmtree(root, ignore_errors=True)


def test_source_lookup() -> None:
    print("\n[5] 源码定位")
    try:
        from webapp import source
    except Exception as exc:  # noqa: BLE001
        check("导入 webapp.source", False, str(exc))
        return
    result = source.lookup(symbol="handle_interrupt_scene")
    first = (result.get("locations") or [{}])[0]
    check("按函数名找到源码", result.get("ok") is True)
    check("路径是 fight_utils.py", first.get("path", "").endswith("fight_utils.py"), str(first))
    check("行号大于 0", int(first.get("line") or 0) > 0, str(first))
    check("返回代码片段", bool((result.get("code") or {}).get("text")))

    result = source.lookup(symbol="SceneGuardTask")
    check("按类名找到源码", result.get("ok") is True and result["locations"][0]["kind"] == "class", str(result.get("locations")))

    result = source.lookup(node="SceneGuard")
    check("按节点名找到 pipeline", result.get("ok") is True, str(result.get("locations")))
    check(
        "节点在 auto_task.json",
        (result.get("locations") or [{}])[0].get("path", "").endswith("auto_task.json"),
        str(result.get("locations")),
    )

    check("非法路径被拒绝", source.lookup(path="../../config/config.json").get("ok") is False)
    check("非法函数名被拒绝", source.lookup(symbol="a; del").get("ok") is False)
    check("链接是 vscode 协议", source.editor_uri(Path(ROOT) / "agent" / "main.py", 12).startswith("vscode://file/"))


def main() -> int:
    print("未知界面 / 返回路线 / 源码定位 自检")
    test_signature()
    test_record_flow()
    test_changed_and_route()
    test_route_adoption()
    test_source_lookup()
    print(f"\n结果：{PASSED} 通过，{FAILED} 失败")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
