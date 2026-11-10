# -*- coding: utf-8 -*-
"""临时脚本：启动界面、填入示例文件并截图，用于检查排版"""
import sys
import tkinter as tk
from pathlib import Path

from PIL import ImageGrab

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app as ui_app

try:
    from tkinterdnd2 import TkinterDnD
    root = TkinterDnD.Tk()
    dnd = True
except Exception:
    root = tk.Tk()
    dnd = False

ui_app.enable_dpi_awareness()
ui = ui_app.OcrApp(root, dnd_enabled=dnd)
ui._add_paths([str(Path("samples"))])
ui.out_var.set(str(Path("output").resolve()))
root.update_idletasks()
root.update()

root.lift()
root.attributes("-topmost", True)
root.update()


def capture():
    root.update_idletasks()
    x, y = root.winfo_rootx(), root.winfo_rooty()
    w, h = root.winfo_width(), root.winfo_height()
    img = ImageGrab.grab(bbox=(x, y, x + w, y + h))
    img.save("ui_preview.png")
    print("saved ui_preview.png", img.size)
    root.destroy()


root.after(1500, capture)
root.mainloop()
