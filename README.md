# remake_any_video · 视频逆向拆解与复刻

> 给一条视频，反推出它**怎么被做出来的**，并生成能照着复刻的一整套资产。
> 面向 MiniMax H3 等**音视频联合生成**模型，也可用于实拍仿拍。

[![skill](https://img.shields.io/badge/DSH-Skill-blue)](#安装)
[![license](https://img.shields.io/badge/license-MIT-green)](#许可证)

---

## 它解决什么问题

用户甩来一条视频，目的通常不是"看懂讲了什么"（那是视频总结），而是"**看懂它是怎么被做出来的**"，
然后照着做一条。这两件事的分析深度完全不同：内容总结关心信息，这个技能关心**制作层面**——
镜头怎么分、怎么运镜、光线怎么打、什么调色、剪辑节奏多快、每一帧要用什么提示词生成。

与常见的"视频拆解"提示词相比，这个技能的重点在**别的地方**：

| 常见做法 | 这个技能 |
|---|---|
| 均匀抽几帧，写一段画面描述 | **先做场景检测定切点 → 按模型时长约束规划分段 → 再按分段表抽锚点 → 最后写提示词**（顺序反了必然出错） |
| 只看单帧写提示词 | **必看动作条与对白条**：动作弧、事件、说话人都只在帧间可见 |
| 只写统一文字描述 | **建立资产库（角色/服装/道具/场景/风格/音色）并逐条引用**，否则同一件衣服会变成三种样子 |
| 跑完再看效果 | **G0~G7 八道门禁**，把问题拦在便宜的那一侧 |

## 核心特性

- **三层描述**：资产库 → 逐帧状态 → 相邻帧过渡与动作弧
- **动作与说话人分析**：抽多帧做动作条看运动；用**口型法**判断谁在说话（不许猜）
- **分段规划**：按源片切点 + 模型时长档位（帧数对齐规则）自动规划，保证**镜头覆盖率 100%**
- **字幕复刻**：从烧录字幕提取文本与时间码，**只产出唯一一个 `.ass`**，按分段边界做逐段线性时间映射
- **音色一致性**：用 `<Audio N>` 音色参考锚定每个角色的音色，避免"同一个人听起来像两个人"
- **可选操作**：角色替换 / 动作迁移 / 音色替换（默认 1:1 完整复刻）
- **质检门禁 G0~G7**：每道工序产出后立即验收，不合格绝不进入下一道

## 安装

> ### ⚠️ 先看清两个名字不一样（这是刻意的）
>
> | | 名字 | 为什么 |
> |---|---|---|
> | **GitHub 仓库名** | `remake_any_video` | 仓库名可以是任意合法字符串，下划线没问题 |
> | **技能名 / 安装目录名** | **`remake-any-video`** | **DSH 规定技能名必须是 kebab-case（小写字母、数字、连字符）**，下划线不合法；而且发现规则是一层目录 `<root>/<name>/SKILL.md`，所以**目录名必须与 frontmatter 里的 `name` 一致** |
>
> 也就是说：**clone 下来的文件夹叫 `remake_any_video`，但放进技能目录时必须叫 `remake-any-video`。**
> 直接放错名字不会加载。下面的命令已经处理好了这件事。

### 安装步骤

**第 1 步：克隆到任意位置**

```bash
git clone https://github.com/reinwu/remake_any_video.git
```

**第 2 步：复制进技能目录，并把目录名改成 `remake-any-video`**

macOS / Linux：

```bash
cp -r remake_any_video ~/.dsh/skills/remake-any-video
```

Windows PowerShell：

```powershell
Copy-Item remake_any_video "$env:USERPROFILE\.dsh\skills\remake-any-video" -Recurse
```

Windows CMD：

```cmd
xcopy /E /I remake_any_video %USERPROFILE%\.dsh\skills\remake-any-video
```

**第 3 步：确认目录结构正确**

安装后的路径必须是：

```
~/.dsh/skills/remake-any-video/SKILL.md          ← 必须正好在这一层
~/.dsh/skills/remake-any-video/references/
~/.dsh/skills/remake-any-video/scripts/
```

❌ 常见错误：`~/.dsh/skills/remake_any_video/SKILL.md`（用了仓库名，带下划线）
❌ 常见错误：`~/.dsh/skills/remake-any-video/remake_any_video/SKILL.md`（多套了一层）

**第 4 步：自检（可选但推荐）**

```bash
python ~/.dsh/skills/remake-any-video/tools/validate_skill.py
```

应输出 `RESULT: PASS`。技能由 DSH 实时发现，**无需重启**。

### 环境依赖

- **ffmpeg / ffprobe**（必需）——抽帧、切音频、拼接、烧字幕
  - Windows：`winget install Gyan.FFmpeg` 或 `scoop install ffmpeg`
  - macOS：`brew install ffmpeg`
- **yt-dlp**（视频来源是网址时必需）
- **Python 3.9+**，脚本用到 `numpy`（`check_voice_consistency.py`）
  ```bash
  pip install numpy
  ```
- **读图能力**（技能靠看图判断动作与口型，纯文本模型无法执行第 4 步）

## 使用

技能会在你说这些话时自动触发：

> "分析这个视频"、"这个视频怎么拍的"、"拆解一下这个视频"、
> "复刻/仿拍这个视频"、"学习这个视频的运镜和风格"
>
> 英文：analyze / breakdown / reverse-engineer / recreate this video

即使你只发一个链接、没说"拆解"两个字，只要意图是**理解或复刻制作方法**，就会触发。

技能会先问你一个问题（**默认 1:1 完整复刻**）：

| 选项 | 含义 |
|---|---|
| **直接用原音频、原角色、原场景，1:1 完整复刻**（默认） | 最忠实 |
| 角色替换 | 换角色，保留原构图与运镜 |
| 动作迁移 | 动作来源可以是另一条视频或文字描述 |
| 音色替换 | 换音色，保留原台词与节奏 |
| 以上组合 | — |

## 工作流程

```
抓素材 → 镜头清单 → 动作与说话人 → 资产库 → 序列描述
   → 分段规划 → 交付物 → 【用户人工复核】→ 【试跑 1~3 段】→ 全片 → 成片质检
```

**两道最容易跳过的门**：

- **G5b 生成前人工复核**：把字幕文本、关键帧锚点、生成提示词交给用户确认后才提交生成。
  脚本只给字幕**图**不给文字，转录靠人眼，而相邻重复字（如「不能不要」）极易读漏、
  漏字后句子往往仍通顺（语感校验失效）——所以必须由人看
- **G6 试跑**：全片前先跑 1~3 段。上游任何一个错误，在这里暴露只要几十分钟，
  全片跑完再发现就是几小时

## 脚本

| 脚本 | 用途 |
|---|---|
| `scripts/extract_shots.sh` | 场景检测、抽帧、切音频 |
| `scripts/plan_segments.py` | 按切点与模型时长档位规划分段（含覆盖率校验） |
| `scripts/extract_shot_frames.py` | 抽动作条与对白条 |
| `scripts/extract_subtitles.py` | 从烧录字幕提取文本与时间码（默认放大 2 倍便于逐字核对） |
| `scripts/build_subtitle_asset.py` | 生成**唯一的** `.ass` 字幕文件 |
| `scripts/build_manifest.py` | 产出可复核的《产出物清单》 |
| `scripts/qc_check.py` | 中间产物质检 + 提示词官方格式核验 |
| `scripts/check_voice_consistency.py` | 角色音色跨段一致性核验 |
| `tools/validate_skill.py` | 技能自身校验 |

## 这些数字是实测的

技能里的阈值与判断标准都来自真实复刻实验，不是拍的：

| 指标 | 数值 |
|---|---|
| 构图遵循度（NCC，正序流程 vs 先写词后抽帧） | **0.88 vs 0.24** |
| 无音色锚定时的跨段音色差异 | **源片基线的 18 倍**（加 `<Audio N>` 音色参考后降到 **0.3 倍**） |
| 0.4MP 与 768p 的生成耗时 | 17 段 **50.7 分钟 vs 154 分钟** |
| H3 时长档位换算 | 设定 4→4.4583s，5→5.1667s，…，15→15.0833s（帧数向上对齐到 17k+5） |

技能文档里也如实记录了**试过但不可靠的方法**，避免后来者重走弯路：

- **用"墨迹宽度 ÷ 字数"反推字幕是否缺字** —— 理论上诱人，实测会给出**相反建议**
  （漏字的 7 字版本反而比正确的 9 字版本更贴近均值）
- **用自相关基频（F0）判断音色是否一致** —— 卡通配音上噪声极大，
  实测两个角色的 F0 中位数关系是反的

原则：**宁可不加检查，也不加一个会误导人的检查。**

## 目录结构

```
remake_any_video/
├── SKILL.md                              主文档：工作流程与八道门禁
├── references/
│   ├── keyframe_sequence_description.md  三层描述 · 资产库 · 动作弧 · 口型法
│   ├── generation_segmentation.md         时长档位 · 分段算法 · 多镜头
│   ├── subtitle_replication.md            字幕类型 · 样式分析 · 转录校验
│   ├── voice_consistency.md               音色漂移根因 · <Audio N> 参考 · 两个坑
│   ├── qc_checklist.md                    G0~G7 门禁逐项检查
│   ├── output_templates.md                交付物模板
│   ├── shot_glossary.md                   镜头语言中英对照
│   └── optional_operations.md             角色替换 / 动作迁移 / 音色替换
├── scripts/                               可执行脚本（见上表）
└── tools/validate_skill.py                技能自检
```

## 作者

**[reinwu](https://github.com/reinwu)**

## 许可证

[MIT](LICENSE)

## 致谢

技能中的官方提示词规范部分参考 **MiniMax H3** 的 prompt 规范文件
（`h3-prompt-writing` 技能的 `references/base-en.txt` / `ref-en.txt`）。

复刻示例中出现的所有角色与素材均来自**学习研究用途**的临摹/二创，
分镜锚点直接取自原片画面，**不可商用**。
