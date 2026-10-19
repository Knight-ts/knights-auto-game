# -*- coding: utf-8 -*-
"""孩子命名速度配置。

用户可在网页「好娃提醒」页选择 fast / normal / slow，影响：
    - feature_max_swipe：特性面板最多滑动几次（越少越快，但可能漏特性）
    - step_delay_ms：点名、输入、确认等 UI 步骤的固定等待
    - timeout_ms：这些节点识别失败时的等待上限

配置文件：table/child_name_config.json
    {"speed": "normal"}
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "table" / "child_name_config.json"

SPEED_PROFILES: dict[str, dict] = {
    "fast": {
        "name": "fast",
        "label": "快速（跳过部分滑动，UI 等待 200ms）",
        "feature_max_swipe": 2,
        "step_delay_ms": 200,
        "timeout_ms": 1500,
    },
    "normal": {
        "name": "normal",
        "label": "标准（默认）",
        "feature_max_swipe": 5,
        "step_delay_ms": 500,
        "timeout_ms": 2000,
    },
    "slow": {
        "name": "slow",
        "label": "慢速（适合卡顿的模拟器）",
        "feature_max_swipe": 8,
        "step_delay_ms": 800,
        "timeout_ms": 3000,
    },
}

DEFAULT_SPEED = "normal"


def load_speed() -> str:
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        speed = str(data.get("speed", DEFAULT_SPEED)).strip().lower()
    except (OSError, json.JSONDecodeError):
        return DEFAULT_SPEED
    return speed if speed in SPEED_PROFILES else DEFAULT_SPEED


def save_speed(speed: str) -> str:
    speed = str(speed).strip().lower()
    if speed not in SPEED_PROFILES:
        raise ValueError(f"未知速度：{speed}")
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps({"speed": speed}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return speed


def get_profile() -> dict:
    return SPEED_PROFILES[load_speed()]
