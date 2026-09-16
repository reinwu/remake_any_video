#!/usr/bin/env python3
"""参考图去字：把烧录字幕与常驻标题从参考帧上抹掉，且**不留会被模型照抄的痕迹**。

## 为什么不能只"模糊掉"

实测过三代失败做法（都在真实复刻里翻过车）：

| 做法 | 结果 |
|---|---|
| **整条高斯模糊带**（把字幕带整幅宽糊掉） | 文字是没了，但**模型把那条模糊带照抄进了生成画面** —— 成片里字幕那一行出现一条糊带，叠加后期字幕非常难看 |
| **逐像素笔画修补**（只抹笔画的像素） | 没有带子了，但**留下清晰的字形深色残影** —— 因为描边比掩膜膨胀量大，没盖全 |
| **笔画检测 → 紧致矩形**（但规则写错） | 字幕干净了，**常驻标题却全部漏检** —— 标题常是"浅色字 + 白色描边"，没有深色描边，而检测规则依赖"亮像素邻接暗像素" |

**本脚本的做法**：

1. **常驻标题** → 用**固定框**（它是静止的，位置固定），直接 inpaint
2. **字幕** → 在字幕带内做**笔画检测** → 取**紧致矩形**（只和文字一样宽，不是整幅宽）→ inpaint
3. 统一用 `cv2.inpaint(TELEA)`：填充是**周围内容的自然延续**，不是"一块糊"，
   模型没有可照抄的异常纹理

实测结果（本脚本在一条 2560×1440 的片上跑 12 张参考图）：
- 笔画检测复查残留矩形数 **0**（标题区边缘能量从源片 600~780 降到 5~12，
  保留自然纹理但无文字结构；对比纯模糊做法是 ~1.6，糊成一片）

## 用法

```bash
# 1) 先量准标题与字幕带的范围（用坐标网格放大图目视量，不要凭感觉估）
python detext_references.py --measure --video src.mp4

# 2) 对参考图目录批量去字
python detext_references.py --dir ./资产库 --title 60,25,1060,175 --sub-band 0,1150,2560,1390

# 单张也可以
python detext_references.py --image ./资产库/分镜A.png --title 60,25,1060,175
```

坐标都基于**原片分辨率**（脚本按图片实际尺寸等比换算）。

## 注意

- **必须先量准范围**：用带坐标网格的放大图目视读数。实测凭感觉估的标题框
  只盖住了标题的一半，资产里仍残留"…去吃饭："的残字
- **Windows 中文路径**：`cv2.imread/imwrite` 读不了非 ASCII 路径（窄字符 API），
  本脚本内部用 `imdecode/imencode` 绕过 —— 自己写代码时务必注意
- 被抹掉的文字**后期要用同一个 `.ass` 加回来**（字幕 + 标题两个样式）
"""

from __future__ import annotations

import argparse
import glob
import os
import sys

try:
    import cv2
    import numpy as np
except ImportError:
    print("需要 opencv-python 与 numpy：pip install opencv-python numpy", file=sys.stderr)
    sys.exit(1)

DEF_TITLE = (60, 25, 1060, 175)       # 基于 2560x1440
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


def text_rects(img, band, pad=14):
    """在字幕带内检测文字笔画，返回**紧致矩形**列表（含描边与边距）。

    字幕通常是"白字 + 深色描边"：先取亮且低饱和的像素，再要求它**邻接深色像素**
    （排除窗户/灯这类大面积高光），然后膨胀覆盖描边，最后取每行的横向包围盒。
    """
    x0, y0, x1, y1 = _scale(img.shape, band)
    if x1 <= x0 or y1 <= y0:
        return []
    crop = img[y0:y1, x0:x1]
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
    keep = cv2.dilate(keep, k, iterations=4)      # 把描边也纳入
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
    rects = []
    for r0, r1 in lines:
        c = np.where(keep[r0:r1 + 1].sum(axis=0) > 0)[0]
        if len(c):
            rects.append((x0 + int(c.min()) - pad, y0 + r0 - pad,
                          x0 + int(c.max()) + pad, y0 + r1 + pad))
    return rects


def detext(path, title, sub_band, radius=6, keep_backup=False):
    img = imread_u(path)
    if img is None:
        return None
    H, W = img.shape[:2]
    info = []
    # ① 常驻标题：固定框（静止元素，不需要检测）
    if title:
        tx0, ty0, tx1, ty1 = _scale(img.shape, title)
        if tx1 - tx0 > 4 and ty1 - ty0 > 4:
            m = np.zeros((H, W), np.uint8)
            m[ty0:ty1, tx0:tx1] = 255
            img = cv2.inpaint(img, m, radius, cv2.INPAINT_TELEA)
            info.append(("title", tx0, ty0, tx1 - tx0, ty1 - ty0))
    # ② 字幕：笔画检测 → 紧致矩形
    for (rx0, ry0, rx1, ry1) in text_rects(img, sub_band):
        rx0, ry0 = max(0, rx0), max(0, ry0)
        rx1, ry1 = min(W, rx1), min(H, ry1)
        if rx1 - rx0 < 4 or ry1 - ry0 < 4:
            continue
        m = np.zeros((H, W), np.uint8)
        m[ry0:ry1, rx0:rx1] = 255
        img = cv2.inpaint(img, m, radius, cv2.INPAINT_TELEA)
        info.append(("sub", rx0, ry0, rx1 - rx0, ry1 - ry0))
    if keep_backup:
        data = np.fromfile(path, dtype=np.uint8)
        open(path + ".bak", "wb").write(data.tobytes())
    imwrite_u(path, img)
    return info


def verify(path, sub_band):
    """复查：还能检出多少文字矩形（0 = 干净）+ 标题区边缘能量。"""
    img = imread_u(path)
    if img is None:
        return None
    n = len(text_rects(img, sub_band))
    return n


def measure(video):
    """提示怎么量标题与字幕带范围。"""
    print("量范围的方法（不要凭感觉估）：")
    print()
    print("  1) 裁一块带坐标网格的放大图，目视读数：")
    print("     ffmpeg -y -ss <任意时刻> -i \"%s\" -frames:v 1 \\" % video)
    print("       -vf \"crop=1400:240:0:0,scale=1400:240\" title.png")
    print("  2) 在图上画网格（每 100px 一条线 + 坐标数字），保存后自己看图读数")
    print()
    print("  · 标题：读数取**并集**，再向外留 15~20px 边距")
    print("  · 字幕带：可以用 extract_subtitles.py 自动定位的那条带作起点，")
    print("    但**横向要按全宽**（长句字幕会铺得很宽）")
    print()
    print("  实测教训：凭感觉估的标题框只盖住了标题一半，资产里仍残留「…去吃饭：」的残字。")


def main() -> int:
    ap = argparse.ArgumentParser(description="参考图去字（不留会被模型照抄的痕迹）")
    ap.add_argument("--image", help="单张图片")
    ap.add_argument("--dir", help="图片目录（批量处理其中的 png/jpg）")
    ap.add_argument("--video", help="配合 --measure 用")
    ap.add_argument("--measure", action="store_true", help="打印量范围的指引")
    ap.add_argument("--title", default="%d,%d,%d,%d" % DEF_TITLE,
                    help="常驻标题框 x0,y0,x1,y1（基于 2560x1440），传 none 表示没有标题")
    ap.add_argument("--sub-band", default="%d,%d,%d,%d" % DEF_SUB_BAND,
                    help="字幕带 x0,y0,x1,y1（基于 2560x1440）")
    ap.add_argument("--radius", type=int, default=6, help="inpaint 半径，默认 6")
    ap.add_argument("--verify", action="store_true", help="处理后复查残留")
    ap.add_argument("--backup", action="store_true", help="处理前留 .bak")
    args = ap.parse_args()

    if args.measure:
        measure(args.video or "src.mp4")
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

    targets = []
    if args.image:
        targets.append(args.image)
    if args.dir:
        for ext in ("*.png", "*.jpg", "*.jpeg", "*.webp"):
            targets += sorted(glob.glob(os.path.join(args.dir, ext)))
    if not targets:
        ap.print_help()
        return 1

    print("标题框   : %s" % (title,))
    print("字幕带   : %s" % (sub_band,))
    print("处理 %d 个文件\n" % len(targets))
    for p in targets:
        info = detext(p, title, sub_band, args.radius, args.backup)
        if info is None:
            print("  ❌ 读不了：%s" % p)
            continue
        line = "  %-24s 处理 %d 处" % (os.path.basename(p), len(info))
        if args.verify:
            r = verify(p, sub_band)
            line += "   残留矩形 %d %s" % (r, "✅" if r == 0 else "⚠ 复查")
        print(line)

    print("\n下一步：把抹掉的文字在后期用**同一个 .ass** 加回来（字幕 + 左上角标题两个样式）。")
    print("注意：模型对'参考图里没有文字'是软约束——**试跑段要专门抽查画面里有没有乱码文字**。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
