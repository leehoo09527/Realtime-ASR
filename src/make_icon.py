#!/usr/bin/env python3
"""生成应用图标（.icns）——纯本地绘制，不依赖任何外部素材。"""
import shutil
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path("/tmp/asr-icon")
ICONSET = OUT / "AppIcon.iconset"
OUT.mkdir(parents=True, exist_ok=True)
if ICONSET.exists():
    shutil.rmtree(ICONSET)
ICONSET.mkdir(parents=True)

S = 1024
MARGIN = 92      # 外边距，符合 macOS 图标留白习惯
RADIUS = 224

# ---------- 背景：竖向渐变 + 圆角遮罩 ----------
grad = Image.new("RGB", (1, S))
top, bottom = (0x35, 0x76, 0xF0), (0x17, 0x3F, 0xC4)
for y in range(S):
    t = y / (S - 1)
    grad.putpixel((0, y), tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)))
grad = grad.resize((S, S))

mask = Image.new("L", (S, S), 0)
ImageDraw.Draw(mask).rounded_rectangle(
    [MARGIN, MARGIN, S - MARGIN, S - MARGIN], radius=RADIUS, fill=255
)

img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
img.paste(grad, (0, 0), mask)
d = ImageDraw.Draw(img)

# ---------- 前景：五条声波竖条 ----------
ratios = [0.34, 0.58, 0.84, 0.58, 0.34]
bar_w, gap = 54, 46
total = len(ratios) * bar_w + (len(ratios) - 1) * gap
x0 = (S - total) // 2
cy = S // 2
for i, ratio in enumerate(ratios):
    h = int(340 * ratio) + 70
    x = x0 + i * (bar_w + gap)
    d.rounded_rectangle(
        [x, cy - h // 2, x + bar_w, cy + h // 2],
        radius=bar_w // 2,
        fill=(255, 255, 255, 242),
    )

# ---------- 输出各尺寸 ----------
sizes = [
    (16, "icon_16x16.png"), (32, "icon_16x16@2x.png"),
    (32, "icon_32x32.png"), (64, "icon_32x32@2x.png"),
    (128, "icon_128x128.png"), (256, "icon_128x128@2x.png"),
    (256, "icon_256x256.png"), (512, "icon_256x256@2x.png"),
    (512, "icon_512x512.png"), (1024, "icon_512x512@2x.png"),
]
for px, name in sizes:
    img.resize((px, px), Image.LANCZOS).save(ICONSET / name)

img.resize((512, 512), Image.LANCZOS).save(OUT / "preview.png")
print("图标源已生成:", ICONSET)
