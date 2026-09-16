#!/usr/bin/env python3
"""生成「产出物清单」——把项目里所有输入与产出列成一张可复核的表。

为什么要这个：决定生成结果的东西（**生成提示词**、字幕文本、关键帧锚点）散落在各个子目录里，
用户看不到，也就无从复核。本项目约定把它们集中列进 `产出物清单.md`，并**逐条写明复核要点**。

用法：
  python build_manifest.py --project M:/path/复刻-XXX-V1
  python build_manifest.py --project ./proj --source "D:/原片.mp4" --out 产出物清单.md

规范项目结构（复核相关的东西**必须落在项目目录内**，不能留在工具的 input 或临时目录里，
否则用户看不到、也就无从复核）：

    复刻-<名字>-<版本>/
    ├── 产出物清单.md        ← 复核入口
    ├── 交付说明.md
    ├── 素材/                源片 + meta.json + audio/
    ├── 关键帧/               每镜首帧 + timestamps.txt
    ├── 动作分析/             strips/ + dialogue/ + shot_frames.json
    ├── 资产库/               角色* / 场景* / 风格* / 分镜*
    ├── 字幕/                 lines/ + subtitle_asset.* + subtitles.ass
    ├── prompts/              生成剧本（H3 提示词）
    ├── 分段/ · 帧/ · 参考答案/ · tools/
"""

from __future__ import annotations

import argparse
import glob
import os
import sys

# (分组, 路径(相对 project，可含一次 *), 说明, 复核要点 or None)
SPEC = [
    ("一、素材", [
        ("素材", "源片副本", None),
        ("素材/meta.json", "分辨率/帧率/时长/切点/镜头表", None),
        ("素材/audio", "抽取的音轨（判断语速、语气、音色的依据）", None),
    ]),
    ("二、分析", [
        ("关键帧", "每个源片镜头的首帧（**生成锚点的来源**）",
         "每张是否清晰、**无字幕污染**、构图确实代表该镜头内容"),
        ("关键帧/timestamps.txt", "每张关键帧的时间码",
         "数量是否等于关键帧张数、时间码是否与镜头边界一致"),
        ("动作分析/strips", "每条镜头动作条（看运动、写动作弧的依据）",
         "**逐条看过**：主体怎么动、有无中途入出场、镜头有无推拉"),
        ("动作分析/dialogue", "每条对白条（看口型、判断说话人的依据）",
         "**逐条看过**：谁的嘴在帧间开合 → 说话人是否判对；画外音是否标了 off-screen"),
        ("动作分析/shot_frames.json", "镜头帧清单", None),
    ]),
    ("三、资产库", [
        ("资产库/角色*", "角色参考图",
         "身份特征是否清晰可辨（体型/发型/面部特征/配饰）；是否避开字幕"),
        ("资产库/服装*", "服装参考图（服饰形制+颜色+材质）",
         "**服饰跨镜是否一致**——换镜后衣服不能变样；"
         "注意：导演台的素材类型只有「角色/场景/道具/分镜/其他」，"
         "**没有「服装」**，所以引用时挂到 `角色` 或 `其他` 槽位"),
        ("资产库/道具*", "道具参考图（关键物件）",
         "外观/尺寸/在画面中的常态位置是否清楚；道具换样子观众立刻察觉"),
        ("资产库/场景*", "场景参考图",
         "空间结构、光源方向是否清楚；是否避开字幕"),
        ("资产库/风格*", "风格参考图", None),
        ("资产库/分镜*", "分镜锚点（每段首帧）",
         "每段的锚点是否与该段提示词描述的内容一致（**不一致必然导致构图跑偏**）"),
    ]),
    ("四、字幕（目录内只应有 1 个文件）", [
        ("参考答案/字幕裁剪图", "逐条字幕裁剪图（默认已放大 2 倍）——**复核用中间物，不是字幕产出**",
         "**逐字核对文本**——相邻重复字（如「不能不要」）极易读漏，"
         "漏字后句子往往仍通顺，语感校验靠不住"),
        ("字幕/*.ass", "**字幕的唯一权威文件**（目录内只应有这一个文件）",
         "文本逐字正确；说话对象（Name 字段）与口型判断一致；时间码正确；"
         "同一句重复检出是否已合并且取最早时间；紧邻注释行里的语速/语气是否合理"
         "（**不要**再维护 json/csv/md 多份，避免不同步）"),
    ]),
    ("五、生成提示词（决定生成结果的核心输入）", [
        ("prompts/*_EN*", "**英文版生成提示词**——严格按 MiniMax H3 官方 Ref2VA 规范，"
                          "提交生成用的就是这一份",
         "① 六个 section 顺序与命名是否与官方一致（subject_definitions / summary / "
         "retention_analysis / detailed_description / overall_soundscape / non_diegetic_music）；"
         "② 六个 section 是否**全英文**（只有 `<d>` 内对白保留原语言）；"
         "③ 可复用内容是否用 `<Subject N>`、具体帧锚点才用 `<Picture N>`；"
         "④ 每段的**动作事件**是否与原片一致（不是只写画面外观）；"
         "⑤ **谁在说话**是否绑定到正确的 Subject 且带 `(S1)/(S2)`；"
         "⑥ `<Picture N>` 编号是否与 SCENE_INSTRUCTION.slots 顺序一致；"
         "⑦ 对白有无重复（同一句只能出现一次）"),
        ("prompts/*_CN*", "**中文版提示词**——供人工复核用的对照译本，**不用于提交生成**",
         "与英文版逐段对应；专有名词与角色代号一致；确认内容无误后改动要**同步回英文版**"),
    ]),
    ("六、分段规划", [
        ("tools/分段规划.json", "分段表（段边界/时长档位/锚点/覆盖镜头）",
         "段边界是否落在源片切点上；源片镜头覆盖率是否 100%；总长偏差是否可接受"),
    ]),
    ("七、生成结果", [
        ("分段", "生成的分段视频", None),
        ("帧", "分段中间帧", None),
    ]),
    ("八、质检与对照", [
        ("参考答案", "对照图、样式对比、总览图", None),
        ("tools", "质检数据、分段表等", None),
    ]),
]


def collate(project, pat):
    """收集路径。支持路径中**任意位置**的通配（如 `字幕/*.ass`、`prompts/*_EN*`），
    但不递归——`glob` 默认不跨目录分隔符，正好满足"不递归"，避免子目录内容被重复计数。"""
    if any(ch in pat for ch in "*?"):
        return sorted(glob.glob(os.path.join(project, pat)))
    p = os.path.join(project, pat)
    return [p] if os.path.exists(p) else []


def total(hits):
    files = 0
    for h in hits:
        if os.path.isdir(h):
            for root, _dirs, fs in os.walk(h):
                files += len(fs)
        else:
            files += 1
    return files


def main() -> int:
    ap = argparse.ArgumentParser(description="生成产出物清单")
    ap.add_argument("--project", required=True)
    ap.add_argument("--source", help="源片路径（写入清单头部）")
    ap.add_argument("--out", help="输出路径，默认 <project>/产出物清单.md")
    args = ap.parse_args()

    proj = args.project
    if not os.path.isdir(proj):
        print("项目目录不存在：%s" % proj, file=sys.stderr)
        return 1
    out = args.out or os.path.join(proj, "产出物清单.md")

    L = ["# 产出物清单\n"]
    if args.source:
        L.append("> 源片：`%s`\n" % args.source)
    L.append("> **复核列为「必」的条目，必须在开始生成前由用户确认。**"
             "其中三类最关键：**字幕文本 / 关键帧锚点 / 生成提示词**。\n")
    L.append("| 分组 | 项目 | 路径 | 数量 | 存在 | 复核 |")
    L.append("|---|---|---|---|---|---|")

    review, missing = [], []
    for group, items in SPEC:
        for pat, desc, note in items:
            hits = collate(proj, pat)
            n = total(hits)
            if not n:
                missing.append((group, desc, pat))
                L.append("| %s | %s | `%s` | 0 | **缺失** | %s |"
                         % (group, desc, pat, "**必**" if note else ""))
                continue
            shown = hits[0] if len(hits) == 1 else os.path.dirname(hits[0]) or hits[0]
            rel = os.path.relpath(shown, proj).replace("\\", "/")
            L.append("| %s | %s | `%s` | %d | 是 | %s |"
                     % (group, desc, rel, n, "**必**" if note else ""))
            if note:
                review.append((group, desc, rel, n, note))

    if review:
        L.append("\n## 生成前必须复核的条目\n")
        L.append("| 分组 | 条目 | 位置 | 数量 | 复核要点 |")
        L.append("|---|---|---|---|---|")
        for group, desc, rel, n, note in review:
            L.append("| %s | %s | `%s` | %d | %s |" % (group, desc, rel, n, note))

        L.append("\n### 用结构化提问向用户确认\n")
        L.append("按 `references/qc_checklist.md` 的 G5b 门禁，用提问工具问用户，"
                 "不要自行假定「应该没问题」。至少覆盖：\n")
        L.append("- 字幕文本逐字核对结果（放大的 `字幕/lines/` 图是可读版本）")
        L.append("- 关键帧 / 分镜锚点是否可用")
        L.append("- `prompts/` 里的生成提示词是否通过")

    if missing:
        L.append("\n## 缺失项（生成前应补齐）\n")
        for group, desc, pat in missing:
            L.append("- [%s] %s（`%s`）" % (group, desc, pat))

    open(out, "w", encoding="utf-8").write("\n".join(L) + "\n")
    n_items = sum(len(i) for _, i in SPEC)
    print("产出物清单 -> %s" % out)
    print("  条目 %d，必须复核 %d，缺失 %d" % (n_items, len(review), len(missing)))
    if missing:
        for group, desc, pat in missing:
            print("    [缺] [%s] %s (%s)" % (group, desc, pat))
    return 0


if __name__ == "__main__":
    sys.exit(main())
