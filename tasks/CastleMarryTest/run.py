# -*- coding: utf-8 -*-
"""单独测试「城堡相亲（单项测试）」（CastleMarryTest）。

用法：
    python tasks/CastleMarryTest/run.py
    python tasks/CastleMarryTest/run.py --set 选项=值
    python tasks/CastleMarryTest/run.py --no-wait

等价于 python tools/taskpack.py run CastleMarryTest ...
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from taskpack import main

if __name__ == "__main__":
    raise SystemExit(main(["run", "CastleMarryTest", *sys.argv[1:]]))
