# -*- coding: utf-8 -*-
"""评估指标：图像级 AUROC、像素级 AUROC、最佳 F1 与阈值。"""
from __future__ import annotations

import numpy as np
from sklearn.metrics import auc, precision_recall_curve, roc_auc_score, roc_curve


def image_auroc(scores: np.ndarray, labels: np.ndarray) -> float:
    if len(np.unique(labels)) < 2:
        return float("nan")
    return float(roc_auc_score(labels, scores))


def pixel_auroc(maps: np.ndarray, masks: np.ndarray, max_pixels: int = 4_000_000) -> float:
    """像素级 AUROC；全部异常像素都采样一次，避免内存爆掉。"""
    scores = maps.reshape(-1)
    targets = (masks.reshape(-1) > 0.5).astype(np.uint8)
    if len(np.unique(targets)) < 2:
        return float("nan")
    if len(scores) > max_pixels:
        rng = np.random.default_rng(0)
        idx = rng.choice(len(scores), max_pixels, replace=False)
        scores, targets = scores[idx], targets[idx]
        if len(np.unique(targets)) < 2:
            return float("nan")
    return float(roc_auc_score(targets, scores))


def best_f1(scores: np.ndarray, labels: np.ndarray) -> tuple[float, float]:
    """返回 (最佳 F1, 对应阈值)。"""
    if len(np.unique(labels)) < 2:
        return float("nan"), float("nan")
    prec, rec, thr = precision_recall_curve(labels, scores)
    f1 = np.divide(2 * prec * rec, prec + rec, out=np.zeros_like(prec), where=(prec + rec) > 0)
    i = int(np.nanargmax(f1))
    threshold = thr[min(i, len(thr) - 1)] if len(thr) else float("nan")
    return float(f1[i]), float(threshold)


def auroc_curve(scores: np.ndarray, labels: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    fpr, tpr, _ = roc_curve(labels, scores)
    return fpr, tpr


def pr_auc(scores: np.ndarray, labels: np.ndarray) -> float:
    if len(np.unique(labels)) < 2:
        return float("nan")
    prec, rec, _ = precision_recall_curve(labels, scores)
    return float(auc(rec, prec))
