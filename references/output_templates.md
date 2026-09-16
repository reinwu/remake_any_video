# 三份交付物：模板与完整范例

本文件在第六步"生成交付物"时查阅——不确定格式或者"该写多细"时，对照下面这个完整跑通的例子。
SKILL.md 正文里的是精简模板，这里是模板 + 填好的范例。

## 目录
1. 范例背景说明
2. 交付物一：分镜脚本模板 + 范例
3. 交付物二：制图提示词模板 + 范例
4. 交付物三：复刻指南模板 + 范例
5. 交付前质量检查清单

---

## 1. 范例背景说明

假设拆解的是一支 15 秒、竖屏 9:16 的"咖啡店氛围感开场"短视频，共 5 个镜头，晨光 + 爵士乐 + 手冲咖啡的
慢生活调性、全程无出镜人脸。下面三份交付物都以这条虚构视频完整示范一遍。真实分析时把内容换成实际
拆解的那条视频，结构和颗粒度照此执行——不要比这个例子更粗略。

---

## 2. 交付物一：分镜脚本 Shot List

### 模板

| 镜号 | 时间码 | 时长 | 景别 | 运镜 | 画面描述 | 台词/字幕/文案 | 音乐音效 | 转场 |
|---|---|---|---|---|---|---|---|---|
| 01 | 00:00–00:03 | 3s | | | | | | |

时间码必须来自 `extract_shots.sh` 产出的 `timestamps.txt`，不要凭肉眼估算；时长 = 下一镜时间码 − 本镜
时间码，最后一镜用视频总时长相减。

### 范例

| 镜号 | 时间码 | 时长 | 景别 | 运镜 | 画面描述 | 台词/字幕/文案 | 音乐音效 | 转场 |
|---|---|---|---|---|---|---|---|---|
| 01 | 00:00–00:03 | 3s | 大远景 | 固定镜头 | 清晨阳光透过咖啡店大面积落地窗斜射入内，木质桌椅剪影，空气中细小尘埃浮动，暖橙色调 | 无 | 环境音（门铃/杯碟轻响）淡入，轻爵士乐（钢琴+萨克斯）起 | — |
| 02 | 00:03–00:06 | 3s | 中近景 | 缓慢推镜 Dolly in | 咖啡师手持细口壶画圈注水，水流特写，蒸汽升起 | 无 | 音乐持续，水流声加重 | 硬切 |
| 03 | 00:06–00:09 | 3s | 近景 | 固定 + 浅景深 | 白色咖啡杯特写，热气袅袅，背景虚化成暖色光斑 | 无 | 音乐持续 | 硬切 |
| 04 | 00:09–00:12 | 3s | 中景 | 手持，轻微跟拍 | 一只手端起咖啡杯，逆光剪影感，窗外街景虚化 | 无 | 音乐渐强 | 硬切 |
| 05 | 00:12–00:15 | 3s | 远景 | 固定 + 文字动画 | 店内环境全景，标题文字从下方浮现淡入 | "慢下来 · MORNING COFFEE"（衬线体，白色，轻投影） | 音乐收尾，钢琴单音 | 淡出 |

---

## 3. 交付物二：制图提示词 AI Generation Prompts

对每个关键镜头产出"生图 Prompt + 运镜 Prompt"一组，对应当下主流的 AI 视频生产流程：先用文生图
工具画出关键帧静态画面 → 用图生视频工具把静态画面变成动态镜头 → 剪辑软件里按分镜脚本顺序、时长、
转场拼接成片。

### 模板（每个镜头一组）

```
镜头 0X｜[一句话中文描述这个镜头]
生图 Prompt（文生图，适用于 Midjourney / 即梦 / Stable Diffusion / DALL·E 等）：
  [主体 subject], [景别+机位 shot type & angle], [构图 composition], [光线 lighting],
  [色彩与氛围 color & mood], [风格/媒介参考 style reference], [画质关键词], --ar [宽高比]
运镜 Prompt（图生视频，以上图为首帧，适用于 可灵 / Runway / Pika / Vidu / 海螺 等）：
  [主体动作 subject motion], [镜头运动 camera movement], [运动速度/节奏], [建议时长]
备注：[这一帧在全片里承担什么作用/情绪]
```

### 范例

**镜头 01｜清晨落地窗光影，空镜头建立氛围**
- 生图 Prompt：`Empty cozy coffee shop interior at sunrise, warm golden sunlight streaming through large glass windows, wooden tables and chairs in silhouette, floating dust particles visible in light beams, extreme wide shot, eye-level, symmetrical composition, warm orange and amber tones, cinematic film look, shot on 35mm lens, soft natural light, --ar 9:16`
- 运镜 Prompt：`Static locked-off shot, dust particles drifting slowly in the light beam, no camera movement, gentle ambient motion only, 3 seconds, slow contemplative pacing`
- 备注：开场空镜负责"定调"——告诉观众这是慢节奏氛围感内容，不需要出现人。

**镜头 02｜手冲咖啡注水特写**
- 生图 Prompt：`Close-up of a hand pouring hot water from a gooseneck kettle into a coffee dripper in circular motion, visible steam rising, warm side lighting, shallow depth of field, blurred warm-toned background, medium close-up, cinematic food photography style, high detail, --ar 9:16`
- 运镜 Prompt：`Slow dolly-in movement pushing closer to the pouring water, water stream continues in circular motion, steam rising steadily, 3 seconds, smooth deliberate pacing`
- 备注：全片最具"过程感"的镜头，运镜强调缓慢推进以配合手冲的仪式感。

**镜头 03｜咖啡杯热气特写**
- 生图 Prompt：`Extreme close-up of a white ceramic coffee cup with steam rising, warm bokeh light spots in blurred background, soft warm lighting, shallow depth of field, cinematic still life photography, cozy atmosphere, --ar 9:16`
- 运镜 Prompt：`Static shot, steam continuously rising and dissipating, subtle light flicker in background bokeh, no camera movement, 3 seconds`
- 备注：纯氛围空镜，剪辑时作节奏缓冲。

**镜头 04｜端杯剪影动作**
- 生图 Prompt：`Medium shot of a hand holding a coffee cup, backlit silhouette against a blurred window with street view outside, warm rim light on hand edges, cinematic backlight photography, --ar 9:16`
- 运镜 Prompt：`Slight handheld camera movement with subtle natural shake, hand slowly lifts the cup, gentle follow motion, 3 seconds, relaxed pacing`
- 备注：全片唯一有"人"的镜头，只露手不露脸，逆光保留氛围感的同时增加温度。

**镜头 05｜结尾全景 + 标题**
- 生图 Prompt：`Wide shot of full coffee shop interior, warm morning light, cozy minimalist decor, empty space reserved at bottom third of frame for text overlay, cinematic warm color grade, --ar 9:16`
- 运镜 Prompt：`Static wide shot, no camera movement; title text fades in from bottom, 3 seconds`
- 备注：生图阶段就要给文字条留白，避免后期加字遮挡画面主体。

---

## 4. 交付物三：完整复刻指南 Full Replication Guide

拆成两条路径，按实际制作条件选用：
- **路径 A（AI 生成流程）**：没有实拍团队/设备，从零 AI 生成，成本最低，适合快速产出风格相似的原创内容。
- **路径 B（实拍流程）**：有拍摄条件，追求更高真实感和可控性。

两条路径最后汇总到同一个"后期制作"环节。

### 模板结构
```
一、创意与前期准备（核心创意 / 目标平台规格 / 情绪基调）
二、路径 A：AI 生成流程（分步）
三、路径 B：实拍流程（分步）
四、后期制作（剪辑节奏 / 调色 / 字幕 / 音乐音效 / 导出参数）
五、平台适配注意事项
```

### 范例

**一、创意与前期准备**
- 核心创意：用"手冲咖啡的仪式感"传递慢生活氛围，全程无对白、无出镜人脸，靠光影、声音、运镜节奏说故事。
- 目标平台与规格：抖音/小红书/Reels，竖屏 9:16，1080×1920，时长 15 秒，前 3 秒必须是强氛围空镜。
- 情绪基调：温暖、松弛、治愈。

**二、路径 A：AI 生成流程**
1. 用"交付物二"的 5 组生图 Prompt，每个镜头生成 4 张备选图，挑光影和构图最贴近预期的一张。
2. 5 张关键帧保持同一色调、光线方向、画幅比例（同批次生成或用同一张风格参考图，避免风格跳脱）。
3. 把每张关键帧连同对应运镜 Prompt 投入图生视频工具，生成约 3 秒动态片段，共 5 段。
4. 检查每段有无明显 AI 瑕疵（手部变形、物理逻辑错误），必要时重新生成或补充负面提示词排除。
5. 把 5 段素材导入剪辑软件，进入"后期制作"。

**三、路径 B：实拍流程**
1. 设备：手机主摄或微单 + 稳定器/三脚架，35mm 左右等效焦段即可覆盖全部 5 镜。
2. 场地：自然采光好的咖啡店，早晨 10 点前，窗边逆光/侧逆光位置。
3. 建议按"空镜 → 过程 → 细节 → 人物 → 收尾"顺序拍，方便剪辑时按分镜表精确匹配时长：
   - 镜头 01：三脚架固定，等光线角度合适拍 3–5 秒素材，留剪辑余量
   - 镜头 02：手持稳定器俯拍注水过程，连续拍 15–20 秒，后期挑最流畅的 3 秒
   - 镜头 03：微距/近摄，大光圈虚化背景（如 f/1.8–2.8）
   - 镜头 04：手持跟拍端杯动作，站在逆光位置，多拍几条挑动作最自然的
   - 镜头 05：固定机位拍店内全景，后期加字
4. 现场同步录制环境自然音（杯碟声、门铃声）备用，音乐后期另铺。

**四、后期制作**
- 剪辑软件：剪映/CapCut（快）或 Premiere/DaVinci Resolve（精细调色）。
- 剪辑节奏：5 镜每镜约 3 秒，全硬切，卡在音乐鼓点/旋律转折处剪，制造踩点感。
- 调色：暖橙色调，提高高光部分橙黄饱和度，压低阴影对比度，让画面"发暖发软"。
- 字幕/文字：仅结尾一处标题，衬线字体，白色 + 轻投影，避免花哨动画抢戏。
- 音乐：无版权爵士乐（钢琴+萨克斯），BPM 70–85 契合慢节奏剪辑；叠加轻量环境音效增强真实感。
- 导出：9:16，1080×1920，H.264，帧率与原片一致（一般 30fps）。

**五、平台适配注意事项**
- 封面/首帧建议用镜头 05 截图，空间感 + 标题最容易吸引点击。
- 前 3 秒（镜头 01）决定完播率，避免黑屏/logo 开场浪费黄金时间。
- 若同步投放海外平台，需确认所用音乐在对应平台的版权授权状态，必要时换成曲风相似的曲库音乐。

---

## 5. 交付前质量检查清单

- [ ] 分镜脚本每行时间码来自抽帧脚本产出的真实时间戳，不是估算的
- [ ] 各镜时长总和等于视频总时长（允许 ±1 帧误差）
- [ ] 每条生图 Prompt 包含七个要素：主体、景别/角度、构图、光线、色彩/氛围、风格参考、画幅比例——缺一都会导致生成结果和原片风格对不上
- [ ] 运镜 Prompt 与分镜表里该镜头的"运镜"列描述一致，不要生图和分镜表各说各话
- [ ] 复刻指南两条路径都给出了具体可执行的步骤，不是"用 AI 工具生成"这类空话
- [ ] 若原视频出现真人清晰面部、可识别品牌标识、或受版权保护的原曲，已在复刻指南里提醒用户：直接挪用有肖像权/商标/音乐版权风险，AI 路径建议生成虚构人物、实拍路径建议自行拍摄真人出镜、音乐建议换成曲风相似的无版权替代曲目
