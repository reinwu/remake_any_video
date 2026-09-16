#!/usr/bin/env python3
"""核验角色音色跨段一致性。

## 为什么需要专用核验

H3 这类模型是**音视频联合生成**的，而且**每个分段独立生成、种子随机**。
如果提示词里没有音色锚定，模型每段各"挑"一个嗓子——
同一个角色在第 1 段和第 5 段听起来会像两个人。

实测（一条 25 秒、6 段的复刻片）：

| 指标（对数梅尔谱余弦距离，越小越一致） | 源片 | 无音色锚定的复刻 |
|---|---|---|
| 主角「噜噜」同角色跨段 | **0.0844** | **1.5226**（18 倍） |
| 配角同角色跨段 | 0.0714 | 0.6436（9 倍） |
| 不同角色之间 | 0.0745 | 0.7480 |

**复刻里主角自己两段的差异，比他跟另一个角色之间的差异还大一倍** —— 这就是"像两个人"的量化表现。

## 指标怎么算

1. 按字幕时间把每段里**该角色在说话的片段**切出来
2. 算**对数梅尔谱**，取每个梅尔带的能量中位数 → 归一化成"音色向量"
3. 比较同一角色跨段的余弦距离（应小），以及不同角色之间的距离（应大）

**不要用自相关基频（F0）**：卡通配音上噪声极大，实测两个角色的 F0 中位数差异不可靠，
会给出误导性的结论——宁可不加指标，也不加一个会误导人的指标。

用法：
  python check_voice_consistency.py --replica 成片.mp4 --source 源片.mp4 \\
      --plan plan.json --lines lines.json --out ./音色核验

  `lines.json` 形如：
  [{"start": 1.50, "end": 4.25, "speaker": "噜噜"}, ...]   （源片时间轴）
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

try:
    import numpy as np
except ImportError:
    print("需要 numpy：pip install numpy", file=sys.stderr)
    sys.exit(1)

SR = 16000
NFFT = 512
NMEL = 40


def which(n):
    from shutil import which as w
    return w(n) or n


def mel_filterbank(n_mel, n_fft, sr, fmin=60.0, fmax=7600.0):
    def hz2mel(f):
        return 2595.0 * np.log10(1.0 + f / 700.0)

    def mel2hz(m):
        return 700.0 * (10 ** (m / 2595.0) - 1.0)

    pts = mel2hz(np.linspace(hz2mel(fmin), hz2mel(fmax), n_mel + 2))
    bins = np.floor((n_fft + 1) * pts / sr).astype(int)
    fb = np.zeros((n_mel, n_fft // 2 + 1))
    for m in range(1, n_mel + 1):
        l, c, r = bins[m - 1], bins[m], bins[m + 1]
        c = max(c, l + 1)
        r = max(r, c + 1)
        for k in range(l, min(c, fb.shape[1])):
            fb[m - 1, k] = (k - l) / max(1, c - l)
        for k in range(c, min(r, fb.shape[1])):
            fb[m - 1, k] = (r - k) / max(1, r - c)
    return fb


FB = mel_filterbank(NMEL, NFFT, SR)
WINDOW = np.hamming(NFFT)


def read_audio(path, a=None, b=None, out=None):
    ff = which("ffmpeg")
    if out:
        cmd = [ff, "-y", "-v", "error"]
        if a is not None:
            cmd += ["-ss", "%.3f" % a, "-t", "%.3f" % (b - a)]
        cmd += ["-i", path, "-vn", "-ac", "1", "-ar", str(SR), "-c:a", "pcm_s16le", out]
        subprocess.run(cmd, check=True)
        path = out
    raw = subprocess.run([ff, "-v", "error", "-i", path, "-vn", "-f", "s16le",
                          "-ac", "1", "-ar", str(SR), "-"], capture_output=True).stdout
    return np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0


def voice_vector(x):
    """对数梅尔谱 → 每带能量中位数 → 归一化。只统计有声帧。"""
    hop = NFFT // 2
    frames = []
    for st in range(0, max(1, len(x) - NFFT), hop):
        fr = x[st:st + NFFT] * WINDOW
        if np.sqrt((fr ** 2).mean()) < 0.008:
            continue
        sp = np.abs(np.fft.rfft(fr)) ** 2
        frames.append(FB @ sp)
    if len(frames) < 20:
        return None, 0
    v = np.median(np.array(frames), axis=0)
    v = np.log(v + 1e-9)
    v = v - v.mean()
    n = np.linalg.norm(v)
    return (v / n if n > 0 else v), len(frames)


def cos_dist(a, b):
    return float(1.0 - np.dot(a, b))


def build_timemap(plan_path):
    plan = json.load(open(plan_path, encoding="utf-8-sig"))
    pairs, t = [], 0.0
    for s in sorted(plan["segments_detail"], key=lambda x: x["index"]):
        pairs.append((s["source_start"], s["source_end"], t, t + s["actual_seconds"]))
        t += s["actual_seconds"]
    return pairs


def map_t(pairs, x):
    for a, b, ra, rb in pairs:
        if a <= x <= b:
            return ra + (x - a) / (b - a) * (rb - ra)
    return x


def collect(tag, media, lines, pairs, tmp):
    """返回 {说话人: [音色向量, ...]}"""
    os.makedirs(tmp, exist_ok=True)
    out = {}
    for k, ln in enumerate(lines):
        a, b = float(ln["start"]), float(ln["end"])
        who = str(ln.get("speaker") or "未知")
        if pairs:
            a, b = map_t(pairs, a), map_t(pairs, b)
        if b - a < 0.4:
            continue
        w = os.path.join(tmp, "%s_%03d.wav" % (tag, k))
        v, n = voice_vector(read_audio(media, a, b, w))
        if v is None:
            continue
        out.setdefault(who, []).append(v)
    return out


def report(tag, vecs):
    print("\n[%s]" % tag)
    for who, lst in vecs.items():
        if len(lst) >= 2:
            ds = [cos_dist(lst[i], lst[j]) for i in range(len(lst)) for j in range(i + 1, len(lst))]
            print("  同角色跨段  %-8s 距离 %s  均值 %.4f"
                  % (who, ["%.4f" % x for x in ds], float(np.mean(ds))))
        else:
            print("  同角色跨段  %-8s 只有 1 个说话段，无法比较" % who)
    ks = [k for k, v in vecs.items() if v]
    if len(ks) >= 2:
        ds = [cos_dist(vecs[ks[i]][0], vecs[ks[j]][0])
              for i in range(len(ks)) for j in range(i + 1, len(ks))]
        print("  不同角色之间 %s vs %s  距离 %s" % (ks[0], ks[1], ["%.4f" % x for x in ds]))


def main() -> int:
    ap = argparse.ArgumentParser(description="核验角色音色跨段一致性")
    ap.add_argument("--replica", required=True, help="待检成片")
    ap.add_argument("--source", help="源片（可选，作为基线对照）")
    ap.add_argument("--plan", help="分段表 JSON（把源片时间映射到成片时间轴）")
    ap.add_argument("--lines", required=True, help="台词表 JSON：[{start,end,speaker},…]（源片时间轴）")
    ap.add_argument("--out", default=".", help="临时目录")
    args = ap.parse_args()

    lines = json.load(open(args.lines, encoding="utf-8-sig"))  # Windows 工具常写 BOM
    if isinstance(lines, dict):
        lines = lines.get("lines", [])
    pairs = build_timemap(args.plan) if args.plan and os.path.exists(args.plan) else None
    if not pairs:
        print("提示：未提供 --plan，将按原始时间轴切片（成片与源片时长不同时会错位）")

    tmp = os.path.join(args.out, "_tmp")
    rv = collect("replica", args.replica, lines, pairs, tmp)
    report("复刻成片", rv)
    if args.source and os.path.exists(args.source):
        sv = collect("source", args.source, lines, None, tmp)
        report("源片（基线）", sv)

        print("\n" + "=" * 62)
        print("结论")
        print("=" * 62)
        for who in rv:
            r = [cos_dist(rv[who][i], rv[who][j])
                 for i in range(len(rv[who])) for j in range(i + 1, len(rv[who]))]
            s = [cos_dist(sv[who][i], sv[who][j])
                 for i in range(len(sv.get(who, []))) for j in range(i + 1, len(sv.get(who, [])))]
            if r and s:
                ratio = float(np.mean(r)) / max(1e-9, float(np.mean(s)))
                verdict = "一致 ✅" if ratio < 2.5 else ("偏松 ⚠" if ratio < 5 else "**不一致，需加音色参考** ❌")
                print("  %-8s 复刻 %.4f / 源片 %.4f = %.1f 倍   %s"
                      % (who, float(np.mean(r)), float(np.mean(s)), ratio, verdict))
    print("\n判断标准：同角色跨段距离应接近源片基线（比值 < 2.5）。")
    print("偏大说明该角色的音色在段间漂移——用 `<Audio N>` 音色参考锚定后重跑。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
