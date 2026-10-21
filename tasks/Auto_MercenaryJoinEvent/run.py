# -*- coding: utf-8 -*-
"""单独测试「事件：佣兵加入」（Auto_MercenaryJoinEvent）。

用法：
    python tasks/Auto_MercenaryJoinEvent/run.py
    python tasks/Auto_MercenaryJoinEvent/run.py --no-wait

等价于 python tools/taskpack.py run Auto_MercenaryJoinEvent ...
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from taskpack import main

if __name__ == "__main__":
    raise SystemExit(main(["run", "Auto_MercenaryJoinEvent", *sys.argv[1:]]))
