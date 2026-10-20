# -*- coding: utf-8 -*-
"""单独测试「战斗任务主动」（AutoFight_Start）。

用法：
    python tasks/AutoFight_Start/run.py
    python tasks/AutoFight_Start/run.py --set 选项=值
    python tasks/AutoFight_Start/run.py --no-wait

等价于 python tools/taskpack.py run AutoFight_Start ...
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from taskpack import main

if __name__ == "__main__":
    raise SystemExit(main(["run", "AutoFight_Start", *sys.argv[1:]]))
