# -*- coding: utf-8 -*-
"""Knights Auto Game 网页版运行时：直接用 Python 驱动 MaaFramework，不依赖 MFAAvalonia。

职责：
    - 发现/连接模拟器（AdbController）
    - 加载 resource/base（Resource）
    - 启动并连接 Python Agent（AgentClient + agent/main.py）
    - 按 interface.json 的任务/选项组合 pipeline_override 并执行（Tasker）
    - 提供截图、日志、状态

线程模型：HTTP 线程只做读写；任务在独立线程里跑，状态用锁保护。
"""

from __future__ import annotations

import copy
import atexit
import json
import os
import random
import re
import string
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

import cv2

from maa.agent_client import AgentClient
from maa.controller import AdbController
from maa.resource import Resource
from maa.tasker import Tasker
from maa.toolkit import Toolkit

ROOT = Path(__file__).resolve().parent.parent
CREATE_NO_WINDOW = 0x08000000


def _now() -> str:
    return time.strftime("%H:%M:%S")


class Runtime:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.logs: deque[str] = deque(maxlen=3000)
        self.interfaces = self._load_interface()
        atexit.register(self.stop_agent)

        self.controller: AdbController | None = None
        self.resource: Resource | None = None
        self.tasker: Tasker | None = None
        self.agent: AgentClient | None = None
        self.agent_proc: subprocess.Popen | None = None
        self.agent_lines: threading.Thread | None = None

        self.device_info: dict = {}
        self.resource_loaded = False
        self.task_state = "idle"  # idle | running | stopping | done | failed
        self.task_name = ""
        self.task_thread: threading.Thread | None = None
        self.last_error = ""
        self.history: deque[dict] = deque(maxlen=50)
        self.history_sink = None
        self.error_sink = None

    # ------------------------------------------------------------ 日志

    def log(self, message: str) -> None:
        line = f"[{_now()}] {message}"
        with self.lock:
            self.logs.append(line)
        print(line, flush=True)

    def tail_logs(self, limit: int = 200) -> list[str]:
        with self.lock:
            return list(self.logs)[-limit:]

    # ------------------------------------------------------------ interface.json

    def _load_interface(self) -> dict:
        try:
            return json.loads((ROOT / "interface.json").read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            self.log(f"读取 interface.json 失败：{exc}")
            return {"task": [], "option": {}}

    def tasks(self, labels: dict | None = None, hidden: list | None = None) -> list[dict]:
        options = self.interfaces.get("option", {})
        labels = labels or {}
        hidden = set(hidden or [])
        result = []
        for task in self.interfaces.get("task", []):
            entry = task.get("entry", "")
            if entry in hidden:
                continue
            item = {
                "name": labels.get(entry) or task.get("name", ""),
                "entry": entry,
                "description": task.get("description", ""),
                "doc": task.get("doc", []),
                "options": [],
            }
            for option_name in task.get("option", []) or []:
                option = options.get(option_name)
                if not option:
                    continue
                item["options"].append(
                    {
                        "name": option_name,
                        "type": option.get("type", "switch"),
                        "label": option.get("label") or option_name,
                        "description": option.get("description", ""),
                        "cases": [
                            {
                                "name": case.get("name", ""),
                                "description": case.get("description", ""),
                            }
                            for case in option.get("cases", []) or []
                        ],
                        "default_case": option.get("default_case", ""),
                        "inputs": option.get("inputs", []),
                    }
                )
            result.append(item)
        return result

    def build_override(self, task: dict, selection: dict | None) -> dict:
        """把界面上的选择组合成 pipeline_override。"""
        selection = selection or {}
        options = self.interfaces.get("option", {})
        override: dict = {}

        def merge(target: dict, source: dict) -> None:
            for key, value in (source or {}).items():
                if isinstance(value, dict) and isinstance(target.get(key), dict):
                    merge(target[key], value)
                else:
                    target[key] = copy.deepcopy(value)

        for option_name in task.get("option", []) or []:
            option = options.get(option_name)
            if not option:
                continue
            otype = option.get("type", "switch")

            if otype == "input":
                values = selection.get(option_name) or {}
                inputs = {
                    item.get("name", ""): (
                        values.get(item.get("name", "")) or item.get("default", "")
                    )
                    for item in option.get("inputs", []) or []
                }
                if not any(str(v).strip() for v in inputs.values()):
                    continue
                template = json.dumps(option.get("pipeline_override", {}), ensure_ascii=False)
                for key, value in inputs.items():
                    template = template.replace("{" + key + "}", str(value))
                merge(override, json.loads(template))
                continue

            default = option.get("default_case", "")
            default_list = default if isinstance(default, list) else ([default] if default else [])
            selected = selection.get(option_name)
            if selected is None:
                selected = default_list
            if isinstance(selected, str):
                selected = [selected]
            for case in option.get("cases", []) or []:
                if case.get("name") in selected:
                    merge(override, case.get("pipeline_override", {}))

        return override

    # ------------------------------------------------------------ 设备

    def list_devices(self) -> list[dict]:
        devices = []
        try:
            for item in Toolkit.find_adb_devices():
                devices.append(
                    {
                        "name": getattr(item, "name", ""),
                        "adb_path": str(getattr(item, "adb_path", "")),
                        "address": getattr(item, "address", ""),
                        "screencap_methods": getattr(item, "screencap_methods", 0),
                        "input_methods": getattr(item, "input_methods", 0),
                        "config": getattr(item, "config", "{}"),
                    }
                )
        except Exception as exc:  # noqa: BLE001
            self.log(f"搜索设备失败：{exc}")
        return devices

    def connect(self, adb_path: str, address: str) -> dict:
        with self.lock:
            if self.controller is not None:
                try:
                    self.controller.post_connection().wait()
                except Exception:  # noqa: BLE001
                    pass
            try:
                # 复用 MaaToolkit 探测到的设备信息：截图/输入方式 + MuMu extras 配置。
                # 只传 adb_path/address 会退回默认方式，截图 250ms 起步，推年这类
                # 大量 2s 超时的节点会成片失败。
                device = next(
                    (
                        item
                        for item in self.list_devices()
                        if item["address"] == address and item["adb_path"] == adb_path
                    ),
                    None,
                )
                kwargs: dict = {}
                if device:
                    kwargs["screencap_methods"] = device["screencap_methods"]
                    kwargs["input_methods"] = device["input_methods"]
                    if isinstance(device.get("config"), dict):
                        kwargs["config"] = device["config"]
                    self.log(
                        f"使用设备配置：screencap={device['screencap_methods']} "
                        f"input={device['input_methods']} extras={bool(kwargs.get('config'))}"
                    )
                controller = AdbController(adb_path, address, **kwargs)
                job = controller.post_connection()
                job.wait()
                if not job.succeeded:
                    raise RuntimeError("adb 连接失败")
                self.controller = controller
                self.device_info = {"adb_path": adb_path, "address": address}
                self.log(f"已连接设备：{address}")
            except Exception as exc:  # noqa: BLE001
                self.last_error = str(exc)
                self.log(f"连接设备失败：{exc}")
                raise

            self._ensure_resource()
            self._ensure_tasker()
            return self.status()

    def _ensure_resource(self) -> None:
        if self.resource is not None and self.resource_loaded:
            return
        resource = Resource()
        job = resource.post_bundle(ROOT / "resource" / "base")
        job.wait()
        if not job.succeeded:
            raise RuntimeError("资源加载失败")
        self.resource = resource
        self.resource_loaded = True
        self.log("资源加载完成：resource/base")

    def _ensure_tasker(self) -> None:
        if self.tasker is not None:
            return
        if self.resource is None or self.controller is None:
            return
        tasker = Tasker()
        tasker.bind(self.resource, self.controller)
        if not tasker.inited:
            raise RuntimeError("Tasker 初始化失败")
        self.tasker = tasker
        self.log("Tasker 就绪")

    def screenshot(self) -> bytes:
        if self.controller is None:
            raise RuntimeError("设备未连接")
        image = self.controller.post_screencap().wait().get()
        ok, buffer = cv2.imencode(".png", image)
        if not ok:
            raise RuntimeError("截图编码失败")
        return buffer.tobytes()

    # ------------------------------------------------------------ 报错现场

    def capture_error(
        self,
        source: str,
        title: str,
        error: str = "",
        *,
        entry: str = "",
        task_name: str | None = None,
        state: str | None = None,
        extra: dict | None = None,
        dedupe_key: str | None = None,
    ) -> None:
        """把当前画面和上下文交给 error_sink（server 里接的是 webapp.selfheal.ErrorStore）。

        截图失败不影响主流程：设备没连上时只存日志和错误文字。
        """
        sink = getattr(self, "error_sink", None)
        if sink is None:
            return
        image = None
        try:
            if self.controller is not None:
                image = self.screenshot()
        except Exception as exc:  # noqa: BLE001
            self.log(f"报错截图失败：{exc}")
        event = {
            "source": source,
            "title": title,
            "error": error,
            "entry": entry,
            "task_name": self.task_name if task_name is None else task_name,
            "state": self.task_state if state is None else state,
            "logs": self.tail_logs(120),
            "image": image,
            "extra": extra or {},
            "dedupe_key": dedupe_key,
        }
        try:
            sink(event)
        except Exception as exc:  # noqa: BLE001
            self.log(f"保存报错现场失败：{exc}")

    # ------------------------------------------------------------ Agent

    def start_agent(self) -> dict:
        with self.lock:
            if self.agent and self.agent.connected:
                return self.status()
            if self.resource is None:
                raise RuntimeError("资源未加载，无法启动 Agent")

            # 上一次启动失败可能留下半死的进程，先收掉再开新的
            if self.agent_proc is not None and self.agent_proc.poll() is None:
                try:
                    self.agent_proc.terminate()
                    self.agent_proc.wait(timeout=3)
                except Exception:  # noqa: BLE001
                    pass
            self.agent_proc = None
            self.agent = None

            identifier = "maagc" + "".join(
                random.choices(string.ascii_letters + string.digits, k=6)
            )
            client = AgentClient(identifier)
            client.bind(self.resource)
            client.set_timeout(8000)

            command = [sys.executable, "-u", str(ROOT / "agent" / "main.py"), identifier]
            self.log("启动 Agent：" + " ".join(command))
            env = dict(os.environ)
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONUTF8"] = "1"
            env["PYTHONUNBUFFERED"] = "1"
            env["MAAGC_DISABLE_CONSOLE"] = "1"
            proc = subprocess.Popen(
                command,
                cwd=str(ROOT),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=CREATE_NO_WINDOW,
            )
            self.agent_proc = proc
            self.agent = client

            ansi = re.compile(r"\x1b\[[0-9;]*m")

            def pump() -> None:
                assert proc.stdout is not None
                trace: list[str] = []
                for raw in iter(proc.stdout.readline, b""):
                    if not raw:
                        break
                    try:
                        line = raw.decode("utf-8")
                    except UnicodeDecodeError:
                        line = raw.decode("gbk", errors="replace")
                    line = ansi.sub("", line).rstrip()
                    if not line:
                        continue
                    self.log("[agent] " + line)

                    # Agent 里抛异常时，把整段 Traceback 存成一条“问题现场”
                    if "Traceback (most recent call last)" in line:
                        trace = [line]
                        continue
                    if trace:
                        trace.append(line)
                        # Traceback 的最后一层（ValueError: ...）顶格，遇到就收尾
                        if len(trace) >= 60 or not line[:1].isspace():
                            self.capture_error(
                                "agent",
                                "Agent 异常",
                                "\n".join(trace[-40:]),
                                extra={"kind": "traceback"},
                                dedupe_key="agent-traceback",
                            )
                            trace = []

            self.agent_lines = threading.Thread(target=pump, name="agent-stdout", daemon=True)
            self.agent_lines.start()

            connected = False
            deadline = time.time() + 60
            while time.time() < deadline:
                if proc.poll() is not None:
                    self.log(f"Agent 进程提前退出，code={proc.returncode}")
                    break
                try:
                    if client.connect():
                        connected = True
                        break
                except Exception as exc:  # noqa: BLE001
                    self.log(f"连接 Agent 重试：{exc}")
                time.sleep(0.8)

            if not connected:
                self.last_error = "Agent 连接失败"
                self.log("Agent 连接失败")
                self.capture_error(
                    "agent",
                    "Agent 启动失败",
                    "60 秒内没有连上 Agent，进程可能已经崩溃",
                    state="failed",
                    extra={"returncode": proc.poll()},
                    dedupe_key="agent-start",
                )
                self.agent = None
                try:
                    if proc.poll() is None:
                        proc.terminate()
                except Exception:  # noqa: BLE001
                    pass
                self.agent_proc = None
                raise RuntimeError("Agent 连接失败")
            self.log("Agent 已连接")

            def watch() -> None:
                """Agent 进程非主动退出时留一张现场图。"""
                code = proc.wait()
                if self.agent is None or self.agent_proc is not proc:
                    return  # 用户主动停止 / 重启，不算报错
                self.log(f"Agent 进程退出，code={code}")
                self.capture_error(
                    "agent",
                    f"Agent 进程退出（code={code}）",
                    "Agent 进程意外退出，任务无法继续",
                    extra={"returncode": code},
                    dedupe_key="agent-exit",
                )

            threading.Thread(target=watch, name="agent-watch", daemon=True).start()
            return self.status()

    def stop_agent(self) -> None:
        with self.lock:
            agent = self.agent
            self.agent = None  # 先置空，避免 watch 线程把“主动停止”当成崩溃
            if agent:
                try:
                    agent.disconnect()
                except Exception:  # noqa: BLE001
                    pass
            if self.agent_proc and self.agent_proc.poll() is None:
                try:
                    self.agent_proc.terminate()
                    self.agent_proc.wait(timeout=5)
                except Exception:  # noqa: BLE001
                    try:
                        self.agent_proc.kill()
                    except Exception:  # noqa: BLE001
                        pass
            self.agent_proc = None

    # ------------------------------------------------------------ 任务

    def run_task(self, entry: str, name: str, selection: dict | None) -> dict:
        with self.lock:
            if self.task_state == "running":
                raise RuntimeError("已有任务在运行")
            if self.tasker is None:
                raise RuntimeError("Tasker 未就绪，请先连接设备")
            if self.agent is None:
                raise RuntimeError("Agent 未连接")

            task = next((t for t in self.interfaces.get("task", []) if t.get("entry") == entry), None)
            if task is None:
                raise RuntimeError(f"找不到任务：{entry}")

            override = self.build_override(task, selection)
            self.task_state = "running"
            self.task_name = name or entry
            self.last_error = ""
            self.log(f"开始任务：{self.task_name}（{entry}），override 节点 {len(override)} 个")

        def worker() -> None:
            started = time.time()
            state = "failed"
            error = ""
            try:
                job = self.tasker.post_task(entry, override)
                job.wait()
                ok = job.succeeded
                state = "done" if ok else "failed"
                if not ok:
                    error = "MaaFramework 返回失败：任务节点没有走完（多为识别超时或界面卡住）"
                with self.lock:
                    self.task_state = state
                if not ok:
                    self.capture_error(
                        "task",
                        f"任务失败：{name or entry}",
                        error,
                        entry=entry,
                        task_name=name or entry,
                        extra={
                            "override_nodes": len(override),
                            "selection": selection or {},
                        },
                        dedupe_key=f"task|{entry}",
                    )
                self.log(f"任务结束：{self.task_name} → {'成功' if ok else '失败'}")
            except Exception as exc:  # noqa: BLE001
                error = str(exc)
                with self.lock:
                    self.task_state = "failed"
                    self.last_error = error
                self.capture_error(
                    "task",
                    f"任务异常：{name or entry}",
                    error,
                    entry=entry,
                    task_name=name or entry,
                    extra={"selection": selection or {}},
                    dedupe_key=f"task-exc|{entry}",
                )
                self.log(f"任务异常：{exc}")
            finally:
                with self.lock:
                    self.history.appendleft(
                        {
                            "time": time.strftime("%H:%M:%S"),
                            "name": name or entry,
                            "entry": entry,
                            "state": state,
                            "seconds": round(time.time() - started, 1),
                            "error": error,
                        }
                    )
                    snapshot = list(self.history)[:20]
                if self.history_sink:
                    try:
                        self.history_sink(snapshot)
                    except Exception as exc:  # noqa: BLE001
                        self.log(f"保存任务历史失败：{exc}")

        self.task_thread = threading.Thread(target=worker, name="task-runner", daemon=True)
        self.task_thread.start()
        return self.status()

    def stop_task(self) -> dict:
        with self.lock:
            if self.tasker is not None and self.task_state == "running":
                self.task_state = "stopping"
                try:
                    self.tasker.post_stop()
                    self.log("已请求停止任务")
                except Exception as exc:  # noqa: BLE001
                    self.log(f"停止任务失败：{exc}")
        return self.status()

    # ------------------------------------------------------------ 状态

    def clear_logs(self) -> None:
        with self.lock:
            self.logs.clear()

    def status(self) -> dict:
        with self.lock:
            return {
                "device": self.device_info,
                "resource": self.resource_loaded,
                "agent": bool(self.agent and self.agent.connected),
                "task": {"state": self.task_state, "name": self.task_name},
                "error": self.last_error,
                "history": list(self.history)[:20],
                "logs": self.tail_logs(120),
            }


runtime = Runtime()
