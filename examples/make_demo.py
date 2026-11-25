#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""make_demo.py - 生成两份演示日志 + 一份演示 DBC，用来快速体验工具。

    python examples/make_demo.py
    python asc2html.py examples/run1.asc examples/run2.asc examples/demo.dbc --merge -o demo.html
"""
from __future__ import annotations

import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))

DBC = '''VERSION ""

NS_ :

BS_:

BU_: ECU

BO_ 256 M1: 8 ECU
 SG_ Speed : 0|16@1+ (0.1,0) [0|6553.5] "km/h" ECU
 SG_ Torque : 16|16@1+ (0.5,-1000) [-1000|1000] "Nm" ECU
'''


def write_asc(path: str, phase: float, amp: float, points: int = 2000) -> None:
    lines = [
        "date Wed Jan 01 10:00:00 AM 2025",
        "base hex timestamps absolute",
        "// version 7.0.0",
    ]
    for k in range(points):
        t = k * 0.01
        sp = int(500 + 300 * amp * math.sin(t * 1.2 + phase)) & 0xFFFF      # 0.1 km/h
        tq = int(1000 + 600 * amp * math.cos(t * 0.9 + phase)) & 0xFFFF     # 0.5 Nm, offset -1000
        data = [sp & 0xFF, (sp >> 8) & 0xFF, tq & 0xFF, (tq >> 8) & 0xFF, 0, 0, 0, 0]
        lines.append("%.2f 1 100 Rx d 8 %s" % (t + 1.0, " ".join("%02X" % b for b in data)))
    with open(path, "w", encoding="ascii", newline="\n") as f:
        f.write("\n".join(lines) + "\n")


def main() -> None:
    with open(os.path.join(HERE, "demo.dbc"), "w", encoding="ascii", newline="\n") as f:
        f.write(DBC)
    write_asc(os.path.join(HERE, "run1.asc"), 0.0, 1.0)
    write_asc(os.path.join(HERE, "run2.asc"), 0.9, 1.35)
    print("已生成:")
    for name in ("demo.dbc", "run1.asc", "run2.asc"):
        p = os.path.join(HERE, name)
        print(f"  {p}  ({os.path.getsize(p)/1024:.0f} KB)")
    print("\n试试:")
    print("  python asc2html.py examples/run1.asc examples/run2.asc examples/demo.dbc --merge -o demo.html")


if __name__ == "__main__":
    main()
