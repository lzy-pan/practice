# -*- coding: utf-8 -*-
"""
PatchCore 核心实现（自己写的，不依赖 anomalib）。

流程：
    1. 预训练骨干提取中层特征（layer2 / layer3）
    2. 局部邻域聚合（3x3 平均池化）—— 抑制位置微小偏移带来的误匹配
    3. 拼接多尺度特征，摊平成 patch 描述子
    4. 贪心 coreset 采样，压缩记忆库（保留"有代表性"的正常 patch）
    5. 推理时计算测试 patch 到记忆库的最近邻距离
       → patch 级异常图（上采样回原图）+ 图像级分数

设计上留了几个可替换点，方便做消融：
    - layers：用哪几层特征（多尺度融合是本项目的主要改进方向之一）
    - coreset_ratio / sampling：记忆库采样策略
    - image_score：图像级聚合方式（max / top-k 均值）
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np
import torch
import torch.nn.functional as F
from torch import nn
from torch.utils.data import DataLoader
from torchvision.models import (
    ResNet18_Weights,
    ResNet50_Weights,
    Wide_ResNet50_2_Weights,
    resnet18,
    resnet50,
    wide_resnet50_2,
)

ARCHS = {
    "resnet18": (resnet18, ResNet18_Weights.IMAGENET1K_V1),
    "resnet50": (resnet50, ResNet50_Weights.IMAGENET1K_V2),
    "wide_resnet50_2": (wide_resnet50_2, Wide_ResNet50_2_Weights.IMAGENET1K_V2),
}

# 各骨干每层的输出通道，用于拼接前做类型检查
LAYER_DIMS = {
    "resnet18": {"layer1": 64, "layer2": 128, "layer3": 256, "layer4": 512},
    "resnet50": {"layer1": 256, "layer2": 512, "layer3": 1024, "layer4": 2048},
    "wide_resnet50_2": {"layer1": 256, "layer2": 512, "layer3": 1024, "layer4": 2048},
}


class FeatureExtractor(nn.Module):
    """把 ResNet 截断成特征提取器，forward 返回指定层的特征图列表。"""

    def __init__(self, arch: str = "resnet18", layers: tuple[str, ...] = ("layer2", "layer3"),
                 pretrained: bool = True):
        super().__init__()
        if arch not in ARCHS:
            raise ValueError(f"不支持的骨干: {arch}，可选 {list(ARCHS)}")
        factory, weights = ARCHS[arch]
        net = factory(weights=weights if pretrained else None)
        self.layers = layers
        self.stem = nn.Sequential(net.conv1, net.bn1, net.relu, net.maxpool)
        self.stages = nn.ModuleDict({name: getattr(net, name) for name in ("layer1", "layer2", "layer3", "layer4")})
        self.eval()
        for p in self.parameters():
            p.requires_grad_(False)

    @torch.no_grad()
    def forward(self, x: torch.Tensor) -> list[torch.Tensor]:
        out: list[torch.Tensor] = []
        feat = self.stem(x)
        for name in ("layer1", "layer2", "layer3", "layer4"):
            feat = self.stages[name](feat)
            if name in self.layers:
                out.append(feat)
        return out


def local_neighborhood_aggregation(feats: list[torch.Tensor]) -> torch.Tensor:
    """
    局部邻域聚合 + 多尺度对齐。

    每个 patch 用 3x3 邻域平均代替单点特征：物体在画面里稍有位移时，
    单点特征会失配，邻域平均能显著降低这种假阳性。
    各层分辨率不同，统一插值到最粗的那一层，再在通道维拼接。
    """
    agg = [F.avg_pool2d(f, kernel_size=3, stride=1, padding=1) for f in feats]
    target = agg[-1].shape[-2:]
    ups = [F.interpolate(a, size=target, mode="bilinear", align_corners=False) for a in agg]
    return torch.cat(ups, dim=1)


@dataclass
class PatchCoreConfig:
    arch: str = "resnet18"
    layers: tuple[str, ...] = ("layer2", "layer3")
    image_size: int = 224
    coreset_ratio: float = 0.01          # 记忆库保留比例
    coreset_min: int = 2000              # 记忆库下限，小数据集别采太狠
    n_neighbors: int = 1                 # 最近邻个数（>1 时取均值，抗噪）
    image_score: str = "max"             # max / topk
    topk_ratio: float = 0.01             # image_score=topk 时取前百分之几
    proj_dim: int = 0                    # >0 时做随机投影降维，加速大记忆库检索
    lcn_kernel: int = 0                  # >0 时对异常图做局部对比度归一化（抑制整体基底）
    coreset_presample: int = 1           # >1 时先随机下采样到 target*该倍数再贪心，加速建库
    seed: int = 0


class PatchCore:
    def __init__(self, cfg: PatchCoreConfig):
        self.cfg = cfg
        torch.manual_seed(cfg.seed)
        np.random.seed(cfg.seed)
        self.extractor = FeatureExtractor(cfg.arch, cfg.layers)
        self.memory: torch.Tensor | None = None
        self.proj: torch.Tensor | None = None
        self.feat_dims = [LAYER_DIMS[cfg.arch][l] for l in cfg.layers]
        self.patch_grid: tuple[int, int] | None = None

    # ------------------------------------------------------------------ 训练
    def _embed(self, batch: torch.Tensor) -> torch.Tensor:
        feats = self.extractor(batch)
        emb = local_neighborhood_aggregation(feats)
        b, c, h, w = emb.shape
        self.patch_grid = (h, w)
        return emb.permute(0, 2, 3, 1).reshape(b, h * w, c)   # (B, P, C)

    def fit(self, loader: DataLoader, verbose: bool = True) -> "PatchCore":
        t0 = time.time()
        chunks = []
        for item in loader:
            batch, labels = item[0], item[1]
            keep = labels >= 0
            if not bool(keep.any()):
                continue
            chunks.append(self._embed(batch if bool(keep.all()) else batch[keep]))
        feats = torch.cat(chunks, 0).reshape(-1, chunks[0].shape[-1])   # (N*P, C)
        n_total = feats.shape[0]

        if self.cfg.proj_dim and self.cfg.proj_dim < feats.shape[1]:
            gen = torch.Generator().manual_seed(self.cfg.seed)
            self.proj = torch.randn(feats.shape[1], self.cfg.proj_dim, generator=gen) / np.sqrt(self.cfg.proj_dim)
            feats = feats @ self.proj

        target = max(self.cfg.coreset_min, int(n_total * self.cfg.coreset_ratio))
        target = min(target, n_total)
        idx = self._greedy_coreset(feats, target, presample=self.cfg.coreset_presample)
        self.memory = feats[idx].contiguous()

        if verbose:
            print(f"    训练样本 patch {n_total} -> 记忆库 {self.memory.shape[0]} "
                  f"（{self.memory.shape[1]} 维），耗时 {time.time() - t0:.1f}s")
        return self

    @staticmethod
    def _greedy_coreset(feats: torch.Tensor, target: int, presample: int = 1,
                        chunk: int = 4096) -> torch.Tensor:
        """
        k-center 贪心采样：每次挑"离已选集合最远"的点，用最少样本覆盖特征分布。

        朴素实现要做 target 次全量距离计算，几十万 patch 时非常慢。这里两步走：
        先随机下采样到 target*presample，再在子集上贪心 —— 选出的点仍然覆盖了
        特征分布的主要区域，但距离计算量降到 1/presample。
        """
        n = feats.shape[0]
        if target >= n:
            return torch.arange(n)

        keep = torch.arange(n)
        sub = feats
        if presample > 1 and n > target * presample:
            gen = torch.Generator().manual_seed(0)
            keep = torch.randperm(n, generator=gen)[: target * presample].sort().values
            sub = feats[keep]

        n_sub = sub.shape[0]
        gen = torch.Generator().manual_seed(0)
        first = int(torch.randint(0, n_sub, (1,), generator=gen).item())
        selected = [first]
        min_dist = torch.full((n_sub,), float("inf"))

        def update(sel_idx: int) -> None:
            nonlocal min_dist
            d = torch.cdist(sub, sub[sel_idx:sel_idx + 1]).squeeze(1)
            min_dist = torch.minimum(min_dist, d)

        update(first)
        for _ in range(target - 1):
            nxt = int(torch.argmax(min_dist).item())
            selected.append(nxt)
            update(nxt)

        return keep[torch.tensor(selected, dtype=torch.long)]

    # ------------------------------------------------------------------ 推理
    def _nearest(self, patches: torch.Tensor) -> torch.Tensor:
        """返回每个 patch 到记忆库的最近邻距离（n_neighbors>1 时取均值）。"""
        assert self.memory is not None
        if self.proj is not None:
            patches = patches @ self.proj
        d = torch.cdist(patches, self.memory)                    # (P, M)
        k = min(self.cfg.n_neighbors, d.shape[1])
        return d.topk(k, dim=1, largest=False).values.mean(dim=1)

    @torch.no_grad()
    def predict_batch(self, batch: torch.Tensor) -> tuple[np.ndarray, np.ndarray]:
        """返回 (图像级分数, patch 级分数图)，分数图尺寸为 (h, w)。"""
        emb = self._embed(batch)                                  # (B, P, C)
        scores = []
        for i in range(emb.shape[0]):
            s = self._nearest(emb[i])                             # (P,)
            scores.append(s)
        patch_scores = torch.stack(scores, 0).reshape(len(scores), *self.patch_grid)

        if self.cfg.image_score == "max":
            img = patch_scores.flatten(1).max(dim=1).values
        elif self.cfg.image_score == "topk":
            flat = patch_scores.flatten(1)
            k = max(1, int(flat.shape[1] * self.cfg.topk_ratio))
            img = flat.topk(k, dim=1).values.mean(dim=1)
        else:
            raise ValueError(f"未知的 image_score: {self.cfg.image_score}")
        return img.numpy(), patch_scores.numpy()

    def anomaly_map(self, patch_score: np.ndarray, out_size: int) -> np.ndarray:
        """把粗网格的 patch 分数双线性插值回原图尺寸，再轻度高斯平滑。"""
        t = torch.from_numpy(patch_score)[None, None].float()
        up = F.interpolate(t, size=(out_size, out_size), mode="bilinear", align_corners=False)
        up = F.avg_pool2d(F.pad(up, (1, 1, 1, 1), mode="replicate"), 3, 1)
        m = up[0, 0].numpy()
        if self.cfg.lcn_kernel > 1:
            m = local_contrast_normalize(m, self.cfg.lcn_kernel)
        return m


def local_contrast_normalize(score_map: np.ndarray, kernel: int = 15) -> np.ndarray:
    """
    局部对比度归一化：减去大核均值（背景基底），只保留"比周围更异常"的部分。

    为什么需要：不同图像的曝光、纹理、工件摆放姿态会让整片区域的最近邻距离
    整体抬高，形成大块低对比的"伪异常"。缺陷的典型特征是局部突起，减去局部
    均值后这类基底被压平，缺陷反而更突出 —— 像素级排序指标因此提升。
    """
    import cv2

    k = int(kernel) | 1                     # 均值滤波核必须是奇数
    bg = cv2.blur(score_map.astype(np.float32), (k, k))
    return np.maximum(score_map - bg, 0.0)
