# -*- coding: utf-8 -*-
"""
文档批量 OCR —— 核心处理逻辑（与界面无关）

支持输入：图片（png/jpg/bmp/webp/tif）与 PDF（扫描版或混合版）
支持输出：TXT / Markdown / Word / 可搜索 PDF
"""
from __future__ import annotations

import csv
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

import pymupdf
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Pt

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}
PDF_EXTS = {".pdf"}
SUPPORTED_EXTS = IMAGE_EXTS | PDF_EXTS

RENDER_DPI = 200          # 扫描版 PDF 渲染为图片的分辨率
IMAGE_EMBED_DPI = 150     # 单张图片转 PDF 时假定的分辨率
OCR_MIN_SIDE = 960        # 图片最短边小于该值时放大，提升小字识别率


# ---------------------------------------------------------------- 数据结构
@dataclass
class PageResult:
    index: int                       # 第几页，从 1 开始
    lines: list[str] = field(default_factory=list)
    source: str = "ocr"              # ocr = 识别得到，text = PDF 原生文字层

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


@dataclass
class Options:
    formats: set[str] = field(default_factory=lambda: {"txt", "md", "docx"})
    min_score: float = 0.5           # 低于该置信度的行丢弃
    smart_skip: bool = True          # PDF 已有文字层则直接提取，不再识别
    keep_layout: bool = False        # TXT 中按缩进保留大致横坐标

    def __post_init__(self):
        self.formats = {f.lower() for f in self.formats}


@dataclass
class FileReport:
    name: str
    pages: int = 0
    lines: int = 0
    chars: int = 0
    seconds: float = 0.0
    status: str = "成功"
    outputs: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- OCR 引擎
class _Engine:
    """RapidOCR 单例：模型只加载一次，批量处理时复用。"""
    _instance = None

    @classmethod
    def get(cls):
        if cls._instance is None:
            from rapidocr import RapidOCR
            cls._instance = RapidOCR()
        return cls._instance


class _CameraEngine:
    """
    摄像头专用引擎：检测阶段按最长边缩放到 960，速度约为默认配置的 2 倍。
    摄像头画面文字通常较大，牺牲一点小字召回换取流畅度是划算的。
    """
    _instance = None

    @classmethod
    def get(cls):
        if cls._instance is None:
            from rapidocr import RapidOCR
            cls._instance = RapidOCR(params={
                "Det.limit_type": "max",
                "Det.limit_side_len": 960,
            })
        return cls._instance


def ocr_image(image, min_score: float = 0.5,
              profile: str = "doc") -> list[tuple[str, list, float]]:
    """
    识别一张图片，返回 [(文本, 坐标框, 置信度), ...]（按阅读顺序合并同行）。

    image   文件路径，或 numpy 数组（摄像头帧，BGR/RGB 均可）
    profile "doc" 高精度（批量文档）；"camera" 高速度（实时预览）
    """
    source = str(image) if isinstance(image, (str, Path)) else image
    engine = _CameraEngine.get() if profile == "camera" else _Engine.get()
    result = engine(source)
    txts = list(result.txts or [])
    boxes = result.boxes if result.boxes is not None else []
    scores = list(result.scores or [])

    raw_items = []
    for i, raw in enumerate(txts):
        text = (raw or "").strip()
        if not text:
            continue
        score = float(scores[i]) if i < len(scores) else 0.0
        if score < min_score:
            continue
        box = _to_list(boxes[i]) if i < len(boxes) else []
        raw_items.append((text, box, score))

    return _merge_rows(raw_items)


def _merge_rows(items: list[tuple[str, list, float]]) -> list[tuple[str, list, float]]:
    """
    把检测框按"行"聚类后横向拼接。

    检测器会把一行切成多个框（表格尤其明显），直接按左上角排序会导致
    顺序错乱。这里以垂直中心点聚类成行，行内按 x 排序，并按间距补空格。
    """
    if not items:
        return []

    enriched = []
    for text, box, score in items:
        if box:
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            x0, x1 = min(xs), max(xs)
            y0, y1 = min(ys), max(ys)
        else:                                   # 没有坐标的兜底
            x0 = x1 = y0 = y1 = 0.0
        enriched.append({
            "text": text, "box": box, "score": score,
            "x0": x0, "x1": x1, "y0": y0, "y1": y1,
            "cy": (y0 + y1) / 2, "h": max(y1 - y0, 1.0),
        })

    enriched.sort(key=lambda it: (it["cy"], it["x0"]))

    rows: list[list[dict]] = []
    for it in enriched:
        if rows:
            row = rows[-1]
            ref_cy = sum(x["cy"] for x in row) / len(row)
            ref_h = max(x["h"] for x in row)
            # 垂直中心落在同一行高度范围内即视为同行
            if abs(it["cy"] - ref_cy) <= max(ref_h * 0.55, 6):
                row.append(it)
                continue
        rows.append([it])

    merged: list[tuple[str, list, float]] = []
    for row in rows:
        row.sort(key=lambda it: it["x0"])
        parts: list[str] = []
        prev_x1 = None
        row_h = max(x["h"] for x in row)
        for it in row:
            if prev_x1 is not None:
                gap = it["x0"] - prev_x1
                ratio = gap / row_h
                if ratio > 0.8:                 # 明显是表格列间距
                    parts.append("    ")
                elif ratio > 0.12:
                    parts.append(" ")
            parts.append(it["text"])
            prev_x1 = it["x1"] if prev_x1 is None else max(prev_x1, it["x1"])

        text = "".join(parts).strip()
        # 合并后的整行坐标框
        x0 = min(x["x0"] for x in row)
        y0 = min(x["y0"] for x in row)
        x1 = max(x["x1"] for x in row)
        y1 = max(x["y1"] for x in row)
        score = sum(x["score"] for x in row) / len(row)
        merged.append((text, [[x0, y0], [x1, y0], [x1, y1], [x0, y1]], score))

    return merged


def _to_list(box) -> list:
    """把 numpy 坐标框转成 [[x, y], ...] 的纯 Python 结构"""
    if box is None:
        return []
    try:
        return [[float(p[0]), float(p[1])] for p in box]
    except (TypeError, IndexError):
        return []


# ---------------------------------------------------------------- 单文件处理
def process_file(
    src: Path,
    out_dir: Path,
    opts: Options,
    on_progress: Callable[[str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    stem: str | None = None,
    on_page: Callable[[int], None] | None = None,
) -> FileReport:
    """处理单个图片或 PDF，生成所选格式的产物。"""
    start = time.time()
    stem = stem or src.stem
    report = FileReport(name=src.name)
    log = on_progress or (lambda msg: None)

    try:
        if src.suffix.lower() in PDF_EXTS:
            pages, searchable_doc = _process_pdf(src, opts, log, should_stop, on_page)
        elif src.suffix.lower() in IMAGE_EXTS:
            pages, searchable_doc = _process_image(src, opts, report, log, on_page)
        else:
            raise ValueError(f"不支持的文件类型: {src.suffix}")
    except StopProcessing:
        raise
    except Exception as exc:                     # 单个文件失败不影响整体
        report.status = f"失败: {exc}"
        report.seconds = time.time() - start
        log(f"[失败] {src.name}: {exc}")
        return report

    if should_stop and should_stop():
        raise StopProcessing

    report.pages = len(pages)
    report.lines = sum(len(p.lines) for p in pages)
    report.chars = sum(len(p.text) for p in pages)

    out_dir.mkdir(parents=True, exist_ok=True)

    if "txt" in opts.formats:
        path = _unique(out_dir / f"{stem}.txt")
        path.write_text(_pages_to_text(pages, opts, with_marker=len(pages) > 1), encoding="utf-8")
        report.outputs.append(str(path))

    if "md" in opts.formats:
        path = _unique(out_dir / f"{stem}.md")
        path.write_text(_pages_to_markdown(pages, stem), encoding="utf-8")
        report.outputs.append(str(path))

    if "docx" in opts.formats:
        path = _unique(out_dir / f"{stem}.docx")
        _pages_to_docx(pages, stem, path)
        report.outputs.append(str(path))

    if "pdf" in opts.formats and searchable_doc is not None:
        path = _unique(out_dir / f"{stem}_可搜索.pdf")
        searchable_doc.save(str(path))
        report.outputs.append(str(path))

    if searchable_doc is not None:
        searchable_doc.close()

    report.seconds = time.time() - start
    log(f"[完成] {src.name}：{report.pages} 页 / {report.lines} 行 / {report.chars} 字 / {report.seconds:.1f}s")
    return report


class StopProcessing(Exception):
    """用户主动停止"""


def _check_stop(should_stop: Callable[[], bool] | None):
    if should_stop and should_stop():
        raise StopProcessing


# ---------------------------------------------------------------- PDF
def _process_pdf(src: Path, opts: Options, log, should_stop, on_page=None):
    doc = pymupdf.open(src)
    pages: list[PageResult] = []
    out_doc = pymupdf.open() if "pdf" in opts.formats else None

    try:
        for i, page in enumerate(doc):
            _check_stop(should_stop)
            idx = i + 1
            native = page.get_text().strip() if opts.smart_skip else ""

            if len(native) >= 20:                    # 已有文字层，直接提取
                lines = [ln.strip() for ln in native.splitlines() if ln.strip()]
                pages.append(PageResult(idx, lines, "text"))
                if on_page:
                    on_page(1)
                log(f"  第 {idx} 页：使用原生文字层（{len(lines)} 行）")
                if out_doc is not None:
                    out_doc.insert_pdf(doc, from_page=i, to_page=i)
                continue

            log(f"  第 {idx} 页：OCR 识别中…")
            pix = page.get_pixmap(dpi=RENDER_DPI)
            tmp_path = _temp_png(pix)
            try:
                items = ocr_image(tmp_path, opts.min_score)
            finally:
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

            pages.append(PageResult(idx, [t for t, _, _ in items], "ocr"))
            if on_page:
                on_page(1)

            if out_doc is not None:
                out_doc.insert_pdf(doc, from_page=i, to_page=i)
                _add_text_layer(out_doc[-1], items, 72.0 / RENDER_DPI)
    finally:
        doc.close()

    return pages, out_doc


def _add_text_layer(page: "pymupdf.Page", items, scale: float):
    """在页面图片上叠加不可见文字层，让 PDF 可搜索、可复制。"""
    for text, box, _score in items:
        if not box:
            continue
        xs = [p[0] * scale for p in box]
        ys = [p[1] * scale for p in box]
        x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
        height = max(y1 - y0, 3)
        fontsize = max(3.5, min(height * 0.82, 60))
        try:
            page.insert_text(
                (x0, y0 + height * 0.85),
                text,
                fontsize=fontsize,
                fontname="china-s",     # PyMuPDF 内置中文字体
                render_mode=3,          # 3 = 不可见（仅参与文本提取）
                color=(0, 0, 0),
            )
        except Exception:
            continue


# ---------------------------------------------------------------- 图片
def _process_image(src: Path, opts: Options, report: FileReport, log, on_page=None):
    from PIL import Image

    log("  OCR 识别中…")
    work_path = src
    resized_tmp = None
    factor = 1.0                     # 识别用图相对原图的放大倍数

    # 小图先放大，能明显改善小字识别
    try:
        with Image.open(src) as im:
            w, h = im.size
        if min(w, h) < OCR_MIN_SIDE:
            factor = OCR_MIN_SIDE / min(w, h)
            if factor > 1:
                resized_tmp = Path(_temp_name(".png"))
                with Image.open(src) as im:
                    im.convert("RGB").resize(
                        (int(w * factor), int(h * factor)), Image.LANCZOS
                    ).save(resized_tmp)
                work_path = resized_tmp
                log(f"  图片已放大 {factor:.1f}x 以提升识别率")
    except Exception:
        factor = 1.0
        resized_tmp = None
        work_path = src

    try:
        items = ocr_image(work_path, opts.min_score)
    finally:
        if resized_tmp and resized_tmp.exists():
            try:
                resized_tmp.unlink()
            except OSError:
                pass

    pages = [PageResult(1, [t for t, _, _ in items], "ocr")]
    if on_page:
        on_page(1)

    out_doc = None
    if "pdf" in opts.formats:
        out_doc = pymupdf.open()
        with Image.open(src) as im:
            w, h = im.size
        scale = 72.0 / IMAGE_EMBED_DPI
        page = out_doc.new_page(width=w * scale, height=h * scale)
        page.insert_image(page.rect, filename=str(src))
        # 坐标框来自（可能放大的）识别图，需按 factor 折回原图尺寸
        _add_text_layer(page, items, scale / factor)

    return pages, out_doc


# ---------------------------------------------------------------- 导出格式
def _pages_to_text(pages: Sequence[PageResult], opts: Options, with_marker: bool) -> str:
    chunks = []
    for p in pages:
        body = _indent_lines(p.lines) if opts.keep_layout else "\n".join(p.lines)
        if with_marker:
            chunks.append(f"===== 第 {p.index} 页 =====\n{body}")
        else:
            chunks.append(body)
    return "\n\n".join(chunks) + "\n"


def _indent_lines(lines: Sequence[str]) -> str:
    return "\n".join(lines)


def _pages_to_markdown(pages: Sequence[PageResult], title: str) -> str:
    parts = [f"# {title}\n"]
    for p in pages:
        parts.append(f"## 第 {p.index} 页\n")
        parts.append("\n".join(p.lines) if p.lines else "_（本页未识别到文字）_")
    return "\n\n".join(parts) + "\n"


def _pages_to_docx(pages: Sequence[PageResult], title: str, path: Path):
    doc = Document()

    # 正文默认中文字体
    style = doc.styles["Normal"]
    style.font.name = "微软雅黑"
    style.font.size = Pt(10.5)
    style.element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")

    doc.add_heading(title, level=1)
    for p in pages:
        if len(pages) > 1:
            doc.add_heading(f"第 {p.index} 页", level=2)
        if not p.lines:
            para = doc.add_paragraph("（本页未识别到文字）")
            para.alignment = WD_ALIGN_PARAGRAPH.LEFT
            continue
        for line in p.lines:
            doc.add_paragraph(line)

    doc.save(str(path))


# ---------------------------------------------------------------- 工具函数
def _temp_name(suffix: str) -> str:
    import tempfile
    fd, name = tempfile.mkstemp(suffix=suffix)
    import os
    os.close(fd)
    return name


def _temp_png(pix) -> Path:
    path = Path(_temp_name(".png"))
    pix.save(str(path))
    return path


def _unique(path: Path) -> Path:
    """同名文件自动加序号，避免互相覆盖"""
    if not path.exists():
        return path
    n = 1
    while True:
        candidate = path.with_name(f"{path.stem}({n}){path.suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def collect_files(paths: Iterable[str | Path]) -> list[Path]:
    """把文件/文件夹混合列表展开成受支持的文件列表（去重、保持顺序）"""
    result: list[Path] = []
    seen = set()

    def add(p: Path):
        rp = p.resolve()
        if rp in seen:
            return
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS:
            seen.add(rp)
            result.append(p)

    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            for item in sorted(p.rglob("*")):
                add(item)
        else:
            add(p)
    return result


def count_pages(files: Sequence[Path]) -> int:
    """预估总页数，用于进度条（PDF 按实际页数，图片按 1 页）"""
    total = 0
    for f in files:
        if f.suffix.lower() in PDF_EXTS:
            try:
                doc = pymupdf.open(f)
                total += doc.page_count
                doc.close()
            except Exception:
                total += 1
        else:
            total += 1
    return max(total, 1)


def process_batch(
    files: Sequence[Path],
    out_dir: Path,
    opts: Options,
    on_log: Callable[[str], None] | None = None,
    on_page_done: Callable[[int], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> list[FileReport]:
    """批量处理，返回每个文件的报告，并写出汇总 CSV。"""
    log = on_log or (lambda msg: None)
    out_dir.mkdir(parents=True, exist_ok=True)
    reports: list[FileReport] = []

    for n, src in enumerate(files, 1):
        if should_stop and should_stop():
            log("已停止。")
            break

        log(f"[{n}/{len(files)}] {src.name}")
        try:
            report = process_file(
                src, out_dir, opts,
                on_progress=log,
                should_stop=should_stop,
                stem=_safe_stem(src),
                on_page=on_page_done,
            )
        except StopProcessing:
            log("已停止。")
            break

        reports.append(report)

    _write_manifest(reports, out_dir)
    return reports


def _safe_stem(src: Path) -> str:
    stem = src.stem.strip() or "未命名"
    for ch in '<>:"/\\|?*':
        stem = stem.replace(ch, "_")
    return stem


def _write_manifest(reports: Sequence[FileReport], out_dir: Path):
    if not reports:
        return
    path = _unique(out_dir / "识别汇总.csv")
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(["文件", "页数", "识别行数", "字符数", "耗时(秒)", "状态", "输出文件"])
        for r in reports:
            writer.writerow([
                r.name, r.pages, r.lines, r.chars,
                f"{r.seconds:.1f}", r.status, " | ".join(Path(o).name for o in r.outputs),
            ])
