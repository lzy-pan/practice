# -*- coding: utf-8 -*-
"""
命令行入口（适合批量任务和自动化）

用法:
    python cli.py 文件或文件夹 [更多...] -o 输出目录
    python cli.py 扫描件 -o 结果 -f txt,md,docx,pdf --min-score 0.6
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import core


def main():
    parser = argparse.ArgumentParser(description="文档批量 OCR（图片 / 扫描版 PDF）")
    parser.add_argument("inputs", nargs="+", help="图片、PDF 或包含它们的文件夹")
    parser.add_argument("-o", "--out", default="ocr_output", help="输出目录（默认 ocr_output）")
    parser.add_argument("-f", "--formats", default="txt,md,docx",
                        help="输出格式，逗号分隔：txt,md,docx,pdf（默认 txt,md,docx）")
    parser.add_argument("--min-score", type=float, default=0.5, help="置信度阈值（默认 0.5）")
    parser.add_argument("--no-smart", action="store_true", help="关闭智能跳过（PDF 已有文字层也重新识别）")
    args = parser.parse_args()

    files = core.collect_files(args.inputs)
    if not files:
        print("没有找到可处理的文件（支持 png/jpg/bmp/webp/tif/pdf）。")
        return 1

    out_dir = Path(args.out)
    opts = core.Options(
        formats=set(args.formats.split(",")),
        min_score=args.min_score,
        smart_skip=not args.no_smart,
    )

    print(f"待处理 {len(files)} 个文件，输出到 {out_dir.resolve()}")
    start = time.time()
    reports = core.process_batch(files, out_dir, opts, on_log=print)
    elapsed = time.time() - start

    ok = sum(1 for r in reports if r.status == "成功")
    print(f"\n全部完成：成功 {ok} / {len(reports)}，共 {elapsed:.1f} 秒")
    print(f"汇总表：{out_dir / '识别汇总.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
