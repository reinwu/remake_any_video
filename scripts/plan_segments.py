#!/usr/bin/env python3
"""把源片镜头列表规划成视频模型生成分段。

设计目标（严格按优先级）：
  1. 守住模型的单段时长约束（最短/最长）
  2. **尽可能一个源片镜头对应一个生成段**（合并越少越好）
  3. 过短的镜头才合并；过长的镜头才拆分
  4. 段的边界落在源片真实切点上，不用均匀切

取舍说明：合并决策只看"是否够长"，不为了凑时长档位去吞并后续镜头——
后者会让段数骤减、破坏"一镜一段"。档位选择在分组完成之后单独进行。

用法：
  python plan_segments.py --video input.mp4 --out plan.json
  python plan_segments.py --cuts 0,1.12,2.48 --duration 92.88 --out plan.json
  python plan_segments.py --video input.mp4 --policy keep-shots --out plan.json

参数：
  --policy keep-duration  保总长（默认）：短镜合并，段数尽量多
  --policy keep-shots     保镜头数：每镜一段，总长会被拉长
  --min-fill 0.85         段跨度达到最短时长的这个比例即可独立成段（避免为差 0.1 秒去合并）
  --setting-fit nearest   档位选择：nearest=最近档位（默认）/ up=不小于跨度的最小档位
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys

FPS = 24
MIN_SETTING = 4
MAX_SETTING = 15


def align_frame_count(n: int) -> int:
    while n % 17 != 5:
        n += 1
    return n


def actual_seconds(setting: float) -> float:
    return align_frame_count(max(5, round(setting * FPS))) / FPS


DUR_TABLE = {s: actual_seconds(s) for s in range(MIN_SETTING, MAX_SETTING + 1)}
MIN_ACTUAL = DUR_TABLE[MIN_SETTING]
MAX_ACTUAL = DUR_TABLE[MAX_SETTING]


def pick_setting(span: float, fit: str = "nearest") -> int:
    if fit == "up":
        cands = [s for s in DUR_TABLE if DUR_TABLE[s] >= span - 1e-9]
        return min(cands) if cands else MAX_SETTING
    return min(DUR_TABLE, key=lambda s: abs(DUR_TABLE[s] - span))


# ---------------------------------------------------------------- 切点

def detect_cuts(video: str, threshold: float = 0.35, min_gap: float = 0.4,
                probe_width: int = 640) -> list[float]:
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    cmd = [ffmpeg, "-hide_banner", "-i", video,
           "-vf", f"scale={probe_width}:-2,select='gt(scene,{threshold})',showinfo",
           "-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    raw = sorted({float(m.group(1)) for m in re.finditer(r"pts_time:([0-9.]+)", proc.stderr)})
    cuts: list[float] = []
    for c in raw:
        if not cuts or c - cuts[-1] > min_gap:
            cuts.append(c)
    return cuts


def probe_duration(video: str) -> float:
    ffprobe = shutil.which("ffprobe") or "ffprobe"
    proc = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration",
                           "-of", "csv=p=0", video], capture_output=True, text=True)
    return float(proc.stdout.strip())


def shots_from_cuts(cuts, duration) -> list[tuple[float, float]]:
    bounds = [0.0] + [c for c in cuts if 0 < c < duration] + [duration]
    return [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)]


# ---------------------------------------------------------------- 规划

def split_long(shots, max_actual=MAX_ACTUAL):
    """长于最大实际时长的镜头均分为多段（同场景延续，锚点各取子段起点）。"""
    out = []
    for i, (a, b) in enumerate(shots):
        d = b - a
        if d <= max_actual:
            out.append((a, b, [i + 1]))
            continue
        k = math.ceil(d / max_actual)
        step = d / k
        for j in range(k):
            out.append((a + j * step, a + (j + 1) * step, [i + 1]))
    return out


def group_pieces(pieces, min_actual=MIN_ACTUAL, max_actual=MAX_ACTUAL, min_fill=0.85):
    """按最小时长分组：跨度一达标就闭合，绝不为了凑档位继续吞并。"""
    threshold = min_actual * min_fill
    groups, cur = [], []
    for piece in pieces:
        cur.append(piece)
        if (cur[-1][1] - cur[0][0]) >= threshold:
            groups.append(cur)
            cur = []
    if cur:
        tail_span = cur[-1][1] - cur[0][0]
        if groups:
            merged_span = cur[-1][1] - groups[-1][0][0]
            if tail_span < threshold and merged_span <= max_actual:
                groups[-1].extend(cur)
            else:
                groups.append(cur)
        else:
            groups.append(cur)
    # 兜底：任何超过上限的组再拆一次
    # 注意：必须按 piece 边界拆，且每个子段保留自己的源片镜头编号——
    # 早期版本把所有子段都标成 g[0][2]（首个 piece 的编号），
    # 导致源片镜头覆盖率不足、同一个镜头被多个段重复覆盖。
    fixed = []
    for g in groups:
        span = g[-1][1] - g[0][0]
        if span <= max_actual:
            fixed.append(g)
            continue
        k = math.ceil(span / max_actual)
        per = max(1, math.ceil(len(g) / k))
        for j in range(0, len(g), per):
            chunk = g[j:j + per]
            if chunk:
                fixed.append(chunk)
    return fixed


def plan(shots, duration, policy="keep-duration", min_fill=0.85, setting_fit="nearest"):
    if policy == "keep-shots":
        groups = [[(a, b, [i + 1])] for i, (a, b) in enumerate(shots)]
    else:
        groups = group_pieces(split_long(shots), min_fill=min_fill)

    segs = []
    for g in groups:
        start, end = g[0][0], g[-1][1]
        span = end - start
        setting = pick_setting(span, setting_fit)
        src_shots = sorted({s for p in g for s in p[2]})
        segs.append({
            "index": len(segs) + 1,
            "source_start": round(start, 3),
            "source_end": round(end, 3),
            "source_span": round(span, 3),
            "setting_seconds": setting,
            "actual_seconds": round(DUR_TABLE[setting], 4),
            "actual_frames": align_frame_count(max(5, round(setting * FPS))),
            "anchor_time": round(start + 0.15, 3),
            "source_shots": src_shots,
            "merged_count": len(src_shots),
        })

    total_actual = sum(s["actual_seconds"] for s in segs)
    merged_segs = [s for s in segs if s["merged_count"] > 1]
    solo_segs = [s for s in segs if s["merged_count"] == 1]
    covered = sorted({sh for s in segs for sh in s["source_shots"]})
    return {
        "source_duration": round(duration, 3),
        "source_shots": len(shots),
        "avg_source_shot_len": round(duration / max(1, len(shots)), 3),
        "segments": len(segs),
        "solo_segments": len(solo_segs),
        "merged_segments": len(merged_segs),
        "one_to_one_ratio": round(len(solo_segs) / max(1, len(segs)) * 100, 1),
        "covered_source_shots": len(covered),
        "total_generated_seconds": round(total_actual, 3),
        "length_delta_pct": round((total_actual - duration) / duration * 100, 1),
        "model_min_actual": round(MIN_ACTUAL, 4),
        "model_max_actual": round(MAX_ACTUAL, 4),
        "policy": policy,
        "min_fill": min_fill,
        "setting_fit": setting_fit,
        # 源片镜头清单：让分段表自包含，下游（抽锚点/写描述/质检）不必再去别处找
        "shots": [{"idx": i + 1, "start": round(a, 3), "end": round(b, 3), "dur": round(b - a, 3)}
                  for i, (a, b) in enumerate(shots)],
        "segments_detail": segs,
    }


def render_table(summary) -> str:
    lines = ["| 生成段 | 源片时间窗 | 源片跨度 | 设定秒 | 实际时长 | 锚点 | 覆盖源片镜头 | 合并 |",
             "|---|---|---|---|---|---|---|---|"]
    for s in summary["segments_detail"]:
        lines.append("| {idx:02d} | {a:.2f}–{b:.2f}s | {span:.2f}s | {st} | {act:.4f}s | {anc:.2f}s | {sh} | {mg} |".format(
            idx=s["index"], a=s["source_start"], b=s["source_end"], span=s["source_span"],
            st=s["setting_seconds"], act=s["actual_seconds"], anc=s["anchor_time"],
            sh=",".join("#%d" % x for x in s["source_shots"]),
            mg=("%d 镜合并" % s["merged_count"]) if s["merged_count"] > 1 else ""))
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="规划视频模型生成分段")
    ap.add_argument("--video")
    ap.add_argument("--cuts")
    ap.add_argument("--cuts-file")
    ap.add_argument("--duration", type=float)
    ap.add_argument("--threshold", type=float, default=0.35)
    ap.add_argument("--min-gap", type=float, default=0.4)
    ap.add_argument("--policy", choices=["keep-duration", "keep-shots"], default="keep-duration")
    ap.add_argument("--min-fill", type=float, default=0.85,
                    help="跨度达到最短时长的该比例即可独立成段，默认 0.85")
    ap.add_argument("--setting-fit", choices=["nearest", "up"], default="nearest")
    ap.add_argument("--out")
    args = ap.parse_args()

    # --cuts / --cuts-file 优先于自动检测：调用方给出人工核实过的切点时必须用它，
    # 此时 --video 只用来取时长（早期版本这里让 --video 分支先命中，
    # 导致同时传 --video 与 --cuts 时切点被静默丢弃）。
    if args.cuts or args.cuts_file:
        if args.cuts_file:
            with open(args.cuts_file, encoding="utf-8") as fh:
                cuts = [float(x) for x in re.findall(r"[0-9.]+", fh.read())]
        else:
            cuts = [float(x) for x in re.findall(r"[0-9.]+", args.cuts)]
        if args.video:
            if not os.path.exists(args.video):
                print("video not found: %s" % args.video, file=sys.stderr)
                return 1
            duration = probe_duration(args.video)
        elif args.duration:
            duration = args.duration
        else:
            print("--duration (or --video) is required with --cuts/--cuts-file", file=sys.stderr)
            return 1
        cuts = sorted(c for c in cuts if 0 < c < duration)
        print("cuts source          : 显式给出（%d 个切点，未做自动检测）" % len(cuts))
    elif args.video:
        if not os.path.exists(args.video):
            print("video not found: %s" % args.video, file=sys.stderr)
            return 1
        duration = probe_duration(args.video)
        cuts = detect_cuts(args.video, args.threshold, args.min_gap)
    else:
        ap.print_help()
        return 1

    shots = shots_from_cuts(cuts, duration)
    summary = plan(shots, duration, args.policy, args.min_fill, args.setting_fit)

    print("source duration      : %.3fs" % summary["source_duration"])
    print("detected shots       : %d  (avg %.3fs)" % (summary["source_shots"], summary["avg_source_shot_len"]))
    print("model segment range  : %.4f - %.4f s" % (summary["model_min_actual"], summary["model_max_actual"]))
    print("planned segments     : %d   (one-to-one %d, merged %d, 1:1 ratio %.1f%%)" % (
        summary["segments"], summary["solo_segments"], summary["merged_segments"], summary["one_to_one_ratio"]))
    cov = summary["covered_source_shots"]; tot = summary["source_shots"]
    flag = "" if cov == tot else "   <== 覆盖率不足，请检查！"
    print("source shots covered : %d / %d%s" % (cov, tot, flag))
    if cov != tot:
        missing = sorted(set(range(1, tot + 1)) - {sh for s in summary["segments_detail"] for sh in s["source_shots"]})
        print("  未被覆盖的源片镜头: %s" % missing)
    print("total generated      : %.3fs  (%+.1f%% vs source)" % (
        summary["total_generated_seconds"], summary["length_delta_pct"]))
    print()
    print(render_table(summary))

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, ensure_ascii=False, indent=2)
        print("\nwrote %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
