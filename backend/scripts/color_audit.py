"""UI 截图颜色审计脚本：对一张截图取样主色调、卡片背景色、文字对比度，辅助颜色打磨。

用法:
    python color_audit.py <image_path> [--grid] [--regions NxN]

说明:
    纯确定性分析（PIL），无需外部 API；不识别语义，只量化颜色与对比度。
"""
import argparse
import sys
from collections import Counter

from PIL import Image, ImageStat, ImageDraw

DEFAULT_CARD_HS = (35.0, 55.0)  # 暖黄/琥珀色相的判定范围

def hexc(rgb):
    return "#%02x%02x%02x" % tuple(int(v) for v in rgb)

def to_hsl(r, g, b):
    r, g, b = r / 255.0, g / 255.0, b / 255.0
    mx, mn = max(r, g, b), min(r, g, b)
    l = (mx + mn) / 2
    if mx == mn:
        return 0.0, 0.0, l
    d = mx - mn
    s = d / (2 - mx - mn) if l > 0.5 else d / (mx + mn)
    if mx == r:
        h = (g - b) / d + (6 if g < b else 0)
    elif mx == g:
        h = (b - r) / d + 2
    else:
        h = (r - g) / d + 4
    return h * 60, s, l

def rel(a, b, _0=0.0):
    return a if a > _0 else _0
def lin(c):
    c /= 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
def luminance(rgb):
    r, g, b = rgb[:3]
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
def contrast(rgba, rgbb):
    l1, l2 = luminance(rgba), luminance(rgbb)
    if l1 < l2:
        l1, l2 = l2, l1
    return (l1 + 0.05) / (l2 + 0.05)

def dominant_colors(img, n=12):
    if img.mode != "RGB":
        img = img.convert("RGB")
    small = img.resize((max(1, img.width // 4), max(1, img.height // 4)), Image.LANCZOS)
    px = list(small.getdata())
    mc = [c for c, _ in Counter(px).most_common()]
    # 合并相近色（按 HSL 归类主色），保留像素占比
    buckets = []
    for c in mc:
        h, s, l = to_hsl(*c)
        placed = False
        for b in buckets:
            eh, es, el = b["hsl"]
            if abs(h - eh) < 18 and abs(s - es) < 0.12 and abs(l - el) < 0.10:
                b["rgb"] = c
                b["hsl"] = (h, s, l)
                placed = True
                break
        if not placed:
            buckets.append({"rgb": c, "hsl": (h, s, l)})
    counts = Counter(fp := [hexc(b["rgb"]) for b in buckets])
    return buckets

def audit_region(img, box):
    """对区域内背景主色与色彩分布做量化；返回候选卡片暖色。"""
    sub = img.crop(box)
    sub_rgb = sub.convert("RGB")
    colors = sub_rgb.getcolors(maxcolors=sub_rgb.width * sub_rgb.height)
    colors.sort(reverse=True)
    total = sum(n for n, _ in colors)
    warm = []
    out = []
    for n, c in colors[:8]:
        h, s, l = to_hsl(*c)
        row = {"hex": hexc(c), "pct": round(n / total * 100, 1), "hsl": f"{h:.0f}/{s:.2f}/{l:.2f}"}
        out.append(row)
        if DEFAULT_CARD_HS[0] <= h <= DEFAULT_CARD_HS[1] and 0.3 < l < 0.98 and 0.1 < s < 0.9:
            warm.append((n, c))
    return out, warm

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--grid", action="store_true", help="按网格输出各块背景主色")
    ap.add_argument("--regions", default="3x6", help="网格: 3x6 → 横向6块")
    args = ap.parse_args()

    img = Image.open(args.image)
    print(f"文件: {args.image}  尺寸: {img.width}x{img.height}  mode={img.mode}")

    print("\n== 整体主色调（按像素占比）==")
    small = img.convert("RGB").resize((max(1, img.width // 4), max(1, img.height // 4)), Image.LANCZOS)
    cnt = Counter(small.getdata())
    total = sum(cnt.values())
    for c, n in cnt.most_common(10):
        h, s, l = to_hsl(*c)
        print(f"  {hexc(c)}  pct={n/total*100:5.1f}%  HSL=({h:.0f},{s:.2f},{l:.2f})")

    if args.grid:
        r, c = map(int, args.regions.split("x"))
        cw, ch = img.width // c, img.height // r
        print(f"\n== 网格 {r}x{c} 每块背景主色 ==")
        Aug = img.convert("RGB")
        for i in range(r):
            line = []
            for j in range(c):
                box = (j * cw, i * ch, min((j + 1) * cw, img.width), min((i + 1) * ch, img.height))
                sub = Aug.crop(box)
                col = sub.getcolors(maxcolors=sub.width * sub.height)
                col.sort(reverse=True)
                top = col[0][1]
                line.append(hexc(top))
            print("  " + "  ".join(line))

    # 检测“暖色卡片”所在区域并评估文字对比度
    print("\n== 暖色卡片检测（近琥珀/浅黄）==")
    Aug = img.convert("RGB")
    r, c = 6, 8
    cw, ch = Aug.width // c, Aug.height // r
    found = False
    for i in range(r):
        for j in range(c):
            box = (j * cw, i * ch, min((j + 1) * cw, Aug.width), min((i + 1) * ch, Aug.height))
            col = Aug.crop(box).getcolors(maxcolors=cw * ch)
            col.sort(reverse=True)
            covered = 0
            for n, px in col[:4]:
                hasl = to_hsl(*px)
                if DEFAULT_CARD_HS[0] <= hasl[0] <= DEFAULT_CARD_HS[1] and 0.55 < hasl[2] < 0.98 and 0.15 < hasl[1] < 0.8:
                    covered += n
            if covered / (cw * ch) > 0.12:
                dark = (31, 47, 70)
                used = col[0][1]
                print(f"  块[{i},{j}] 背景≈{hexc(used)} 暖色覆盖{covered/(cw*ch)*100:.0f}%  "
                      f"对比度(vs 文字#1f2d46)={contrast(used, (31,45,70)):.2f}:1")
                found = True
    if not found:
        print("  未检测到明显暖黄卡片区域（可能截取的不是通知区，或已是深色主题）。")

if __name__ == "__main__":
    main()