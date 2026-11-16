# -*- coding: utf-8 -*-
"""
从 HuggingFace 镜像下载 MVTec AD，并重组成官方目录结构：

    data/mvtec/<category>/train/good/*.png
    data/mvtec/<category>/test/<defect>/*.png
    data/mvtec/<category>/ground_truth/<defect>/*_mask.png

用法：
    python tools/download_mvtec.py                 # 全部 15 类
    python tools/download_mvtec.py bottle,screw    # 指定类
"""
from __future__ import annotations

import json
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

BASE = "https://hf-mirror.com/datasets/Voxel51/mvtec-ad/resolve/main/"
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
MVTEC = DATA / "mvtec"

_lock = threading.Lock()


def build_tasks(categories: set[str] | None) -> list[tuple[str, Path]]:
    samples = json.load(open(DATA / "samples.json", encoding="utf-8"))["samples"]
    tasks: list[tuple[str, Path]] = []
    for s in samples:
        cat = s["category"]["label"]
        if categories and cat not in categories:
            continue
        split, defect = s["split"], s["defect"]["label"]
        name = Path(s["filepath"]).name
        if split == "train":
            dst = MVTEC / cat / "train" / "good" / name
        else:
            dst = MVTEC / cat / "test" / defect / name
        tasks.append((BASE + s["filepath"], dst))

        mask_path = (s.get("defect_mask") or {}).get("mask_path")
        if mask_path:
            dst = MVTEC / cat / "ground_truth" / defect / f"{Path(name).stem}_mask.png"
            tasks.append((BASE + mask_path, dst))
    return tasks


def fetch(item: tuple[str, Path]) -> str:
    url, dst = item
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() and dst.stat().st_size > 0:
        return "skip"
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=60) as resp, open(dst, "wb") as fh:
                fh.write(resp.read())
            return "ok"
        except Exception as exc:  # noqa: BLE001
            if attempt == 2:
                return f"fail:{dst.name}:{exc}"
            time.sleep(1 + attempt)
    return "fail"


def main() -> None:
    categories = None
    if len(sys.argv) > 1:
        categories = {c.strip() for c in sys.argv[1].split(",") if c.strip()}

    tasks = build_tasks(categories)
    total = len(tasks)
    print(f"待下载 {total} 个文件 -> {MVTEC}", flush=True)

    done = ok = skip = failed = 0
    errors: list[str] = []
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = [pool.submit(fetch, t) for t in tasks]
        for fut in as_completed(futures):
            status = fut.result()
            with _lock:
                done += 1
                if status == "ok":
                    ok += 1
                elif status == "skip":
                    skip += 1
                else:
                    failed += 1
                    errors.append(status)
                if done % 200 == 0 or done == total:
                    speed = done / max(time.time() - t0, 1e-6)
                    print(f"  {done}/{total}  新下 {ok}  跳过 {skip}  失败 {failed}  "
                          f"{speed:.1f} file/s", flush=True)

    print(f"完成：新下 {ok}，跳过 {skip}，失败 {failed}，用时 {time.time() - t0:.1f}s")
    for e in errors[:10]:
        print("  ", e)


if __name__ == "__main__":
    main()
