# -*- coding: utf-8 -*-
"""问题自纠的 AI 修复调度器。

发现新的报错截图 -> 通过 DSH Bridge 起一个真正的 DSH 会话 -> AI 读截图和日志修代码 ->
写回结果。整个过程有硬上限，不会无限循环：

    每条报错最多 maxAttempts 次（默认 2）；
    每次会话最多 timeoutSeconds 秒（默认 600），超时由调度器打断；
    同类报错（source+entry+title+首行错误）用指纹记账，同一指纹累计尝试到上限就升级；
    全局每小时最多 maxPerHour 次；连续失败 3 次自动暂停一段时间；
    升级后记录 repair_state=escalated，生成 ESCALATION.md，停止自动处理，等用户判断。

用户在网页上点「交给 AI 修复」可以手动重试（会重置该指纹的失败计数）；
点「停止」只打断当前会话，点「重置」清空该记录的修复状态。
"""

from __future__ import annotations

import json
import shutil
import threading
import time
import uuid
from collections import deque
from pathlib import Path

from webapp.dsh_bridge import BridgeError, load_endpoint, run_repair_session

DEFAULTS = {
    "enabled": False,
    "auto": False,
    "model": "DeepSeek-V4.1-Flash",
    "effort": "high",
    "permission": "workspace-write",
    "agentPreset": "cordis",
    "maxAttempts": 2,
    "timeoutSeconds": 600,
    "cooldownSeconds": 20,
    "maxPerHour": 6,
    "pollSeconds": 3,
    "autoSince": 0,
    "dryRun": False,
}

DONE_STATES = {"fixed", "escalated", "stopped", "unavailable"}


def _number(value, default) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


class RepairManager:
    """后台线程：扫描新报错、调用 DSH 会话、记录结果、必要时升级给用户。"""

    def __init__(self, root: Path, store, config_provider, runner=None) -> None:
        self.root = Path(root)
        self.store = store
        self._config_provider = config_provider
        self.runner = runner or run_repair_session
        self.dir = self.root / "debug" / "repair"
        self.results_dir = self.dir / "results"
        self.jobs_dir = self.dir / "jobs"
        self.ledger_path = self.dir / "fingerprints.json"
        self.escalation_path = self.dir / "ESCALATION.md"
        self.log_path = self.dir / "repair.log"

        self._lock = threading.RLock()
        self._wake = threading.Event()
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.started_at = time.time()
        self.current: dict | None = None
        self.current_stop: threading.Event | None = None
        self.pause_until = 0.0
        self.pause_reason = ""
        self.consecutive_failures = 0
        self.last_job_at = 0.0
        self._attempts_window: deque[float] = deque(maxlen=200)
        self._ledger: dict[str, dict] = {}
        self._ensure_dirs()
        self._load_ledger()

    # ------------------------------------------------------------ 配置/目录

    def _ensure_dirs(self) -> None:
        for folder in (self.dir, self.results_dir, self.jobs_dir):
            folder.mkdir(parents=True, exist_ok=True)

    def config(self) -> dict:
        cfg = dict(DEFAULTS)
        try:
            live = self._config_provider() or {}
            section = live.get("repair") or {}
            if isinstance(section, dict):
                for key in DEFAULTS:
                    if key in section and section[key] is not None:
                        cfg[key] = section[key]
        except Exception:  # noqa: BLE001
            pass
        cfg["enabled"] = bool(cfg.get("enabled", False))
        cfg["auto"] = bool(cfg.get("auto", False))
        cfg["dryRun"] = bool(cfg.get("dryRun", False))
        cfg["maxAttempts"] = min(5, max(1, int(_number(cfg.get("maxAttempts"), 2))))
        cfg["timeoutSeconds"] = min(3600, max(60, int(_number(cfg.get("timeoutSeconds"), 600))))
        cfg["cooldownSeconds"] = min(600, max(0, int(_number(cfg.get("cooldownSeconds"), 20))))
        cfg["maxPerHour"] = min(24, max(1, int(_number(cfg.get("maxPerHour"), 6))))
        cfg["pollSeconds"] = min(30, max(1, int(_number(cfg.get("pollSeconds"), 3))))
        cfg["autoSince"] = max(0.0, _number(cfg.get("autoSince"), 0))
        cfg["model"] = str(cfg.get("model") or DEFAULTS["model"])
        cfg["effort"] = str(cfg.get("effort") or DEFAULTS["effort"])
        cfg["permission"] = str(cfg.get("permission") or DEFAULTS["permission"])
        cfg["agentPreset"] = str(cfg.get("agentPreset") or DEFAULTS["agentPreset"])
        return cfg

    def _log(self, message: str) -> None:
        line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}"
        try:
            with open(self.log_path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            pass

    # ------------------------------------------------------------ 指纹账本

    def _load_ledger(self) -> None:
        try:
            data = json.loads(self.ledger_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._ledger = {
                    str(key): value for key, value in data.items() if isinstance(value, dict)
                }
        except (OSError, ValueError):
            self._ledger = {}

    def _save_ledger(self) -> None:
        self._ensure_dirs()
        try:
            tmp = self.ledger_path.with_name(self.ledger_path.name + ".tmp")
            tmp.write_text(
                json.dumps(self._ledger, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            tmp.replace(self.ledger_path)
        except OSError:
            pass

    def _fingerprint(self, record: dict) -> str:
        return record.get("repair_fingerprint") or self.store.fingerprint_of(record)

    def _ledger_entry(self, record: dict) -> dict:
        fp = self._fingerprint(record)
        return self._ledger.get(fp) or {"attempts": 0, "state": "idle", "last": ""}

    def _update_ledger(self, record: dict, state: str, summary: str = "") -> None:
        fp = self._fingerprint(record)
        if not fp:
            return
        entry = self._ledger.get(fp) or {"attempts": 0, "state": "idle", "last": ""}
        entry["attempts"] = int(entry.get("attempts", 0)) + 1
        entry["state"] = state
        entry["last"] = time.strftime("%Y-%m-%d %H:%M:%S")
        entry["record"] = record.get("id", "")
        if summary:
            entry["summary"] = summary[:800]
        self._ledger[fp] = entry
        self._save_ledger()

    def _auto_allowed(self, record: dict) -> bool:
        cfg = self.config()
        entry = self.store.get(record["id"]) or record
        if int(entry.get("repair_attempts", 0)) >= cfg["maxAttempts"]:
            return False
        led = self._ledger_entry(entry)
        if led.get("state") == "escalated":
            return False
        if int(led.get("attempts", 0)) >= cfg["maxAttempts"]:
            return False
        return True

    # ------------------------------------------------------------ 生命周期

    def start(self) -> None:
        with self._lock:
            if self.thread is not None and self.thread.is_alive():
                return
            self.stop_event.clear()
            self.thread = threading.Thread(target=self._loop, name="selfheal-repair", daemon=True)
            self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.stop_current()
        thread = self.thread
        if thread is not None:
            thread.join(timeout=5)
        self.thread = None

    def _loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                self._pump()
            except Exception as exc:  # noqa: BLE001
                self._log(f"调度异常：{exc}")
            self._wake.wait(5)
            self._wake.clear()
        self._log("调度线程退出")

    # ------------------------------------------------------------ 状态

    def status(self) -> dict:
        cfg = self.config()
        with self._lock:
            current = dict(self.current) if self.current else None
            paused = time.time() < self.pause_until
            queued = sum(
                1 for item in self.store.list(limit=1000) if item.get("repair_state") == "queued"
            )
            endpoint = ""
            bridge_ok = False
            try:
                data = load_endpoint()
                endpoint = f"{data['host']}:{data['port']}"
                bridge_ok = True
            except Exception:  # noqa: BLE001
                endpoint = ""
            return {
                "enabled": cfg["enabled"],
                "auto": cfg["auto"],
                "dryRun": cfg["dryRun"],
                "running": current is not None,
                "current": current,
                "queued": queued,
                "paused": paused,
                "pauseUntil": self.pause_until if paused else 0,
                "pauseReason": self.pause_reason if paused else "",
                "consecutiveFailures": self.consecutive_failures,
                "bridge": bridge_ok,
                "bridgeEndpoint": endpoint,
                "maxAttempts": cfg["maxAttempts"],
                "timeoutSeconds": cfg["timeoutSeconds"],
                "escalation": "debug/repair/ESCALATION.md",
                "log": "debug/repair/repair.log",
            }

    # ------------------------------------------------------------ 入队/停止/重置

    def queue(self, sid: str, manual: bool = True) -> dict:
        cfg = self.config()
        if not cfg["enabled"]:
            raise ValueError("AI 修复已关闭，当前只保留报错记录")
        record = self.store.get(sid)
        if record is None:
            raise ValueError("没有这条报错记录")
        if record.get("status") == "deleted":
            raise ValueError("这条记录已删除，先恢复再修")
        with self._lock:
            if self.current and self.current.get("id") == sid:
                return {"ok": True, "message": "这条已经在修复中", "status": self.status()}
            if self.current:
                raise ValueError("已有另一个修复任务在运行，等它结束或先停止")
            if manual:
                fp = self._fingerprint(record)
                if fp and fp in self._ledger:
                    self._ledger.pop(fp, None)
                    self._save_ledger()
                self.store.update_repair(
                    sid,
                    repair_state="queued",
                    repair_attempts=0,
                    repair_message="已加入手动修复队列",
                    repair_session="",
                    repair_started_at="",
                    repair_finished_at="",
                    repair_summary="",
                    repair_files=[],
                    repair_fingerprint=fp,
                )
            else:
                self.store.update_repair(sid, repair_state="queued", repair_message="自动修复排队中")
            if manual:
                self.last_job_at = 0.0  # 手动点击不等待冷却
            self._log(f"入队 {sid}（{'手动' if manual else '自动'}）")
        self._wake.set()
        return {"ok": True, "message": "已加入 AI 修复队列", "status": self.status()}

    def stop_current(self) -> dict:
        with self._lock:
            if self.current_stop is not None:
                self.current_stop.set()
            current = dict(self.current) if self.current else None
        if current:
            self._log(f"请求停止 {current.get('id')}")
        return {"ok": True, "current": current, "status": self.status()}

    def reset(self, sid: str) -> dict:
        with self._lock:
            if self.current and self.current.get("id") == sid:
                raise ValueError("正在修复中，先停止再重置")
            record = self.store.get(sid)
            if record is None:
                raise ValueError("没有这条记录")
            fp = self._fingerprint(record)
            if fp:
                self._ledger.pop(fp, None)
                self._save_ledger()
            updated = self.store.update_repair(
                sid,
                repair_state="idle",
                repair_attempts=0,
                repair_session="",
                repair_started_at="",
                repair_finished_at="",
                repair_message="",
                repair_summary="",
                repair_files=[],
            )
        return {"ok": True, "item": updated}

    # ------------------------------------------------------------ 调度

    def _pump(self) -> None:
        cfg = self.config()
        if not cfg["enabled"]:
            return
        if time.time() < self.pause_until:
            return
        if self.current is not None:
            return
        now = time.time()
        if now - self.last_job_at < cfg["cooldownSeconds"]:
            return
        self._prune_attempts(now)
        if len(self._attempts_window) >= cfg["maxPerHour"]:
            self.pause_until = now + 900
            self.pause_reason = f"每小时最多 {cfg['maxPerHour']} 次，先暂停 15 分钟"
            self._log(self.pause_reason)
            return

        records = self.store.list(limit=1000)
        queued = [item for item in reversed(records) if item.get("repair_state") == "queued"]
        candidate = None
        manual = False
        if queued:
            candidate = queued[0]
            manual = True
        elif cfg["auto"]:
            auto_since = cfg["autoSince"] or self.started_at
            for item in reversed(records):
                if item.get("status") != "pending" or item.get("source") == "manual":
                    continue
                if item.get("repair_state") != "idle":
                    continue
                if float(item.get("ts") or 0) < auto_since:
                    continue
                if not self._auto_allowed(item):
                    msg = "同类报错已经尝试到上限，等待用户判断"
                    self.store.update_repair(item["id"], repair_state="escalated", repair_message=msg)
                    continue
                candidate = item
                break
        if candidate is None:
            return
        self._run_job(candidate, manual=manual)

    def _prune_attempts(self, now: float) -> None:
        while self._attempts_window and now - self._attempts_window[0] > 3600:
            self._attempts_window.popleft()

    def _run_job(self, record: dict, manual: bool) -> None:
        cfg = self.config()
        sid = record["id"]
        fp = self._fingerprint(record)
        session_label = f"repair-{uuid.uuid4().hex[:8]}"
        started = time.strftime("%Y-%m-%d %H:%M:%S")
        attempts = int(record.get("repair_attempts", 0)) + 1
        stop = threading.Event()
        with self._lock:
            self.current = {
                "id": sid,
                "title": record.get("title", ""),
                "started": started,
                "attempt": attempts,
                "session": "",
                "state": "running",
                "manual": manual,
            }
            self.current_stop = stop
            self._attempts_window.append(time.time())
        self.store.update_repair(
            sid,
            repair_state="running",
            repair_attempts=attempts,
            repair_started_at=started,
            repair_finished_at="",
            repair_message="AI 正在读取截图和代码…",
            repair_summary="",
            repair_files=[],
            repair_fingerprint=fp,
        )
        self._log(f"开始修复 {sid}（第 {attempts} 次，{'手动' if manual else '自动'}）")

        result_path = self.results_dir / f"{sid}.json"
        prompt_path = self.jobs_dir / f"{sid}.prompt.md"
        prompt = self._prompt(record, result_path, attempts)
        try:
            prompt_path.write_text(prompt, encoding="utf-8")
        except OSError:
            pass

        def on_update(state: str, session: str, external: str, message: str) -> None:
            with self._lock:
                if self.current and self.current.get("id") == sid:
                    self.current["state"] = state
                    self.current["session"] = session or external
            self.store.update_repair(
                sid,
                repair_session=session or external,
                repair_message=(message or f"AI 会话状态：{state}")[:2000],
            )

        try:
            outcome = self.runner(
                prompt,
                str(self.root),
                model=cfg["model"],
                effort=cfg["effort"],
                permission=cfg["permission"],
                agent_preset=cfg["agentPreset"],
                timeout=cfg["timeoutSeconds"],
                poll=cfg["pollSeconds"],
                stop_event=stop,
                on_update=on_update,
                session_label=session_label,
            )
        except BridgeError as exc:
            outcome = {"status": "error", "message": str(exc), "code": exc.code, "session_id": ""}
        except Exception as exc:  # noqa: BLE001
            outcome = {"status": "error", "message": str(exc), "code": "RUNNER_ERROR", "session_id": ""}

        try:
            self._finish_job(record, outcome, result_path, manual=manual, attempts=attempts)
        except Exception as exc:  # noqa: BLE001
            self._log(f"收尾失败 {sid}：{exc}")
            try:
                self.store.update_repair(
                    sid,
                    repair_state="escalated",
                    repair_finished_at=time.strftime("%Y-%m-%d %H:%M:%S"),
                    repair_message=f"修复流程收尾失败：{exc}",
                )
            except Exception:  # noqa: BLE001
                pass
        finally:
            with self._lock:
                self.current = None
                self.current_stop = None
            self.last_job_at = time.time()
            self._wake.set()

    def _finish_job(self, record: dict, outcome: dict, result_path: Path, *, manual: bool, attempts: int) -> None:
        cfg = self.config()
        sid = record["id"]
        status = str(outcome.get("status") or "error")
        session = str(outcome.get("session_id") or "")
        finished = time.strftime("%Y-%m-%d %H:%M:%S")
        result = self._read_result(result_path)
        summary = str(result.get("summary") or "")
        if not summary:
            summary = self._assistant_text(outcome)
        files = result.get("files") if isinstance(result.get("files"), list) else []

        if status == "completed":
            item_status = str(result.get("status") or "cannot_fix")
            if item_status == "fixed":
                self.store.update_repair(
                    sid,
                    repair_state="fixed",
                    repair_finished_at=finished,
                    repair_session=session,
                    repair_message=summary or "AI 已提交修复，请查看改动并验证",
                    repair_summary=summary,
                    repair_files=[str(item) for item in files][:40],
                )
                self._update_ledger(record, "fixed", summary)
                self.consecutive_failures = 0
                self._log(f"修复完成 {sid}：{summary[:120]}")
            else:
                self._after_unfixed(record, summary or "AI 认为暂时无法确定原因", attempts, session, finished)
        elif status == "timeout":
            self._after_unfixed(
                record,
                f"AI 思考超过 {cfg['timeoutSeconds']} 秒未完成，已打断",
                attempts,
                session,
                finished,
            )
        elif status == "stopped":
            self.store.update_repair(
                sid,
                repair_state="stopped",
                repair_finished_at=finished,
                repair_session=session,
                repair_message="用户停止了本次修复",
            )
            self._log(f"已停止 {sid}")
        elif status == "error":
            code = str(outcome.get("code") or "")
            message = str(outcome.get("message") or "AI 修复通道出错")
            state = "unavailable" if code.startswith("BRIDGE_") or code in ("TIMEOUT", "DISCONNECTED", "NO_MODEL") else "escalated"
            self.store.update_repair(
                sid,
                repair_state=state,
                repair_finished_at=finished,
                repair_session=session,
                repair_message=message,
            )
            if state == "escalated":
                self._write_escalation()
            self._update_ledger(record, "error", message)
            self.consecutive_failures += 1
            if self.consecutive_failures >= 3:
                self.pause_until = time.time() + 1800
                self.pause_reason = "连续 3 次修复失败，暂停自动修复 30 分钟"
            self._log(f"修复失败 {sid}：{message[:160]}")
        else:
            self._after_unfixed(record, f"未知结果：{status}", attempts, session, finished)

        with self._lock:
            self.current = None
            self.current_stop = None
        self.last_job_at = time.time()
        self._wake.set()

    def _after_unfixed(self, record: dict, message: str, attempts: int, session: str, finished: str) -> None:
        cfg = self.config()
        sid = record["id"]
        if attempts < cfg["maxAttempts"]:
            self.store.update_repair(
                sid,
                repair_state="idle",
                repair_finished_at=finished,
                repair_session=session,
                repair_message=f"{message}；稍后自动重试（{attempts}/{cfg['maxAttempts']}）",
                repair_summary=message,
            )
            self._update_ledger(record, "retry", message)
            self.consecutive_failures += 1
            self._log(f"未修复 {sid}，允许重试：{message[:120]}")
            return
        self.store.update_repair(
            sid,
            repair_state="escalated",
            repair_finished_at=finished,
            repair_session=session,
            repair_message=message,
            repair_summary=message,
        )
        self._update_ledger(record, "escalated", message)
        self.consecutive_failures += 1
        self._write_escalation()
        if self.consecutive_failures >= 3:
            self.pause_until = time.time() + 1800
            self.pause_reason = "连续 3 次修复失败，暂停自动修复 30 分钟"
        self._log(f"升级给用户 {sid}：{message[:120]}")

    # ------------------------------------------------------------ 结果/升级

    def _assistant_text(self, outcome: dict) -> str:
        """结果文件没写出来时，用最后一条 assistant 回复做结论。"""
        text = ""
        for item in outcome.get("items") or []:
            if not isinstance(item, dict) or item.get("role") != "assistant":
                continue
            content = item.get("content") or {}
            if isinstance(content, dict) and content.get("kind") == "markdown":
                value = str(content.get("text") or "").strip()
                if value:
                    text = value
        return text[-2000:]

    def _read_result(self, path: Path) -> dict:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        status = data.get("status")
        if status not in ("fixed", "cannot_fix"):
            return {}
        return data

    def _write_escalation(self) -> None:
        records = [
            item for item in self.store.list(limit=1000)
            if item.get("repair_state") == "escalated"
        ]
        lines = [
            "# 问题自纠 · 需要你判断",
            "",
            "> AI 已经尝试过，但没能在上限内解决。下面这些报错**不会继续自动重试**，",
            "> 请你看截图和日志后，判断原因、补充备注，再决定是否点「交给 AI 修复」重试。",
            "",
            f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"- 待判断：{len(records)} 条",
            f"- 单次上限：{self.config()['timeoutSeconds']} 秒 / {self.config()['maxAttempts']} 次",
            "",
        ]
        for index, item in enumerate(records[:50], 1):
            lines.extend(
                [
                    f"## {index}. {item.get('title', '')}",
                    f"- id：`{item.get('id', '')}`",
                    f"- 时间：{item.get('created_at', '')}",
                    f"- 来源：{item.get('source', '')}"
                    + (f" · 任务：{item.get('task_name') or item.get('entry')}" if (item.get("task_name") or item.get("entry")) else ""),
                    f"- 尝试：{item.get('repair_attempts', 0)} 次；最后状态：{item.get('repair_message', '')}",
                ]
            )
            if item.get("image_path"):
                lines.append(f"- 截图：`{item['image_path']}`")
            if item.get("repair_summary"):
                lines.append(f"- AI 结论：{item['repair_summary']}")
            if item.get("error"):
                lines.append("- 错误：")
                lines.append("")
                lines.append("```text")
                lines.extend(str(item["error"]).splitlines()[:12])
                lines.append("```")
            lines.extend(
                [
                    "",
                    "**请你判断**：是否可复现？是模板/界面变化还是代码 bug？需要补充什么信息？",
                    "把结论写进这条记录的「备注」，再点「交给 AI 修复」重试，或手动修改。",
                    "",
                ]
            )
        try:
            self.escalation_path.write_text("\n".join(lines), encoding="utf-8")
        except OSError:
            pass

    # ------------------------------------------------------------ 提示词

    def _prompt(self, record: dict, result_path: Path, attempt: int) -> str:
        cfg = self.config()
        sid = record.get("id", "")
        image = record.get("image_path") or "(没有截图)"
        logs = ""
        try:
            folder = self.store._folder_locked(sid, record.get("status"))  # noqa: SLF001
            if folder is not None:
                logs = (folder / "logs.txt").read_text(encoding="utf-8")[-5000:]
        except OSError:
            logs = ""
        result_rel = str(result_path.relative_to(self.root)).replace("\\", "/")
        mode = (
            "【只分析模式】这次不要修改任何文件，只读截图/日志/代码，给出原因和下一步建议。"
            if cfg["dryRun"]
            else "可以修改你判断必要的项目文件，但改动要小，不要动 table/ 下的游戏数据。"
        )
        return f"""你是 DeepSeek Harness 的自动修复代理。项目根目录：{self.root}

有一条新的报错现场需要处理（第 {attempt}/{cfg['maxAttempts']} 次尝试）。只处理这一条，不要运行游戏任务，不要启动模拟器。

【报错记录】
- id：{sid}
- 标题：{record.get('title', '')}
- 来源：{record.get('source', '')}
- 任务：{record.get('task_name') or record.get('entry') or '-'}
- 时间：{record.get('created_at', '')}
- 截图：{image}
- 错误：
{record.get('error', '')[:3000]}

【最近日志（尾部）】
```text
{logs}
```

【你的任务】
1. 先看截图，确认当时游戏停在哪；再结合日志定位是识别/模板/流程/代码问题。
2. {mode}
3. 修改后跑相关静态检查（例如 python -m py_compile、node --check、tools/selftest_*.py），不要启动模拟器或游戏任务。
4. 如果 10 分钟内无法确定原因，停下来，不要反复猜。

【必须写结果文件】
把结论写到 `{result_rel}`（UTF-8 JSON，不要写别的格式）：
{{
  "status": "fixed" 或 "cannot_fix",
  "summary": "一句话结论；fixed 时说明改了什么，cannot_fix 时说明卡在哪、需要用户提供什么",
  "files": ["改动的文件，相对项目根目录"],
  "tests": ["跑过的检查命令"],
  "confidence": "high/medium/low"
}}
如果 {result_rel} 写不出来，也请在最后一条回复里给出同样的结论。

约束：
- 不要删除或修改 debug/errors/ 下的任何记录和截图；
- 不要修改 table/ 里的用户数据；
- 不确定就写 cannot_fix，让用户判断，不要为了“修好”而乱改。
"""

    def clear_state(self) -> None:
        """一键清除截图时，把修复队列/结果/升级文件也清掉。"""
        self.stop_current()
        for folder in (self.results_dir, self.jobs_dir):
            if folder.exists():
                for path in folder.glob("*"):
                    try:
                        if path.is_dir():
                            shutil.rmtree(path, ignore_errors=True)
                        else:
                            path.unlink()
                    except OSError:
                        pass
        for path in (self.ledger_path, self.escalation_path, self.log_path):
            try:
                path.unlink()
            except OSError:
                pass
        with self._lock:
            self._ledger = {}
            self.current = None
            self.current_stop = None
            self.pause_until = 0.0
            self.pause_reason = ""
            self.consecutive_failures = 0
            self._attempts_window.clear()
