# -*- coding: utf-8 -*-
"""临时脚本：验证「只识别中央区域」时坐标是否偏移正确"""
import sys
import time
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import app as ui_app
import camera as cam_mod
from fake_camera import FakeCapture

root = tk.Tk()
ui_app.enable_dpi_awareness()
app = ui_app.OcrApp(root, dnd_enabled=False)
app.out_var.set(str(Path("output_cam").resolve()))
win = cam_mod.CameraWindow(root, app, capture_factory=lambda idx: FakeCapture())
win.open_camera()
win.roi_var.set(True)

info = {}


def check():
    res = win.result
    if not res or not res.get("boxes"):
        root.after(400, check)
        return
    fw, fh = 1280, 720
    lo_x, hi_x = fw * (1 - cam_mod.ROI_RATIO) / 2, fw * (1 + cam_mod.ROI_RATIO) / 2
    lo_y, hi_y = fh * (1 - cam_mod.ROI_RATIO) / 2, fh * (1 + cam_mod.ROI_RATIO) / 2
    ok = True
    for box in res["boxes"]:
        for x, y in box:
            if not (lo_x - 2 <= x <= hi_x + 2 and lo_y - 2 <= y <= hi_y + 2):
                ok = False
    print(f"ROI 区域 x[{lo_x:.0f},{hi_x:.0f}] y[{lo_y:.0f},{hi_y:.0f}]")
    print("框坐标全部落在中央区域内:", ok)
    print("示例框:", [[round(v) for v in p] for p in res["boxes"][0]] if res["boxes"] else None)
    print("识别行数:", len(res.get("texts") or []), "耗时:", round(res.get("cost", 0), 2))
    print("文本:", (res.get("texts") or [])[:3])
    root.after(200, root.destroy)


root.after(4500, check)
root.mainloop()
