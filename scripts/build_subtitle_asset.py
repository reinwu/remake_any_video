#!/usr/bin/env python3
"""把全片字幕整理成**唯一一个文件**（.ass），避免多份产出不同步。

为什么要合成一份：字幕以前会同时产出 `.json` / `.csv` / `.md` / `.ass` 四份，
任何一次修改都要同步四个文件，极易出现"改了 json 忘了 ass"这类隐患。
现在**只有一份 `.ass`**，它同时满足三个用途：

  - **人看**：普通文本，记事本可读；每条的说话对象 / 语速 / 语气写在紧邻的注释行里
  - **机器读**：ASS 有固定格式，解析稳定
  - **直接可用**：ffmpeg 的 `ass=` 滤镜可直接烧录，无需再转换

ASS 字段承载关系：
  `Name` 字段 -> **说话对象**（角色代号 + 全片统一编号）
  `Text` 字段 -> 台词（保留原语言）
  `Start/End` -> 精确时间（同一句重复检出时**取最早一次**）
  `Style`     -> 字幕样式（字体/字号/颜色/描边/位置）
  紧邻的 `;` 注释行 -> **语速下限 / 语气 / 检出次数**

## 关键规则

1. **同一句在多次检测中重复出现时，以第一次出现的时间为准**并合并成一条。角色说话慢或台词长时，
   同一句会横跨很多采样帧；采样抖动还会把它断成几条。不合并就会出现成片里同一句被说好几遍。
2. **说话对象必须来自口型判断**，不能靠"谁在画面里"猜；判断不了就写 `待确认`，不要编。
3. 语速一栏是**下限**（字幕停留时长 ≥ 实际说话时长）。算出来明显偏低时，**先怀疑转录漏字**。

用法：
  python build_subtitle_asset.py --transcribed subs/transcribed.csv \\
      --out ./字幕 --name 字幕 --plan plan.json --play-res 864x480
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys

SPEED_SLOW, SPEED_FAST = 3.5, 5.0


def norm_text(t: str) -> str:
    t = re.sub(r"\s+", "", t or "")
    t = re.sub(r"[~～]+", "~", t)
    t = re.sub(r"[!！]{2,}", "！", t)
    t = re.sub(r"[?？]{2,}", "？", t)
    return t


def char_count(t: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", t or ""))


def infer_tone(t: str) -> str:
    if not t:
        return ""
    if re.search(r"[!！]{3,}", t):
        return "惊慌/急切"
    if re.search(r"[!！]{2}", t):
        return "强调/激动"
    if "？" in t or "?" in t:
        return "疑问"
    if "！" in t or "!" in t:
        return "感叹"
    if "~" in t:
        return "俏皮/轻松"
    if "…" in t or "..." in t:
        return "迟疑/未完"
    return "陈述"


def speed_label(cps: float) -> str:
    if cps <= 0:
        return "—"
    if cps < SPEED_SLOW:
        return "慢"
    if cps > SPEED_FAST:
        return "快"
    return "中"


def load_transcribed(path):
    if path.endswith(".json"):
        d = json.load(open(path, encoding="utf-8"))
        return d.get("lines", d if isinstance(d, list) else [])
    with open(path, encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def num(row, *keys):
    for k in keys:
        if k in row and str(row[k]).strip() != "":
            try:
                return float(row[k])
            except Exception:
                pass
    return None


def ass_time(sec: float) -> str:
    sec = max(0.0, sec)
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    return "%d:%02d:%05.2f" % (h, m, sec % 60)


def bgr(c):
    return "&H00%02X%02X%02X" % (c[2], c[1], c[0])


def main() -> int:
    ap = argparse.ArgumentParser(description="生成唯一的字幕文件（.ass）")
    ap.add_argument("--transcribed", required=True, help="已转录的字幕表（CSV 或 JSON）")
    ap.add_argument("--out", required=True, help="输出目录")
    ap.add_argument("--name", default="字幕", help="输出文件名（不带扩展名），默认 字幕")
    ap.add_argument("--plan", help="分段表 JSON（用于标注每条属于哪个生成段）")
    ap.add_argument("--source", help="源片路径（写入头部注释）")
    ap.add_argument("--play-res", default="864x480", help="成片分辨率 WxH，默认 864x480")
    ap.add_argument("--font", default="Microsoft YaHei", help="字幕字体")
    ap.add_argument("--fontsize", type=int, default=37, help="字号，默认 37")
    ap.add_argument("--outline", type=int, default=3, help="描边宽度，默认 3")
    ap.add_argument("--marginv", type=int, default=20, help="底部边距，默认 20")
    ap.add_argument("--fill", default="245,210,30", help="填充色 R,G,B（默认金黄）")
    ap.add_argument("--outline-color", default="25,25,10", help="描边色 R,G,B")
    ap.add_argument("--time-map", help="逐段时间映射 JSON（源片时间->成片时间），可选")
    ap.add_argument("--merge-gap", type=float, default=1.0, help="同文本两次检出间隔小于该秒数视为同一句")
    args = ap.parse_args()

    try:
        W, H = [int(x) for x in args.play_res.lower().split("x")]
    except Exception:
        print("--play-res 格式应为 WxH，如 864x480", file=sys.stderr)
        return 1
    fill = [int(x) for x in args.fill.split(",")]
    outl = [int(x) for x in args.outline_color.split(",")]

    rows = load_transcribed(args.transcribed)
    if not rows:
        print("转录表为空", file=sys.stderr)
        return 1
    print("读入 %d 条检测" % len(rows))

    # 段号映射
    segs = []
    if args.plan and os.path.exists(args.plan):
        plan = json.load(open(args.plan, encoding="utf-8"))
        segs = [(s["index"], s["source_start"], s["source_end"])
                for s in plan.get("segments_detail", [])]

    def seg_of(t):
        for idx, a, b in segs:
            if a <= t < b:
                return idx
        return None

    # 时间映射（源片 -> 成片）
    tmap = None
    if args.time_map and os.path.exists(args.time_map):
        d = json.load(open(args.time_map, encoding="utf-8"))
        tmap = d.get("pairs") or d.get("mapping")

    def mp(t):
        if not tmap:
            return t
        for a, b, ra, rb in tmap:
            if a <= t <= b:
                return ra + (t - a) / (b - a) * (rb - ra)
        return tmap[-1][3] if t > tmap[-1][1] else tmap[0][2]

    # ---- 规则 1：同文本合并，起点取最早 ----
    groups, order = {}, []
    for r in rows:
        txt = str(r.get("text", "") or "").strip()
        a, b = num(r, "start", "start_sec"), num(r, "end", "end_sec")
        if a is None:
            continue
        key = norm_text(txt) if txt else "__blank_%d" % len(order)
        if key not in groups:
            groups[key] = {"text": txt, "dets": [], "speaker": "", "tone": ""}
            order.append(key)
        groups[key]["dets"].append((a, b if b is not None else a))
        for f, k in (("speaker", "speaker"), ("tone", "tone")):
            v = str(r.get(f, "") or "").strip()
            if v and not groups[key][k]:
                groups[key][k] = v

    lines, merged = [], []
    for key in order:
        g = groups[key]
        dets = sorted(g["dets"])
        first = dets[0][0]
        last = max(d[1] for d in dets)
        spans, ca, cb = [], dets[0][0], dets[0][1]
        for a, b in dets[1:]:
            if a - cb <= args.merge_gap:
                cb = max(cb, b)
            else:
                spans.append((ca, cb)); ca, cb = a, b
        spans.append((ca, cb))
        if len(spans) > 1:
            merged.append((g["text"], spans))
        lines.append({"text": g["text"], "start": first, "end": last, "dets": dets,
                      "spans": spans, "speaker": g["speaker"], "tone_in": g["tone"]})
    lines.sort(key=lambda x: x["start"])

    real = []
    for i, ln in enumerate(lines, 1):
        if not ln["text"]:
            continue
        ln["index"] = len(real) + 1
        ln["duration"] = round(max(0.0, ln["end"] - ln["start"]), 3)
        ln["chars"] = char_count(ln["text"])
        ln["cps"] = round(ln["chars"] / ln["duration"], 2) if ln["duration"] > 0 else 0.0
        ln["speed"] = speed_label(ln["cps"])
        ln["tone"] = ln["tone_in"] or infer_tone(ln["text"])
        ln["segment"] = seg_of(ln["start"])
        normal = ln["chars"] / 4.5 if ln["chars"] else 0.0
        ln["low_speed"] = bool(normal and ln["duration"] > normal + 1.0)
        real.append(ln)

    # ---- 生成唯一的 ASS ----
    os.makedirs(args.out, exist_ok=True)
    dst = os.path.join(args.out, "%s.ass" % args.name)
    L = []
    L.append("[Script Info]")
    L.append("; ============================================================")
    L.append("; 全片字幕资产 —— 本文件是字幕的【唯一权威来源】")
    L.append("; 不要再单独维护 json / csv / md 版本，避免多份不同步")
    L.append("; ------------------------------------------------------------")
    if args.source:
        L.append("; 源片          : %s" % args.source)
    L.append("; 成片分辨率    : %dx%d" % (W, H))
    L.append("; 字幕条数      : %d" % len(real))
    if tmap:
        L.append("; 时间映射      : 已按分段边界逐段线性映射到成片时间轴")
    L.append("; 规则          : 同一句重复检出时合并，且以【最早一次】出现的时间为准")
    L.append("; 说话对象      : 来自口型判断（对白条），未判定写「待确认」")
    L.append("; 语速          : 为【下限】（字幕停留时长 >= 实际说话时长）；")
    L.append(";                 算出来明显偏低时先怀疑转录漏字，而不是默认字幕悬挂")
    L.append("; 字段承载      : Name=说话对象 | Text=台词 | Start/End=时间 | Style=样式")
    L.append(";                 每条紧邻的 ; 注释行 = 语速 / 语气 / 检出次数 / 所属生成段")
    L.append("; ============================================================")
    L.append("ScriptType: v4.00+")
    L.append("PlayResX: %d" % W)
    L.append("PlayResY: %d" % H)
    L.append("WrapStyle: 2")
    L.append("ScaledBorderAndShadow: yes")
    L.append("")
    L.append("[V4+ Styles]")
    L.append("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
             "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
             "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding")
    L.append("Style: Sub,%s,%d,%s,%s,%s,&H80000000,1,0,0,0,100,100,0,0,1,%d,1,2,40,40,%d,1"
             % (args.font, args.fontsize, bgr(fill), bgr(fill), bgr(outl), args.outline, args.marginv))
    L.append("")
    L.append("[Events]")
    L.append("Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text")
    for ln in real:
        sp = ln["speaker"] or "待确认"
        L.append("; #%02d 说话对象=%s | 停留 %.2fs | %d 字 | 语速下限 %.2f 字/秒(%s) | 语气=%s | 检出 %d 次%s"
                 % (ln["index"], sp, ln["duration"], ln["chars"], ln["cps"], ln["speed"],
                    ln["tone"], len(ln["dets"]),
                    (" | 生成段 %s" % ln["segment"]) if ln["segment"] else ""))
        L.append("Dialogue: 0,%s,%s,Sub,%s,0,0,0,,%s"
                 % (ass_time(mp(ln["start"])), ass_time(mp(ln["end"])), sp, ln["text"]))
    L.append("")

    open(dst, "w", encoding="utf-8", newline="\n").write("\n".join(L))

    # ---- 报告（不产出额外文件）----
    print("\n字幕 %d 条，全部写入单一文件：%s" % (len(real), dst))
    if merged:
        print("\n以下台词在不相邻时间段被多次检出，已合并（确认是否真的出现两次）：")
        for txt, spans in merged:
            print("  「%s」 -> %s" % (txt, "; ".join("%.1f-%.1f" % (a, b) for a, b in spans)))
    multi = [l for l in real if len(l["dets"]) > 1]
    if multi:
        print("\n重复检出已合并 %d 条（首次时间已作为起点）：" % len(multi))
        for l in multi:
            print("  #%02d 「%s」 %.2fs 起（%d 次检出）" % (l["index"], l["text"], l["start"], len(l["dets"])))
    low = [l for l in real if l["low_speed"]]
    if low:
        print("\n⚠ 有 %d 条停留时长明显长于正常语速所需 —— **首先怀疑转录取漏了字**：" % len(low))
        for l in low:
            print("  #%02d 「%s」 停留 %.2fs / 仅 %d 字 -> 语速下限 %.2f 字/秒"
                  % (l["index"], l["text"], l["duration"], l["chars"], l["cps"]))
        print("  排查顺序：(a) 转录缺字——把裁剪图放大逐字重读（最常见）；(b) 字幕确实悬挂")
        print("  注意：相邻重复字（如「不能不要」的两个「不」）在缩略图上极易读漏，")
        print("        漏字后句子往往仍通顺，所以语感校验靠不住。")
    need = [l for l in real if not l["speaker"]]
    if need:
        print("\n⚠ 有 %d 条未判定说话对象：%s（在对白条上看口型后回填 speaker 列再重跑）"
              % (len(need), [l["index"] for l in need]))
    print("\n未产出其他字幕文件（按'单一来源'原则）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
