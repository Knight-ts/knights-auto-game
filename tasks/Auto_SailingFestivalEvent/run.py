# -*- coding: utf-8 -*-
"""单独测试「节日：3月启航节」（Auto_SailingFestivalEvent）。

用法：
    python tasks/Auto_SailingFestivalEvent/run.py
    python tasks/Auto_SailingFestivalEvent/run.py --no-wait

等价于 python tools/taskpack.py run Auto_SailingFestivalEvent ...
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from taskpack import main

if __name__ == "__main__":
    raise SystemExit(main(["run", "Auto_SailingFestivalEvent", *sys.argv[1:]]))
