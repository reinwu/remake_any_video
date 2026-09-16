#!/usr/bin/env python3
"""参考图去字：把烧录字幕与常驻标题从参考帧上抹掉，且**不留会被模型照抄的痕迹**。

## 为什么不能只"模糊掉"

实测过三代失败做法（都在真实复刻里翻过车）：

| 做法 | 结果 |
|---|---|
| **整条高斯模糊带**（把字幕带整幅宽糊掉） | 文字是没了，但**模型把那条模糊带照抄进了生成画面** —— 成片里字幕那一行出现一条糊带，叠加后期字幕非常难看 |
| **逐像素笔画修补** | 没有带子了，但**留下清晰的字形深色残影** —— 描边比掩膜膨胀量大，没盖全 |
| **笔画检测 → 紧致矩形**（但按颜色写死） | 只对"白字 + 深描边"有效 —— **黄字/金字/无描边/深字亮边全部静默漏检** |

**最终做法：颜色与字体无关的检测 + inpaint**

1. **检测**（`--detect contrast`，默认）：
   - **局部对比度**：形态学 top-hat（比周围**亮**的小结构）∪ black-hat（比周围**暗**的小结构）
     —— **不管字是亮是暗、什么颜色，只要与局部背景有对比就能抓到**
   - **字形几何筛选**：小连通块 + 文字式的宽高比与填充率
   - **成行聚合**：同高、横向排布、且一行内至少 2 块（单块多半是场景细节）
   - **常驻标题**：仍是**固定框**（静止元素，位置固定）—— 有些标题是"浅色字 + 白描边"，
     对比度极低，靠检测不可靠
2. **修补**：`cv2.inpaint(TELEA)` —— 填充是**周围内容的自然延续**，不是"一块糊"，
   模型没有可照抄的异常纹理

实测（一条 2560×1440 的片，12 张参考图）：标题区边缘能量从源片 600~780 降到 **5~12**
（保留自然纹理但无文字结构；纯模糊做法是 ~1.6，那是"糊成一片"），笔画复查残留 **0**。

### 为什么不用颜色阈值

用"亮且低饱和 + 邻接深色"这类**颜色特征**写过一版，实测**只对白字深描边有效**：

| 字幕样式 | 检出 |
|---|---|
| 白字 + 深描边 | ✅ |
| **黄字 + 深描边**（短剧最常见） | ❌ **静默返回 0** |
| **金色渐变字** | ❌ **静默返回 0** |
| 白字无描边 / 深字亮描边 | ⚠️ 侥幸通过（检出的矩形并不在文字上） |

**静默漏检最危险**：返回 0 处，看起来像"这张图本来就没字"，字就这么留进参考图了。

## 用法

```bash
# 1) 先量准标题与字幕带的范围（用坐标网格放大图目视量，不要凭感觉估）
python detext_references.py --measure

# 2) 探查：看它检出了什么（会存一张可视化图，务必看一眼）
python detext_references.py --image ./资产库/分镜A.png --probe \
    --title 60,25,1060,175 --sub-band 0,1150,2560,1390

# 3) 批量去字并自检
python detext_references.py --dir ./资产库 --verify --backup \
    --title 60,25,1060,175 --sub-band 0,1150,2560,1390
```

坐标基于**原片分辨率**（脚本按图片实际尺寸等比换算）。

## 注意

- **必须先量准范围**（`--measure` 有指引）。实测凭感觉估的标题框只盖住标题一半，
  资产里仍残留"…去吃饭："的残字
- **务必用 `--probe` 看一眼检出结果**：字幕带里若有大量场景细节（菜盘、热气、栏杆），
  偶有误检是正常的（多抹一点不影响），但**漏检必须发现**
- 文字样式极特殊（发光、半透明、艺术字）时，用 `--sub-band` 缩小范围，
  或直接改用 `--detect box`（整框 inpaint）
- **Windows 中文路径**：`cv2.imread/imwrite` 读不了非 ASCII 路径（窄字符 API），
  本脚本内部用 `imdecode/imencode` 绕过
- 被抹掉的文字**后期要用同一个 `.ass` 加回来**（字幕 + 标题两个样式）
"""

from __future__ import annotations

import argparse
import glob
import os
import subprocess
import sys

try:
    import cv2
    import numpy as np
except ImportError:
    print("需要 opencv-python 与 numpy：pip install opencv-python numpy", file=sys.stderr)
    sys.exit(1)

DEF_TITLE = (60, 25, 1060, 175)       # 基于 2560x1440
# 检测用的字幕带：**给足够余量**（对比度算子需要比字更大的核，带太窄会漏检）
# 但**掩膜会被纵向夹到文字高度**（见 glyph_lines），所以带子宽松不会导致糊一片
DEF_SUB_BAND = (0, 1150, 2560, 1390)
REF_W, REF_H = 2560, 1440


def imread_u(path):
    """cv2.imread 在 Windows 上读不了中文路径，用 imdecode 绕过。"""
    data = np.fromfile(path, dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def imwrite_u(path, img):
    ok, buf = cv2.imencode(os.path.splitext(path)[1], img)
    if ok:
        buf.tofile(path)
    return ok


def _scale(shape, box):
    H, W = shape[:2]
    sx, sy = W / REF_W, H / REF_H
    return (max(0, int(box[0] * sx)), max(0, int(box[1] * sy)),
            min(W, int(box[2] * sx)), min(H, int(box[3] * sy)))


def contrast_mask(crop, ksize=None):
    """颜色无关的"小结构"掩膜：比周围亮(top-hat) ∪ 比周围暗(black-hat)。

    字无论什么颜色、有没有描边，只要与局部背景有对比就会被抓到。
    ksize 取比笔画宽度大一些（笔画通常 3~10px，取 ~25 覆盖整字高度）。
    """
    g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    h, w = g.shape[:2]
    if ksize is None:
        ksize = max(15, int(min(h, w) * 0.12) | 1)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (ksize, ksize))
    tophat = cv2.morphologyEx(g, cv2.MORPH_TOPHAT, k)      # 亮的小结构
    blackhat = cv2.morphologyEx(g, cv2.MORPH_BLACKHAT, k)  # 暗的小结构
    m = cv2.max(tophat, blackhat)
    # 自适应阈值：只看显著高于本区域噪声的部分
    thr = max(18, int(np.percentile(m, 97) * 0.45))
    return (m >= thr).astype(np.uint8)


def glyph_lines(crop, min_blocks=2, frame_h=None):
    """把对比度掩膜里"像文字"的连通块按行聚起来，返回各行在 crop 内的矩形。

    **关键：必须收紧到文字行的实际高度。**
    实测教训：把整条字幕带（240px 高）交给检测器，它会把菜盘/热气这类场景细节也算成候选，
    于是 inpaint 出一大片糊区 —— 那等于把"整条模糊带会被模型照抄"的老问题重新造出来。
    字幕本身只有 ~85px 高（约占画面高 6%），所以这里：
      · 丢掉高度超出合理范围的行（场景结构）
      · 把保留行的矩形**纵向夹到该行连通块的中位高度 × 1.8**（含描边余量）
    """
    m = contrast_mask(crop)
    if m.sum() == 0:
        return []
    k = np.ones((3, 3), np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, k, iterations=2)
    n, lab, stats, cent = cv2.connectedComponentsWithStats(m, 8)
    h, w = crop.shape[:2]
    boxes = []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if area < 6 or bh < 4 or bw < 2:
            continue
        if bh > h * 0.8 or bw > w * 0.6:          # 太大：多半是场景结构
            continue
        fill = area / float(bw * bh)
        if fill < 0.08 or fill > 0.95:            # 太细碎或太实心
            continue
        boxes.append((x, y, bw, bh))
    if not boxes:
        return []
    # 按高度聚类成行
    boxes.sort(key=lambda b: b[1])
    med_h = float(np.median([b[3] for b in boxes]))
    rows, cur = [], [boxes[0]]
    for b in boxes[1:]:
        if abs((b[1] + b[3] / 2) - (cur[-1][1] + cur[-1][3] / 2)) <= max(med_h * 0.9, 6):
            cur.append(b)
        else:
            rows.append(cur)
            cur = [b]
    rows.append(cur)
    out = []
    H_c = crop.shape[0]
    for r in rows:
        if len(r) < min_blocks:                   # 单块多半是场景细节
            continue
        hs = [b[3] for b in r]
        row_h = float(np.median(hs))
        if row_h < 3:
            continue
        x0 = min(b[0] for b in r); x1 = max(b[0] + b[2] for b in r)
        cy = float(np.median([b[1] + b[3] / 2.0 for b in r]))
        # ★ 纵向夹到"连通块中位高度 × 1.8"（含描边余量），而不是取整行包围盒 ——
        #   整行包围盒会把该行里混进来的场景细节也算上，inpaint 出一大片糊区，
        #   那等于把「整条模糊带会被模型照抄」的老问题重新造出来。
        half = row_h * 0.9
        y0 = int(max(0, cy - half)); y1 = int(min(H_c, cy + half))
        if (x1 - x0) < row_h * 1.2:               # 横向太窄，不像一行字
            continue
        out.append((x0, y0, x1, y1))
    return out


def color_rects(crop, min_blocks=2):
    """颜色法：假设"亮字 + 深色描边"。**最精准**，但只适用于这一种样式。

    实测：对"白字+深描边"很准；对黄字/金字会**静默漏检**（返回 0，看起来像图里本来没字）。
    所以它适合**已知样式**的场景（例如本片就是白字深描边）。
    """
    mn = crop.min(axis=2).astype(np.int16)
    mx = crop.max(axis=2).astype(np.int16)
    bright = ((mn > 160) & ((mx - mn) < 70)).astype(np.uint8)
    dark = (mx < 120).astype(np.uint8)
    k = np.ones((3, 3), np.uint8)
    cand = bright & cv2.dilate(dark, k, iterations=3)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(cand, 8)
    keep = np.zeros_like(cand)
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] >= 3:
            keep[lab == i] = 1
    if keep.sum() == 0:
        return []
    keep = cv2.dilate(keep, k, iterations=4)
    rp = keep.sum(axis=1)
    lines, cur = [], None
    for r in range(len(rp)):
        if rp[r] > 0:
            cur = [r, r] if cur is None else [cur[0], r]
        else:
            if cur is not None and cur[1] - cur[0] >= 4:
                lines.append(cur)
            cur = None
    if cur is not None and cur[1] - cur[0] >= 4:
        lines.append(cur)
    out = []
    for r0, r1 in lines:
        c2 = np.where(keep[r0:r1 + 1].sum(axis=0) > 0)[0]
        if len(c2):
            out.append((int(c2.min()), r0, int(c2.max()), r1))
    return out


def text_rects(img, band, pad=14, mode="contrast", min_blocks=2):
    """在 band 内检测文字，返回**紧致矩形**列表（含边距）。

    mode:
      contrast —— **颜色/字体无关**（局部对比度 + 字形几何 + 成行聚合）。
                  通用性好，但会连带检出场景细节 → 可能过度遮罩，
                  而**过度遮罩会把"整条模糊带被模型照抄"的老问题造回来**。
      color    —— 颜色法（亮字 + 深描边）。**最精准、不留痕**，但只对那一种样式有效，
                  换成黄字/金字会静默漏检。
      box      —— 整框 inpaint（样式极特殊时的兜底）。
    """
    x0, y0, x1, y1 = _scale(img.shape, band)
    if x1 <= x0 or y1 <= y0:
        return []
    crop = img[y0:y1, x0:x1]
    if mode == "box":
        rects = [(0, 0, crop.shape[1], crop.shape[0])]
    elif mode == "color":
        rects = color_rects(crop, min_blocks)
    else:
        rects = glyph_lines(crop, min_blocks, frame_h=img.shape[0])
    return [(x0 + rx0 - pad, y0 + ry0 - pad, x0 + rx1 + pad, y0 + ry1 + pad)
            for (rx0, ry0, rx1, ry1) in rects]


def detect_and_inpaint(img, band, radius=6, mode="contrast", pad=14, min_blocks=2):
    H, W = img.shape[:2]
    info = []
    for (rx0, ry0, rx1, ry1) in text_rects(img, band, pad, mode, min_blocks):
        rx0, ry0 = max(0, rx0), max(0, ry0)
        rx1, ry1 = min(W, rx1), min(H, ry1)
        if rx1 - rx0 < 4 or ry1 - ry0 < 4:
            continue
        m = np.zeros((H, W), np.uint8)
        m[ry0:ry1, rx0:rx1] = 255
        img = cv2.inpaint(img, m, radius, cv2.INPAINT_TELEA)
        info.append((rx0, ry0, rx1 - rx0, ry1 - ry0))
    return img, info


def detext(path, title, sub_band, radius=6, backup=False, mode="contrast",
           title_mode="box", min_blocks=2):
    img = imread_u(path)
    if img is None:
        return None
    H, W = img.shape[:2]
    info = []
    # ① 常驻标题
    if title:
        if title_mode == "box":
            tx0, ty0, tx1, ty1 = _scale(img.shape, title)
            if tx1 - tx0 > 4 and ty1 - ty0 > 4:
                m = np.zeros((H, W), np.uint8)
                m[ty0:ty1, tx0:tx1] = 255
                img = cv2.inpaint(img, m, radius, cv2.INPAINT_TELEA)
                info.append(("title", tx0, ty0, tx1 - tx0, ty1 - ty0))
        else:
            img, rs = detect_and_inpaint(img, title, radius, mode, 10, max(2, min_blocks))
            info += [("title",) + r for r in rs]
    # ② 字幕带
    img, rs = detect_and_inpaint(img, sub_band, radius, mode, 14, min_blocks)
    info += [("sub",) + r for r in rs]
    if backup:
        open(path + ".bak", "wb").write(np.fromfile(path, dtype=np.uint8).tobytes())
    imwrite_u(path, img)
    return info


def probe(path, title, sub_band, out_png=None, mode="contrast", min_blocks=2):
    """把检出结果画出来，便于人工确认（**务必看一眼**）。"""
    img = imread_u(path)
    if img is None:
        return None
    vis = img.copy()
    rects = text_rects(img, sub_band, 14, mode, min_blocks)
    for (a, b, c, d) in rects:
        cv2.rectangle(vis, (a, b), (c, d), (0, 0, 255), 3)
    if title:
        tx0, ty0, tx1, ty1 = _scale(img.shape, title)
        cv2.rectangle(vis, (tx0, ty0), (tx1, ty1), (255, 0, 0), 3)
    if out_png:
        imwrite_u(out_png, vis)
    return rects


def measure():
    print("量范围的方法（不要凭感觉估）：")
    print()
    print("  1) 裁一块带坐标网格的放大图，目视读数：")
    print("     ffmpeg -y -ss <任意时刻> -i src.mp4 -frames:v 1 \\")
    print("       -vf \"crop=1400:240:0:0,scale=1400:240\" title.png")
    print("  2) 在图上画网格（每 100px 一条线 + 坐标数字），保存后自己看图读数")
    print()
    print("  · 标题：读数取**并集**，再向外留 15~20px 边距")
    print("  · 字幕带：可用 extract_subtitles.py 自动定位的带作起点，但**横向按全宽**")
    print()
    print("  实测教训：凭感觉估的标题框只盖住标题一半，资产里仍残留「…去吃饭：」的残字。")


def main() -> int:
    ap = argparse.ArgumentParser(description="参考图去字（颜色/字体无关）")
    ap.add_argument("--image")
    ap.add_argument("--dir")
    ap.add_argument("--measure", action="store_true")
    ap.add_argument("--probe", action="store_true", help="只画检出框、不改图（务必看一眼）")
    ap.add_argument("--probe-out", help="probe 可视化输出路径")
    ap.add_argument("--detect", choices=["contrast", "color", "box"], default="contrast",
                    help="contrast=颜色/字体无关（通用，但可能过度遮罩）；color=亮字+深描边专用（最精准）；box=整框 inpaint")
    ap.add_argument("--title-mode", choices=["box", "detect"], default="box",
                    help="标题处理方式，默认 box（固定框，最稳）")
    ap.add_argument("--min-blocks", type=int, default=2,
                    help="一行至少几个连通块才算文字，默认 2")
    ap.add_argument("--title", default="%d,%d,%d,%d" % DEF_TITLE)
    ap.add_argument("--sub-band", default="%d,%d,%d,%d" % DEF_SUB_BAND)
    ap.add_argument("--radius", type=int, default=6)
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--backup", action="store_true")
    args = ap.parse_args()

    if args.measure:
        measure()
        return 0

    def parse_box(s):
        if not s or s.lower() in ("none", "no", ""):
            return None
        v = [int(x) for x in s.split(",")]
        if len(v) != 4:
            raise ValueError("框要写成 x0,y0,x1,y1")
        return tuple(v)

    title = parse_box(args.title)
    sub_band = parse_box(args.sub_band)

    if args.probe:
        targets = [args.image] if args.image else []
        if args.dir:
            for ext in ("*.png", "*.jpg", "*.jpeg"):
                targets += sorted(glob.glob(os.path.join(args.dir, ext)))
        for p in targets[:6]:
            out = args.probe_out or (p + ".probe.png")
            r = probe(p, title, sub_band, out, args.detect, args.min_blocks)
            print("  %-24s 检出 %d 行 %s" % (os.path.basename(p), len(r or []),
                                            "-> " + out))
            for (a, b, c, d) in (r or []):
                print("      矩形 x=%d y=%d w=%d h=%d" % (a, b, c - a, d - b))
        print("\n**请打开可视化图看一眼**：红框=字幕检出，蓝框=标题框；漏检必须发现。")
        return 0

    targets = [args.image] if args.image else []
    if args.dir:
        for ext in ("*.png", "*.jpg", "*.jpeg", "*.webp"):
            targets += sorted(glob.glob(os.path.join(args.dir, ext)))
    if not targets:
        ap.print_help()
        return 1

    print("检测方式 : %s   标题处理: %s   一行最少块数: %d"
          % (args.detect, args.title_mode, args.min_blocks))
    print("标题框   : %s" % (title,))
    print("字幕带   : %s" % (sub_band,))
    print("处理 %d 个文件\n" % len(targets))
    for p in targets:
        info = detext(p, title, sub_band, args.radius, args.backup,
                      args.detect, args.title_mode, args.min_blocks)
        if info is None:
            print("  ❌ 读不了：%s" % p)
            continue
        subs = [i for i in info if i[0] == "sub"]
        line = "  %-24s 标题 %d 处  字幕 %d 行" % (os.path.basename(p),
                                                 len(info) - len(subs), len(subs))
        if args.verify:
            r = text_rects(imread_u(p), sub_band, 14, args.detect, args.min_blocks)
            line += "   复查残留 %d %s" % (len(r), "✅" if not r else "⚠ 再跑一次或改 box 模式")
        print(line)

    print("\n下一步：把抹掉的文字在后期用**同一个 .ass** 加回来（字幕 + 左上角标题两个样式）。")
    print("注意：模型对'参考图里没有文字'是软约束——**试跑段要专门抽查画面里有没有乱码文字**。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
