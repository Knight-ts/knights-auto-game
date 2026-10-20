# -*- coding: utf-8 -*-
"""单独测试「推年计划」（Auto_YearlyTask）。

用法：
    python tasks/Auto_YearlyTask/run.py
    python tasks/Auto_YearlyTask/run.py --set 选项=值
    python tasks/Auto_YearlyTask/run.py --no-wait

等价于 python tools/taskpack.py run Auto_YearlyTask ...
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from taskpack import main

if __name__ == "__main__":
    raise SystemExit(main(["run", "Auto_YearlyTask", *sys.argv[1:]]))
