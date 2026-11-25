#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gui.py - ASC → 交互式曲线的图形界面（tkinter，随 exe 一起打包）。"""
from __future__ import annotations

import os
import queue
import sys
import threading
import traceback

import tkinter as tk
from tkinter import filedialog, messagebox, ttk


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.q: "queue.Queue" = queue.Queue()
        self.busy = False

        root.title("ASC → 交互式曲线")
        root.geometry("820x600")
        root.minsize(700, 480)

        pad = {"padx": 8, "pady": 4}
        frm = ttk.Frame(root)
        frm.pack(fill="both", expand=True, padx=10, pady=8)
        frm.columnconfigure(1, weight=1)

        self.asc = tk.StringVar()
        self.dbc = tk.StringVar()
        self.out = tk.StringVar()
        self.files: list[str] = []
        self._asc_display = ""
        self.merge = tk.BooleanVar(value=False)

        r = 0
        ttk.Label(frm, text="ASC 日志（必填）").grid(row=r, column=0, sticky="w", **pad)
        ttk.Entry(frm, textvariable=self.asc).grid(row=r, column=1, sticky="ew", **pad)
        ttk.Button(frm, text="浏览…", command=self.pick_asc, width=8).grid(row=r, column=2, **pad)

        r += 1
        ttk.Label(frm, text="DBC 矩阵（可选）").grid(row=r, column=0, sticky="w", **pad)
        ttk.Entry(frm, textvariable=self.dbc).grid(row=r, column=1, sticky="ew", **pad)
        bf = ttk.Frame(frm)
        bf.grid(row=r, column=2, **pad)
        ttk.Button(bf, text="浏览…", command=self.pick_dbc, width=6).pack(side="left")
        ttk.Button(bf, text="清空", command=lambda: self.dbc.set(""), width=4).pack(side="left", padx=(4, 0))

        r += 1
        ttk.Label(frm, text="输出 HTML").grid(row=r, column=0, sticky="w", **pad)
        ttk.Entry(frm, textvariable=self.out).grid(row=r, column=1, sticky="ew", **pad)
        ttk.Button(frm, text="浏览…", command=self.pick_out, width=8).grid(row=r, column=2, **pad)

        # 选项
        r += 1
        opt = ttk.LabelFrame(frm, text="选项")
        opt.grid(row=r, column=0, columnspan=3, sticky="ew", **pad)
        self.points = tk.StringVar(value="6000")
        self.ids = tk.StringVar()
        self.sig = tk.StringVar()
        self.auto_open = tk.BooleanVar(value=True)
        ttk.Label(opt, text="采样点数（约）").grid(row=0, column=0, sticky="w", padx=8, pady=6)
        ttk.Entry(opt, textvariable=self.points, width=8).grid(row=0, column=1, sticky="w")
        ttk.Label(opt, text="只处理 ID（如 10C,32B）").grid(row=0, column=2, sticky="w", padx=(20, 4))
        ttk.Entry(opt, textvariable=self.ids, width=16).grid(row=0, column=3, sticky="w")
        ttk.Label(opt, text="信号过滤（正则）").grid(row=0, column=4, sticky="w", padx=(20, 4))
        ttk.Entry(opt, textvariable=self.sig, width=16).grid(row=0, column=5, sticky="w")
        ttk.Checkbutton(opt, text="生成后打开浏览器", variable=self.auto_open).grid(
            row=1, column=0, columnspan=2, sticky="w", padx=8, pady=(0, 8))
        ttk.Checkbutton(opt, text="多个日志合并为一张图", variable=self.merge).grid(
            row=1, column=2, columnspan=2, sticky="w", padx=(20, 4), pady=(0, 8))

        # 操作
        r += 1
        bar = ttk.Frame(frm)
        bar.grid(row=r, column=0, columnspan=3, sticky="ew", **pad)
        self.btn = ttk.Button(bar, text="生成", command=self.start, width=12)
        self.btn.pack(side="left")
        self.state = ttk.Label(bar, text="就绪。也可以直接把 asc 文件拖到本程序图标上。", foreground="#666")
        self.state.pack(side="left", padx=12)

        # 日志
        r += 1
        frm.rowconfigure(r, weight=1)
        self.log = tk.Text(frm, height=12, wrap="none", background="#1b1d21", foreground="#d7dae0",
                           insertbackground="#d7dae0", font=("Consolas", 9))
        self.log.grid(row=r, column=0, columnspan=3, sticky="nsew", **pad)
        sb = ttk.Scrollbar(frm, command=self.log.yview)
        sb.grid(row=r, column=3, sticky="ns")
        self.log.configure(yscrollcommand=sb.set)

        root.after(200, self.drain)

    # ---------------- 文件选择 ----------------
    def pick_asc(self):
        ps = filedialog.askopenfilenames(
            title="选择 ASC / BLF 日志（可多选）",
            filetypes=[("CAN 日志", "*.asc *.blf *.ASC *.BLF"), ("所有文件", "*.*")])
        if not ps:
            return
        self.files = list(ps)
        self._asc_display = ps[0] if len(ps) == 1 else f"{ps[0]}   等 {len(ps)} 个文件"
        self.asc.set(self._asc_display)
        self.out.set(os.path.splitext(ps[0])[0] + ".html" if len(ps) == 1 else "")
        if not self.dbc.get():
            d = self.guess_dbc(ps[0])
            if d:
                self.dbc.set(d)

    def pick_dbc(self):
        p = filedialog.askopenfilename(
            title="选择 DBC", filetypes=[("DBC", "*.dbc *.DBC *.arxml"), ("所有文件", "*.*")])
        if p:
            self.dbc.set(p)

    def pick_out(self):
        p = filedialog.asksaveasfilename(title="保存为", defaultextension=".html",
                                         filetypes=[("HTML", "*.html")])
        if p:
            self.out.set(p)

    @staticmethod
    def guess_dbc(asc_path: str) -> str:
        d = os.path.dirname(os.path.abspath(asc_path))
        stem = os.path.splitext(os.path.basename(asc_path))[0]
        for c in (stem + ".dbc", stem + ".DBC"):
            if os.path.isfile(os.path.join(d, c)):
                return os.path.join(d, c)
        try:
            cands = [f for f in os.listdir(d) if f.lower().endswith(".dbc")]
        except OSError:
            return ""
        return os.path.join(d, cands[0]) if len(cands) == 1 else ""

    # ---------------- 运行 ----------------
    def start(self):
        if self.busy:
            return
        txt = self.asc.get().strip().strip('"')
        files = list(self.files) if (self.files and txt == self._asc_display) else ([txt] if txt else [])
        files = [f for f in files if os.path.isfile(f)]
        if not files:
            messagebox.showwarning("缺少输入", "请选择存在的 ASC / BLF 文件（可多选）。")
            return
        dbc = self.dbc.get().strip().strip('"')
        if dbc and not os.path.isfile(dbc):
            messagebox.showwarning("DBC 无效", "DBC 文件不存在，或点“清空”后按原始字节看图。")
            return
        out = self.out.get().strip().strip('"')
        multi = len(files) > 1

        args = list(files)
        if dbc:
            args.append(dbc)
        if out:
            args += ["-o", out]
        if self.points.get().strip().isdigit():
            args += ["--max-points", self.points.get().strip()]
        if self.ids.get().strip():
            args += ["--ids", self.ids.get().strip()]
        if self.sig.get().strip():
            args += ["--signal", self.sig.get().strip()]
        if multi and self.merge.get():
            args.append("--merge")
        if self.auto_open.get() and (not multi or self.merge.get()):
            args.append("--open")

        self.log.delete("1.0", "end")
        self.busy = True
        self.btn.state(["disabled"])
        self.state.configure(text="正在解析，请稍候…（大文件约需 40 秒）", foreground="#b8860b")
        threading.Thread(target=self.run_job, args=(args, out), daemon=True).start()

    def run_job(self, args, out):
        import asc2html

        class Writer:
            def __init__(self, q):
                self.q = q

            def write(self, s):
                if s:
                    self.q.put(("log", s))

            def flush(self):
                pass

        old = sys.stdout
        rc = 1
        try:
            sys.stdout = Writer(self.q)
            rc = asc2html.main(args)
        except SystemExit as e:
            rc = 0 if e.code in (0, None) else (e.code if isinstance(e.code, int) else 1)
        except Exception:
            self.q.put(("log", traceback.format_exc()))
            rc = 1
        finally:
            sys.stdout = old
            self.q.put(("done", rc, out))

    def drain(self):
        try:
            while True:
                msg = self.q.get_nowait()
                if msg[0] == "log":
                    self.log.insert("end", msg[1])
                    self.log.see("end")
                elif msg[0] == "done":
                    rc, out = msg[1], msg[2]
                    self.busy = False
                    self.btn.state(["!disabled"])
                    if rc == 0:
                        self.state.configure(text="完成：" + (out or "已生成，见下方日志"), foreground="#2e7d32")
                    else:
                        self.state.configure(text="失败，请查看下方日志", foreground="#c62828")
        except queue.Empty:
            pass
        self.root.after(200, self.drain)


def run() -> int:
    root = tk.Tk()
    try:
        root.call("tk", "scaling", 1.25)
    except tk.TclError:
        pass
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
