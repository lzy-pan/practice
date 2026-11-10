# -*- coding: utf-8 -*-
"""
实时摄像头 OCR

独立窗口：预览 + 实时识别 + 检测框叠加 + 取字。
采集与识别都在后台线程，界面只负责渲染，避免卡顿。

依赖：opencv-python（pip install opencv-python）
"""
from __future__ import annotations

import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

from PIL import Image, ImageDraw, ImageTk

import core

BG = "#F5F7FA"
CARD = "#FFFFFF"
TEXT = "#1F2430"
MUTED = "#6B7280"
BORDER = "#E3E7EF"
ACCENT = "#2F6FED"

PREVIEW_INTERVAL_MS = 45          # 预览刷新间隔（约 22 fps）
OCR_MAX_WIDTH = 960               # 送去识别的帧最大宽度，超过则缩小
OCR_UPGRADE_MIN_SIDE = 720        # 帧短边低于该值时先放大再识别（摄像头原生分辨率低）
UPSCALE_MAX = 2.0                 # 放大上限
ROI_RATIO = 0.82                  # 「只识别中央区域」时保留的画面比例
ROI_DRAG_MIN = 30                 # 框选有效的最小像素（显示坐标）


class CameraWindow(tk.Toplevel):
    def __init__(self, master, app, capture_factory=None):
        super().__init__(master)
        self.app = app
        self.capture_factory = capture_factory

        self.title("实时摄像头识别")
        self.geometry("1100x720")
        self.minsize(880, 600)
        self.configure(bg=BG)

        self.cap = None
        self.running = False
        self.frame_lock = threading.Lock()
        self.latest_frame = None
        self.frame_seq = 0
        self.last_fps_ts = time.time()
        self.last_fps_frames = 0
        self.fps = 0.0

        self.result = None                 # {"boxes": [...], "texts": [...], "seq": n, "cost": s}
        self.result_stamp = 0.0
        self.applied_result_id = None
        self.photo = None
        self._preview_job = None

        # 框选区域（原始帧坐标）；None = 未框选
        self.roi_rect = None
        self._drag_start = None          # 画布坐标
        self._drag_rect = None           # 拖拽过程中的帧坐标
        self._view = None                # (ox, oy, ratio, fw, fh) 当前渲染几何

        self._build_style()
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self.close)
        try:
            self.transient(master)          # 作为主窗口的工具窗，保持在前面
        except Exception:
            pass
        self.after(120, self._bring_front)
        self.after(200, self._scan_cameras_async)

    def _bring_front(self):
        try:
            self.deiconify()
            self.lift()
            self.focus_force()
        except Exception:
            pass

    # ------------------------------------------------------------ 界面
    def _build_style(self):
        style = ttk.Style(self)
        style.configure("Cam.TLabel", background=BG, foreground=TEXT)
        style.configure("CamMuted.TLabel", background=BG, foreground=MUTED,
                        font=("Microsoft YaHei UI", 9))
        style.configure("CamSection.TLabel", background=CARD, foreground=TEXT,
                        font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("CamCard.TFrame", background=CARD)
        style.configure("Cam.TCheckbutton", background=BG, foreground=TEXT)
        style.map("Cam.TCheckbutton", background=[("active", BG)])

    def _build_ui(self):
        outer = ttk.Frame(self, padding=(14, 12))
        outer.pack(fill="both", expand=True)

        # ---- 顶部工具条
        bar = ttk.Frame(outer, style="CamCard.TFrame", padding=(14, 10))
        bar.pack(fill="x")

        ttk.Label(bar, text="摄像头", style="CamSection.TLabel").pack(side="left")
        self.cam_var = tk.StringVar(value="0")
        self.cam_box = ttk.Combobox(bar, textvariable=self.cam_var, width=4, state="readonly",
                                    values=["0", "1", "2", "3"])
        self.cam_box.pack(side="left", padx=(8, 6))

        self.open_btn = ttk.Button(bar, text="打开摄像头", command=self.open_camera)
        self.open_btn.pack(side="left")
        self.close_btn = ttk.Button(bar, text="关闭", command=self.stop_camera, state="disabled")
        self.close_btn.pack(side="left", padx=(6, 0))

        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=12)

        self.live_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="实时识别", variable=self.live_var,
                        style="Cam.TCheckbutton", command=self._on_live_toggle).pack(side="left")

        ttk.Label(bar, text="间隔", style="Cam.TLabel").pack(side="left", padx=(12, 4))
        self.interval_var = tk.StringVar(value="0.5 秒")
        ttk.Combobox(bar, textvariable=self.interval_var, width=7, state="readonly",
                     values=["0.3 秒", "0.5 秒", "1 秒", "2 秒"]).pack(side="left")

        self.roi_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="只识别中央区域", variable=self.roi_var,
                        style="Cam.TCheckbutton").pack(side="left", padx=(12, 0))

        ttk.Label(bar, text="档位", style="Cam.TLabel").pack(side="left", padx=(12, 4))
        self.profile_var = tk.StringVar(value="速度优先")
        ttk.Combobox(bar, textvariable=self.profile_var, width=8, state="readonly",
                     values=["速度优先", "精度优先"]).pack(side="left")

        self.shot_btn = ttk.Button(bar, text="识别当前画面", command=self.recognize_once, state="disabled")
        self.shot_btn.pack(side="left", padx=(12, 0))

        # ---- 主体
        body = ttk.Frame(outer)
        body.pack(fill="both", expand=True, pady=(10, 0))
        body.columnconfigure(0, weight=1, minsize=620)
        body.columnconfigure(1, weight=0, minsize=290)
        body.rowconfigure(0, weight=1)

        stage = tk.Frame(body, bg="#101418", highlightthickness=0)
        stage.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        stage.rowconfigure(0, weight=1)
        stage.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(stage, bg="#101418", highlightthickness=0, cursor="crosshair")
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.canvas.bind("<Configure>", lambda e: self._render())
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Button-3>", self._clear_roi)          # 右键清除框选

        panel = ttk.Frame(body, style="CamCard.TFrame", padding=(12, 10))
        panel.grid(row=0, column=1, sticky="nsew")
        panel.rowconfigure(1, weight=1)
        panel.columnconfigure(0, weight=1)

        head = ttk.Frame(panel, style="CamCard.TFrame")
        head.grid(row=0, column=0, sticky="ew")
        ttk.Label(head, text="识别结果", style="CamSection.TLabel").pack(side="left")
        self.count_label = ttk.Label(head, text="0 行", style="CamMuted.TLabel")
        self.count_label.pack(side="right")

        text_wrap = ttk.Frame(panel, style="CamCard.TFrame")
        text_wrap.grid(row=1, column=0, sticky="nsew", pady=(8, 8))
        text_wrap.rowconfigure(0, weight=1)
        text_wrap.columnconfigure(0, weight=1)
        self.text = tk.Text(text_wrap, wrap="word", width=1, height=1, relief="flat",
                            bg="#FBFCFE", fg=TEXT,
                            font=("Microsoft YaHei UI", 10), padx=8, pady=6,
                            highlightthickness=1, highlightbackground=BORDER,
                            highlightcolor=BORDER)
        self.text.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(text_wrap, orient="vertical", command=self.text.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.text.configure(yscrollcommand=scroll.set)

        btns = ttk.Frame(panel, style="CamCard.TFrame")
        btns.grid(row=2, column=0, sticky="ew")
        self.clear_roi_btn = ttk.Button(btns, text="清除框选区域", command=self._clear_roi,
                                        state="disabled")
        self.clear_roi_btn.pack(fill="x")
        ttk.Button(btns, text="复制文字", command=self.copy_text).pack(fill="x", pady=(6, 0))
        ttk.Button(btns, text="拍照并加入待处理", command=self.snapshot).pack(fill="x", pady=(6, 0))

        ttk.Label(panel,
                  text=("在画面上按住左键拖一个框，只识别框内文字（最有效）\n"
                        "右键或「清除框选区域」可取消\n"
                        "文字尽量大、正对镜头、光线充足"),
                  style="CamMuted.TLabel", wraplength=270, justify="left").grid(
            row=3, column=0, sticky="w", pady=(8, 0))

        # ---- 底部状态
        self.status = ttk.Label(outer, text="未打开摄像头", style="CamMuted.TLabel")
        self.status.pack(anchor="w", pady=(8, 0))

    # ------------------------------------------------------------ 摄像头
    def _new_capture(self, index: int):
        if self.capture_factory is not None:
            return self.capture_factory(index)
        import cv2
        cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap.release()
            cap = cv2.VideoCapture(index)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        return cap

    def _scan_cameras_async(self):
        if self.capture_factory is not None:
            self.cam_box.configure(values=["0"])
            return

        def work():
            found = []
            try:
                import cv2
                for idx in range(4):
                    cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
                    if cap.isOpened():
                        found.append(str(idx))
                    cap.release()
            except Exception:
                pass
            if found:
                self.after(0, lambda: self.cam_box.configure(values=found))
                if self.cam_var.get() not in found:
                    self.after(0, lambda: self.cam_var.set(found[0]))

        threading.Thread(target=work, daemon=True).start()

    def open_camera(self):
        if self.running:
            return
        idx = int(self.cam_var.get() or 0)
        try:
            self.cap = self._new_capture(idx)
        except Exception as exc:
            messagebox.showerror("摄像头", f"无法打开摄像头：{exc}\n\n请确认：\n"
                                          "1) 摄像头未被其他程序占用\n"
                                          "2) 已安装 opencv-python")
            return

        if not getattr(self.cap, "isOpened", lambda: False)():
            messagebox.showwarning("摄像头", f"摄像头 {idx} 打不开，换一个序号或检查是否被占用。")
            return

        self.running = True
        self.open_btn.configure(state="disabled")
        self.close_btn.configure(state="normal")
        self.shot_btn.configure(state="normal")
        self.status.configure(text="摄像头已打开，正在启动预览…")

        threading.Thread(target=self._capture_loop, daemon=True).start()
        threading.Thread(target=self._ocr_loop, daemon=True).start()
        self._schedule_preview()

    def stop_camera(self):
        self.running = False
        time.sleep(0.05)
        if self.cap is not None:
            try:
                self.cap.release()
            except Exception:
                pass
            self.cap = None
        self.open_btn.configure(state="normal")
        self.close_btn.configure(state="disabled")
        self.shot_btn.configure(state="disabled")
        self.status.configure(text="摄像头已关闭")
        self.canvas.delete("all")
        self.photo = None

    def _capture_loop(self):
        while self.running:
            cap = self.cap
            if cap is None:
                break
            try:
                ok, frame = cap.read()
            except Exception:
                ok, frame = False, None
            if not ok or frame is None:
                time.sleep(0.03)
                continue

            with self.frame_lock:
                self.latest_frame = frame
                self.frame_seq += 1
                seq = self.frame_seq

            now = time.time()
            self.last_fps_frames += 1
            if now - self.last_fps_ts >= 1.0:
                self.fps = self.last_fps_frames / (now - self.last_fps_ts)
                self.last_fps_frames = 0
                self.last_fps_ts = now

    # ------------------------------------------------------------ 识别
    def _interval_seconds(self) -> float:
        raw = self.interval_var.get().replace(" 秒", "").strip()
        try:
            return float(raw)
        except ValueError:
            return 0.5

    def _ocr_loop(self):
        while self.running:
            if not self.live_var.get():
                time.sleep(0.15)
                continue
            interval = self._interval_seconds()
            time.sleep(interval)
            if not self.running:
                break
            self._recognize_latest()

    def _recognize_latest(self):
        with self.frame_lock:
            frame = None if self.latest_frame is None else self.latest_frame.copy()
            seq = self.frame_seq
        if frame is None:
            return
        self._run_ocr(frame, seq)

    def _active_roi(self, fw: int, fh: int):
        """当前生效的识别区域（帧坐标），无则返回 None"""
        if self.roi_rect:
            x0, y0, x1, y1 = self.roi_rect
            return (max(0, int(x0)), max(0, int(y0)),
                    min(fw, int(x1)), min(fh, int(y1)))
        if self.roi_var.get():
            cw, ch = int(fw * ROI_RATIO), int(fh * ROI_RATIO)
            return ((fw - cw) // 2, (fh - ch) // 2,
                    (fw + cw) // 2, (fh + ch) // 2)
        return None

    def _run_ocr(self, frame, seq: int):
        try:
            import cv2
            src_h, src_w = frame.shape[:2]
            roi = self._active_roi(src_w, src_h)

            off_x = off_y = 0
            if roi:
                rx0, ry0, rx1, ry1 = roi
                frame = frame[ry0:ry1, rx0:rx1]
                off_x, off_y = rx0, ry0

            h, w = frame.shape[:2]
            total = 1.0                       # 检测坐标 -> 原帧坐标 的总缩放系数
            if min(h, w) < OCR_UPGRADE_MIN_SIDE:
                # 摄像头原生分辨率低：先放大，小字才识别得出来
                factor = min(OCR_UPGRADE_MIN_SIDE / min(h, w), UPSCALE_MAX)
                frame = cv2.resize(frame, (int(w * factor), int(h * factor)),
                                   interpolation=cv2.INTER_CUBIC)
                total *= factor
            h, w = frame.shape[:2]
            if w > OCR_MAX_WIDTH:
                down = OCR_MAX_WIDTH / w
                frame = cv2.resize(frame, (int(w * down), int(h * down)),
                                   interpolation=cv2.INTER_AREA)
                total *= down

            t0 = time.time()
            items = core.ocr_image(frame, self._min_score(), profile=self._profile())
            cost = time.time() - t0

            boxes = []
            texts = []
            for text, box, _score in items:
                texts.append(text)
                if box:
                    boxes.append([[p[0] / total + off_x, p[1] / total + off_y] for p in box])
                else:
                    boxes.append([])

            self.result = {"boxes": boxes, "texts": texts, "seq": seq, "cost": cost,
                           "stamp": time.time()}
            self.result_stamp = time.time()
        except Exception as exc:
            self.result = {"boxes": [], "texts": [], "seq": seq, "cost": 0.0,
                           "stamp": time.time(), "error": str(exc)}

    def _profile(self) -> str:
        return "doc" if self.profile_var.get() == "精度优先" else "camera"

    def _min_score(self) -> float:
        try:
            value = getattr(self.app, "score_var", None)
            if value is not None:
                s = value.get()
                for cand in ("0.3", "0.5", "0.7"):
                    if s.startswith(cand):
                        return float(cand)
        except Exception:
            pass
        return 0.5

    # ------------------------------------------------------------ 框选区域
    def _canvas_to_frame(self, cx, cy):
        if not self._view:
            return None
        ox, oy, ratio, fw, fh = self._view
        x = (cx - ox) / ratio
        y = (cy - oy) / ratio
        return (max(0.0, min(x, float(fw))), max(0.0, min(y, float(fh))))

    def _on_press(self, event):
        pt = self._canvas_to_frame(event.x, event.y)
        if pt:
            self._drag_start = pt
            self._drag_rect = None

    def _on_drag(self, event):
        if not self._drag_start:
            return
        pt = self._canvas_to_frame(event.x, event.y)
        if not pt:
            return
        x0, y0 = self._drag_start
        x1, y1 = pt
        self._drag_rect = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))

    def _on_release(self, event):
        start, rect = self._drag_start, self._drag_rect
        self._drag_start = None
        self._drag_rect = None
        if not start or not rect or not self._view:
            return
        ratio = self._view[2]
        w_disp = (rect[2] - rect[0]) * ratio
        h_disp = (rect[3] - rect[1]) * ratio
        if w_disp >= ROI_DRAG_MIN and h_disp >= ROI_DRAG_MIN:
            self.roi_rect = rect
            self.clear_roi_btn.configure(state="normal")
            self.status.configure(text="已框选区域：只识别框内文字（右键可清除）")
            self._recognize_latest()          # 立刻按新区域识别一次
        else:
            self._clear_roi()                 # 太小的拖拽视为取消

    def _clear_roi(self, event=None):
        if self.roi_rect is None and str(self.clear_roi_btn["state"]) == "disabled":
            return
        self.roi_rect = None
        self.clear_roi_btn.configure(state="disabled")
        if self.running:
            self.status.configure(text="已清除框选区域")
            self._recognize_latest()

    def recognize_once(self):
        if not self.running:
            return
        with self.frame_lock:
            frame = None if self.latest_frame is None else self.latest_frame.copy()
            seq = self.frame_seq
        if frame is None:
            return
        self.status.configure(text="正在识别当前画面…")
        threading.Thread(target=self._recognize_latest, daemon=True).start()

    def _on_live_toggle(self):
        if self.live_var.get() and self.running:
            self.status.configure(text="实时识别已开启")
        elif self.running:
            self.status.configure(text="实时识别已暂停，可点「识别当前画面」单次识别")

    # ------------------------------------------------------------ 渲染
    def _schedule_preview(self):
        if not self.running:
            return
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        self._render()
        self._preview_job = self.after(PREVIEW_INTERVAL_MS, self._schedule_preview)

    def _render(self):
        with self.frame_lock:
            frame = None if self.latest_frame is None else self.latest_frame.copy()
            seq = self.frame_seq
        if frame is None:
            return

        try:
            import cv2
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        except Exception:
            return

        img = Image.fromarray(rgb)
        cw = max(self.canvas.winfo_width(), 2)
        ch = max(self.canvas.winfo_height(), 2)
        iw, ih = img.size
        ratio = min(cw / iw, ch / ih)
        dw, dh = int(iw * ratio), int(ih * ratio)
        img = img.resize((dw, dh), Image.BILINEAR)

        # 叠加检测框（只在结果较新时画，避免框和画面脱节）
        result = self.result
        box_ttl = max(1.5, float(result.get("cost", 0)) * 2.0) if result else 0
        if result and result.get("boxes") and (time.time() - result["stamp"] < box_ttl):
            draw = ImageDraw.Draw(img)
            for box in result["boxes"]:
                if not box:
                    continue
                pts = [(p[0] * ratio, p[1] * ratio) for p in box]
                draw.line(pts + [pts[0]], fill="#22C55E", width=2)

        # 画布居中，并记录渲染几何（鼠标坐标 <-> 帧坐标换算用）
        ox = (cw - dw) // 2
        oy = (ch - dh) // 2
        self._view = (ox, oy, ratio, iw, ih)

        draw = ImageDraw.Draw(img)
        if self._drag_rect:                       # 正在拖拽：橙色框
            r = self._drag_rect
            draw.rectangle([r[0] * ratio, r[1] * ratio, r[2] * ratio, r[3] * ratio],
                           outline="#F59E0B", width=2)
        elif self.roi_rect:                       # 已框选：蓝色框
            r = self.roi_rect
            draw.rectangle([r[0] * ratio, r[1] * ratio, r[2] * ratio, r[3] * ratio],
                           outline="#60A5FA", width=2)
        elif self.roi_var.get():                  # 中央区域引导框
            cw2, ch2 = dw * ROI_RATIO, dh * ROI_RATIO
            gx, gy = (dw - cw2) / 2, (dh - ch2) / 2
            draw.rectangle([gx, gy, gx + cw2, gy + ch2], outline="#60A5FA", width=2)

        self.canvas.delete("all")
        self.photo = ImageTk.PhotoImage(img)
        self.canvas.create_image(ox, oy, anchor="nw", image=self.photo)

        if result and result.get("texts") is not None:
            self._update_text_panel(result)
            cost = result.get("cost", 0)
            self.status.configure(
                text=f"预览 {self.fps:.0f} fps · 上次识别 {cost:.2f} 秒 · "
                     f"共 {len(result.get('texts') or [])} 行 · 画面 {iw}×{ih}"
            )
        else:
            self.status.configure(text=f"预览 {self.fps:.0f} fps · 画面 {iw}×{ih} · 等待识别…")

    def _update_text_panel(self, result):
        key = (result["seq"], tuple(result.get("texts") or []))
        if key == self.applied_result_id:
            return
        self.applied_result_id = key
        texts = result.get("texts") or []
        self.text.delete("1.0", "end")
        if texts:
            self.text.insert("1.0", "\n".join(texts))
        self.count_label.configure(text=f"{len(texts)} 行")

    # ------------------------------------------------------------ 操作
    def copy_text(self):
        content = self.text.get("1.0", "end").strip()
        if not content:
            self.status.configure(text="还没有可复制的文字")
            return
        self.clipboard_clear()
        self.clipboard_append(content)
        self.status.configure(text=f"已复制 {len(content)} 个字符到剪贴板")

    def snapshot(self):
        with self.frame_lock:
            frame = None if self.latest_frame is None else self.latest_frame.copy()
        if frame is None:
            self.status.configure(text="没有可用画面，请先打开摄像头")
            return

        out_dir = Path(getattr(self.app, "out_var").get().strip() or "ocr_output") / "camera"
        out_dir.mkdir(parents=True, exist_ok=True)
        name = f"拍照_{datetime.now():%Y%m%d_%H%M%S}.png"
        path = out_dir / name

        try:
            import cv2
            ok, buf = cv2.imencode(".png", frame)
            if not ok:
                raise RuntimeError("编码失败")
            path.write_bytes(buf.tobytes())
        except Exception as exc:
            self.status.configure(text=f"保存失败：{exc}")
            return

        try:
            self.app._add_paths([str(path)])
        except Exception:
            pass
        self.status.configure(text=f"已保存并加入待处理列表：{path.name}")

    # ------------------------------------------------------------ 关闭
    def close(self):
        self.running = False
        if self._preview_job is not None:
            try:
                self.after_cancel(self._preview_job)
            except Exception:
                pass
            self._preview_job = None
        time.sleep(0.05)
        if self.cap is not None:
            try:
                self.cap.release()
            except Exception:
                pass
            self.cap = None
        try:
            self.app.camera_win = None
        except Exception:
            pass
        self.destroy()
