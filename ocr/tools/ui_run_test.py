# -*- coding: utf-8 -*-
"""临时脚本：通过界面跑完整流程，验证线程/进度/产物"""
import shutil
import sys
import tkinter as tk
from pathlib import Path

from PIL import ImageGrab

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app as ui_app
from tkinter import messagebox

messagebox.askyesno = lambda *a, **k: False   # 测试时不弹窗

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output_gui"
if OUT.exists():
    shutil.rmtree(OUT)

root = tk.Tk()
ui_app.enable_dpi_awareness()
ui = ui_app.OcrApp(root, dnd_enabled=False)
ui._add_paths([str(ROOT / "samples")])
ui.out_var.set(str(OUT))
root.update()
ui.start()

shots = {"mid": False, "end": False}


def grab(name):
    root.update_idletasks()
    x, y = root.winfo_rootx(), root.winfo_rooty()
    ImageGrab.grab(bbox=(x, y, x + root.winfo_width(), y + root.winfo_height())).save(name)


idle_ticks = {"n": 0}


def tick():
    if not ui.is_running():
        idle_ticks["n"] += 1
    else:
        idle_ticks["n"] = 0
    if idle_ticks["n"] >= 4 and not shots["end"]:
        shots["end"] = True
        root.update()
        grab("ui_running.png")
        print("STATUS:", ui.status_label.cget("text"))
        print("LOG:", ui.log_text.get("1.0", "end").strip().splitlines()[-3:])
        root.after(300, root.destroy)
        return
    if ui.is_running() and not shots["mid"] and ui.done_pages >= 1:
        shots["mid"] = True
        grab("ui_mid.png")
    root.after(250, tick)


root.after(400, tick)
root.mainloop()
print("done")
