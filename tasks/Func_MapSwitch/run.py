# -*- coding: utf-8 -*-
"""单独测试「功能：地图切换」（Func_MapSwitch）。

用法：
    python tasks/Func_MapSwitch/run.py
    python tasks/Func_MapSwitch/run.py --set 地图切换目标=加尔提兰
    python tasks/Func_MapSwitch/run.py --no-wait

等价于 python tools/taskpack.py run Func_MapSwitch ...
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from taskpack import main

if __name__ == "__main__":
    raise SystemExit(main(["run", "Func_MapSwitch", *sys.argv[1:]]))
