# -*- coding: utf-8 -*-
"""临时脚本：验证框选识别、小帧自动放大、精度档位"""
import sys
import time
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import app as ui_app
import camera as cam_mod
from fake_camera import FakeCapture


class Ev:
    def __init__(self, x, y):
        self.x, self.y = x, y


def frame_to_canvas(win, fx, fy):
    ox, oy, ratio, _, _ = win._view
    return fx * ratio + ox, fy * ratio + oy


root = tk.Tk()
ui_app.enable_dpi_awareness()
app = ui_app.OcrApp(root, dnd_enabled=False)
win = cam_mod.CameraWindow(root, app, capture_factory=lambda idx: FakeCapture())
win.open_camera()

log = {}


def stage1_drag_roi():
    """模拟鼠标拖拽，框住报销单文字区域"""
    if not win._view:
        root.after(300, stage1_drag_roi)
        return
    x0, y0 = frame_to_canvas(win, 360, 170)
    x1, y1 = frame_to_canvas(win, 920, 560)
    win._on_press(Ev(x0, y0))
    win._on_drag(Ev((x0 + x1) / 2, (y0 + y1) / 2))
    win._on_drag(Ev(x1, y1))
    win._on_release(Ev(x1, y1))
    log["t_drag"] = time.time()
    root.after(4500, stage2_check_roi)


def stage2_check_roi():
    res = win.result or {}
    texts = res.get("texts") or []
    rect = win.roi_rect
    inside = all(
        rect[0] - 2 <= p[0] <= rect[2] + 2 and rect[1] - 2 <= p[1] <= rect[3] + 2
        for box in (res.get("boxes") or []) for p in box
    ) if rect and res.get("boxes") else False
    print("== 框选识别 ==")
    print("框选区域:", None if not rect else [round(v) for v in rect])
    print("识别行数:", len(texts), "| 框都在区域内:", inside)
    print("文本:", texts[:3])
    print("状态栏:", win.status.cget("text"))

    # 清除框选
    win._clear_roi()
    print("清除后 roi_rect:", win.roi_rect, "| 按钮:", win.clear_roi_btn["state"])
    root.after(400, stage3_small_frame)


def stage3_small_frame():
    """切换到 640x360 低分辨率虚拟摄像头，验证自动放大"""
    win.stop_camera()
    win.capture_factory = lambda idx: FakeCapture(canvas=(640, 360))
    win.open_camera()
    root.after(6000, stage4_check_small)


def stage4_check_small():
    res = win.result or {}
    texts = res.get("texts") or []
    print("== 低分辨率 640x360（自动放大后识别）==")
    print("识别行数:", len(texts))
    print("文本:", texts[:3])
    print("状态栏:", win.status.cget("text"))

    # 精度档位
    win.profile_var.set("精度优先")
    root.after(5000, stage5_check_profile)


def stage5_check_profile():
    res = win.result or {}
    print("== 精度优先档位 ==")
    print("识别行数:", len(res.get("texts") or []), "| 耗时:", round(res.get("cost", 0), 2))
    root.after(200, finish)


def finish():
    win.close()
    root.destroy()


start = time.time()
root.after(4000, stage1_drag_roi)
root.mainloop()
print("done")
