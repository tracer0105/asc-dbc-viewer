#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""asc2html.py - 把 Vector ASC (CAN / CAN FD) 日志 +/- DBC 转成可离线交互的 HTML 曲线图。

依赖: python-can, cantools   （不依赖 matplotlib / numpy / 联网）

用法:
    python asc2html.py log.asc                      # 无 DBC: 按 ID+字节看原始帧
    python asc2html.py log.asc net.dbc              # 有 DBC: 解码成物理量
    python asc2html.py a.asc b.asc net.dbc           # 多份日志（同一 DBC）各出一份
    python asc2html.py a.asc b.asc net.dbc --merge    # 多份日志合成一张对比图

可选:
    -o, --out PATH       输出 HTML；多个 ASC 时视为输出目录
    --dbc FILE           显式指定矩阵（也可把 .dbc 直接写在文件列表里）
    --merge              多份日志合并成一张图（同信号叠加，时间各自从 0 开始）
    --ids 10C,32B        只处理指定 ID (十六进制)
    --signal REGEX       只输出匹配“报文名.信号名”的信号
    --max-points N       每条信号的降采样桶大小 (默认 6000, 保留极值)
    --max-signals N      无 DBC 时最多输出多少 ID (默认 8)
    --bytes N            无 DBC 时每个 ID 取前几个字节 (默认 8)
    --from / --to        只取某段时间 (秒, 重整后的时间轴)
    --no-rebase          不修正时间戳回绕/重置
    --no-dbc             忽略 DBC（含自动发现）
    --open               生成后用默认浏览器打开
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
from collections import OrderedDict

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

try:
    import can
except ImportError:
    sys.exit("缺少依赖: pip install python-can")
try:
    import cantools
except ImportError:
    cantools = None  # 只有给了 DBC 才必须

HERE = os.path.dirname(os.path.abspath(__file__))
VERSION = "1.1.0"


def resource(name: str) -> str:
    """打包成 exe 后，--add-data 的资源在 sys._MEIPASS 下。"""
    base = getattr(sys, "_MEIPASS", HERE)
    p = os.path.join(base, name)
    return p if os.path.exists(p) else os.path.join(HERE, name)


TEMPLATE = resource("template.html")

# 报文长度 -> CAN FD DLC 代码
LEN2DLC = {0: 0, 1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6, 7: 7, 8: 8,
           12: 9, 16: 10, 20: 11, 24: 12, 32: 13, 48: 14, 64: 15}


# --------------------------------------------------------------------------- #
# ASC 读取
# --------------------------------------------------------------------------- #
def detect_layout(path: str, probe: int = 2000) -> str:
    """Vector 标准是 `CANFD <ch> <dir> <id> ...`；
    部分台架/工具写成 `CANFD <ch> <id> <dir> ...`，需要交换字段。
    返回 'dir_id' 或 'id_dir'。
    """
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f):
            if i > probe:
                break
            p = line.split()
            if len(p) > 4 and p[1] == "CANFD":
                if p[2] in ("Rx", "Tx"):
                    return "dir_id"
                if p[4] in ("Rx", "Tx"):
                    return "id_dir"
    return "dir_id"


def detect_base(path: str, probe: int = 6) -> str:
    """读文件头，判断 ID/数据是 hex 还是 dec（默认 hex）。"""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f):
            if i > probe:
                break
            m = re.match(r"base\s+(hex|dec)", line.strip(), re.I)
            if m:
                return m.group(1).lower()
    return "hex"


class _NormalizedFile:
    """行迭代包装：把各家工具写出的 ASC 变体重排成 python-can ASCReader 认识的格式。

    变体 1  id/dir 顺序：`CANFD 1 519 Rx ...`    -> `CANFD 1 Rx 519 ...`
    变体 2  'd' 标记位置：`... 1 0 d 15 64 ...`   -> `... 1 0 f 64 ...`（dlc 换成本文件进制）
    """

    def __init__(self, path: str, layout: str, base: str = "hex"):
        self._f = open(path, "r", encoding="utf-8", errors="replace")
        self._layout = layout
        self._base = base

    def __iter__(self):
        # 先喂一段合成文件头：ASCReader 读头时会“吃掉”遇到的第一条非头部行，
        # 这样被吃掉的只是这里的 "internal events logged"，不会丢真实报文
        yield "date Wed Jan 01 00:00:00 2020\n"
        yield f"base {self._base} timestamps absolute\n"
        yield "internal events logged\n"
        for line in self._f:
            yield self._fix(line)

    def _fix(self, line: str) -> str:
        p = line.split()
        if len(p) < 9 or p[1] != "CANFD":
            return line
        if p[3] not in ("Rx", "Tx") and p[4] in ("Rx", "Tx"):
            p[2], p[3], p[4] = p[2], p[4], p[3]
        if p[3] not in ("Rx", "Tx"):
            return line                      # ErrorFrame / 其他事件
        rest = p[5:]
        if len(rest) < 4:
            return line
        if rest[2] == "d":                   # 变体：brs esi d dlc len data
            dlc_s, len_s, data = rest[3], rest[4], rest[5:]
        else:                                # 标准：brs esi dlc len d data
            dlc_s, len_s = rest[2], rest[3]
            data = rest[5:] if len(rest) > 4 and rest[4] == "d" else rest[4:]
        try:
            ln = int(len_s)
        except ValueError:
            return line
        code = LEN2DLC.get(ln)
        if code is None:                     # 长度不标准，退回文件里的 dlc
            try:
                code = int(dlc_s, 16) if any(c in "abcdefABCDEF" for c in dlc_s) else int(dlc_s)
            except ValueError:
                return line
        data = data[:ln]                     # 截断尾部时长/CRC 等字段
        dlc_out = f"{code:x}" if self._base == "hex" else str(code)
        # 注意: python-can 的 FD 行没有 'd' 标记
        return f"{p[0]} CANFD {p[2]} {p[3]} {p[4]} {p[5]} {p[6]} {dlc_out} {ln} " + " ".join(data) + "\n"

    def close(self):
        self._f.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def iter_messages(path: str, layout: str, rebase: bool = True, stats: dict | None = None,
                  base: str = "hex"):
    """迭代 CAN 报文；rebase=True 时把时间戳重置（回绕）累加修正为单调时间。"""
    stats = stats if stats is not None else {}
    stats.setdefault("resets", 0)

    if path.lower().endswith(".blf"):        # BLF 是二进制、时间戳本身单调
        with can.BLFReader(path) as reader:
            for msg in reader:
                yield msg
        return

    off = 0.0
    prev_raw = None
    f = _NormalizedFile(path, layout, base)
    try:
        with can.ASCReader(f) as reader:
            for msg in reader:
                tr = msg.timestamp
                if rebase:
                    if prev_raw is not None and tr < prev_raw - 1e-9:
                        off += prev_raw      # 分段日志：时间戳归零后继续累加
                        stats["resets"] += 1
                    msg.timestamp = tr + off
                    prev_raw = tr
                else:
                    msg.timestamp = tr
                yield msg
    finally:
        f.close()


# --------------------------------------------------------------------------- #
# 降采样（保留每桶极值，避免丢毛刺）
# --------------------------------------------------------------------------- #
def downsample(ts: list, vs: list, maxn: int):
    n = len(ts)
    if n <= maxn or maxn <= 0:
        return ts, vs
    bucket = math.ceil(n / maxn)
    out_t: list = []
    out_v: list = []
    for i in range(0, n, bucket):
        j = min(n, i + bucket)
        imin = imax = None
        for k in range(i, j):
            v = vs[k]
            if v is None:
                continue
            if imin is None or v < vs[imin]:
                imin = k
            if imax is None or v > vs[imax]:
                imax = k
        idx = {i, j - 1}
        if imin is not None:
            idx.add(imin)
        if imax is not None:
            idx.add(imax)
        for k in sorted(idx):
            if vs[k] is None:
                continue
            out_t.append(ts[k])
            out_v.append(vs[k])
    return out_t, out_v


def pstdev(vals: list) -> float:
    xs = [v for v in vals if v is not None]
    if len(xs) < 2:
        return 0.0
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs))


# --------------------------------------------------------------------------- #
# DBC 预处理：修掉常见的矩阵问题（信号重叠 = 别名、信号超出报文长度）
# --------------------------------------------------------------------------- #
def _bit_start(sig) -> int:
    if sig.byte_order == "big_endian":
        return (8 * (sig.start // 8)) + (7 - (sig.start % 8))
    return sig.start


def prepare_message(msg, log=lambda *a: None):
    """返回 (可解码的 message, 别名映射)；别名 = 完全同位置的重定义信号。"""
    import copy as _copy

    if msg.is_multiplexed():
        return msg, {}

    sigs = list(msg.signals)
    spans = [(_bit_start(s), _bit_start(s) + s.length, s) for s in sigs]
    spans.sort(key=lambda x: (x[0], -x[1]))

    limit = msg.length * 8
    keep, alias = [], {}
    last_end = -1
    last_span = None
    for st, en, s in spans:
        if st >= last_end:                       # 不重叠
            keep.append(s)
            last_end = en
            last_span = (st, en)
        elif last_span == (st, en):              # 完全同位 -> 别名
            alias[s.name] = keep[-1].name
        # 部分重叠的畸形信号：直接丢弃

    changed = len(keep) != len(sigs) or any(en > limit for _, en, _ in spans)
    if not changed:
        return msg, {}

    need = max(limit, max(en for _, en, _ in spans) if spans else limit)
    msg = _copy.deepcopy(msg)
    msg._signals = keep
    msg.length = max(msg.length, math.ceil(need / 8))
    msg.refresh()
    try:
        f = msg._codecs["formats"]
        f.big_endian.unpack(bytes(msg.length))
        f.little_endian.unpack(bytes(msg.length)[::-1])
    except Exception:                           # 还是建不出来就整体放弃
        return None, {}
    if alias:
        log(f"      {msg.name}: {len(alias)} 个别名信号 / 丢弃 {len(sigs) - len(keep)} 个重叠信号")
    return msg, alias


# --------------------------------------------------------------------------- #
# 输出与合并
# --------------------------------------------------------------------------- #
def pick_dbc(asc: str, explicit: "str | None", no_dbc: bool) -> "str | None":
    """显式指定 > 同名 .dbc > 目录里唯一的 .dbc。"""
    if no_dbc:
        return None
    if explicit:
        return explicit
    stem = os.path.splitext(asc)[0]
    for c in (stem + ".dbc", stem + ".DBC"):
        if os.path.isfile(c):
            return c
    try:
        d = os.path.dirname(asc) or "."
        cands = [os.path.join(d, f) for f in os.listdir(d) if f.lower().endswith(".dbc")]
    except OSError:
        return None
    return cands[0] if len(cands) == 1 else None


def merge_payloads(payloads: list, files: list) -> dict:
    """多份日志 -> 一个 payload：同一信号只出现一次，多个文件的 series 由前端叠加。"""
    signals = []
    for fi, (f, p) in enumerate(zip(files, payloads)):
        stem = os.path.splitext(os.path.basename(f))[0]
        t0 = p["meta"]["t0"]
        for s in p["signals"]:
            s2 = dict(s)
            s2["key"] = f"{stem}|{s['key']}"
            s2["file"] = stem
            s2["fi"] = fi
            s2["t"] = [round(x - t0, 6) for x in s["t"]]   # 各自从 0 开始
            signals.append(s2)
    signals.sort(key=lambda x: (x["msg"], x["label"], x["fi"]))

    msgs: dict = {}
    for p in payloads:
        for m in p["messages"]:
            k = (m["id"], m["ch"])
            if k in msgs:
                msgs[k]["n"] += m["n"]
            else:
                msgs[k] = dict(m)

    return {
        "meta": {
            "asc": f"{len(payloads)} 个日志合并",
            "dbc": payloads[0]["meta"]["dbc"],
            "files": [os.path.splitext(os.path.basename(f))[0] for f in files],
            "frames": sum(p["meta"]["frames"] for p in payloads),
            "signals": len(signals),
            "resets": sum(p["meta"]["resets"] for p in payloads),
            "t0": 0.0,
            "t1": round(max(p["meta"]["t1"] - p["meta"]["t0"] for p in payloads), 6),
            "channels": sorted({c for p in payloads for c in p["meta"]["channels"]}),
            "decode_err": sum(p["meta"]["decode_err"] for p in payloads),
            "err_sample": next((p["meta"]["err_sample"] for p in payloads if p["meta"]["err_sample"]), ""),
            "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
            "elapsed": round(sum(p["meta"]["elapsed"] for p in payloads), 2),
        },
        "messages": list(msgs.values()),
        "signals": signals,
    }


def write_html(payload: dict, out: str, log) -> str:
    out = os.path.abspath(out)
    d = os.path.dirname(out)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(TEMPLATE, "r", encoding="utf-8") as f:
        tpl = f.read()
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = tpl.replace("__TITLE__", os.path.basename(out)).replace("__PAYLOAD__", blob)
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    size_mb = os.path.getsize(out) / 1024 / 1024
    m = payload["meta"]
    log(f"[4/4] 输出: {out}  ({size_mb:.2f} MB)")
    log(f"      信号 {m['signals']} 条 | 帧 {m['frames']} | 时间 {m['t0']:.3f}~{m['t1']:.3f}s"
        f" | 重置 {m['resets']} | 解码失败 {m['decode_err']}")
    return out


def _open(path: str) -> None:
    try:
        os.startfile(path)  # type: ignore[attr-defined]
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# 单个文件的完整流程
# --------------------------------------------------------------------------- #
def process_one(asc: str, dbc: "str | None", args, log, collect: "list | None" = None) -> int:
    """解析一个文件；collect 不为 None 时把 payload 追加进去、不写 HTML。"""
    t_start = time.time()
    jump = f"[{os.path.basename(asc)}] " if collect is not None else ""

    want_ids = None
    if args.ids:
        want_ids = {int(x.strip(), 16) & 0x1FFFFFFF for x in args.ids.split(",") if x.strip()}

    layout = detect_layout(asc)
    base = detect_base(asc)
    rebase = not args.no_rebase
    stat = {"resets": 0}
    log(f"{jump}[1/4] 解析 {os.path.basename(asc)}  (FD 字段顺序: {layout}, 进制: {base}, rebase={rebase})")

    db = None
    sig_meta: dict = {}
    by_id: dict = {}
    if dbc:
        if cantools is None:
            sys.exit("读取 DBC 需要: pip install cantools")
        db = cantools.database.load_file(dbc, strict=False)
        for m in db.messages:
            for s in m.signals:
                key = f"{m.name}.{s.name}"
                if args.signal and not re.search(args.signal, key, re.I):
                    continue
                sig_meta[key] = {
                    "label": key,
                    "unit": s.unit or "",
                    "msg": m.name,
                    "id": m.frame_id & 0x1FFFFFFF,
                    "ext": bool(getattr(m, "is_extended_frame", False)),
                    "comment": (s.comment or "").strip(),
                }
        for m in db.messages:
            cm, alias = prepare_message(m, log)
            if cm is None:
                log(f"      ! {m.name} (0x{m.frame_id:X}) 信号定义冲突，跳过")
                continue
            by_id.setdefault(cm.frame_id & 0x1FFFFFFF, []).append((cm, alias))
        log(f"      DBC: {os.path.basename(dbc)}  报文 {len(db.messages)}  信号 {len(sig_meta)}")

    # ---- 无 DBC: 先统计 ID 频次，取前 N 个 ----
    picked_ids = None
    if db is None:
        log("[2/4] 统计报文 ID ...")
        counter: dict = {}
        for msg in iter_messages(asc, layout, rebase, {}, base):
            if msg.is_error_frame or msg.is_remote_frame:
                continue
            k = msg.arbitration_id & 0x1FFFFFFF
            counter[(k, msg.channel)] = counter.get((k, msg.channel), 0) + 1
        order = sorted(counter.items(), key=lambda kv: -kv[1])
        if want_ids:
            order = [kv for kv in order if kv[0][0] in want_ids]
        picked_ids = set()
        for (k, ch), n in order:
            if len(picked_ids) >= args.max_signals:
                break
            picked_ids.add(k)
    else:
        log("[2/4] 解码报文 ...")

    # ---- 采集 ----
    series: "OrderedDict[str, dict]" = OrderedDict()
    msg_count: dict = {}
    msg_first: dict = {}
    msg_last: dict = {}
    decode_err = 0
    err_sample = ""
    frames = 0
    chan_seen = set()

    def ensure(key, meta):
        s = series.get(key)
        if s is None:
            s = {"meta": meta, "t": [], "v": []}
            series[key] = s
        return s

    for msg in iter_messages(asc, layout, rebase, stat, base):
        frames += 1
        t = msg.timestamp
        if args.t_from is not None and t < args.t_from:
            continue
        if args.t_to is not None and t > args.t_to:
            continue
        if msg.is_error_frame or msg.is_remote_frame:
            continue
        cid = msg.arbitration_id & 0x1FFFFFFF
        chan_seen.add(msg.channel)
        if want_ids and cid not in want_ids:
            continue

        if db is None:
            if cid not in picked_ids:
                continue
            mk = (cid, msg.channel)
            msg_count[mk] = msg_count.get(mk, 0) + 1
            msg_first.setdefault(mk, t)
            msg_last[mk] = t
            data = msg.data
            for i in range(min(args.bytes, len(data))):
                key = f"0x{cid:X}.b{i}"
                s = ensure(key, {"label": key, "unit": "", "msg": f"0x{cid:X}",
                                 "id": cid, "ext": msg.is_extended_id, "comment": ""})
                s["t"].append(t)
                s["v"].append(data[i])
            continue

        cands = by_id.get(cid)
        if not cands:
            continue
        cm, alias = cands[0]
        if len(cands) > 1:
            for c2, a2 in cands:
                if bool(getattr(c2, "is_extended_frame", False)) == bool(msg.is_extended_id):
                    cm, alias = c2, a2
                    break
        mk = (cid, msg.channel)
        msg_count[mk] = msg_count.get(mk, 0) + 1
        msg_first.setdefault(mk, t)
        msg_last[mk] = t
        try:
            dec = cm.decode(msg.data, decode_choices=False, scaling=True,
                            allow_truncated=True, allow_excess=True)
        except TypeError:
            dec = cm.decode(msg.data, decode_choices=False, scaling=True)
        except Exception as e:  # noqa: BLE001
            decode_err += 1
            if not err_sample:
                err_sample = f"{cm.name}: {e}"
            continue
        if alias:
            for a, src in alias.items():
                if src in dec:
                    dec[a] = dec[src]
        for sname, val in dec.items():
            key = f"{cm.name}.{sname}"
            meta = sig_meta.get(key)
            if meta is None or val is None or isinstance(val, (bytes, bytearray)):
                continue
            if isinstance(val, bool):
                val = int(val)
            s = ensure(key, meta)
            s["t"].append(t)
            s["v"].append(val)

    if not series:
        sys.exit("没有采集到任何信号：检查 --ids / --signal 过滤条件，或 DBC 是否匹配该报文。")

    log(f"[3/4] 采集完成: {frames} 帧, {len(series)} 条信号, 时间戳重置 {stat['resets']} 次")

    # ---- 组装 payload ----
    signals = []
    for key, s in series.items():
        ts, vs = s["t"], s["v"]
        if not ts:
            continue
        n_raw = len(ts)
        ts, vs = downsample(ts, vs, args.max_points)
        vals = [v for v in vs if v is not None]
        if not vals:
            continue
        signals.append({
            "key": key,
            "label": s["meta"]["label"],
            "unit": s["meta"]["unit"],
            "msg": s["meta"]["msg"],
            "id": f"0x{s['meta']['id']:X}",
            "ext": s["meta"]["ext"],
            "comment": s["meta"].get("comment", ""),
            "n": n_raw,
            "min": min(vals),
            "max": max(vals),
            "std": round(pstdev(vals), 6),
            "t": [round(x, 6) for x in ts],
            "v": [round(x, 6) if isinstance(x, float) else x for x in vs],
        })
    signals.sort(key=lambda x: (x["msg"], x["label"]))

    messages = []
    for (cid, ch), n in sorted(msg_count.items(), key=lambda kv: -kv[1]):
        f0, f1 = msg_first.get((cid, ch), 0.0), msg_last.get((cid, ch), 0.0)
        dur = max(f1 - f0, 1e-9)
        messages.append({
            "id": f"0x{cid:X}",
            "ch": ch,
            "n": n,
            "period": round(dur / max(n - 1, 1) * 1000, 3),
            "ext": cid > 0x7FF,
        })

    payload = {
        "meta": {
            "asc": os.path.basename(asc),
            "dbc": os.path.basename(dbc) if dbc else "",
            "frames": frames,
            "signals": len(signals),
            "resets": stat["resets"],
            "t0": round(min(s["t"][0] for s in signals), 6),
            "t1": round(max(s["t"][-1] for s in signals), 6),
            "channels": sorted(chan_seen),
            "decode_err": decode_err,
            "err_sample": err_sample,
            "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
            "elapsed": round(time.time() - t_start, 2),
        },
        "messages": messages,
        "signals": signals,
    }

    if collect is not None:
        collect.append(payload)
        return 0

    out = args.out or (os.path.splitext(asc)[0] + ".html")
    out = write_html(payload, out, log)
    if args.open:
        _open(out)
    return 0


# --------------------------------------------------------------------------- #
# 单文件入口（保持纯单文件的语义）
# --------------------------------------------------------------------------- #
def single_main(argv=None, collect=None) -> int:
    ap = argparse.ArgumentParser(prog="asc2html",
                                 description="ASC/BLF(+DBC) -> 离线交互式 HTML 曲线")
    ap.add_argument("asc", nargs="+", help="ASC/BLF 日志（此处只应有一个）")
    ap.add_argument("--dbc", dest="dbc_opt", default=None, help="DBC/ARXML 矩阵")
    ap.add_argument("--merge", action="store_true", help="（由上层处理）")
    ap.add_argument("-o", "--out", default=None, help="输出 HTML")
    ap.add_argument("--ids", default=None, help="只处理指定 ID，逗号分隔，如 10C,32B")
    ap.add_argument("--signal", default=None, help="只输出匹配该正则的信号")
    ap.add_argument("--max-points", type=int, default=6000, help="降采样桶大小（保留极值）")
    ap.add_argument("--max-signals", type=int, default=8, help="无 DBC 时最多输出 ID 数")
    ap.add_argument("--bytes", type=int, default=8, help="无 DBC 时每 ID 取前 N 字节")
    ap.add_argument("--from", dest="t_from", type=float, default=None)
    ap.add_argument("--to", dest="t_to", type=float, default=None)
    ap.add_argument("--no-rebase", action="store_true", help="不修正时间戳重置")
    ap.add_argument("--no-dbc", action="store_true", help="忽略 DBC，按原始字节显示")
    ap.add_argument("--open", action="store_true", help="生成后打开浏览器")
    ap.add_argument("-q", "--quiet", action="store_true")
    ap.add_argument("-V", "--version", action="version", version=f"asc2html {VERSION}")
    args = ap.parse_args(argv)

    if len(args.asc) != 1:
        sys.exit("内部错误：single_main 只接收一个文件")
    asc = os.path.abspath(args.asc[0])
    if not os.path.isfile(asc):
        sys.exit(f"找不到文件: {asc}")

    dbc = args.dbc_opt
    if args.no_dbc:
        dbc = None
    elif dbc is None:
        dbc = pick_dbc(asc, None, False)
    if dbc and not os.path.isfile(dbc):
        sys.exit(f"找不到 DBC: {dbc}")

    log = (lambda *a: None) if args.quiet else (lambda *a: print(*a))
    return process_one(asc, dbc, args, log, collect)


# --------------------------------------------------------------------------- #
# 多文件入口
# --------------------------------------------------------------------------- #
_VALUE_OPTS = {"--dbc", "-o", "--out", "--ids", "--signal", "--max-points",
               "--max-signals", "--bytes", "--from", "--to"}


def split_argv(argv: list):
    """拆出 文件们 / 矩阵 / 输出 / 合并 / 打开，其余选项原样透传。"""
    files: list = []
    extra: list = []
    dbc = out = None
    merge = do_open = False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--merge":
            merge = True
            i += 1
            continue
        if a == "--open":
            do_open = True
            i += 1
            continue
        if a in _VALUE_OPTS:
            if i + 1 >= len(argv):
                sys.exit(f"参数 {a} 缺少值")
            v = argv[i + 1]
            if a == "--dbc":
                dbc = v
            elif a in ("-o", "--out"):
                out = v
            else:
                extra += [a, v]
            i += 2
            continue
        if a.startswith("--") and "=" in a:
            k, v = a.split("=", 1)
            if k == "--dbc":
                dbc = v
            elif k == "--out":
                out = v
            elif k in _VALUE_OPTS:
                extra += [k, v]
            else:
                extra.append(a)
            i += 1
            continue
        if a.startswith("-") and a != "-":
            extra.append(a)
            i += 1
            continue
        if a.lower().endswith((".dbc", ".arxml")):
            if dbc:
                sys.exit("一次只能用一个 DBC")
            dbc = a
        else:
            files.append(a)
        i += 1
    return files, dbc, out, merge, do_open, extra


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if any(a in ("-h", "--help", "-V", "--version") for a in argv):
        return single_main(argv)

    files, dbc, out_arg, merge, do_open, extra = split_argv(argv)
    if not files:
        sys.exit("没有指定 ASC/BLF 文件。用法: asc2html 日志.asc [矩阵.dbc] [更多日志...]")
    files = [os.path.abspath(f) for f in files]
    for f in files:
        if not os.path.isfile(f):
            sys.exit(f"找不到文件: {f}")
    if dbc:
        dbc = os.path.abspath(dbc)
        if not os.path.isfile(dbc):
            sys.exit(f"找不到 DBC: {dbc}")

    def run(f: str, out: "str | None" = None, collect: "list | None" = None) -> int:
        a = [f] + extra
        if dbc:
            a += ["--dbc", dbc]
        if out:
            a += ["-o", out]
        return single_main(a, collect)

    # ---- 合并成一张对比图 ----
    if merge and len(files) > 1:
        payloads: list = []
        for i, f in enumerate(files, 1):
            print(f"\n===== [{i}/{len(files)}] {os.path.basename(f)} =====")
            run(f, None, payloads)
        payload = merge_payloads(payloads, files)
        out = out_arg or (os.path.splitext(files[0])[0] + "_合并.html")
        out = write_html(payload, out, print)
        print(f"（{len(files)} 个日志已合并，时间轴各自从 0 开始）")
        if do_open:
            _open(out)
        return 0

    # ---- 单个文件 ----
    if len(files) == 1:
        if out_arg and os.path.isdir(out_arg):
            out_arg = os.path.join(out_arg, os.path.splitext(os.path.basename(files[0]))[0] + ".html")
        a = [files[0]] + extra + (["--dbc", dbc] if dbc else [])
        if out_arg:
            a += ["-o", out_arg]
        if do_open:
            a.append("--open")
        return single_main(a)

    # ---- 多个文件逐个生成 ----
    if out_arg and out_arg.lower().endswith(".html"):
        sys.exit("多个 ASC 时 -o 请给“输出目录”；想合成一张图请加 --merge")
    outs: list = []
    failed = 0
    for i, f in enumerate(files, 1):
        print(f"\n===== [{i}/{len(files)}] {os.path.basename(f)} =====")
        out = (os.path.join(out_arg, os.path.splitext(os.path.basename(f))[0] + ".html")
               if out_arg else os.path.splitext(f)[0] + ".html")
        try:
            run(f, out)
            outs.append(out)
        except SystemExit as e:
            failed += 1
            print(f"[跳过] {os.path.basename(f)}: {e}")
    print(f"\n完成：成功 {len(outs)} 个，失败 {failed} 个")
    for o in outs:
        print("  " + o)
    if do_open and len(outs) == 1:
        _open(outs[0])
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
