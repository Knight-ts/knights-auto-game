# -*- coding: utf-8 -*-
"""好娃提醒的平台推送钩子（可以后期接）。

配置：table/notify_config.json
    {
      "enabled": false,
      "format": "feishu",          // feishu | json
      "webhook": "",               // 留空时自动使用 config/config.json 里的飞书 webhook
      "timeout": 8
    }

默认关闭；打开并填好 webhook 后，每次触发好娃提醒会 POST 一条消息。
推送失败只记日志，不会影响主流程。
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

try:
    from utils.logger import logger  # type: ignore
except Exception:  # noqa: BLE001  （网页版等独立进程里可能没有 utils 包）
    import logging

    logger = logging.getLogger("maagc.notifier")

CONFIG_PATH = Path("table") / "notify_config.json"
APP_CONFIG_PATH = Path("config") / "config.json"

DEFAULT_CONFIG = {
    "enabled": False,
    "format": "feishu",
    "webhook": "",
    "timeout": 8,
}


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        try:
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            CONFIG_PATH.write_text(
                json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError:
            pass
        return dict(DEFAULT_CONFIG)

    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(f"读取推送配置失败，使用默认值：{exc}")
        return dict(DEFAULT_CONFIG)

    merged = dict(DEFAULT_CONFIG)
    merged.update({k: v for k, v in data.items() if k in DEFAULT_CONFIG})
    return merged


def _fallback_webhook() -> str:
    """没填 webhook 时，复用 GUI 里配置的飞书机器人地址。"""
    try:
        data = json.loads(APP_CONFIG_PATH.read_text(encoding="utf-8"))
        return (data.get("ExternalNotificationLarkWebhookUrl") or "").strip()
    except (OSError, json.JSONDecodeError):
        return ""


def effective_webhook() -> str:
    """实际使用的 webhook：优先配置里的，其次复用 GUI 的飞书地址。"""
    config = load_config()
    return (config.get("webhook") or "").strip() or _fallback_webhook()


def notify(title: str, content: str) -> bool:
    """推送一条消息；未启用/未配置/失败都返回 False。"""
    config = load_config()
    if not config.get("enabled"):
        return False

    webhook = (config.get("webhook") or "").strip() or _fallback_webhook()
    if not webhook:
        logger.warning("好娃提醒推送已启用，但没有配置 webhook")
        return False

    if config.get("format") == "json":
        payload = {"title": title, "content": content}
    else:
        payload = {"msg_type": "text", "content": {"text": f"{title}\n{content}"}}

    try:
        request = urllib.request.Request(
            webhook,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=float(config.get("timeout", 8))) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        logger.info(f"好娃提醒已推送到平台：{body[:200]}")
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"好娃提醒推送失败：{exc}")
        return False
