"""裁剪并放大预览图，便于视觉确认局部重叠。

用法: python .dsh-ops/crop.py <src.png> <out.png> x0 y0 x1 y1 [scale]
"""
import sys
from PIL import Image

src, out = sys.argv[1], sys.argv[2]
x0, y0, x1, y1 = (int(v) for v in sys.argv[3:7])
scale = float(sys.argv[7]) if len(sys.argv) > 7 else 2.0

im = Image.open(src)
box = (x0, y0, x1, y1)
crop = im.crop(box)
crop = crop.resize((int(crop.width * scale), int(crop.height * scale)), Image.LANCZOS)
crop.save(out)
print(f"{src} {box} -> {out} {crop.size}")
