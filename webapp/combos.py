# -*- coding: utf-8 -*-
"""任务组合：把任务列表里的模块按顺序串起来，命名保存、循环执行，并按规则自动处理异常。

组合保存在 table/task_combos.json：
    {"combos": [{
        "id": "c1", "name": "紫砂盾循环", "loops": 0,
        "description": "每天先领礼包，再跑月度循环",
        "steps": [{"entry": "Auto_DailyTask", "selection": {...}}, {"entry": "Auto_YearlyTask", "selection": {...}}],
        "rules": [{"when": "fail", "action": "retry", "value": 2},
                  {"when": "child_alert", "action": "stop", "value": 0},
                  {"when": "loop_end", "action": "wait", "value": 10}]
    }]}

- steps 存 entry + 该步骤自己的选项（selection），名字从 interface.json 现查；
- loops = 0 表示一直循环到手动停止，1 表示只跑一遍；
- rules 是“遇到什么情况做什么”：
    when:  fail（任务失败）/ child_alert（好娃提醒）/ offline（设备离线）/ loop_end（每轮结束）
    action: retry（重试，value=次数）/ skip（跳过）/ stop（停止）/ continue（继续）/
            notify（发通知）/ reconnect（重连后重试）/ wait（等待，value=秒）
"""

from __future__ import annotations

import json
import shutil
import threading
import time
import uuid
from pathlib import Path

COMBOS_REL = Path("table") / "task_combos.json"
ALERT_REL = Path("temp") / "child_alert.json"

RULE_WHEN = ("fail", "child_alert", "offline", "loop_end")
RULE_ACTION = ("retry", "skip", "stop", "continue", "notify", "reconnect", "wait")


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


def _int(value, default=0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def normalize_rule(rule: dict) -> dict:
    return {
        "when": str(rule.get("when") or "").strip(),
        "action": str(rule.get("action") or "").strip(),
        "value": _int(rule.get("value", 0)),
    }


def normalize(combo: dict) -> dict:
    steps = []
    for step in combo.get("steps", []) or []:
        if isinstance(step, str):
            step = {"entry": step}
        entry = str(step.get("entry") or "").strip()
        selection = step.get("selection")
        if not isinstance(selection, dict):
            selection = {}
        if entry:
            steps.append({"entry": entry, "selection": selection})
    rules = [
        normalize_rule(rule)
        for rule in combo.get("rules", []) or []
        if isinstance(rule, dict) and rule.get("when") and rule.get("action")
    ]
    return {
        "id": str(combo.get("id") or "").strip() or f"c{uuid.uuid4().hex[:8]}",
        "name": str(combo.get("name") or "").strip() or "未命名组合",
        "description": str(combo.get("description") or "").strip(),
        "loops": _int(combo.get("loops", 1), 1),
        "steps": steps,
        "rules": rules,
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
            if not isinstance(step.get("selection"), dict):
                errors.append(f"{name}：{entry} 的选项格式不对")
        for rule in combo.get("rules") or []:
            when = rule.get("when")
            action = rule.get("action")
            if when not in RULE_WHEN:
                errors.append(f"{name}：不认识的触发条件 {when}")
            if action not in RULE_ACTION:
                errors.append(f"{name}：不认识的处理动作 {action}")
            if action in ("retry", "wait") and _int(rule.get("value")) <= 0:
                errors.append(f"{name}：{action} 需要填写次数/秒数")
    return errors


# ---------------------------------------------------------------- 运行器


class ComboRunner:
    """按顺序执行组合里的任务，可循环，并按 rules 处理失败 / 好娃提醒 / 离线。"""

    def __init__(
        self,
        runtime,
        root: Path,
        selection_provider=None,
        reconnect_provider=None,
        notify_provider=None,
    ):
        self.runtime = runtime
        self.root = root
        self.selection_provider = selection_provider or (lambda entry: {})
        self.reconnect_provider = reconnect_provider
        self.notify_provider = notify_provider
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.combo: dict = {}
        self.state = "idle"  # idle | running | stopping | done | failed | stopped
        self.loop_index = 0
        self.step_index = 0
        self.step_name = ""
        self.message = ""

    # ------------------------------------------------------------ 工具

    def _task_names(self) -> dict[str, str]:
        try:
            return {
                task.get("entry", ""): task.get("name", task.get("entry", ""))
                for task in self.runtime.interfaces.get("task", [])
            }
        except Exception:  # noqa: BLE001
            return {}

    def _rule(self, when: str) -> dict | None:
        for rule in self.combo.get("rules", []) or []:
            if rule.get("when") == when:
                return rule
        return None

    def _online(self) -> bool:
        try:
            return bool(self.runtime.agent and self.runtime.agent.connected)
        except Exception:  # noqa: BLE001
            return True

    def _alert_since(self, started: float) -> bool:
        try:
            return (self.root / ALERT_REL).stat().st_mtime > started
        except OSError:
            return False

    def _notify(self, title: str, content: str) -> None:
        if self.notify_provider:
            try:
                self.notify_provider(title, content)
            except Exception:  # noqa: BLE001
                pass

    def _sleep(self, seconds: float) -> bool:
        """可被停止打断的等待；返回 False 表示收到停止。"""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self.stop_event.is_set():
                return False
            time.sleep(0.2)
        return True

    # ------------------------------------------------------------ 状态

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

    # ------------------------------------------------------------ 执行

    def _run_step(self, step: dict, name: str, index: int, total: int, loop: int) -> str:
        """执行一个步骤；返回 done / skip / stopped / stop。"""
        attempts = 0
        while True:
            if self.stop_event.is_set():
                return "stopped"

            if not self._online():
                rule = self._rule("offline") or {"action": "stop", "value": 0}
                if rule["action"] == "reconnect" and self.reconnect_provider:
                    with self.lock:
                        self.message = f"{name}：设备离线，正在重连"
                    if self.reconnect_provider():
                        continue
                with self.lock:
                    self.state = "failed"
                    self.message = f"{name}：设备/Agent 离线，组合停止"
                return "stop"

            selection = step.get("selection")
            if not isinstance(selection, dict) or not selection:
                selection = self.selection_provider(step["entry"]) or {}

            with self.lock:
                self.step_index = index
                self.step_name = name
                self.message = f"第 {loop} 轮 · {index}/{total} {name}"
            started = time.time()
            try:
                self.runtime.run_task(step["entry"], name, selection)
            except Exception as exc:  # noqa: BLE001
                state = "failed"
                error = str(exc)
                with self.lock:
                    self.message = f"{name} 启动失败：{error}"
            else:
                error = ""
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

            if self.stop_event.is_set():
                return "stopped"

            if self._alert_since(started):
                rule = self._rule("child_alert") or {"action": "stop", "value": 0}
                action = rule["action"] if rule["action"] in ("continue", "notify", "stop") else "stop"
                if action == "continue":
                    with self.lock:
                        self.message = "好娃提醒触发，按规则继续"
                    return "done"
                if action == "notify":
                    self._notify("好娃提醒", f"{self.combo.get('name')}：好娃提醒触发，已暂停")
                    with self.lock:
                        self.message = "好娃提醒触发，已发通知并继续"
                    return "done"
                with self.lock:
                    self.state = "stopped"
                    self.message = "好娃提醒触发，组合停止（请在游戏里处理后再运行）"
                return "stop"

            if state == "done":
                return "done"

            # 任务失败
            rule = self._rule("fail")
            action = rule["action"] if rule else "stop"
            if action == "retry" and rule and attempts < rule["value"]:
                attempts += 1
                with self.lock:
                    self.message = f"{name} 失败，按规则重试 {attempts}/{rule['value']}"
                continue
            if action == "skip":
                with self.lock:
                    self.message = f"{name} 失败，按规则跳过"
                return "skip"
            if action == "continue":
                with self.lock:
                    self.message = f"{name} 失败，按规则继续下一步"
                return "done"
            if action == "notify":
                self._notify("组合任务失败", f"{self.combo.get('name')}：{name} 失败")
                with self.lock:
                    self.message = f"{name} 失败，已发通知，组合停止"
                return "stop"
            with self.lock:
                self.state = "failed"
                self.message = f"步骤失败：{name}" + (f"（{error}）" if error else "")
            return "stop"

    def _run(self) -> None:
        try:
            names = self._task_names()
            loops = int(self.combo.get("loops", 1) or 0)
            while not self.stop_event.is_set():
                self.loop_index += 1
                steps = self.combo.get("steps", [])
                for index, step in enumerate(steps, 1):
                    if self.stop_event.is_set():
                        break
                    name = names.get(step.get("entry", ""), step.get("entry", ""))
                    result = self._run_step(step, name, index, len(steps), self.loop_index)
                    if result == "stop":
                        with self.lock:
                            if self.state not in ("failed", "stopped"):
                                self.state = "stopped"
                                self.message = "已停止"
                        return
                    if result == "stopped":
                        with self.lock:
                            self.state = "stopped"
                            self.message = "已停止"
                        return

                if loops > 0 and self.loop_index >= loops:
                    with self.lock:
                        self.state = "done"
                        self.message = f"完成 {self.loop_index} 轮"
                    return

                rule = self._rule("loop_end")
                if rule and rule["action"] == "wait" and rule["value"] > 0:
                    with self.lock:
                        self.message = f"第 {self.loop_index} 轮结束，等待 {rule['value']} 秒"
                    if not self._sleep(rule["value"]):
                        with self.lock:
                            self.state = "stopped"
                            self.message = "已停止"
                        return

            with self.lock:
                self.state = "stopped"
                self.message = "已停止"
        except Exception as exc:  # noqa: BLE001
            with self.lock:
                self.state = "failed"
                self.message = f"组合异常：{exc}"
