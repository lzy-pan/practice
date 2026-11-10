# -*- coding: utf-8 -*-
"""生成测试素材：多页扫描风格 PDF + 小字图片 + 倾斜模糊页"""
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = Path(__file__).resolve().parent.parent / "samples"
OUT.mkdir(exist_ok=True)

FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
]


def font(size: int):
    for path in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def page_image(lines, size=(1240, 1754), bg="white", ink="black", start_y=120):
    img = Image.new("RGB", size, bg)
    draw = ImageDraw.Draw(img)
    y = start_y
    for text, sz in lines:
        draw.text((100, y), text, fill=ink, font=font(sz))
        y += sz + 26
    return img


def make_pdf():
    doc = pymupdf.open()

    p1 = page_image([
        ("设备采购合同", 44),
        ("合同编号：HT-2026-1109-007", 26),
        ("甲方：深圳前海智造科技有限公司", 26),
        ("乙方：广州精工机械有限公司", 26),
        ("签订日期：2026年11月9日", 26),
        ("第一条 采购内容", 30),
        ("乙方向甲方供应数控加工中心 3 台，型号 CNC-850。", 24),
        ("第二条 合同金额", 30),
        ("合同总金额为人民币 1,286,000.00 元（大写：壹佰贰拾捌万陆仟元整）。", 24),
        ("第三条 交付期限", 30),
        ("乙方应于 2026 年 12 月 31 日前完成交付并调试。", 24),
    ])
    p2 = page_image([
        ("附件一：设备清单", 40),
        ("序号    设备名称          型号        数量    单价(元)", 24),
        ("1       数控加工中心      CNC-850     2       428,000.00", 24),
        ("2       数控车床          CK-6150     1       430,000.00", 24),
        ("合计                                       1,286,000.00", 24),
        ("备注：以上价格含税、含运费及安装调试费用。", 22),
    ])
    # 第三页做成倾斜 + 模糊，模拟手机拍摄
    p3 = page_image([
        ("补充条款", 40),
        ("1. 质保期为验收合格之日起 24 个月。", 24),
        ("2. 逾期交付按合同总额每日 0.3% 支付违约金。", 24),
        ("3. 争议由合同签订地人民法院管辖。", 24),
    ]).rotate(-1.6, expand=True, fillcolor="white").filter(ImageFilter.GaussianBlur(0.6))

    for img in (p1, p2, p3):
        tmp = OUT / "_tmp.jpg"
        img.convert("RGB").save(tmp, quality=82, optimize=True)
        page = doc.new_page(width=595, height=842)   # A4
        page.insert_image(page.rect, filename=str(tmp))
        tmp.unlink()

    path = OUT / "采购合同_扫描件.pdf"
    doc.save(path)
    doc.close()
    print(f"已生成 {path}")


def make_small_image():
    img = page_image([
        ("费用报销单", 30),
        ("报销人：李明", 16),
        ("部门：研发中心", 16),
        ("金额：¥3,860.50", 16),
        ("审批：王经理", 16),
    ], size=(520, 340), start_y=50)
    path = OUT / "报销单_小图.png"
    img.save(path)
    print(f"已生成 {path}")


if __name__ == "__main__":
    make_pdf()
    make_small_image()
