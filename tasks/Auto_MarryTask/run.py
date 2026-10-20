# -*- coding: utf-8 -*-
"""单独测试「自动相亲——小工具」（Auto_MarryTask）。

用法：
    python tasks/Auto_MarryTask/run.py
    python tasks/Auto_MarryTask/run.py --set 选项=值
    python tasks/Auto_MarryTask/run.py --no-wait

等价于 python tools/taskpack.py run Auto_MarryTask ...
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from taskpack import main

if __name__ == "__main__":
    raise SystemExit(main(["run", "Auto_MarryTask", *sys.argv[1:]]))
