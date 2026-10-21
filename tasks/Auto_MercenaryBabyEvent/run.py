# -*- coding: utf-8 -*-
"""单独测试「事件：佣兵生娃」（Auto_MercenaryBabyEvent）。

用法：
    python tasks/Auto_MercenaryBabyEvent/run.py
    python tasks/Auto_MercenaryBabyEvent/run.py --no-wait

等价于 python tools/taskpack.py run Auto_MercenaryBabyEvent ...
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from taskpack import main

if __name__ == "__main__":
    raise SystemExit(main(["run", "Auto_MercenaryBabyEvent", *sys.argv[1:]]))
