# -*- coding: utf-8 -*-
"""
文档 OCR 工具 —— 桌面界面

把图片 / 扫描版 PDF 批量转成 文本、Word、可搜索 PDF。
双击 run.bat 启动，或 python app.py。
"""
from __future__ import annotations

import ctypes
import os
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import core

APP_TITLE = "文档 OCR 工具"
ACCENT = "#2F6FED"
BG = "#F5F7FA"
CARD = "#FFFFFF"
TEXT = "#1F2430"
MUTED = "#6B7280"
BORDER = "#E3E7EF"

IMAGE_FILETYPES = [
    ("图片", "*.png *.jpg *.jpeg *.bmp *.webp *.tif *.tiff"),
    ("PDF", "*.pdf"),
    ("所有支持的文件", "*.png *.jpg *.jpeg *.bmp *.webp *.tif *.tiff *.pdf"),
]


def enable_dpi_awareness():
    """让界面在高分屏上不发虚"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


class ProgressBar(tk.Canvas):
    """自绘进度条，圆角 + 品牌蓝，避免系统主题把颜色改掉"""

    def __init__(self, parent, height=10, trough="#E8ECF3", fill=ACCENT, bg=CARD):
        super().__init__(parent, height=height, bg=bg, highlightthickness=0, bd=0)
        self._trough = trough
        self._fill = fill
        self._max = 1.0
        self._value = 0.0
        self.bind("<Configure>", lambda e: self._redraw())

    def set_max(self, maximum: float):
        self._max = max(float(maximum), 1.0)
        self._value = 0.0
        self._redraw()

    def set_value(self, value: float):
        self._value = max(0.0, min(float(value), self._max))
        self._redraw()

    def _round_rect(self, x0, y0, x1, y1, r, **kw):
        r = min(r, (x1 - x0) / 2, (y1 - y0) / 2)
        points = [
            x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r,
            x1, y1 - r, x1, y1, x1 - r, y1, x0 + r, y1,
            x0, y1, x0, y1 - r, x0, y0 + r, x0, y0,
        ]
        return self.create_polygon(points, smooth=True, **kw)

    def _redraw(self):
        self.delete("all")
        w = max(self.winfo_width(), 2)
        h = max(self.winfo_height(), 2)
        radius = h / 2
        self._round_rect(0, 0, w, h, radius, fill=self._trough, outline="")
        frac = self._value / self._max if self._max else 0
        if frac > 0:
            fill_w = max(w * frac, h)
            self._round_rect(0, 0, fill_w, h, radius, fill=self._fill, outline="")


class OcrApp:
    def __init__(self, root: tk.Tk, dnd_enabled: bool = False):
        self.root = root
        self.dnd_enabled = dnd_enabled
        self.files: list[Path] = []
        self.queue: queue.Queue = queue.Queue()
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.total_pages = 0
        self.done_pages = 0
        self.start_time = 0.0
        self.camera_win = None

        self._build_style()
        self._build_ui()
        self._bind_events()
        self._poll_queue()

    # ------------------------------------------------------------ 界面
    def _build_style(self):
        style = ttk.Style()
        for candidate in ("vista", "winnative", "clam"):
            try:
                style.theme_use(candidate)
                break
            except tk.TclError:
                continue

        base_font = ("Microsoft YaHei UI", 10)
        self.root.configure(bg=BG)
        style.configure(".", font=base_font, background=BG, foreground=TEXT)
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=CARD)
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("Card.TLabel", background=CARD, foreground=TEXT)
        style.configure("Muted.TLabel", background=CARD, foreground=MUTED, font=("Microsoft YaHei UI", 9))
        style.configure("Title.TLabel", background=CARD, foreground=TEXT,
                        font=("Microsoft YaHei UI", 15, "bold"))
        style.configure("Sub.TLabel", background=CARD, foreground=MUTED,
                        font=("Microsoft YaHei UI", 9))
        style.configure("Section.TLabel", background=CARD, foreground=TEXT,
                        font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("TButton", padding=(12, 6), font=("Microsoft YaHei UI", 9))
        style.configure("Accent.TButton", padding=(18, 8),
                        font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("TCheckbutton", background=CARD, foreground=TEXT)
        style.map("TCheckbutton", background=[("active", CARD)])
        style.configure("Treeview", background=CARD, fieldbackground=CARD,
                        foreground=TEXT, rowheight=26, borderwidth=0)
        style.configure("Treeview.Heading", foreground=MUTED,
                        font=("Microsoft YaHei UI", 9))
        style.map("Treeview", background=[("selected", "#DCE7FB")],
                  foreground=[("selected", TEXT)])
        style.configure("TCombobox", padding=3)
        style.configure("TEntry", padding=4)
        style.configure("TSeparator", background=BORDER)

    def _build_ui(self):
        self.root.title(f"{APP_TITLE}  ·  本地离线")
        self.root.geometry("1120x780")
        self.root.minsize(980, 660)

        outer = ttk.Frame(self.root, padding=(16, 14, 16, 12))
        outer.pack(fill="both", expand=True)

        # ---- 头部
        header = ttk.Frame(outer, style="Card.TFrame", padding=(18, 14))
        header.pack(fill="x")
        ttk.Label(header, text=APP_TITLE, style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text="图片 / 扫描版 PDF　→　文本 · Word · 可搜索 PDF　　全部在本机完成，文件不上传",
            style="Sub.TLabel",
        ).pack(anchor="w", pady=(3, 0))

        # ---- 底部操作栏先占位，避免被文件列表挤出窗口
        self._build_action_bar(outer)

        # ---- 主体：左文件列表 / 右选项
        body = ttk.Frame(outer)
        body.pack(fill="both", expand=True, pady=(12, 0))
        body.columnconfigure(0, weight=3, minsize=520)
        body.columnconfigure(1, weight=0, minsize=320)
        body.rowconfigure(0, weight=1)

        self._build_file_panel(body)
        self._build_option_panel(body)

    def _build_file_panel(self, parent):
        card = ttk.Frame(parent, style="Card.TFrame", padding=(14, 12))
        card.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        card.rowconfigure(2, weight=1)
        card.columnconfigure(0, weight=1)

        top = ttk.Frame(card, style="Card.TFrame")
        top.grid(row=0, column=0, sticky="ew")
        ttk.Label(top, text="待处理文件", style="Section.TLabel").pack(side="left")
        self.count_label = ttk.Label(top, text="0 个文件", style="Muted.TLabel")
        self.count_label.pack(side="right")

        hint = "把文件或文件夹拖到这里" if self.dnd_enabled else "支持 png / jpg / bmp / webp / tif / pdf"
        ttk.Label(card, text=hint, style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(2, 8))

        wrap = ttk.Frame(card, style="Card.TFrame")
        wrap.grid(row=2, column=0, sticky="nsew")
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)

        columns = ("type", "pages", "size")
        self.tree = ttk.Treeview(wrap, columns=columns, show="tree headings",
                                 selectmode="extended", height=9)
        self.tree.heading("#0", text="文件名")
        self.tree.column("#0", width=330, anchor="w")
        self.tree.heading("type", text="类型")
        self.tree.column("type", width=70, anchor="center")
        self.tree.heading("pages", text="页数")
        self.tree.column("pages", width=60, anchor="center")
        self.tree.heading("size", text="大小")
        self.tree.column("size", width=80, anchor="e")
        self.tree.grid(row=0, column=0, sticky="nsew")

        scroll = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)

        self.tree.tag_configure("odd", background="#FAFBFD")

        btns = ttk.Frame(card, style="Card.TFrame")
        btns.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        ttk.Button(btns, text="添加文件", command=self.add_files).pack(side="left")
        ttk.Button(btns, text="添加文件夹", command=self.add_folder).pack(side="left", padx=(8, 0))
        ttk.Button(btns, text="移除选中", command=self.remove_selected).pack(side="left", padx=(8, 0))
        ttk.Button(btns, text="清空", command=self.clear_files).pack(side="left", padx=(8, 0))
        ttk.Button(btns, text="摄像头识别", command=self.open_camera).pack(side="right")

        # 日志
        log_wrap = ttk.Frame(card, style="Card.TFrame")
        log_wrap.grid(row=4, column=0, sticky="ew", pady=(12, 0))
        log_wrap.columnconfigure(0, weight=1)
        ttk.Label(log_wrap, text="运行日志", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        self.log_text = tk.Text(
            log_wrap, height=4, wrap="none", relief="flat", bg="#FBFCFE",
            fg="#333B4A", insertbackground=TEXT, font=("Consolas", 9),
            highlightthickness=1, highlightbackground=BORDER, highlightcolor=BORDER,
            padx=8, pady=6, state="disabled",
        )
        self.log_text.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        log_scroll = ttk.Scrollbar(log_wrap, orient="vertical", command=self.log_text.yview)
        log_scroll.grid(row=1, column=1, sticky="ns", pady=(6, 0))
        self.log_text.configure(yscrollcommand=log_scroll.set)

    def _build_option_panel(self, parent):
        card = ttk.Frame(parent, style="Card.TFrame", padding=(16, 14))
        card.grid(row=0, column=1, sticky="nsew")
        card.columnconfigure(0, weight=1)

        row = 0
        ttk.Label(card, text="输出格式", style="Section.TLabel").grid(row=row, column=0, sticky="w")
        row += 1

        self.fmt_vars = {
            "txt": tk.BooleanVar(value=True),
            "docx": tk.BooleanVar(value=True),
            "md": tk.BooleanVar(value=False),
            "pdf": tk.BooleanVar(value=True),
        }
        fmt_labels = [
            ("txt", "纯文本 TXT"),
            ("docx", "Word 文档 DOCX"),
            ("md", "Markdown"),
            ("pdf", "可搜索 PDF（保留原件外观）"),
        ]
        for key, label in fmt_labels:
            ttk.Checkbutton(card, text=label, variable=self.fmt_vars[key]).grid(
                row=row, column=0, sticky="w", pady=(6, 0))
            row += 1

        ttk.Separator(card, orient="horizontal").grid(row=row, column=0, sticky="ew", pady=14)
        row += 1

        ttk.Label(card, text="输出位置", style="Section.TLabel").grid(row=row, column=0, sticky="w")
        row += 1
        out_box = ttk.Frame(card, style="Card.TFrame")
        out_box.grid(row=row, column=0, sticky="ew", pady=(6, 0))
        out_box.columnconfigure(0, weight=1)
        self.out_var = tk.StringVar(value=str(Path.cwd() / "ocr_output"))
        ttk.Entry(out_box, textvariable=self.out_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(out_box, text="浏览", width=6, command=self.choose_out_dir).grid(row=0, column=1, padx=(6, 0))
        row += 1

        ttk.Separator(card, orient="horizontal").grid(row=row, column=0, sticky="ew", pady=14)
        row += 1

        ttk.Label(card, text="识别选项", style="Section.TLabel").grid(row=row, column=0, sticky="w")
        row += 1

        self.smart_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(card, text="PDF 已有文字层则直接提取（更快）",
                        variable=self.smart_var).grid(row=row, column=0, sticky="w", pady=(6, 0))
        row += 1

        score_row = ttk.Frame(card, style="Card.TFrame")
        score_row.grid(row=row, column=0, sticky="ew", pady=(10, 0))
        ttk.Label(score_row, text="置信度阈值", style="Card.TLabel").pack(side="left")
        self.score_var = tk.StringVar(value="0.5")
        combo = ttk.Combobox(score_row, textvariable=self.score_var, width=11, state="readonly",
                             values=["0.3（多留）", "0.5（推荐）", "0.7（严格）"])
        combo.set("0.5（推荐）")
        combo.pack(side="right")
        row += 1

        ttk.Label(card, text="低于阈值的结果会被丢弃，可减少乱码。",
                  style="Muted.TLabel", wraplength=270).grid(row=row, column=0, sticky="w", pady=(4, 0))
        row += 1

        ttk.Separator(card, orient="horizontal").grid(row=row, column=0, sticky="ew", pady=14)
        row += 1

        tips = ("小技巧\n· 拍照件先裁剪掉桌面背景，识别更准\n"
                "· 发票/表格建议同时勾选 Word\n"
                "· 可搜索 PDF 可直接在阅读器里 Ctrl+F")
        ttk.Label(card, text=tips, style="Muted.TLabel", justify="left",
                  wraplength=270).grid(row=row, column=0, sticky="w")

        card.rowconfigure(row + 1, weight=1)

    def _build_action_bar(self, parent):
        bar = ttk.Frame(parent, style="Card.TFrame", padding=(16, 12))
        bar.pack(fill="x", side="bottom", pady=(12, 0))
        bar.columnconfigure(0, weight=1)

        self.progress = ProgressBar(bar, height=10)
        self.progress.grid(row=0, column=0, sticky="ew")

        self.status_label = ttk.Label(bar, text="就绪：添加文件后点击开始", style="Muted.TLabel")
        self.status_label.grid(row=1, column=0, sticky="w", pady=(6, 0))

        btns = ttk.Frame(bar, style="Card.TFrame")
        btns.grid(row=0, column=1, rowspan=2, padx=(16, 0))
        self.open_btn = ttk.Button(btns, text="打开输出目录", command=self.open_out_dir)
        self.open_btn.pack(side="left", padx=(0, 8))
        self.stop_btn = ttk.Button(btns, text="停止", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", padx=(0, 10))
        self.start_btn = tk.Button(
            btns, text="开始识别", command=self.start,
            bg=ACCENT, fg="white", activebackground="#255FD0", activeforeground="white",
            disabledforeground="#E8EEFB", relief="flat", bd=0, cursor="hand2",
            font=("Microsoft YaHei UI", 10, "bold"), padx=20, pady=7,
        )
        self.start_btn.pack(side="left")

    def _bind_events(self):
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.tree.bind("<Double-1>", lambda e: self.remove_selected())
        self.tree.bind("<Delete>", lambda e: self.remove_selected())

        if self.dnd_enabled:
            try:
                self.root.drop_target_register("DND_Files")
                self.root.dnd_bind("<<Drop>>", self._on_drop)
            except Exception:
                self.dnd_enabled = False

    # ------------------------------------------------------------ 文件管理
    def _on_drop(self, event):
        paths = self.root.tk.splitlist(event.data)
        self._add_paths(paths)

    def add_files(self):
        paths = filedialog.askopenfilenames(title="选择图片或 PDF", filetypes=IMAGE_FILETYPES)
        if paths:
            self._add_paths(paths)

    def add_folder(self):
        folder = filedialog.askdirectory(title="选择文件夹（含子文件夹一并导入）")
        if folder:
            self._add_paths([folder])

    def _add_paths(self, paths):
        found = core.collect_files(paths)
        added = 0
        for f in found:
            if f in self.files:
                continue
            self.files.append(f)
            pages = self._page_count(f)
            self.tree.insert(
                "", "end", iid=str(len(self.files) - 1),
                text=f.name,
                values=(self._type_label(f), pages, self._size_label(f)),
                tags=("odd",) if len(self.files) % 2 == 0 else (),
            )
            added += 1

        if added == 0:
            self._log("没有新增文件（可能已在不支持列表或重复）。")
        else:
            self._log(f"已添加 {added} 个文件。")
        self._refresh_summary()

    def _page_count(self, path: Path) -> int:
        if path.suffix.lower() in core.PDF_EXTS:
            try:
                doc = core.pymupdf.open(path)
                n = doc.page_count
                doc.close()
                return n
            except Exception:
                return 1
        return 1

    @staticmethod
    def _type_label(path: Path) -> str:
        return "PDF" if path.suffix.lower() in core.PDF_EXTS else "图片"

    @staticmethod
    def _size_label(path: Path) -> str:
        try:
            size = path.stat().st_size
        except OSError:
            return "-"
        if size > 1024 * 1024:
            return f"{size / 1024 / 1024:.1f} MB"
        return f"{max(size // 1024, 1)} KB"

    def remove_selected(self):
        for iid in self.tree.selection():
            idx = int(iid)
            if 0 <= idx < len(self.files):
                self.files[idx] = None  # type: ignore[assignment]
            self.tree.delete(iid)
        self.files = [f for f in self.files if f is not None]
        self._rebuild_tree()

    def open_camera(self):
        """打开实时摄像头识别窗口（单实例）"""
        try:
            import camera
        except Exception as exc:
            messagebox.showerror(
                APP_TITLE,
                f"摄像头功能不可用：{exc}\n\n请先安装依赖：\n"
                "pip install opencv-python",
            )
            return

        win = getattr(self, "camera_win", None)
        if win is not None:
            try:
                if win.winfo_exists():
                    win.lift()
                    win.focus_force()
                    return
            except Exception:
                pass

        self.camera_win = camera.CameraWindow(self.root, self)

    def clear_files(self):
        self.files.clear()
        self.tree.delete(*self.tree.get_children())
        self._refresh_summary()

    def _rebuild_tree(self):
        self.tree.delete(*self.tree.get_children())
        files = list(self.files)
        self.files.clear()
        for i, f in enumerate(files):
            self.files.append(f)
            self.tree.insert(
                "", "end", iid=str(i), text=f.name,
                values=(self._type_label(f), self._page_count(f), self._size_label(f)),
                tags=("odd",) if i % 2 == 1 else (),
            )
        self._refresh_summary()

    def _refresh_summary(self):
        total = sum(self._page_count(f) for f in self.files)
        self.count_label.configure(text=f"{len(self.files)} 个文件 · 约 {total} 页")
        if not self.is_running():
            self.status_label.configure(text="就绪：添加文件后点击开始" if not self.files
                                        else f"已就绪：{len(self.files)} 个文件，约 {total} 页")

    # ------------------------------------------------------------ 选项
    def choose_out_dir(self):
        folder = filedialog.askdirectory(title="选择输出目录")
        if folder:
            self.out_var.set(folder)

    def _current_options(self) -> core.Options:
        formats = {k for k, v in self.fmt_vars.items() if v.get()}
        if not formats:
            formats = {"txt"}
        score_text = self.score_var.get()
        score = 0.5
        for cand in ("0.3", "0.5", "0.7"):
            if score_text.startswith(cand):
                score = float(cand)
        return core.Options(formats=formats, min_score=score, smart_skip=self.smart_var.get())

    # ------------------------------------------------------------ 运行
    def is_running(self) -> bool:
        return self.worker is not None and self.worker.is_alive()

    def start(self):
        if self.is_running():
            return
        if not self.files:
            messagebox.showinfo(APP_TITLE, "请先添加要识别的图片或 PDF。")
            return

        out_dir = Path(self.out_var.get().strip() or "ocr_output")
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"输出目录不可用：{exc}")
            return

        opts = self._current_options()
        self.stop_event.clear()
        self.start_time = time.time()
        self.total_pages = sum(self._page_count(f) for f in self.files) or 1
        self.done_pages = 0
        self.progress.set_max(self.total_pages)
        self._set_running(True)
        self._log("—" * 42)
        self._log(f"开始处理 {len(self.files)} 个文件 → {out_dir}")

        files = list(self.files)
        self.worker = threading.Thread(
            target=self._run_worker, args=(files, out_dir, opts), daemon=True
        )
        self.worker.start()

    def _run_worker(self, files, out_dir: Path, opts: core.Options):
        try:
            reports = core.process_batch(
                files, out_dir, opts,
                on_log=lambda msg: self.queue.put(("log", msg)),
                on_page_done=lambda n: self.queue.put(("pages", n)),
                should_stop=self.stop_event.is_set,
            )
            self.queue.put(("done", (reports, out_dir)))
        except Exception as exc:                       # 兜底，避免线程静默死亡
            self.queue.put(("error", str(exc)))

    def stop(self):
        if self.is_running():
            self.stop_event.set()
            self.status_label.configure(text="正在停止…（当前页处理完就停下）")
            self.stop_btn.configure(state="disabled")

    def _set_running(self, running: bool):
        self.start_btn.configure(state="disabled" if running else "normal")
        self.stop_btn.configure(state="normal" if running else "disabled")

    # ------------------------------------------------------------ 消息循环
    def _poll_queue(self):
        try:
            if not self.root.winfo_exists():
                return
        except Exception:
            return
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "log":
                    self._log(payload)
                elif kind == "pages":
                    self.done_pages += payload
                    self.progress.set_value(self.done_pages)
                    self.status_label.configure(
                        text=f"识别中… 已完成 {min(self.done_pages, self.total_pages)} / {self.total_pages} 页")
                elif kind == "done":
                    self._on_done(*payload)
                elif kind == "error":
                    self._set_running(False)
                    self._log(f"[错误] {payload}")
                    messagebox.showerror(APP_TITLE, f"处理出错：{payload}")
        except queue.Empty:
            pass
        self.root.after(120, self._poll_queue)

    def _on_done(self, reports, out_dir: Path):
        self._set_running(False)
        ok = sum(1 for r in reports if r.status == "成功")
        pages = sum(r.pages for r in reports)
        chars = sum(r.chars for r in reports)
        elapsed = time.time() - self.start_time

        self.progress.set_value(self.total_pages)
        self.status_label.configure(
            text=f"完成：成功 {ok}/{len(reports)} 个文件 · {pages} 页 · {chars} 字 · {elapsed:.1f} 秒")
        self._log(f"完成：成功 {ok}/{len(reports)}，{pages} 页，{chars} 字，耗时 {elapsed:.1f}s")

        if reports and messagebox.askyesno(APP_TITLE, f"处理完成：\n\n成功 {ok} 个文件，共 {pages} 页。\n是否打开输出目录？"):
            self._open_path(out_dir)

    def _open_path(self, path: Path):
        try:
            os.startfile(str(path))          # type: ignore[attr-defined]
        except Exception as exc:
            messagebox.showinfo(APP_TITLE, f"无法自动打开：{exc}\n路径：{path}")

    def open_out_dir(self):
        path = Path(self.out_var.get().strip() or "ocr_output")
        path.mkdir(parents=True, exist_ok=True)
        self._open_path(path)

    def _log(self, msg: str):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", msg + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def on_close(self):
        if self.is_running():
            if not messagebox.askyesno(APP_TITLE, "正在识别中，确定退出吗？"):
                return
            self.stop_event.set()
        self.root.destroy()


def main():
    enable_dpi_awareness()
    dnd = False
    try:
        from tkinterdnd2 import TkinterDnD
        root = TkinterDnD.Tk()
        dnd = True
    except Exception:
        root = tk.Tk()

    OcrApp(root, dnd_enabled=dnd)
    root.mainloop()


if __name__ == "__main__":
    main()
