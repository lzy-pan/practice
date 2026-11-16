# -*- coding: utf-8 -*-
"""MVTec AD 数据读取：训练只用 normal 样本，测试带图像级标签与像素级 mask。"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import torch
from PIL import Image
from torch.utils.data import DataLoader, Dataset
from torchvision import transforms

IMAGENET_MEAN = (0.485, 0.456, 0.406)
IMAGENET_STD = (0.229, 0.224, 0.225)


def build_transform(image_size: int) -> transforms.Compose:
    return transforms.Compose([
        transforms.Resize((image_size, image_size), interpolation=transforms.InterpolationMode.BICUBIC),
        transforms.ToTensor(),
        transforms.Normalize(IMAGENET_MEAN, IMAGENET_STD),
    ])


class MVTecDataset(Dataset):
    """
    mode='train'：只读 train/good，全部视为正常样本
    mode='test' ：读 test/*，标签来自子目录名（good=0，其余=1）
    """

    def __init__(self, root: Path | str, category: str, mode: str = "test", image_size: int = 224):
        self.root = Path(root) / category
        self.category = category
        self.mode = mode
        self.image_size = image_size
        self.tf = build_transform(image_size)
        self.samples: list[tuple[Path, int, Path | None]] = []
        self._index()

    def _index(self) -> None:
        if self.mode == "train":
            good_dir = self.root / "train" / "good"
            for p in sorted(good_dir.glob("*.png")):
                self.samples.append((p, 0, None))
            return

        for defect_dir in sorted((self.root / "test").iterdir()):
            if not defect_dir.is_dir():
                continue
            is_defect = 0 if defect_dir.name == "good" else 1
            for p in sorted(defect_dir.glob("*.png")):
                mask = None
                if is_defect:
                    candidate = self.root / "ground_truth" / defect_dir.name / f"{p.stem}_mask.png"
                    if candidate.exists():
                        mask = candidate
                self.samples.append((p, is_defect, mask))

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, idx: int):
        path, label, mask_path = self.samples[idx]
        try:
            img = Image.open(path).convert("RGB")
            if mask_path is not None:
                mask = Image.open(mask_path).convert("L").resize(
                    (self.image_size, self.image_size), Image.NEAREST)
                mask_arr = (np.array(mask) > 127).astype(np.float32)
            else:
                mask_arr = np.zeros((self.image_size, self.image_size), dtype=np.float32)
            return self.tf(img), label, torch.from_numpy(mask_arr), str(path)
        except Exception:
            # 真实产线数据难免有损坏文件：返回 -1 让上层跳过，而不是中断整批
            blank = torch.zeros(3, self.image_size, self.image_size)
            return blank, -1, torch.zeros(self.image_size, self.image_size), str(path)

    @property
    def defect_types(self) -> list[str]:
        return sorted({p.parent.name for p, l, _ in self.samples if l == 1})


def make_loader(root, category: str, mode: str, image_size: int, batch_size: int = 8,
                shuffle: bool = False, num_workers: int = 0) -> DataLoader:
    ds = MVTecDataset(root, category, mode, image_size)
    return DataLoader(ds, batch_size=batch_size, shuffle=shuffle, num_workers=num_workers)


def list_categories(root: Path | str) -> list[str]:
    root = Path(root)
    if not root.exists():
        return []
    return sorted(p.name for p in root.iterdir()
                  if p.is_dir() and (p / "train" / "good").exists())
