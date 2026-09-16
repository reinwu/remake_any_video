#!/usr/bin/env python3
"""按镜头抽多帧，让"运动"变得可见。

为什么需要这个：单张关键帧只能说明"这一帧长什么样"，说明不了"发生了什么"。
动作、因果关系、说话人（口型）都存在于**帧与帧之间**。只对着一张静帧写提示词，
必然丢失动作弧，也必然猜错说话人。

本脚本产出两类素材：

1. **镜头动作条**（`strips/shot_NNN.png`）：每个镜头横向排 N 帧（起/中/末…），
   一眼看出主体怎么移动、朝向怎么变化、镜头有没有推拉。
2. **对白条**（`dialogue/dlg_NNN.png`）：每条字幕的时间段内抽几帧，
   用来判断**谁在说话**——说话者的嘴部在两帧之间会有明显形变，听者没有。

用法：
  # 按分段表/镜头表抽镜头动作条
  python extract_shot_frames.py --video in.mp4 --plan plan.json --out ./shots --per-shot 4

  # 只看起止时间列表（无 plan 时）
  python extract_shot_frames.py --video in.mp4 --shots 0,1.12,2.48,3.78 \\
         --duration 92.88 --out ./shots --per-shot 4

  # 额外抽对白条（判断说话人）
  python extract_shot_frames.py --video in.mp4 --plan plan.json --out ./shots \\
         --dialogue ./subs/timing.csv --dialogue-frames 4
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("需要 Pillow：pip install pillow", file=sys.stderr)
    sys.exit(1)

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf",
    "/System/Library/Fonts/PingFang.ttc", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def which(name):
    return shutil.which(name) or name


def probe_duration(video):
    p = subprocess.run([which("ffprobe"), "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", video], capture_output=True, text=True)
    return float(p.stdout.strip())


def probe_size(video):
    p = subprocess.run([which("ffprobe"), "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=width,height", "-of", "csv=p=0", video],
                       capture_output=True, text=True)
    w, h = p.stdout.strip().split(",")[:2]
    return int(w), int(h)


def grab(video, t, out, width=420):
    """抽一帧。请求时间超出视频长度时 ffmpeg 返回成功但不写文件——
    所以这里以「文件是否真的生成」为准，失败返回 None，由调用方跳过。"""
    subprocess.run([which("ffmpeg"), "-y", "-v", "error", "-ss", f"{max(0.0, t):.3f}",
                    "-i", video, "-frames:v", "1", "-vf", f"scale={width}:-2", out], check=False)
    return out if os.path.exists(out) and os.path.getsize(out) > 0 else None


def load_font(size=22):
    for c in FONT_CANDIDATES:
        if os.path.exists(c):
            try:
                return ImageFont.truetype(c, size)
            except Exception:
                pass
    return ImageFont.load_default()


def make_strip(paths, labels, out_path, font=None, pad=4, label_h=30):
    """横向拼一条，每格下方标注时间码。缺失的帧（抽帧越界等）直接跳过。"""
    pairs = [(p, l) for p, l in zip(paths, labels) if p and os.path.exists(p)]
    if not pairs:
        return False
    paths = [p for p, _ in pairs]
    labels = [l for _, l in pairs]
    imgs = [Image.open(p).convert("RGB") for p in paths]
    if not imgs:
        return False
    w = max(i.width for i in imgs)
    h = max(i.height for i in imgs)
    W = (w + pad) * len(imgs) + pad
    H = h + label_h + pad * 2
    canvas = Image.new("RGB", (W, H), (20, 20, 20))
    d = ImageDraw.Draw(canvas)
    x = pad
    for im, lab in zip(imgs, labels):
        canvas.paste(im, (x, pad))
        d.text((x + 4, pad + h + 4), lab, fill=(120, 220, 255), font=font)
        x += w + pad
    canvas.save(out_path)
    return True


def frames_for_shot(a, b, n):
    """在 [a,b] 内取 n 个时刻，避开首尾 8% 以躲开切点瞬间。"""
    span = max(0.0, b - a)
    if span <= 0.05 or n <= 1:
        return [a]
    lo, hi = a + span * 0.08, b - span * 0.08
    if n == 1:
        return [(lo + hi) / 2]
    step = (hi - lo) / (n - 1)
    return [lo + step * i for i in range(n)]


def main() -> int:
    ap = argparse.ArgumentParser(description="按镜头抽多帧，让运动可见")
    ap.add_argument("--video", required=True)
    ap.add_argument("--plan", help="plan_segments.py 产出的分段表（含 shots 与 segments_detail）")
    ap.add_argument("--shots", help="逗号分隔的切点秒数（无 plan 时使用）")
    ap.add_argument("--duration", type=float, help="视频总时长（用 --shots 时必填）")
    ap.add_argument("--out", required=True)
    ap.add_argument("--per-shot", type=int, default=4, help="每个镜头抽几帧，默认 4")
    ap.add_argument("--width", type=int, default=420, help="每帧宽度，默认 420")
    ap.add_argument("--dialogue", help="字幕/台词表（timing.csv 或 timing.json），用于抽对白条")
    ap.add_argument("--dialogue-frames", type=int, default=4, help="每条对白抽几帧，默认 4")
    args = ap.parse_args()

    if not os.path.exists(args.video):
        print("video not found: %s" % args.video, file=sys.stderr)
        return 1
    dur = probe_duration(args.video)
    W, H = probe_size(args.video)
    os.makedirs(args.out, exist_ok=True)
    tmp = os.path.join(args.out, ".tmp")
    os.makedirs(tmp, exist_ok=True)
    strips = os.path.join(args.out, "strips")
    os.makedirs(strips, exist_ok=True)
    font = load_font(22)

    # ---- 镜头列表 ----
    shots = []
    if args.plan and os.path.exists(args.plan):
        plan = json.load(open(args.plan, encoding="utf-8"))
        if plan.get("shots"):
            shots = [(s["idx"], s["start"], s["end"]) for s in plan["shots"]]
        elif plan.get("segments_detail"):
            shots = [(s["index"], s["source_start"], s["source_end"])
                     for s in plan["segments_detail"]]
        segmap = {}
        for s in plan.get("segments_detail", []):
            for sh in s.get("source_shots", []):
                segmap[sh] = s["index"]
        print("plan: %d 个镜头" % len(shots))
    elif args.shots:
        if not args.duration:
            print("--duration is required with --shots", file=sys.stderr)
            return 1
        cuts = [float(x) for x in re.findall(r"[0-9.]+", args.shots)]
        bounds = [0.0] + sorted(c for c in cuts if 0 < c < args.duration) + [args.duration]
        shots = [(i + 1, bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)]
        segmap = {}
        print("shots: %d 个镜头" % len(shots))
    else:
        ap.print_help()
        return 1

    print("video: %dx%d %.2fs\n" % (W, H, dur))

    manifest = []
    for idx, a, b in shots:
        ts = frames_for_shot(a, b, args.per_shot)
        paths, labels = [], []
        for k, t in enumerate(ts):
            p = os.path.join(tmp, f"s{idx:03d}_{k}.png")
            grab(args.video, t, p, args.width)
            paths.append(p)
            labels.append(f"#{idx}  {t:.2f}s")
        strip = os.path.join(strips, f"shot_{idx:03d}.png")
        make_strip(paths, labels, strip, font)
        manifest.append({"shot": idx, "start": round(a, 3), "end": round(b, 3),
                         "duration": round(b - a, 3),
                         "segment": segmap.get(idx),
                         "frame_times": [round(t, 3) for t in ts],
                         "strip": os.path.relpath(strip, args.out).replace("\\", "/")})
    print("镜头动作条 %d 条 -> %s" % (len(manifest), strips))

    # ---- 对白条：看口型判断说话人 ----
    dlg_manifest = []
    if args.dialogue and os.path.exists(args.dialogue):
        subs = []
        try:
            if args.dialogue.endswith(".json"):
                subs = json.load(open(args.dialogue, encoding="utf-8")).get("lines", [])
            else:
                with open(args.dialogue, encoding="utf-8-sig") as fh:
                    subs = list(csv.DictReader(fh))
        except Exception as exc:
            print("字幕表读取失败：%s" % exc, file=sys.stderr)
        ddir = os.path.join(args.out, "dialogue")
        os.makedirs(ddir, exist_ok=True)
        for n, s in enumerate(subs, 1):
            try:
                a = float(s.get("start", s.get("start_sec")))
                b = float(s.get("end", s.get("end_sec")))
            except Exception:
                continue
            txt = str(s.get("text", "") or "").strip()
            ts = frames_for_shot(a, b, args.dialogue_frames)
            paths, labels = [], []
            for k, t in enumerate(ts):
                p = os.path.join(tmp, f"d{n:03d}_{k}.png")
                if not grab(args.video, t, p, args.width):
                    print("  跳过越界帧：对白 #%d @ %.2fs（视频长 %.2fs）" % (n, t, dur), file=sys.stderr)
                    continue
                paths.append(p)
                labels.append(f"#{n}  {t:.2f}s")
            strip = os.path.join(ddir, f"dlg_{n:03d}.png")
            make_strip(paths, labels, strip, font)
            dlg_manifest.append({"index": n, "start": round(a, 3), "end": round(b, 3),
                                 "text": txt, "strip": os.path.relpath(strip, args.out).replace("\\", "/")})
        print("对白条 %d 条 -> %s（看图判断谁在说话）" % (len(dlg_manifest), ddir))
        print("  判断依据：说话者的嘴部在两帧之间有明显开合形变，听者没有；")
        print("  再结合语义（谁在回答谁）交叉验证。")

    json.dump({"video": args.video, "size": f"{W}x{H}", "duration": round(dur, 3),
               "per_shot": args.per_shot, "shots": manifest, "dialogue": dlg_manifest},
              open(os.path.join(args.out, "shot_frames.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    print()
    print("下一步：**逐条看 strips/ 里的镜头动作条**，按 references/keyframe_sequence_description.md")
    print("写「动作弧」——起止状态 + 中间运动 + 说话人。不要只对单帧写静态描述。")
    print("清单 -> %s" % os.path.join(args.out, "shot_frames.json"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
