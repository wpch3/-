#!/usr/bin/env python3
"""天际整合包一键汉化 —— 双击/命令行入口。

用法：
    python run.py                      # 交互向导
    python run.py "D:\\MO2\\mods"       # 直接指定目录
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from skytrans.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
