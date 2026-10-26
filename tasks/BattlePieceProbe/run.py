# -*- coding: utf-8 -*-
"""单独测试「功能：棋子识别（战斗中）」（BattlePieceProbe）。

用法：
    python tasks/BattlePieceProbe/run.py
    python tasks/BattlePieceProbe/run.py --set 选项=值
    python tasks/BattlePieceProbe/run.py --no-wait

等价于 python tools/taskpack.py run BattlePieceProbe ...
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from taskpack import main

if __name__ == "__main__":
    raise SystemExit(main(["run", "BattlePieceProbe", *sys.argv[1:]]))
