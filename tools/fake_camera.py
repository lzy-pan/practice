# -*- coding: utf-8 -*-
"""虚拟摄像头：用真实文档画面模拟摄像头，供调试和自动化测试使用"""
import time
from pathlib import Path

import cv2
import numpy as np
import pymupdf
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent


def load_frames():
    frames = []

    doc = pymupdf.open(ROOT / "samples" / "采购合同_扫描件.pdf")
    pix = doc[0].get_pixmap(dpi=110)
    frames.append(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
    doc.close()

    frames.append(Image.open(ROOT / "samples" / "报销单_小图.png").convert("RGB"))
    return frames


class FakeCapture:
    """模拟 cv2.VideoCapture：每 40 帧切换一张画面，约 30fps"""

    def __init__(self, frames=None, switch_every=40, canvas=(1280, 720)):
        self.frames = frames or load_frames()
        self.switch_every = switch_every
        self.canvas_size = canvas
        self.n = 0

    def isOpened(self):
        return True

    def set(self, *args, **kwargs):
        return True

    def read(self):
        img = self.frames[(self.n // self.switch_every) % len(self.frames)].copy()
        self.n += 1
        cw, ch = self.canvas_size
        canvas = Image.new("RGB", (cw, ch), "white")
        img.thumbnail((cw - 80, ch - 60))
        canvas.paste(img, ((cw - img.width) // 2, (ch - img.height) // 2))
        frame = cv2.cvtColor(np.array(canvas), cv2.COLOR_RGB2BGR)
        time.sleep(0.03)
        return True, frame

    def release(self):
        pass
