#!/usr/bin/env python3
"""对复刻流水线的中间产物做自动质检（门禁式）。

能自动查的项全部自动查，查不了的（画面对不对、音色像不像）留给人工目视。
按 G0~G5 的编号输出，与 references/qc_checklist.md 一一对应。

用法：
  python qc_check.py --analysis ./analysis
  python qc_check.py --analysis ./analysis --plan plan.json --prompt 提示词_EN.txt --script 剧本.txt

退出码：0 = 全部通过（可能有 WARN）；1 = 存在 FAIL
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
from collections import Counter
import sys

FPS = 24
MIN_SETTING, MAX_SETTING = 4, 15
IMG_EXT = (".jpg", ".jpeg", ".png", ".webp")

results = []           # (level, gate, message)


def ok(gate, msg):
    results.append(("PASS", gate, msg))


def warn(gate, msg):
    results.append(("WARN", gate, msg))


def fail(gate, msg):
    results.append(("FAIL", gate, msg))


def align_frame_count(n):
    while n % 17 != 5:
        n += 1
    return n


def actual_seconds(setting):
    return align_frame_count(max(5, round(setting * FPS))) / FPS


# ------------------------------------------------------------ G0 素材

def check_materials(d):
    gate = "G0"
    frames_dir = os.path.join(d, "frames")
    if not os.path.isdir(frames_dir):
        fail(gate, "找不到 frames/ 目录：%s" % frames_dir)
        return None, None
    frames = [f for f in os.listdir(frames_dir) if f.lower().endswith(IMG_EXT)]
    if not frames:
        fail(gate, "frames/ 里没有图片")
        return None, None
    ok(gate, "关键帧 %d 张" % len(frames))

    ts = None
    for cand in ("timestamps.txt", "timestamps.csv"):
        p = os.path.join(frames_dir, cand)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                ts = [float(x) for x in re.findall(r"[0-9]+(?:\.[0-9]+)?", fh.read())]
            ok(gate, "时间码来源 %s，%d 行" % (cand, len(ts)))
            break
    if ts is None:
        fail(gate, "缺 timestamps 文件 —— 分镜时间码必须来自它，不能自己估")
    else:
        if len(ts) != len(frames):
            fail(gate, "时间码行数 %d ≠ 关键帧张数 %d（必须一一对应）" % (len(ts), len(frames)))
        noninc = [i for i in range(1, len(ts)) if ts[i] <= ts[i - 1]]
        if noninc:
            fail(gate, "时间码非单调递增，位置：%s" % noninc[:8])
        else:
            ok(gate, "时间码单调递增")

    # 平均镜长合理性
    dur = None
    meta_dir = os.path.join(d, "meta")
    if os.path.isdir(meta_dir):
        for fn in os.listdir(meta_dir):
            p = os.path.join(meta_dir, fn)
            try:
                txt = open(p, encoding="utf-8", errors="replace").read()
            except Exception:
                continue
            m = re.search(r"duration[\"'=:\s]+([0-9.]+)", txt, re.I)
            if m:
                dur = float(m.group(1))
                break
    if dur and len(frames) > 1:
        avg = dur / len(frames)
        ok(gate, "视频时长 %.2fs，平均镜长 %.2fs" % (dur, avg))
        if avg < 0.5:
            fail(gate, "平均镜长 %.2fs 过短 —— 检测阈值太低，把运动误判成切镜。调高 -t 重跑" % avg)
        elif avg > 10:
            warn(gate, "平均镜长 %.2fs 偏长 —— 可能漏检切点。可用 -i 按固定间隔补抽交叉核对" % avg)
        if ts and abs(ts[-1] - dur) > max(2.0, dur * 0.1):
            warn(gate, "末个时间码 %.2fs 与视频时长 %.2fs 差距较大" % (ts[-1], dur))

    # 音轨存在性
    audio_dir = os.path.join(d, "audio")
    if os.path.isdir(audio_dir):
        files = [f for f in os.listdir(audio_dir) if os.path.getsize(os.path.join(audio_dir, f)) > 0]
        if files:
            ok(gate, "音轨存在：%s" % ", ".join(files))
            ffmpeg = _which("ffmpeg")
            if ffmpeg:
                p = os.path.join(audio_dir, files[0])
                try:
                    proc = subprocess.run([ffmpeg, "-hide_banner", "-i", p, "-af", "volumedetect",
                                           "-f", "null", "-"], capture_output=True, text=True, errors="replace")
                    mm = re.search(r"mean_volume:\s*(-?[0-9.]+) dB", proc.stderr)
                    if mm:
                        mean = float(mm.group(1))
                        if mean < -60:
                            fail(gate, "音轨平均电平 %.1f dB，接近静音 —— 提取失败或视频本无音轨" % mean)
                        else:
                            ok(gate, "音轨平均电平 %.1f dB" % mean)
                except Exception:
                    pass
        else:
            warn(gate, "audio/ 为空 —— 源片可能无音轨（此时不存在\"原音频\"可复用）")
    else:
        warn(gate, "没有 audio/ 目录")

    return frames, ts


def _which(name):
    from shutil import which
    return which(name)


# ------------------------------------------------------------ G4 分段计划

def check_plan(path):
    gate = "G4"
    try:
        with open(path, encoding="utf-8") as fh:
            plan = json.load(fh)
    except Exception as exc:
        fail(gate, "分段表读取失败：%s" % exc)
        return
    segs = plan.get("segments_detail", [])
    if not segs:
        fail(gate, "分段表里没有段")
        return
    ok(gate, "共 %d 段，源片 %d 镜" % (len(segs), plan.get("source_shots", 0)))

    bad_grid = []
    out_of_range = []
    # 分段表里的 actual_seconds 通常被四舍五入到 4 位小数，
    # 容差必须大于这个舍入误差，否则 4.45833 会被判成小于下界 4.458333。
    TOL = 0.002
    lo, hi = actual_seconds(MIN_SETTING), actual_seconds(MAX_SETTING)
    for s in segs:
        exp = actual_seconds(s["setting_seconds"])
        if abs(exp - s["actual_seconds"]) > TOL:
            bad_grid.append((s["index"], s["setting_seconds"], s["actual_seconds"], round(exp, 4)))
        if not (lo - TOL <= s["actual_seconds"] <= hi + TOL):
            out_of_range.append((s["index"], s["actual_seconds"]))
    if bad_grid:
        fail(gate, "档位换算不符（设定秒 → 实际时长 必须按 17k+5 帧网格）：%s" % bad_grid[:5])
    else:
        ok(gate, "所有段的档位换算正确")
    if out_of_range:
        fail(gate, "段时长超出模型约束 [%.4f, %.4f]：%s" % (lo, hi, out_of_range[:5]))
    else:
        ok(gate, "所有段时长都在模型约束内 [%.4f, %.4f]" % (lo, hi))

    # 覆盖率
    n_shots = plan.get("source_shots", 0)
    covered = set()
    for s in segs:
        covered.update(s.get("source_shots", []))
    missing = [i for i in range(1, n_shots + 1) if i not in covered]
    if missing:
        fail(gate, "有 %d 个源片镜头未被任何段覆盖：%s" % (len(missing), missing[:10]))
    else:
        ok(gate, "源片镜头覆盖率 100%%（%d/%d）" % (n_shots, n_shots))

    # 总长偏差
    delta = plan.get("length_delta_pct")
    if delta is None:
        total = sum(s["actual_seconds"] for s in segs)
        src = plan.get("source_duration") or 0
        delta = (total - src) / src * 100 if src else 0.0
    if abs(delta) > 10:
        warn(gate, "总长偏差 %+.1f%%（超过 ±10%%）—— 检查分段策略或档位选择" % delta)
    else:
        ok(gate, "总长偏差 %+.1f%%" % delta)

    # 段边界是否落在切点（粗略：段起点集合与源片镜头起点集合的重合度）
    shot_starts = set()
    for i in range(n_shots):
        shot_starts.add(i)  # 占位，真实切点信息在 plan 的 shots 里（若提供）
    shots = plan.get("shots")
    if shots:
        cuts = {round(sh["start"], 2) for sh in shots}
        off = [s["index"] for s in segs if round(s["source_start"], 2) not in cuts]
        if off:
            warn(gate, "有 %d 段的起点不落在源片切点上（若这些是超长镜头的拆分则正常）：%s" % (len(off), off[:8]))
        else:
            ok(gate, "所有段起点都落在源片切点上")


# ------------------------------------------------------------ G2/G3 描述骨架

def parse_md_tables(path):
    """返回 [(表头列表, [行列表, ...]), ...]"""
    tables = []
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().split("\n")
    i = 0
    while i < len(lines):
        if lines[i].strip().startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|[\s\-:|]+\|\s*$", lines[i + 1]):
            header = [c.strip() for c in lines[i].strip().strip("|").split("|")]
            rows = []
            j = i + 2
            while j < len(lines) and lines[j].strip().startswith("|"):
                rows.append([c.strip() for c in lines[j].strip().strip("|").split("|")])
                j += 1
            tables.append((header, rows))
            i = j
        else:
            i += 1
    return tables


BAD_PHRASES = ["缓缓移动", "慢慢移动", "镜头移动", "镜头运动", "缓缓推", "镜头缓缓"]

# 动作类动词：用于粗筛"只写静态外观、没写动作"的提示词
MOTION_WORDS = [
    "walk", "walks", "run", "runs", "running", "turn", "turns", "jump", "jumps", "fall", "falls",
    "reach", "reaches", "lift", "lifts", "raise", "raises", "lower", "lowers", "move", "moves",
    "move", "step", "steps", "crouch", "spring", "chase", "chases", "enter", "enters", "leave",
    "leaves", "tilt", "tilts", "open", "opens", "close", "closes", "blink", "blinks", "sway",
    "sways", "lean", "leans", "point", "points", "hold", "holds", "drop", "drops", "climb", "land",
    "走", "跑", "转身", "跳", "掉落", "伸手", "抬起", "放下", "移动", "迈步", "蹲", "扑", "追",
    "进入", "离开", "倾斜", "张开", "合上", "眨眼", "摇晃", "倾身", "指", "抱", "落下", "爬",
]


def check_dialogue(path):
    """对白唯一性 + 动作动词粗筛。这是最容易出低级错误、也最容易自动化的两项。"""
    gate = "G3"
    try:
        txt = open(path, encoding="utf-8").read()
    except Exception as exc:
        fail(gate, "剧本读取失败：%s" % exc)
        return
    blocks = re.findall(r"\[SHOT_START\](.*?)\[SHOT_END\]", txt, re.S)
    if not blocks:
        fail(gate, "剧本里没有 [SHOT_START]/[SHOT_END] 块")
        return
    ok(gate, "剧本 %d 段" % len(blocks))

    # --- 对白唯一性 ---
    all_d = []
    for i, b in enumerate(blocks, 1):
        # <Audio N> 也按本段 AUDIO_INSTRUCTION 的槽位顺序编号，同样不能越界
        m2 = re.search(r'===AUDIO_INSTRUCTION===\s*\n(\{[^\n]+\})', b)
        if m2:
            try:
                n_aud = len(json.loads(m2.group(1)).get("slots", []))
                ua = [int(x) for x in re.findall(r"<Audio\s+(\d+)>", b)]
                ova = sorted({u for u in ua if u > n_aud})
                if ova:
                    bad_audio.append((i, ova, n_aud))
                # 用了音色参考，summary 的任务类型标记就必须体现出来
                mt = re.search(r"summary:\s*\n(\[[^\]]+\])", b)
                if n_aud and mt and "audio reference" not in mt.group(1):
                    bad_tag.append((i, mt.group(1)))
            except Exception:
                pass
        for _lang, t in re.findall(r"<d>\[([A-Za-z]+)\]\s*(.*?)</d>", b, re.S):
            all_d.append((i, t.strip()))
    if not all_d:
        ok(gate, "剧本内无对白")
    else:
        c = Counter(t for _, t in all_d)
        dups = {k: v for k, v in c.items() if v > 1}
        if dups:
            for k, v in dups.items():
                segs = [s for s, t in all_d if t == k]
                fail(gate, "对白重复：「%s」出现 %d 次 -> 段 %s（同一句会被说好几遍）"
                     % (k, v, segs))
        else:
            ok(gate, "对白 %d 条，全部唯一" % len(all_d))
        # 空对白
        empty = [i for i, t in all_d if not t]
        if empty:
            fail(gate, "有 %d 条 <d> 内容为空 -> 段 %s" % (len(empty), empty))

    # --- 动作动词粗筛 ---
    static = []
    for i, b in enumerate(blocks, 1):
        m = re.search(r"detailed_description:\s*\n(.*?)(?:\n\s*(?:overall_soundscape|non_diegetic_music):)",
                      b, re.S)
        body = m.group(1) if m else b
        low = body.lower()
        if not any(w in low for w in MOTION_WORDS):
            static.append(i)
    if static:
        warn(gate, "有 %d 段的描述里找不到任何动作动词，疑似只写了静态外观（动作弧缺失）：段 %s"
             % (len(static), static))
    else:
        ok(gate, "各段描述都含动作动词")


SECTIONS = ["subject_definitions", "summary", "retention_analysis",
            "detailed_description", "overall_soundscape", "non_diegetic_music"]


def check_prompt_spec(path):
    """核验生成提示词是否符合目标模型的官方格式（对 MiniMax H3 全参考模式即六段式）。

    **只检查六个 section 内部**——`**时长**` / `**场景**` / `**动作描述**` 与
    SCENE_INSTRUCTION 的槽位名是封装工具的结构标记，本来就该是中文，不算违规。
    """
    gate = "G5"
    try:
        raw = open(path, encoding="utf-8").read()
    except Exception as exc:
        fail(gate, "提示词读取失败：%s" % exc)
        return
    blocks = re.findall(r"\[SHOT_START\].*?\[SHOT_END\]", raw, re.S)
    if not blocks:
        fail(gate, "提示词里没有 [SHOT_START]/[SHOT_END] 块")
        return
    ok(gate, "提示词 %d 段" % len(blocks))

    bad_order, bad_cjk, bad_pic, no_spk = [], [], [], []
    bad_audio, bad_tag = [], []
    all_d = []
    for i, b in enumerate(blocks, 1):
        pos = [b.find(s + ":") for s in SECTIONS]
        if not all(p > 0 for p in pos) or pos != sorted(pos):
            bad_order.append(i)
            continue
        # 只取六个 section 的内容：最后一个 section 也不能越过 === 结构标记，
        # 否则会把 SCENE_INSTRUCTION 里的中文槽位名（分镜/角色/场景…）误判成"正文有中文"
        chunks = []
        for k, s in enumerate(SECTIONS):
            st = b.find(s + ":") + len(s) + 1
            if k + 1 < len(SECTIONS):
                en = b.find(SECTIONS[k + 1] + ":")
            else:
                en = b.find("===", st)
            if en <= st:
                en = len(b)
            chunks.append(b[st:en])
        stripped = re.sub(r"<d>.*?</d>", "", "\n".join(chunks), flags=re.S)
        cjk = re.findall(r"[\u4e00-\u9fff]+", stripped)
        if cjk:
            bad_cjk.append((i, cjk[:4]))
        m = re.search(r'===SCENE_INSTRUCTION===\s*\n(\{[^\n]+\})', b)
        if m:
            try:
                n_slot = len(json.loads(m.group(1)).get("slots", []))
                used = [int(x) for x in re.findall(r"<Picture\s+(\d+)>", b)]
                over = sorted({u for u in used if u > n_slot})
                if over:
                    bad_pic.append((i, over, n_slot))
            except Exception:
                pass
        # <Audio N> 也按本段 AUDIO_INSTRUCTION 的槽位顺序编号，同样不能越界
        m2 = re.search(r'===AUDIO_INSTRUCTION===\s*\n(\{[^\n]+\})', b)
        if m2:
            try:
                n_aud = len(json.loads(m2.group(1)).get("slots", []))
                ua = [int(x) for x in re.findall(r"<Audio\s+(\d+)>", b)]
                ova = sorted({u for u in ua if u > n_aud})
                if ova:
                    bad_audio.append((i, ova, n_aud))
                # 用了音色参考，summary 的任务类型标记就必须体现出来
                mt = re.search(r"summary:\s*\n(\[[^\]]+\])", b)
                if n_aud and mt and "audio reference" not in mt.group(1):
                    bad_tag.append((i, mt.group(1)))
            except Exception:
                pass
        for _lang, t in re.findall(r"<d>\[([A-Za-z]+)\]\s*(.*?)</d>", b, re.S):
            all_d.append((i, t.strip()))
        if "<d>" in b and not re.search(r"\(S\d+\)", b):
            no_spk.append(i)

    if bad_order:
        fail(gate, "有 %d 段的六个 section 缺失或顺序不对：%s（官方顺序：%s）"
             % (len(bad_order), bad_order, " / ".join(SECTIONS)))
    else:
        ok(gate, "各段六个 section 顺序正确")
    if bad_cjk:
        for i, c in bad_cjk:
            fail(gate, "段%02d 的 section 正文里有中文（六段式应全英文，只有 <d> 内对白保留原语言）：%s"
                 % (i, c))
    else:
        ok(gate, "各段 section 正文均为英文（对白之外无中文）")
    if bad_pic:
        for i, over, n in bad_pic:
            fail(gate, "段%02d 的 <Picture N> 越界：用到 %s，但 SCENE_INSTRUCTION 只有 %d 个槽位"
                 % (i, over, n))
    else:
        ok(gate, "<Picture N> 编号均未越界")
    if bad_audio:
        for i, ova, n in bad_audio:
            fail(gate, "段%02d 的 <Audio N> 越界：用到 %s，但 AUDIO_INSTRUCTION 只有 %d 个槽位"
                 % (i, ova, n))
    else:
        ok(gate, "<Audio N> 编号均未越界")
    if bad_tag:
        for i, tag in bad_tag:
            fail(gate, "段%02d 声明了音色参考槽位，但 summary 的任务类型标记没体现：%s"
                 "（应为「[reference generation + keyframe completion + audio reference]」）" % (i, tag))
    else:
        ok(gate, "summary 任务类型标记与音色参考使用情况一致")
    if no_spk:
        warn(gate, "有 %d 段写了 <d> 对白但没给说话人 (Sx)：%s" % (len(no_spk), no_spk))
    elif all_d:
        ok(gate, "对白均绑定了说话人 (Sx)")
    if all_d:
        c = Counter(t for _, t in all_d)
        dups = {k: v for k, v in c.items() if v > 1}
        if dups:
            for k, v in dups.items():
                segs = [s for s, t in all_d if t == k]
                fail(gate, "对白重复：「%s」出现 %d 次 -> 段 %s" % (k, v, segs))
        else:
            ok(gate, "对白 %d 条，全部唯一" % len(all_d))


# ------------------------------------------------------------ 输出

def main() -> int:
    ap = argparse.ArgumentParser(description="复刻流水线中间产物自动质检")
    ap.add_argument("--analysis", help="分析目录（含 frames/ audio/ meta/）")
    ap.add_argument("--plan", help="plan_segments.py 产出的分段表 JSON")
    ap.add_argument("--prompt", help="生成提示词（英文版，含 [SHOT_START] 块）——核验官方六段式")
    ap.add_argument("--script", help="导演台剧本，用于对白去重与动作动词粗筛")
    args = ap.parse_args()

    if not (args.analysis or args.plan or args.script or args.prompt):
        ap.print_help()
        return 1

    n_frames = None
    if args.analysis:
        frames, ts = check_materials(args.analysis)
        if frames:
            n_frames = len(frames)
    if args.prompt:
        if os.path.exists(args.prompt):
            check_prompt_spec(args.prompt)
        else:
            fail("G5", "提示词不存在：%s" % args.prompt)
    if args.plan:
        if os.path.exists(args.plan):
            check_plan(args.plan)
        else:
            fail("G4", "分段表不存在：%s" % args.plan)
    if args.script:
        if os.path.exists(args.script):
            check_dialogue(args.script)
        else:
            fail("G3", "剧本不存在：%s" % args.script)

    print("=" * 74)
    print("复刻流水线自动质检")
    print("=" * 74)
    order = {"G0": 0, "G1": 1, "G2": 2, "G3": 3, "G4": 4, "G5": 5, "G6": 6, "G7": 7}
    for level, gate, msg in sorted(results, key=lambda x: (order.get(x[1], 99), x[0])):
        print("  %-4s [%s] %s" % (level, gate, msg))
    n_fail = sum(1 for r in results if r[0] == "FAIL")
    n_warn = sum(1 for r in results if r[0] == "WARN")
    n_pass = sum(1 for r in results if r[0] == "PASS")
    print("-" * 74)
    print("  PASS %d · WARN %d · FAIL %d" % (n_pass, n_warn, n_fail))
    if n_fail:
        print()
        print("  有 FAIL 项 —— 按 references/qc_checklist.md 的处置建议改完再往下走。")
        print("  原则：把问题拦在便宜的那一侧，不要让上游错误流到生成阶段。")
    elif n_warn:
        print()
        print("  仅有 WARN —— 可继续，但建议先确认这些项是否真的可以接受。")
    else:
        print()
        print("  全部通过。")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
