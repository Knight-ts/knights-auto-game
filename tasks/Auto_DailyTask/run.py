# -*- coding: utf-8 -*-
"""单独测试「每日礼包和密令」（Auto_DailyTask）。

用法：
    python tasks/Auto_DailyTask/run.py
    python tasks/Auto_DailyTask/run.py --set 选项=值
    python tasks/Auto_DailyTask/run.py --no-wait

等价于 python tools/taskpack.py run Auto_DailyTask ...
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from taskpack import main

if __name__ == "__main__":
    raise SystemExit(main(["run", "Auto_DailyTask", *sys.argv[1:]]))
