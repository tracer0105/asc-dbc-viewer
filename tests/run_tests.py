#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_tests.py - asc2html 的回归测试。

    python tests/run_tests.py            # 全部
    python tests/run_tests.py -v         # 显示每个用例

不依赖 pytest，退出码 0 表示全部通过。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.dirname(HERE)
SCRIPT = os.path.join(TOOL, "asc2html.py")
TMP = tempfile.mkdtemp(prefix="asc_test_")

RESULTS: list[tuple[str, bool, str]] = []


# --------------------------------------------------------------------------- #
# 工具函数
# --------------------------------------------------------------------------- #
def w(name: str, text: str) -> str:
    p = os.path.join(TMP, name)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="ascii", newline="\n") as f:
        f.write(text.lstrip("\n"))
    return p


def asc(body: str, base: str = "hex") -> str:
    return f'date Wed Jan 01 10:00:00 AM 2025\nbase {base} timestamps absolute\n// version 7.0.0\n{body}'


def run(*args, timeout=180):
    cmd = [sys.executable, SCRIPT, *args]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def payload(html_path: str) -> dict:
    with open(html_path, encoding="utf-8") as f:
        s = f.read()
    m = re.search(r'<script id="payload" type="application/json">(.*?)</script>', s, re.S)
    assert m, "HTML 里找不到 payload"
    assert "__PAYLOAD__" not in s, "模板占位符未替换"
    return json.loads(m.group(1).replace("<\\/", "</"))


def sig(d: dict, needle: str) -> dict:
    for s in d["signals"]:
        if needle in s["label"]:
            return s
    raise AssertionError(f"找不到信号 {needle}，实际有 {[x['label'] for x in d['signals']][:10]}")


def case(name):
    def deco(fn):
        try:
            fn()
            RESULTS.append((name, True, ""))
            print(f"  PASS  {name}")
        except Exception as e:  # noqa: BLE001
            msg = f"{e}"
            RESULTS.append((name, False, msg))
            print(f"  FAIL  {name}\n        {msg.splitlines()[0] if msg else ''}")
            if "-v" in sys.argv:
                traceback.print_exc()
        return fn
    return deco


# --------------------------------------------------------------------------- #
# 用例
# --------------------------------------------------------------------------- #
sys.path.insert(0, TOOL)
import asc2html  # noqa: E402


@case("FD 行三种变体都能识别")
def t_layout():
    a = w("v_std.asc", asc("1.000 CANFD 1 Rx 18F 1 0 8 8 d 11 22 33 44 55 66 77 88\n"))
    b = w("v_iddir.asc", asc("1.000 CANFD 1 18F Rx 1 0 d 8 8 11 22 33 44 55 66 77 88\n"))
    c = w("v_plain.asc", asc("1.000 CANFD 1 Rx 18F 1 0 8 8 11 22 33 44 55 66 77 88\n"))
    assert asc2html.detect_layout(a) == "dir_id"
    assert asc2html.detect_layout(b) == "id_dir"
    datas = []
    for p in (a, b, c):
        data = None
        for m in asc2html.iter_messages(p, asc2html.detect_layout(p), False, {}):
            data = bytes(m.data)
        assert data == bytes([0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x88]), f"{p} -> {data}"
        datas.append(data)


@case("hex / dec 进制与 64 字节 FD")
def t_base():
    body = "1.000 CANFD 1 400 Rx 1 0 15 64 d " + " ".join(str(i) for i in range(1, 65)) + "\n"
    p = w("base_dec64.asc", asc(body, base="dec"))
    assert asc2html.detect_base(p) == "dec"
    msgs = list(asc2html.iter_messages(p, asc2html.detect_layout(p), False, {}, "dec"))
    assert len(msgs) == 1 and len(msgs[0].data) == 64
    assert msgs[0].arbitration_id == 400 and msgs[0].data[0] == 1 and msgs[0].data[-1] == 64


@case("首帧不丢失 / 帧数准确")
def t_first_frame():
    p = w("two.asc", asc("1.000 1 100 Rx d 8 1 2 3 4 5 6 7 8\n1.010 1 100 Rx d 8 8 7 6 5 4 3 2 1\n"))
    msgs = list(asc2html.iter_messages(p, asc2html.detect_layout(p), False, {}))
    assert len(msgs) == 2, f"期望 2 帧，实际 {len(msgs)}"


@case("时间戳重置后累加为单调")
def t_rebase():
    body = ("0.100 1 100 Rx d 8 1 1 1 1 1 1 1 1\n"
            "0.200 1 100 Rx d 8 2 2 2 2 2 2 2 2\n"
            "0.000 1 100 Rx d 8 3 3 3 3 3 3 3 3\n"
            "0.150 1 100 Rx d 8 4 4 4 4 4 4 4 4\n")
    p = w("seg.asc", asc(body))
    st = {"resets": 0}
    ts = [m.timestamp for m in asc2html.iter_messages(p, "dir_id", True, st)]
    assert st["resets"] == 1, f"重置计数 {st['resets']}"
    assert ts == sorted(ts) and abs(ts[2] - 0.2) < 1e-6, ts
    raw = [m.timestamp for m in asc2html.iter_messages(p, "dir_id", False, {})]
    assert raw == [0.1, 0.2, 0.0, 0.15]


@case("无 DBC：按 ID+字节输出")
def t_nodbc():
    p = w("nodbc.asc", asc("1.000 1 100 Rx d 4 10 20 30 40\n1.010 1 101 Rx d 2 50 60\n"))
    out = os.path.join(TMP, "nodbc.html")
    rc, log = run(p, "-o", out, "--no-dbc", "--bytes", "3")
    assert rc == 0, log
    d = payload(out)
    labels = {s["label"] for s in d["signals"]}
    # 0x101 只有 2 字节，所以只出 b0/b1
    assert labels == {"0x100.b0", "0x100.b1", "0x100.b2", "0x101.b0", "0x101.b1"}, labels
    assert sig(d, "0x100.b0")["v"] == [0x10]


@case("DBC 多路复用(mux)解码")
def t_mux():
    dbc = w("mux.dbc", '''VERSION ""

NS_ :

BS_:

BU_: ECU

BO_ 512 MUXMSG: 8 ECU
 SG_ Mode M : 0|8@1+ (1,0) [0|255] "" ECU
 SG_ ValA m0 : 8|8@1+ (1,0) [0|255] "" ECU
 SG_ ValB m1 : 8|8@1+ (0.5,0) [0|255] "V" ECU
 SG_ Temp m1 : 16|8@1+ (1,-40) [-40|215] "degC" ECU
''')
    a = w("mux.asc", asc("1.000 1 200 Rx d 8 00 0A 00 00 00 00 00 00\n"
                        "1.100 1 200 Rx d 8 01 14 64 00 00 00 00 00\n"))
    out = os.path.join(TMP, "mux.html")
    rc, log = run(a, dbc, "-o", out)
    assert rc == 0, log
    d = payload(out)
    assert sig(d, "ValA")["v"] == [10]
    assert abs(sig(d, "ValB")["v"][0] - 10.0) < 1e-9
    assert sig(d, "Temp")["v"] == [60]
    assert sig(d, "Mode")["v"] == [0, 1]


@case("DBC 重叠别名 / 信号超出长度 自动修复")
def t_alias():
    dbc = w("alias.dbc", '''VERSION ""

NS_ :

BS_:

BU_: ECU

BO_ 512 ALI: 8 ECU
 SG_ A : 0|8@1+ (1,0) [0|255] "" ECU
 SG_ B : 0|8@1+ (1,0) [0|255] "" ECU
 SG_ C : 56|16@1+ (1,0) [0|65535] "" ECU
''')
    a = w("alias.asc", asc("1.000 1 200 Rx d 8 2A 00 00 00 00 00 00 00\n"))
    out = os.path.join(TMP, "alias.html")
    rc, log = run(a, dbc, "-o", out)
    assert rc == 0, log
    d = payload(out)
    assert sig(d, "A")["v"] == [42]
    assert sig(d, "B")["v"] == [42], "别名信号没有回填"
    assert "解码失败 0" in log


@case("CLI: --ids / --signal / --from --to / -o")
def t_filters():
    dbc = w("f.dbc", '''VERSION ""

NS_ :

BS_:

BU_: ECU

BO_ 256 M1: 8 ECU
 SG_ Speed : 0|16@1+ (1,0) [0|65535] "km/h" ECU
 SG_ Torque : 16|16@1+ (0.1,-100) [-100|100] "Nm" ECU

BO_ 257 M2: 8 ECU
 SG_ Temp : 0|8@1+ (1,-40) [-40|215] "degC" ECU
''')
    body = "".join(f"{t:.1f} 1 {0x100 if t % 2 else 0x101:03X} Rx d 8 64 00 0A 00 00 00 00 00\n" for t in range(1, 11))
    a = w("filt.asc", asc(body))
    out = os.path.join(TMP, "filt.html")
    rc, log = run(a, dbc, "-o", out, "--ids", "100", "--from", "4", "--to", "6")
    assert rc == 0, log
    d = payload(out)
    assert all(s["msg"] == "M1" for s in d["signals"]), [s["msg"] for s in d["signals"]]
    t = sig(d, "M1.Speed")["t"]
    assert all(4 <= x <= 6 for x in t), t

    rc, log = run(a, dbc, "-o", out, "--signal", "^M2")
    assert rc == 0, log
    d = payload(out)
    assert [s["msg"] for s in d["signals"]] == ["M2"]


@case("payload 结构与数值自洽")
def t_payload():
    dbc = w("p.dbc", '''VERSION ""

NS_ :

BS_:

BU_: ECU

BO_ 256 M1: 8 ECU
 SG_ Speed : 0|16@1+ (1,0) [0|65535] "km/h" ECU
 SG_ Torque : 16|16@1+ (0.1,-100) [-100|100] "Nm" ECU
''')
    a = w("p.asc", asc("1.000 1 100 Rx d 8 E8 03 64 00 00 00 00 00\n"
                      "1.500 1 100 Rx d 8 D0 07 C8 00 00 00 00 00\n"))
    out = os.path.join(TMP, "p.html")
    rc, log = run(a, dbc, "-o", out)
    assert rc == 0, log
    d = payload(out)
    s = sig(d, "Speed")
    assert s["v"] == [1000, 2000] and s["unit"] == "km/h", s["v"]
    assert abs(sig(d, "Torque")["v"][0] - (-90.0)) < 1e-9  # raw 0x64*0.1-100
    for sg in d["signals"]:
        assert len(sg["t"]) == len(sg["v"])
        assert sg["t"] == sorted(sg["t"])
        assert sg["min"] <= sg["max"]
    assert d["meta"]["frames"] == 2
    assert abs(d["meta"]["t0"] - 1.0) < 1e-9 and abs(d["meta"]["t1"] - 1.5) < 1e-9


@case("BLF 输入")
def t_blf():
    try:
        import can
    except ImportError:
        raise AssertionError("未安装 python-can")
    a = w("b.asc", asc("1.000 1 100 Rx d 8 01 02 03 04 05 06 07 08\n1.010 1 100 Rx d 8 08 07 06 05 04 03 02 01\n"))
    blf = os.path.join(TMP, "b.blf")
    with can.ASCReader(a) as r, can.BLFWriter(blf) as wr:
        for m in r:
            wr.on_message_received(m)
    out = os.path.join(TMP, "b.html")
    rc, log = run(blf, "-o", out, "--bytes", "2")
    assert rc == 0, log
    # python-can 的 BLFWriter 会用第一条做时间基准、不写入，所以按实际内容比对
    with can.BLFReader(blf) as r:
        exp = [m.data[0] for m in r]
    assert exp, "BLF 里没有帧"
    d = payload(out)
    assert sig(d, "0x100.b0")["v"] == exp, (sig(d, "0x100.b0")["v"], exp)


@case("错误处理：文件不存在 / 空结果")
def t_errors():
    rc, log = run(os.path.join(TMP, "nope.asc"))
    assert rc != 0 and "找不到文件" in log, log
    a = w("e.asc", asc("1.000 1 100 Rx d 8 1 2 3 4 5 6 7 8\n"))
    rc, log = run(a, "-o", os.path.join(TMP, "e.html"), "--ids", "7FF")
    assert rc != 0 and "没有采集到" in log, log


@case("GUI 模块可加载（不弹窗）")
def t_gui():
    import tkinter as tk
    sys.path.insert(0, TOOL)
    import gui  # noqa: F401
    try:
        root = tk.Tk()
    except tk.TclError as e:
        raise AssertionError(f"无显示环境: {e}")
    root.withdraw()
    app = gui.App(root)
    root.update_idletasks()
    assert app.guess_dbc(os.path.join(TMP, "x.asc")) == ""
    root.destroy()


@case("多文件：一个 DBC + 两个 ASC 各自出图")
def t_multi():
    dbc = w("multi.dbc", '''VERSION ""

NS_ :

BS_:

BU_: ECU

BO_ 256 M1: 8 ECU
 SG_ Speed : 0|16@1+ (1,0) [0|65535] "km/h" ECU
''')
    a1 = w("multi1.asc", asc("1.000 1 100 Rx d 8 E8 03 00 00 00 00 00 00\n"))
    a2 = w("multi2.asc", asc("2.000 1 100 Rx d 8 D0 07 00 00 00 00 00 00\n"))
    outdir = os.path.join(TMP, "multi_out")
    rc, log = run(a1, a2, dbc, "-o", outdir)
    assert rc == 0, log
    f1 = os.path.join(outdir, "multi1.html")
    f2 = os.path.join(outdir, "multi2.html")
    assert os.path.isfile(f1) and os.path.isfile(f2), os.listdir(outdir)
    assert sig(payload(f1), "Speed")["v"] == [1000]
    assert sig(payload(f2), "Speed")["v"] == [2000]


@case("多文件：--merge 合成一张图，时间各自从 0")
def t_merge():
    dbc = w("merge.dbc", '''VERSION ""

NS_ :

BS_:

BU_: ECU

BO_ 256 M1: 8 ECU
 SG_ Speed : 0|16@1+ (1,0) [0|65535] "km/h" ECU
''')
    a1 = w("mg1.asc", asc("5.000 1 100 Rx d 8 E8 03 00 00 00 00 00 00\n6.000 1 100 Rx d 8 D0 07 00 00 00 00 00 00\n"))
    a2 = w("mg2.asc", asc("9.000 1 100 Rx d 8 10 27 00 00 00 00 00 00\n"))
    out = os.path.join(TMP, "merged.html")
    rc, log = run(a1, a2, dbc, "--merge", "-o", out)
    assert rc == 0, log
    d = payload(out)
    assert d["meta"]["files"] == ["mg1", "mg2"], d["meta"].get("files")
    labels = [x["label"] for x in d["signals"]]
    assert labels == ["M1.Speed", "M1.Speed"], labels      # 同一信号两份，前端叠加
    assert [x["fi"] for x in d["signals"]] == [0, 1]
    for sg in d["signals"]:
        assert sg["t"][0] == 0.0, (sg["label"], sg["t"][0])
    assert d["signals"][0]["v"] == [1000, 2000]
    assert d["signals"][1]["v"] == [10000]
    assert d["meta"]["t1"] == 1.0


@case("多文件：-o 给 .html 时报错提示用 --merge")
def t_multi_err():
    a1 = w("e1.asc", asc("1.000 1 100 Rx d 8 1 2 3 4 5 6 7 8\n"))
    a2 = w("e2.asc", asc("1.000 1 100 Rx d 8 1 2 3 4 5 6 7 8\n"))
    rc, log = run(a1, a2, "--no-dbc", "-o", os.path.join(TMP, "x.html"))
    assert rc != 0 and "--merge" in log, log


# --------------------------------------------------------------------------- #
def main() -> int:
    print(f"临时目录: {TMP}\n测试目录: {TOOL}\n")
    ok = sum(1 for r in RESULTS if r[1])
    print(f"\n===== {ok}/{len(RESULTS)} 通过 =====")
    if ok != len(RESULTS):
        for n, good, msg in RESULTS:
            if not good:
                print(f"  FAILED: {n}\n    {msg}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
