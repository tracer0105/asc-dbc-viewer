#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""app.py - 打包入口。

有参数（把 asc 拖到 exe 上 / 命令行）→ 直接生成，控制台里能看到进度；
无参数（双击）→ 隐藏控制台并打开图形界面。
"""
from __future__ import annotations

import os
import sys
import traceback

FROZEN = bool(getattr(sys, "frozen", False))


def _alert_err(text: str) -> None:
    try:
        from tkinter import Tk, messagebox
        r = Tk()
        r.withdraw()
        messagebox.showerror("生成失败", text[:3000])
        r.destroy()
    except Exception:
        pass


def _pause_if_tty() -> None:
    try:
        if sys.stdin and sys.stdin.isatty():
            input("\n按回车退出…")
    except Exception:
        pass


def hide_console() -> None:
    """双击启动 GUI 时把伴随的控制台窗口藏起来。"""
    if not FROZEN:
        return
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)   # SW_HIDE
    except Exception:
        pass


def cli(args: list[str]) -> int:
    import asc2html

    if "--open" not in args:
        args = args + ["--open"]

    rc = 1
    try:
        rc = asc2html.main(args)
    except SystemExit as e:
        rc = 0 if e.code in (0, None) else (e.code if isinstance(e.code, int) else 1)
    except Exception:
        traceback.print_exc()
        rc = 1

    if rc != 0:
        print("\n[失败] 详细信息见上方输出。")
        if FROZEN and not sys.stdout.isatty():
            _alert_err("生成失败，详见控制台输出。")
        _pause_if_tty()
    return rc


def main() -> int:
    args = [a for a in sys.argv[1:] if a]
    if args:
        return cli(args)

    if FROZEN:
        hide_console()
        import gui
        return gui.run()

    try:
        import gui
        return gui.run()
    except Exception:
        print("用法: python app.py 日志.asc [矩阵.dbc] [-o 输出.html] [--open]")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
