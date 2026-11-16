# -*- coding: utf-8 -*-
"""检查 MVTec 图片完整性，列出损坏文件（下载被截断或空文件）。"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
MVTEC = ROOT / "data" / "mvtec"


def main() -> None:
    files = sorted(MVTEC.rglob("*.png"))
    bad: list[tuple[Path, str]] = []
    for i, p in enumerate(files, 1):
        try:
            with Image.open(p) as im:
                im.verify()
        except Exception as exc:  # noqa: BLE001
            bad.append((p, str(exc)[:80]))
        if i % 1000 == 0:
            print(f"  已检查 {i}/{len(files)}，损坏 {len(bad)}", flush=True)

    print(f"\n共 {len(files)} 张，损坏 {len(bad)} 张")
    for p, msg in bad:
        print(f"  {p.relative_to(MVTEC)}  <- {msg}")

    if bad:
        out = ROOT / "tools" / "bad_files.txt"
        out.write_text("\n".join(str(p.relative_to(MVTEC)).replace("\\", "/") for p, _ in bad),
                       encoding="utf-8")
        print(f"\n清单已写入 {out}")
        sys.exit(1)


if __name__ == "__main__":
    main()
