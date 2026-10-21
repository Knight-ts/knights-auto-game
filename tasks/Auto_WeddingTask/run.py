# -*- coding: utf-8 -*-
"""单独测试「婚礼宴会（单项测试）」（Auto_WeddingTask）。

用法：
    python tasks/Auto_WeddingTask/run.py
    python tasks/Auto_WeddingTask/run.py --set 婚礼宴会档位=乡村宴会
    python tasks/Auto_WeddingTask/run.py --no-wait

等价于 python tools/taskpack.py run Auto_WeddingTask ...
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from taskpack import main

if __name__ == "__main__":
    raise SystemExit(main(["run", "Auto_WeddingTask", *sys.argv[1:]]))
