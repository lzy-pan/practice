# -*- coding: utf-8 -*-
"""
跑一个或多个类别的完整流程：训练记忆库 -> 测试集评估 -> 输出指标与异常图。

示例：
    python src/run_experiment.py --categories bottle --tag baseline
    python src/run_experiment.py --tag all --proj-dim 128
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from data import list_categories, make_loader          # noqa: E402
from metrics import best_f1, image_auroc, pixel_auroc, pr_auc  # noqa: E402
from model import PatchCore, PatchCoreConfig          # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "mvtec"
OUT = ROOT / "outputs"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--data", default=str(DATA))
    p.add_argument("--categories", default="bottle", help="逗号分隔，或 all")
    p.add_argument("--tag", default="baseline", help="实验标签，用于输出文件名")
    p.add_argument("--arch", default="resnet18")
    p.add_argument("--layers", default="layer2,layer3")
    p.add_argument("--image-size", type=int, default=224)
    p.add_argument("--coreset-ratio", type=float, default=0.01)
    p.add_argument("--coreset-min", type=int, default=2000)
    p.add_argument("--n-neighbors", type=int, default=1)
    p.add_argument("--image-score", default="max", choices=["max", "topk"])
    p.add_argument("--topk-ratio", type=float, default=0.01)
    p.add_argument("--proj-dim", type=int, default=0)
    p.add_argument("--lcn-kernel", type=int, default=0, help="局部对比度归一化核大小，0=关闭")
    p.add_argument("--coreset-presample", type=int, default=1, help="建库前随机下采样倍数，>1 加速")
    p.add_argument("--batch-size", type=int, default=8)
    p.add_argument("--viz-per-category", type=int, default=3, help="每类保存几张异常图，0=不保存")
    return p.parse_args()


def aggregate_scores(maps: np.ndarray, mode: str, topk_ratio: float) -> np.ndarray:
    """把每张图的异常图聚合成一个图像级分数。"""
    flat = maps.reshape(len(maps), -1)
    if mode == "max":
        return flat.max(axis=1).astype(np.float64)
    k = max(1, int(flat.shape[1] * topk_ratio))
    return np.partition(flat, -k, axis=1)[:, -k:].mean(axis=1).astype(np.float64)


def run_category(cat: str, cfg: PatchCoreConfig, args, out_dir: Path) -> dict:
    print(f"\n[{cat}]", flush=True)
    t0 = time.time()
    train_loader = make_loader(args.data, cat, "train", cfg.image_size, args.batch_size)
    model = PatchCore(cfg).fit(train_loader)
    fit_time = time.time() - t0

    test_loader = make_loader(args.data, cat, "test", cfg.image_size, args.batch_size)
    scores, labels, maps, masks, paths = [], [], [], [], []
    t1 = time.time()
    for batch, label, mask, path in test_loader:
        _, patch_score = model.predict_batch(batch)
        labels_b = np.asarray(label).tolist()
        for i in range(len(patch_score)):
            if labels_b[i] < 0:          # 损坏文件，跳过
                continue
            maps.append(model.anomaly_map(patch_score[i], cfg.image_size))
            labels.append(labels_b[i])
            masks.append(mask[i].numpy())
            paths.append(path[i])
    infer_time = time.time() - t1

    maps_a = np.stack(maps)
    masks_a = np.stack(masks, 0)
    # 图像级分数统一从异常图聚合，保证不同后处理之间可比
    scores_a = aggregate_scores(maps_a, cfg.image_score, cfg.topk_ratio)
    labels_a = np.asarray(labels, dtype=np.int64)

    res = {
        "category": cat,
        "n_test": int(len(labels_a)),
        "n_defect": int(labels_a.sum()),
        "image_auroc": image_auroc(scores_a, labels_a),
        "pixel_auroc": pixel_auroc(maps_a, masks_a),
        "pr_auc": pr_auc(scores_a, labels_a),
        "fit_time_s": round(fit_time, 1),
        "infer_time_s": round(infer_time, 1),
        "ms_per_image": round(infer_time * 1000 / max(len(labels_a), 1), 1),
        "mem_size": int(model.memory.shape[0]),
    }
    f1, thr = best_f1(scores_a, labels_a)
    res["best_f1"] = f1
    res["best_threshold"] = thr

    print(f"  图像 AUROC {res['image_auroc']:.4f} | 像素 AUROC {res['pixel_auroc']:.4f} | "
          f"F1 {f1:.4f} | {res['ms_per_image']:.0f} ms/张 | 记忆库 {res['mem_size']}", flush=True)

    if args.viz_per_category > 0:
        save_viz(cat, out_dir, paths, scores_a, maps_a, masks_a, args.viz_per_category)

    return res


def save_viz(cat: str, out_dir: Path, paths: list[str], scores: np.ndarray,
             maps: np.ndarray, masks: np.ndarray, top_n: int) -> None:
    """挑分数最高的几张（多为真缺陷）保存三联图：原图 / 热力图 / mask。"""
    from PIL import Image
    viz_dir = out_dir / "viz" / cat
    viz_dir.mkdir(parents=True, exist_ok=True)
    order = np.argsort(-scores)[:top_n]
    for rank, idx in enumerate(order):
        try:
            img = Image.open(paths[idx]).convert("RGB").resize((224, 224))
        except Exception:
            continue
        m = maps[idx]
        m = (m - m.min()) / (np.ptp(m) + 1e-8)
        heat = np.zeros((224, 224, 3), dtype=np.uint8)
        heat[..., 0] = (m * 255).astype(np.uint8)
        heat[..., 1] = ((1 - m) * 80).astype(np.uint8)
        overlay = (np.asarray(img).astype(np.float32) * 0.45 + heat.astype(np.float32) * 0.55)
        canvas = np.concatenate([
            np.asarray(img),
            overlay.astype(np.uint8),
            np.stack([(masks[idx] * 255).astype(np.uint8)] * 3, -1),
        ], axis=1)
        Image.fromarray(canvas).save(viz_dir / f"{rank:02d}_{Path(paths[idx]).stem}_s{scores[idx]:.2f}.png")


def main() -> None:
    args = parse_args()
    cats = list_categories(args.data) if args.categories == "all" else \
        [c.strip() for c in args.categories.split(",") if c.strip()]

    cfg = PatchCoreConfig(
        arch=args.arch,
        layers=tuple(l.strip() for l in args.layers.split(",")),
        image_size=args.image_size,
        coreset_ratio=args.coreset_ratio,
        coreset_min=args.coreset_min,
        n_neighbors=args.n_neighbors,
        image_score=args.image_score,
        topk_ratio=args.topk_ratio,
        proj_dim=args.proj_dim,
        lcn_kernel=args.lcn_kernel,
        coreset_presample=args.coreset_presample,
    )

    out_dir = OUT / args.tag
    out_dir.mkdir(parents=True, exist_ok=True)

    results = []
    t_all = time.time()
    for cat in cats:
        if not (Path(args.data) / cat).exists():
            print(f"  跳过 {cat}（数据不存在）")
            continue
        results.append(run_category(cat, cfg, args, out_dir))

    (out_dir / "config.json").write_text(json.dumps({
        "args": vars(args), "runtime_s": round(time.time() - t_all, 1),
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    if not results:
        print("没有跑出结果")
        return

    keys = list(results[0])
    with open(out_dir / "results.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(results)

    def mean(key: str) -> float:
        vals = [r[key] for r in results if isinstance(r[key], float) and not np.isnan(r[key])]
        return float(np.mean(vals)) if vals else float("nan")

    print("\n" + "=" * 62)
    print(f"类别数 {len(results)}  平均图像 AUROC {mean('image_auroc'):.4f}  "
          f"平均像素 AUROC {mean('pixel_auroc'):.4f}  平均 F1 {mean('best_f1'):.4f}")
    print(f"结果已写入 {out_dir / 'results.csv'}")


if __name__ == "__main__":
    main()
