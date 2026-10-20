# -*- coding: utf-8 -*-
"""任务组合：把任务列表里的模块按顺序串起来，命名保存并循环执行。

组合保存在 table/task_combos.json：
    {"combos": [
        {"id": "c1", "name": "紫砂盾循环", "loops": 0,
         "steps": [{"entry": "Auto_YearlyTask"}, {"entry": "AutoFight_Start"}]}
    ]}

- steps 只存 entry，名字从 interface.json 现查，任务改名不会丢；
- loops = 0 表示一直循环到手动停止，1 表示跑一遍；
- 每个步骤的选项用「任务」页给该任务保存的选择（config/webapp.json 的 selections）。
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
import uuid
from pathlib import Path

COMBOS_REL = Path("table") / "task_combos.json"


def _read_json(path: Path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_json(path: Path, data) -> None:
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + ".bak"))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=4)
        handle.write("\n")
    tmp.replace(path)


def normalize(combo: dict) -> dict:
    steps = []
    for step in combo.get("steps", []) or []:
        if isinstance(step, str):
            step = {"entry": step}
        entry = str(step.get("entry") or "").strip()
        if entry:
            steps.append({"entry": entry})
    loops = combo.get("loops", 1)
    try:
        loops = int(loops)
    except (TypeError, ValueError):
        loops = 1
    return {
        "id": str(combo.get("id") or "").strip() or f"c{uuid.uuid4().hex[:8]}",
        "name": str(combo.get("name") or "").strip() or "未命名组合",
        "loops": max(0, loops),
        "steps": steps,
    }


def load(root: Path) -> list[dict]:
    path = root / COMBOS_REL
    if not path.exists():
        return []
    try:
        data = _read_json(path)
    except Exception:  # noqa: BLE001
        return []
    items = data.get("combos") if isinstance(data, dict) else data
    return [normalize(item) for item in items or [] if isinstance(item, dict)]


def save(root: Path, combos: list[dict]) -> list[dict]:
    items = [normalize(item) for item in combos or [] if isinstance(item, dict)]
    _write_json(root / COMBOS_REL, {"combos": items})
    return items


def validate(combos: list[dict], entries: set[str]) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    for index, combo in enumerate(combos, 1):
        name = combo.get("name") or f"第 {index} 个组合"
        if not str(combo.get("name") or "").strip():
            errors.append(f"第 {index} 个组合没有名字")
        if name in seen:
            errors.append(f"组合名重复：{name}")
        seen.add(name)
        steps = combo.get("steps") or []
        if not steps:
            errors.append(f"{name}：至少添加一个任务模块")
        for step in steps:
            entry = step.get("entry", "")
            if entry not in entries:
                errors.append(f"{name}：任务模块不存在（{entry}）")
    return errors


# ---------------------------------------------------------------- 运行器


class ComboRunner:
    """按顺序执行组合里的任务，可循环；同一时间只跑一个组合。"""

    def __init__(self, runtime, root: Path, selection_provider=None):
        self.runtime = runtime
        self.root = root
        self.selection_provider = selection_provider or (lambda entry: {})
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.combo: dict = {}
        self.state = "idle"  # idle | running | done | failed | stopped
        self.loop_index = 0
        self.step_index = 0
        self.step_name = ""
        self.message = ""

    def _task_names(self) -> dict[str, str]:
        try:
            return {
                task.get("entry", ""): task.get("name", task.get("entry", ""))
                for task in self.runtime.interfaces.get("task", [])
            }
        except Exception:  # noqa: BLE001
            return {}

    def status(self) -> dict:
        with self.lock:
            return {
                "state": self.state,
                "combo": self.combo.get("name", ""),
                "loop": self.loop_index,
                "loops": self.combo.get("loops", 1),
                "step": self.step_index,
                "steps": len(self.combo.get("steps", [])),
                "stepName": self.step_name,
                "message": self.message,
            }

    def start(self, combo: dict) -> dict:
        with self.lock:
            if self.state == "running":
                raise RuntimeError("组合已经在运行")
            if self.runtime.task_state == "running":
                raise RuntimeError("已有任务在运行，先停止")

            steps = combo.get("steps") or []
            if not steps:
                raise RuntimeError("组合里没有步骤")

            names = self._task_names()
            for step in steps:
                if step.get("entry") not in names:
                    raise RuntimeError(f"任务模块不存在：{step.get('entry')}")

            self.combo = combo
            self.stop_event.clear()
            self.state = "running"
            self.loop_index = 0
            self.step_index = 0
            self.step_name = ""
            self.message = f"开始运行「{combo.get('name')}」"
            self.thread = threading.Thread(target=self._run, name="combo-runner", daemon=True)
            self.thread.start()
            return self.status()

    def stop(self) -> dict:
        with self.lock:
            if self.state == "running":
                self.stop_event.set()
                self.state = "stopping"
                self.message = "正在停止…"
        try:
            self.runtime.stop_task()
        except Exception:  # noqa: BLE001
            pass
        return self.status()

    def _run(self) -> None:
        try:
            names = self._task_names()
            loops = int(self.combo.get("loops", 1) or 0)
            while not self.stop_event.is_set():
                self.loop_index += 1
                for index, step in enumerate(self.combo.get("steps", []), 1):
                    if self.stop_event.is_set():
                        break
                    entry = step.get("entry", "")
                    name = names.get(entry, entry)
                    self.step_index = index
                    self.step_name = name
                    self.message = f"第 {self.loop_index} 轮 · {index}/{len(self.combo['steps'])} {name}"
                    try:
                        selection = self.selection_provider(entry) or {}
                        self.runtime.run_task(entry, name, selection)
                    except Exception as exc:  # noqa: BLE001
                        with self.lock:
                            self.state = "failed"
                            self.message = f"启动失败：{name}（{exc}）"
                        return

                    while True:
                        if self.stop_event.is_set():
                            try:
                                self.runtime.stop_task()
                            except Exception:  # noqa: BLE001
                                pass
                        state = self.runtime.task_state
                        if state in ("done", "failed"):
                            break
                        time.sleep(0.5)

                    if self.stop_event.is_set() or state == "stopping":
                        with self.lock:
                            self.state = "stopped"
                            self.message = "已停止"
                        return
                    if state == "failed":
                        with self.lock:
                            self.state = "failed"
                            self.message = f"步骤失败：{name}，组合中止"
                        return

                if loops > 0 and self.loop_index >= loops:
                    with self.lock:
                        self.state = "done"
                        self.message = f"完成 {self.loop_index} 轮"
                    return

            with self.lock:
                self.state = "stopped"
                self.message = "已停止"
        except Exception as exc:  # noqa: BLE001
            with self.lock:
                self.state = "failed"
                self.message = f"组合异常：{exc}"
