#!/usr/bin/env python3
"""从视频里提取字幕的位置与时间码（用于后期重新叠加字幕）。

核心思路：同一句字幕横跨多帧，逐帧看毫无意义。本脚本：
  1. 先用高通滤波 + 变异系数扫描，自动定位"字幕横带"所在位置
  2. 在该带上按固定间隔采样，用高通图比较相邻采样
  3. 用**自适应阈值**（均值 + k×标准差）把连续相同的采样归为一条字幕

为什么要自适应阈值：固定阈值在任何素材上都不好用。带字幕的视频里，相邻采样的差异
天然是双峰的——同一句内差异极小、换句时差异极大。取"均值 + k 倍标准差"正好切在两峰之间。

输出：
  <out>/lines/line_0001.png …   每条字幕的裁剪图（一张一句）
  <out>/contact_sheet.png       所有字幕拼成一张总览（一次看完）
  <out>/timing.json / timing.csv  每条字幕的起止时间（text 列待填）

用法：
  python extract_subtitles.py --video input.mp4 --out ./subs
  python extract_subtitles.py --video input.mp4 --out ./subs --band 1230:1330
  python extract_subtitles.py --video input.mp4 --out ./subs --k 1.0 --interval 0.4

依赖：ffmpeg/ffprobe 在 PATH；Python 侧需要 Pillow 与 numpy。
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys

try:
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter
except ImportError:
    print("需要 Pillow 与 numpy：pip install pillow numpy", file=sys.stderr)
    sys.exit(1)


# ------------------------------------------------------------------ 基础

def probe_size(video: str):
    ffprobe = shutil.which("ffprobe") or "ffprobe"
    proc = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0",
                           "-show_entries", "stream=width,height", "-of", "csv=p=0", video],
                          capture_output=True, text=True)
    w, h = proc.stdout.strip().split(",")[:2]
    return int(w), int(h)


def probe_duration(video: str) -> float:
    ffprobe = shutil.which("ffprobe") or "ffprobe"
    proc = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration",
                           "-of", "csv=p=0", video], capture_output=True, text=True)
    return float(proc.stdout.strip())


def has_subtitle_track(video: str) -> bool:
    ffprobe = shutil.which("ffprobe") or "ffprobe"
    proc = subprocess.run([ffprobe, "-v", "error", "-select_streams", "s",
                           "-show_entries", "stream=index", "-of", "csv=p=0", video],
                          capture_output=True, text=True)
    return bool(proc.stdout.strip())


def highpass(gray: np.ndarray, radius: int = 5) -> np.ndarray:
    """高通：减掉低频，压掉大块移动物体的整体明暗变化，保留文字这类高频边缘。"""
    im = Image.fromarray((np.clip(gray, 0, 1) * 255).astype(np.uint8))
    blur = im.filter(ImageFilter.GaussianBlur(radius))
    return np.abs(np.asarray(im, np.float32) - np.asarray(blur, np.float32)) / 255.0


def extract_band(video, w, y0, y1, interval, limit_s, outdir):
    """单次 ffmpeg 调用抽出该横带的所有采样帧。"""
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    if os.path.isdir(outdir):
        shutil.rmtree(outdir)
    os.makedirs(outdir, exist_ok=True)
    cmd = [ffmpeg, "-y", "-v", "error"]
    if limit_s:
        cmd += ["-t", str(limit_s)]
    cmd += ["-i", video, "-vf", f"fps=1/{interval},crop={w}:{y1 - y0}:0:{y0}",
            "-start_number", "0", os.path.join(outdir, "s%05d.png")]
    subprocess.run(cmd, check=False)
    return [os.path.join(outdir, f) for f in sorted(os.listdir(outdir)) if f.endswith(".png")]


def signatures(paths):
    """每帧的字幕带高通签名（降采样后），用于比较。"""
    sigs = []
    for p in paths:
        g = np.asarray(Image.open(p).convert("L"), np.float32) / 255.0
        hp = highpass(g)
        im = Image.fromarray((np.clip(hp * 3, 0, 1) * 255).astype(np.uint8))
        w, h = im.size
        tw = 256
        th = max(1, int(tw * h / max(1, w)))
        sigs.append(np.asarray(im.resize((tw, th)), np.float32) / 255.0)
    return sigs


def diff_series(sigs):
    out = []
    for i in range(len(sigs) - 1):
        a, b = sigs[i], sigs[i + 1]
        if a.shape != b.shape:
            out.append(np.nan)
            continue
        out.append(float(np.abs(a - b).mean()))
    return np.array(out, dtype=np.float64)


# ------------------------------------------------------------------ 字幕带自动定位

def detect_band(video, w, h, interval, probe_seconds=40.0, band_h=None, step=None,
                bottom_frac=0.30, tmpdir="."):
    """在画面下方区域扫描候选横带，选差异序列"变异系数"最高的那条。

    原理：字幕带上的相邻差异是双峰的（同句差异极低、换句差异很高），因此
    标准差/均值（变异系数）明显高于没有字幕的带（那里只有连续变化的背景运动）。

    band_h 默认取画面高度的 7.5%（典型字幕行高），步进取 band_h/6。
    **早期版本用固定的 100px 高 + 20px 步进，在字幕较矮的素材上会选到字幕上方的
    背景带**，于是把背景运动当成字幕，检出的"字幕"区间会跨镜头、把多条字幕并成一条。
    """
    if band_h is None:
        band_h = max(40, int(round(h * 0.075)))
    if step is None:
        step = max(8, band_h // 6)
    top = int(h * (1 - bottom_frac))
    best, best_cv, report = None, -1.0, []
    y = top
    while y + band_h <= h:
        d = extract_band(video, w, y, y + band_h, interval, probe_seconds,
                         os.path.join(tmpdir, "probe"))
        if len(d) >= 8:
            s = signatures(d)
            v = diff_series(s)
            v = v[~np.isnan(v)]
            if len(v) >= 6 and v.mean() > 1e-6:
                cv = float(v.std() / v.mean())
                report.append((y, y + band_h, float(v.mean()), cv))
                if cv > best_cv:
                    best_cv, best = cv, (y, y + band_h)
        y += step
    return best, best_cv, report


# ------------------------------------------------------------------ 分组

def group_lines(paths, sigs, interval, k=1.0, blank_ratio=0.35):
    """自适应阈值分组：阈值 = 差异序列的均值 + k×标准差。"""
    v = diff_series(sigs)
    valid = v[~np.isnan(v)]
    if len(valid) == 0:
        return [], 0.0
    thr = float(valid.mean() + k * valid.std())
    # 空白（无字幕）判定：高通能量明显低于整体中位数
    energies = np.array([float(s.mean()) for s in sigs])
    blank_thr = float(np.median(energies) * blank_ratio)

    groups, cur = [], None
    for i, s in enumerate(sigs):
        t = i * interval
        blank = bool(energies[i] < blank_thr)
        if cur is None:
            cur = {"start": t, "end": t, "i": i, "blank": blank, "path": paths[i],
                   "energy": float(energies[i])}
            continue
        same = (i - 1 < len(v) and not np.isnan(v[i - 1]) and v[i - 1] <= thr
                and blank == cur["blank"])
        if same:
            cur["end"] = t
        else:
            groups.append(cur)
            cur = {"start": t, "end": t, "i": i, "blank": blank, "path": paths[i],
                   "energy": float(energies[i])}
    if cur is not None:
        groups.append(cur)

    kept = [g for g in groups if not g["blank"]]
    if kept:
        # 再滤一遍：明显短于典型长度的碎片多半是误检
        durs = np.array([g["end"] - g["start"] + interval for g in kept])
        keep_thr = max(interval * 1.5, float(np.median(durs)) * 0.4)
        kept = [g for g, d in zip(kept, durs) if d >= keep_thr]
    return kept, thr


def build_contact_sheet(lines, out_path, pad=6, zoom=1):
    imgs = []
    for ln in lines:
        p = ln.get("path")
        if p and os.path.exists(p):
            im = Image.open(p).convert("RGB")
            if zoom and zoom != 1:
                im = im.resize((im.width * zoom, im.height * zoom), Image.LANCZOS)
            imgs.append(im)
    if not imgs:
        return False
    w = max(i.width for i in imgs)
    h = sum(i.height for i in imgs) + pad * (len(imgs) + 1)
    sheet = Image.new("RGB", (w + 2 * pad, h), (24, 24, 24))
    draw = ImageDraw.Draw(sheet)
    y = pad
    for n, im in enumerate(imgs, 1):
        sheet.paste(im, (pad, y))
        draw.text((pad + 4, y + 2), str(n), fill=(255, 80, 80))
        y += im.height + pad
    if h > 20000:                      # 太长就等比例缩小，保证能一次看完
        ratio = 20000 / h
        sheet = sheet.resize((max(1, int(sheet.width * ratio)), 20000))
    sheet.save(out_path)
    return True


def parse_band(s):
    a, _, b = s.partition(":")
    return int(a), int(b)


def main() -> int:
    ap = argparse.ArgumentParser(description="提取字幕的位置与时间码")
    ap.add_argument("--video", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--interval", type=float, default=0.5, help="采样间隔秒数，默认 0.5")
    ap.add_argument("--band", help="显式字幕横带 Y0:Y1（像素）。不给则自动定位")
    ap.add_argument("--band-height", type=int, default=100, help="自动定位时的横带高度，默认 100")
    ap.add_argument("--k", type=float, default=1.0, help="自适应阈值系数：均值 + k×标准差，默认 1.0")
    ap.add_argument("--zoom", type=int, default=2,
                    help="字幕裁剪图放大倍数，默认 2（缩略图上相邻重复字极易读漏，放大后逐字核对）")
    ap.add_argument("--probe-seconds", type=float, default=40.0, help="自动定位时的探测秒数")
    args = ap.parse_args()

    if not os.path.exists(args.video):
        print("video not found: %s" % args.video, file=sys.stderr)
        return 1
    if has_subtitle_track(args.video):
        print("注意：该视频含软字幕轨 —— 优先直接抽取字幕轨，比从画面提取精确得多：")
        print("  ffmpeg -y -i \"%s\" -map 0:s:0 subtitles.srt" % args.video)

    w, h = probe_size(args.video)
    duration = probe_duration(args.video)
    os.makedirs(args.out, exist_ok=True)
    tmpdir = os.path.join(args.out, ".tmp")
    os.makedirs(tmpdir, exist_ok=True)
    print("video: %dx%d  %.2fs" % (w, h, duration))

    if args.band:
        y0, y1 = parse_band(args.band)
        print("band : 显式指定 y%d-%d" % (y0, y1))
    else:
        print("band : 自动定位中（探测前 %.0fs）…" % args.probe_seconds)
        band, cv, report = detect_band(args.video, w, h, args.interval, args.probe_seconds,
                                       args.band_height, tmpdir=tmpdir)
        for y0_, y1_, mean_, cv_ in report[-6:]:
            print("        y%-5d-%-5d  差异均值 %.4f  变异系数 %.3f" % (y0_, y1_, mean_, cv_))
        if not band:
            print("无法自动定位字幕带。请用 --band Y0:Y1 手动指定（先抽一帧看字幕在哪个高度）")
            return 1
        y0, y1 = band
        print("band : 自动定位到 y%d-%d（变异系数 %.3f）" % (y0, y1, cv))

    paths = extract_band(args.video, w, y0, y1, args.interval, None, os.path.join(tmpdir, "band"))
    sigs = signatures(paths)
    lines, thr = group_lines(paths, sigs, args.interval, args.k)
    print("sampled: %d 帧 @ %.2fs   自适应阈值 %.4f" % (len(paths), args.interval, thr))
    print("字幕条数：%d" % len(lines))
    if len(lines) > duration / 1.5:
        print("  提示：条数仍偏多，可调小 --k（如 0.5）或收紧 --band 后重跑。")

    lines_dir = os.path.join(args.out, "lines")
    os.makedirs(lines_dir, exist_ok=True)
    records = []
    for n, ln in enumerate(lines, 1):
        dst = os.path.join(lines_dir, f"line_{n:04d}.png")
        if os.path.exists(ln["path"]):
            # 按 zoom 放大后再保存：缩略图上相邻重复字（如「不能不要」的两个「不」）
            # 极易读漏，而漏字后的句子往往仍然通顺，语感校验靠不住。
            if args.zoom and args.zoom != 1:
                im = Image.open(ln["path"])
                im = im.resize((im.width * args.zoom, im.height * args.zoom), Image.LANCZOS)
                im.save(dst)
            else:
                shutil.copy2(ln["path"], dst)
        records.append({
            "index": n,
            "start": round(ln["start"], 3),
            "end": round(ln["end"] + args.interval, 3),
            "band": [y0, y1],
            "zoom": args.zoom,
            "image": os.path.relpath(dst, args.out).replace("\\", "/"),
            "text": "",
        })

    sheet = os.path.join(args.out, "contact_sheet.png")
    ok = build_contact_sheet(lines, sheet, zoom=args.zoom)
    print("总览图：%s%s" % (sheet if ok else "(空)", ("（放大 %dx）" % args.zoom) if args.zoom != 1 else ""))

    with open(os.path.join(args.out, "timing.json"), "w", encoding="utf-8") as fh:
        json.dump({"video": args.video, "interval": args.interval, "band": [y0, y1],
                   "threshold": thr, "lines": records}, fh, ensure_ascii=False, indent=2)
    with open(os.path.join(args.out, "timing.csv"), "w", encoding="utf-8-sig", newline="") as fh:
        wr = csv.writer(fh)
        wr.writerow(["index", "start_sec", "end_sec", "text", "image"])
        for r in records:
            wr.writerow([r["index"], r["start"], r["end"], r["text"], r["image"]])

    print()
    print("下一步：")
    print("  1. 看 %s（或 lines/ 逐张），把文字填回 timing.csv 的 text 列" % os.path.relpath(sheet, args.out))
    print("  2. 时间码精度 ±%.1fs，交付前按成片微调首尾" % args.interval)
    print("  3. 用 references/subtitle_replication.md 里的 ASS 流程重新叠加")
    return 0


if __name__ == "__main__":
    sys.exit(main())
