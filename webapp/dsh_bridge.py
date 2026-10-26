# -*- coding: utf-8 -*-
"""DSH Bridge 客户端：通过本机 DSH 的「手机连接」Bridge 启动一个真正的 DSH 会话来修报错。

Bridge 是 DSH 插件在本机开的 JSON-RPC（换行分隔）服务：
    ~/.dsh/agents-anywhere/bridge/endpoint.json -> {host, port, token, pid}

协议（1.0）：
    initialize               authToken + connectorId + sessionNamespace
    catalog.listModels       取模型和 reasoning 选项
    catalog.listPermissions  取权限预设
    session.createAndStart   {sessionId, content, clientMessageId, cwd, selections, agentPreset}
    session.getState         {sessionId, externalSessionId?}
    session.getSnapshot      {sessionId, limit, cursor?}
    session.interrupt        {sessionId}

这里只做「启动一次、等它结束、超时就打断」，重试/上限/升级由 webapp.repair 负责。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from pathlib import Path

MAX_FRAME = 8 * 1024 * 1024


def default_endpoint() -> Path:
    home = Path(os.environ.get("DSH_HOME") or (Path.home() / ".dsh"))
    return home / "agents-anywhere" / "bridge" / "endpoint.json"


class BridgeError(RuntimeError):
    def __init__(self, message: str, code: str = "", retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def load_endpoint(path: str | Path | None = None) -> dict:
    candidate = Path(
        path
        or os.environ.get("DSH_BRIDGE_ENDPOINT")
        or default_endpoint()
    )
    try:
        data = json.loads(candidate.read_text(encoding="utf-8"))
    except OSError as exc:
        raise BridgeError(f"找不到 DSH Bridge（{candidate}）：{exc}", "BRIDGE_MISSING") from exc
    except ValueError as exc:
        raise BridgeError(f"DSH Bridge 配置损坏：{exc}", "BRIDGE_BAD_CONFIG") from exc
    try:
        return {
            "host": str(data.get("host") or "127.0.0.1"),
            "port": int(data.get("port")),
            "token": str(data.get("token") or ""),
            "pid": int(data.get("pid") or 0),
        }
    except (TypeError, ValueError) as exc:
        raise BridgeError(f"DSH Bridge 配置缺少端口/令牌：{exc}", "BRIDGE_BAD_CONFIG") from exc


class _Rpc:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.reader = reader
        self.writer = writer
        self._counter = 0
        self.runtime_error = ""

    async def call(self, method: str, params: dict | None = None, timeout: float = 30) -> dict:
        self._counter += 1
        request_id = f"selfheal-{self._counter}"
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
        }
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(line) > MAX_FRAME:
            raise BridgeError("请求内容太大", "FRAME_TOO_LARGE")
        self.writer.write(line + b"\n")
        await self.writer.drain()

        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BridgeError(f"DSH Bridge 响应超时：{method}", "TIMEOUT")
            raw = await asyncio.wait_for(self.reader.readline(), remaining)
            if not raw:
                raise BridgeError("DSH Bridge 连接已断开", "DISCONNECTED")
            if len(raw) > MAX_FRAME:
                raise BridgeError("DSH Bridge 返回内容过大", "FRAME_TOO_LARGE")
            message = json.loads(raw.decode("utf-8"))
            if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                continue
            if message.get("id") != request_id:
                if message.get("method") == "runtime.error":
                    data = (message.get("params") or {}).get("data") or {}
                    self.runtime_error = str(data.get("code") or "")
                continue
            error = message.get("error")
            if error:
                data = error.get("data") if isinstance(error.get("data"), dict) else {}
                raise BridgeError(
                    str(error.get("message") or "DSH Bridge 调用失败"),
                    str(data.get("code") or ""),
                    bool(data.get("retryable")),
                )
            result = message.get("result")
            if not isinstance(result, dict):
                raise BridgeError(f"DSH Bridge 返回格式不对：{method}", "BAD_RESULT")
            return result

    async def close(self) -> None:
        try:
            self.writer.close()
            await self.writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass


def _pick_model(models: list[dict], wanted: str, effort: str) -> tuple[dict, dict | None]:
    wanted = (wanted or "").strip().lower()
    chosen = None
    for item in models:
        if not isinstance(item, dict) or item.get("enabled") is False:
            continue
        names = {
            str(item.get("id") or "").lower(),
            str(item.get("title") or "").lower(),
            str((item.get("metadata") or {}).get("model") or "").lower(),
        }
        if wanted and wanted in names:
            chosen = item
            break
    if chosen is None:
        chosen = next((item for item in models if isinstance(item, dict) and item.get("enabled") is not False), None)
    if chosen is None:
        raise BridgeError("DSH 没有可用模型", "NO_MODEL")

    efforts = [item for item in chosen.get("reasoningItems") or [] if isinstance(item, dict)]
    if not efforts:
        return chosen, None
    wanted_effort = (effort or "").strip().lower()
    picked = next(
        (item for item in efforts if str(item.get("id") or "").lower() == wanted_effort),
        None,
    )
    if picked is None:
        picked = next((item for item in efforts if item.get("id") == "high"), efforts[0])
    return chosen, picked


def _permission(permissions: list[dict], wanted: str) -> dict:
    wanted = (wanted or "workspace-write").strip().lower()
    for item in permissions:
        if not isinstance(item, dict) or item.get("enabled") is False:
            continue
        if wanted in {
            str(item.get("id") or "").lower(),
            str(item.get("title") or "").lower(),
            str((item.get("metadata") or {}).get("preset") or "").lower(),
        }:
            return item
    for item in permissions:
        if isinstance(item, dict) and str(item.get("title")) == "workspace-write":
            return item
    if permissions:
        return permissions[0]
    raise BridgeError("DSH 没有可用权限预设", "NO_PERMISSION")


async def _run(
    prompt: str,
    cwd: str,
    *,
    model: str,
    effort: str,
    permission: str,
    agent_preset: str,
    timeout: float,
    poll: float,
    stop_event,
    on_update,
    session_label: str,
) -> dict:
    endpoint = load_endpoint()
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(
                endpoint["host"], endpoint["port"], limit=MAX_FRAME + 1
            ),
            8,
        )
    except (OSError, TimeoutError) as exc:
        raise BridgeError(f"连不上 DSH Bridge（{endpoint['host']}:{endpoint['port']}）", "BRIDGE_UNAVAILABLE") from exc

    rpc = _Rpc(reader, writer)
    try:
        await rpc.call(
            "initialize",
            {
                "authToken": endpoint["token"],
                "protocolVersion": "1.0",
                "runtime": "dsh",
                "connectorId": "selfheal-repair",
                "sessionNamespace": "selfheal-repair",
                "clientInfo": {"name": "selfheal-repair", "version": "1.0"},
            },
            timeout=10,
        )

        models = (await rpc.call("catalog.listModels", {"limit": 100}, timeout=20)).get("models") or []
        chosen, chosen_effort = _pick_model(models, model, effort)
        permissions = (await rpc.call("catalog.listPermissions", {"limit": 100}, timeout=20)).get("permissions") or []
        chosen_permission = _permission(permissions, permission)
        selection_model = (chosen_effort or chosen).get("selectionId") or chosen.get("selectionId")
        selections = {
            "model": selection_model,
            "permission": chosen_permission.get("selectionId"),
        }

        session_id = f"repair-{uuid.uuid4().hex[:10]}"
        created = await rpc.call(
            "session.createAndStart",
            {
                "sessionId": session_id,
                "content": prompt,
                "clientMessageId": str(uuid.uuid4()),
                "cwd": cwd,
                "selections": selections,
                "agentPreset": agent_preset or "cordis",
            },
            timeout=90,
        )
        if created.get("ok") is False:
            raise BridgeError(
                str(created.get("message") or "DSH 拒绝创建会话"),
                str(created.get("code") or "CREATE_FAILED"),
            )
        external = str(created.get("externalSessionId") or "")
        if on_update:
            on_update("running", session_id, external, f"模型 {chosen.get('title') or model}")

        deadline = time.monotonic() + timeout
        last_status = ""
        seen_active = False
        idle_since = 0.0
        while time.monotonic() < deadline:
            if stop_event is not None and stop_event.is_set():
                try:
                    await rpc.call("session.interrupt", {"sessionId": session_id}, timeout=10)
                except Exception:  # noqa: BLE001
                    pass
                return {
                    "status": "stopped",
                    "session_id": session_id,
                    "external": external,
                    "message": "用户停止",
                }
            state = await rpc.call(
                "session.getState",
                {
                    "sessionId": session_id,
                    **({"externalSessionId": external} if external else {}),
                },
                timeout=20,
            )
            status = str(state.get("status") or "")
            if status != last_status:
                last_status = status
                if on_update:
                    on_update(status or "unknown", session_id, external, "")
            if status in ("running", "pending", "waiting", "waiting_approval", "blocked", "stopping"):
                seen_active = True
                idle_since = 0.0
            if status == "idle":
                # createAndStart 刚返回时状态可能还是 idle，给 turn 一点启动时间
                if not seen_active and idle_since == 0.0:
                    idle_since = time.monotonic()
                if not seen_active and time.monotonic() - idle_since < 8:
                    await asyncio.sleep(1)
                    continue
                snapshot = await _snapshot(rpc, session_id, external)
                items = snapshot.get("items") or []
                end = next((item for item in reversed(items) if item.get("type") == "turn.end"), None)
                if end is None and not seen_active and time.monotonic() - idle_since < 15:
                    await asyncio.sleep(1)
                    continue
                reason = ((end or {}).get("content") or {}).get("reason") or {}
                return {
                    "status": "completed",
                    "session_id": session_id,
                    "external": external,
                    "reason": reason,
                    "items": items,
                }
            if status in ("error", "disconnected"):
                err = state.get("error") or {}
                last_error = str(err.get("message") or state.get("statusReason") or status)
                return {
                    "status": "error",
                    "session_id": session_id,
                    "external": external,
                    "message": last_error,
                }
            await asyncio.sleep(max(1.0, poll))

        try:
            await rpc.call("session.interrupt", {"sessionId": session_id}, timeout=10)
        except Exception:  # noqa: BLE001
            pass
        return {
            "status": "timeout",
            "session_id": session_id,
            "external": external,
            "message": f"超过 {int(timeout)} 秒还没结束，已打断",
        }
    finally:
        await rpc.close()


async def _snapshot(rpc: _Rpc, session_id: str, external: str) -> dict:
    params = {
        "sessionId": session_id,
        "limit": 200,
        **({"externalSessionId": external} if external else {}),
    }
    items: list[dict] = []
    for _ in range(20):
        result = await rpc.call("session.getSnapshot", params, timeout=30)
        items.extend(item for item in result.get("items") or [] if isinstance(item, dict))
        cursor = result.get("nextCursor")
        if not cursor:
            return {"items": items[-800:], "complete": result.get("complete")}
        params["cursor"] = cursor
    return {"items": items[-800:], "complete": False}


def run_repair_session(
    prompt: str,
    cwd: str,
    *,
    model: str = "DeepSeek-V4.1-Flash",
    effort: str = "high",
    permission: str = "workspace-write",
    agent_preset: str = "cordis",
    timeout: float = 600,
    poll: float = 3,
    stop_event=None,
    on_update=None,
    session_label: str = "",
) -> dict:
    """同步入口：给 RepairManager 的线程调用。"""
    return asyncio.run(
        _run(
            prompt,
            cwd,
            model=model,
            effort=effort,
            permission=permission,
            agent_preset=agent_preset,
            timeout=timeout,
            poll=poll,
            stop_event=stop_event,
            on_update=on_update,
            session_label=session_label,
        )
    )
