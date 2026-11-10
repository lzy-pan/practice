# -*- coding: utf-8 -*-
"""临时脚本：用虚拟摄像头跑通「采集→识别→框选→叠加→取字→拍照」并截图"""
import sys
import time
import tkinter as tk
from pathlib import Path

from PIL import ImageGrab

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
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


def main():
    root = tk.Tk()
    ui_app.enable_dpi_awareness()
    app = ui_app.OcrApp(root, dnd_enabled=False)
    app.out_var.set(str(ROOT / "output_cam"))

    win = cam_mod.CameraWindow(
        root, app,
        capture_factory=lambda idx: FakeCapture(switch_every=10**9),  # 固定画面
    )
    win.open_camera()
    win.geometry("1100x720+60+40")
    root.update()

    state = {"phase": 0, "t0": time.time(), "deadline": 0}

    def tick():
        t = time.time() - state["t0"]

        if state["phase"] == 0 and t >= 5 and win._view:
            # 模拟拖拽框选，展示蓝框 + 区域识别
            x0, y0 = frame_to_canvas(win, 370, 180)
            x1, y1 = frame_to_canvas(win, 910, 550)
            win._on_press(Ev(x0, y0))
            win._on_drag(Ev(x1, y1))
            win._on_release(Ev(x1, y1))
            state["phase"] = 1
            state["deadline"] = time.time() + 5
        elif state["phase"] == 1 and time.time() >= state["deadline"]:
            win.update_idletasks()
            win.update()
            x, y = win.winfo_rootx(), win.winfo_rooty()
            ImageGrab.grab(bbox=(x, y, x + win.winfo_width(),
                                 y + win.winfo_height())).save("cam_preview.png")
            print("ROI:", None if not win.roi_rect else [round(v) for v in win.roi_rect])
            print("TEXT PANEL:", repr(win.text.get("1.0", "end").strip()[:150]))
            print("STATUS:", win.status.cget("text"))
            print("BOXES:", len(win.result["boxes"]) if win.result else 0)
            win.snapshot()
            print("SNAPSHOT:", win.status.cget("text"))
            print("FILES IN LIST:", [p.name for p in app.files])
            state["phase"] = 2
            root.after(300, finish)
            return

        root.after(200, tick)

    def finish():
        win.close()
        root.destroy()

    root.after(300, tick)
    root.mainloop()
    print("done")


if __name__ == "__main__":
    main()
