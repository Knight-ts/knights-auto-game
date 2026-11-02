# -*- coding: utf-8 -*-
"""单独测试「功能：市场购买面包」（Func_BuyBread）。

用法：
    python tasks/Func_BuyBread/run.py
    python tasks/Func_BuyBread/run.py --set 选项=值
    python tasks/Func_BuyBread/run.py --no-wait

等价于 python tools/taskpack.py run Func_BuyBread ...
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from taskpack import main

if __name__ == "__main__":
    raise SystemExit(main(["run", "Func_BuyBread", *sys.argv[1:]]))
