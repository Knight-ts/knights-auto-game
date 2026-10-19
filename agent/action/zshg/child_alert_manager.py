# -*- coding: utf-8 -*-
"""好娃提醒入口。

两个版本：
    ChildAlertWindow  —— 原生窗口（WinForms，PowerShell 承载），在 MaaGC 里点任务直接弹窗
    ChildAlertManager —— 浏览器版控制台（主页 / 总控台 / 截图 / 好娃提醒）

数据都通过本机控制台接口读写（127.0.0.1:8790），窗口版不需要浏览器。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction
from utils import logger

from .web_console import console_url, open_console, project_root

POWERSHELL = Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
WINDOW_SCRIPT = "好娃提醒.ps1"
CREATE_NO_WINDOW = 0x08000000


@AgentServer.custom_action("ChildAlertWindow")
class ChildAlertWindow(CustomAction):
    """在 MaaGC 里弹出原生「好娃提醒」管理窗口。"""

    def run(self, context, argv) -> CustomAction.RunResult:
        script = project_root() / "tools" / WINDOW_SCRIPT
        if not script.exists():
            logger.error(f"找不到窗口脚本：{script}")
            return CustomAction.RunResult(success=False)

        try:
            base_url = console_url().rstrip("/")
        except Exception as exc:  # noqa: BLE001
            logger.error(f"启动本地服务失败: {exc}")
            return CustomAction.RunResult(success=False)

        try:
            subprocess.Popen(
                [
                    str(POWERSHELL),
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-STA",
                    "-File",
                    str(script),
                    "-BaseUrl",
                    base_url,
                ],
                cwd=str(project_root()),
                creationflags=CREATE_NO_WINDOW,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(f"打开好娃提醒窗口失败: {exc}")
            return CustomAction.RunResult(success=False)

        logger.info("好娃提醒窗口已打开（修改后点「保存并同步」）")
        return CustomAction.RunResult(success=True)


@AgentServer.custom_action("ChildAlertManager")
class ChildAlertManager(CustomAction):
    """打开浏览器版网页控制台。"""

    def run(self, context, argv) -> CustomAction.RunResult:
        try:
            url = open_console()
        except Exception as exc:  # noqa: BLE001
            logger.error(f"启动网页控制台失败: {exc}")
            return CustomAction.RunResult(success=False)

        logger.info(f"网页控制台：{url}（关闭页面不影响任务）")
        return CustomAction.RunResult(success=True)
