# -*- coding: utf-8 -*-
"""
汇总多个实验的结果，输出对比表与柱状图。

用法：
    python src/plot_results.py baseline lcn multiscale ours
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "outputs"

SHOW_NAMES = {
    "baseline": "Baseline (PatchCore)",
    "lcn": "+ Local Contrast Norm",
    "multiscale": "+ Multi-scale Feat",
    "ours": "+ Both (Ours)",
}


def load(tag: str) -> dict[str, dict]:
    path = OUT / tag / "results.csv"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as fh:
        return {r["category"]: r for r in csv.DictReader(fh)}


def main() -> None:
    tags = sys.argv[1:] or ["baseline"]
    data = {t: load(t) for t in tags}
    tags = [t for t in tags if data[t]]
    if not tags:
        print("没有可用的结果")
        return

    cats = sorted(set.intersection(*[set(data[t]) for t in tags]))
    print(f"共同类别（{len(cats)}）：{', '.join(cats)}\n")

    print(f"{'方法':<26}{'图像AUROC':>11}{'像素AUROC':>11}{'F1':>8}{'建库(s)':>10}{'ms/张':>8}")
    print("-" * 74)
    rows = []
    for t in tags:
        img = np.mean([float(data[t][c]["image_auroc"]) for c in cats])
        pix = np.mean([float(data[t][c]["pixel_auroc"]) for c in cats])
        f1 = np.mean([float(data[t][c]["best_f1"]) for c in cats])
        fit = np.mean([float(data[t][c]["fit_time_s"]) for c in cats])
        ms = np.mean([float(data[t][c]["ms_per_image"]) for c in cats])
        print(f"{SHOW_NAMES.get(t, t):<24}{img:>12.4f}{pix:>12.4f}{f1:>9.4f}{fit:>10.1f}{ms:>8.0f}")
        rows.append((t, img, pix, f1, fit, ms))

    # 对比表落盘
    out_csv = OUT / "comparison.csv"
    with open(out_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["method", "image_auroc", "pixel_auroc", "f1", "fit_time_s", "ms_per_image", "n_categories"])
        for t, img, pix, f1, fit, ms in rows:
            w.writerow([t, f"{img:.4f}", f"{pix:.4f}", f"{f1:.4f}", f"{fit:.1f}", f"{ms:.0f}", len(cats)])

    # 柱状图
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.2))
    labels = [SHOW_NAMES.get(t, t).replace(" (", "\n(") for t in tags]
    colors = ["#94a3b8", "#60a5fa", "#34d399", "#f59e0b", "#a78bfa"][: len(tags)]
    for ax, key, title in zip(axes, [1, 2, 3], ["Image AUROC", "Pixel AUROC", "Best F1"]):
        vals = [r[key] for r in rows]
        bars = ax.bar(labels, vals, color=colors, width=0.62)
        ax.set_title(title, fontsize=11, fontweight="bold")
        lo = min(vals) - 0.02 if min(vals) > 0.5 else 0
        ax.set_ylim(max(0, lo), 1.005)
        ax.grid(axis="y", alpha=0.25, linestyle="--")
        ax.tick_params(labelsize=8)
        for b, v in zip(bars, vals):
            ax.text(b.get_x() + b.get_width() / 2, v + 0.004, f"{v:.4f}",
                    ha="center", fontsize=8)
    for ax in axes:
        ax.spines[["top", "right"]].set_visible(False)
    fig.suptitle(f"MVTec AD  ·  {len(cats)} categories  ·  CPU inference", fontsize=11)
    fig.tight_layout()
    fig.savefig(OUT / "comparison.png", dpi=160, bbox_inches="tight")
    print(f"\n对比表 {out_csv}\n柱状图 {OUT / 'comparison.png'}")


if __name__ == "__main__":
    main()
