#!/usr/bin/env bash
#
# extract_shots.sh —— 下载/读取视频，抽取关键帧（按场景切换）、字幕、音频与元数据，
# 为后续逐镜头视觉分析做准备。这是本 Skill 第二步"抓取素材"的执行脚本，
# 目的是把易出错、重复性高的 ffmpeg/yt-dlp 命令固定下来，避免每次分析都重新拼命令、
# 拼错参数（例如场景检测遗漏开场第一帧、时间戳对不上帧文件等)。
#
# 用法:
#   bash extract_shots.sh -u <视频URL或本地文件路径> -o <输出目录> [-t 场景阈值] [-i 补抽间隔秒数] [-s 最大高度]
#
# 示例:
#   bash extract_shots.sh -u "https://www.bilibili.com/video/BVxxxxxx" -o ./analysis
#   bash extract_shots.sh -u "./my_video.mp4" -o ./analysis -t 0.22 -i 2
#
# 参数说明:
#   -u  视频 URL（http/https 开头会走 yt-dlp 下载）或本地视频文件路径
#   -o  输出目录（会自动创建 frames/ audio/ subs/ meta/ 子目录）
#   -t  场景切换检测阈值，默认 0.28。数值越小越敏感（切出的镜头更多）。
#       如果发现明显漏掉了镜头切换，调低到 0.15~0.2 再跑一次。
#   -i  补充抽帧的间隔秒数（可选）。当场景检测抓到的镜头数量明显偏少
#       （例如整条视频只切出 1~2 张图，但视频有 30 秒以上），
#       很可能是慢速运镜/推拉摇移没有触发"场景切换"，用这个参数按固定间隔兜底抽帧。
#   -s  下载视频的最大高度，默认 1080（无需更高分辨率来做构图/色彩分析，下载更快）。
#
# 依赖: yt-dlp, ffmpeg, ffprobe
#   - URL 输入需要 yt-dlp，以及当前网络环境能访问目标视频平台（YouTube/B站/抖音等）。
#   - 如果目标平台有登录墙、地区限制或反爬机制导致下载失败，脚本会提示失败原因，
#     请改用 SKILL.md 中"素材获取失败时的降级方案"。

set -euo pipefail

THRESHOLD=0.28
INTERVAL=""
MAX_HEIGHT=1080
INPUT=""
OUTDIR=""

while getopts "u:o:t:i:s:" opt; do
  case $opt in
    u) INPUT="$OPTARG" ;;
    o) OUTDIR="$OPTARG" ;;
    t) THRESHOLD="$OPTARG" ;;
    i) INTERVAL="$OPTARG" ;;
    s) MAX_HEIGHT="$OPTARG" ;;
    *) echo "未知参数"; exit 1 ;;
  esac
done

if [[ -z "$INPUT" || -z "$OUTDIR" ]]; then
  echo "用法: bash $0 -u <视频URL或本地路径> -o <输出目录> [-t 场景阈值=0.28] [-i 补抽间隔秒数] [-s 最大高度=1080]"
  exit 1
fi

# ---- 0. 依赖自检 ----
for _bin in ffmpeg ffprobe; do
  command -v "$_bin" >/dev/null || {
    echo "❌ 缺少 $_bin。安装方式：macOS \`brew install ffmpeg\` / Linux \`apt install ffmpeg\` / Windows \`winget install Gyan.FFmpeg\`"
    exit 1
  }
done

# ffmpeg 5.0 起 -vsync 被 -fps_mode 取代（旧参数仍可用但会告警），这里按实际支持情况选。
# 用纯字符串匹配（避免 `printf | grep -q` 因管道提前关闭触发 SIGPIPE / Broken pipe 告警）。
FFHELP="$(ffmpeg -hide_banner -h full 2>/dev/null || true)"
if [[ "$FFHELP" == *"-fps_mode"* ]]; then
  FPS_ARGS=(-fps_mode vfr)
else
  FPS_ARGS=(-vsync vfr)
fi
unset FFHELP

mkdir -p "$OUTDIR/frames" "$OUTDIR/audio" "$OUTDIR/subs" "$OUTDIR/meta"

# ---- 1. 获取视频本体 ----
if [[ "$INPUT" =~ ^https?:// ]]; then
  echo "[1/5] 检测到 URL，使用 yt-dlp 下载视频、字幕与元数据..."
  command -v yt-dlp >/dev/null || {
    echo "❌ 缺少 yt-dlp。安装方式：Linux/macOS \`pip install yt-dlp --break-system-packages\` / Windows \`pip install yt-dlp\`"
    exit 1
  }
  yt-dlp -f "bv*[height<=${MAX_HEIGHT}]+ba/b[height<=${MAX_HEIGHT}]" \
    --merge-output-format mp4 \
    --write-info-json \
    --write-subs --write-auto-subs --sub-langs "zh-Hans,zh,en,en-orig" --convert-subs srt \
    --sleep-requests 1 \
    -o "$OUTDIR/source.%(ext)s" \
    "$INPUT" || echo "⚠️ 下载或字幕获取部分失败（可能是登录墙/地区限制/反爬），若视频文件仍已生成可继续；否则请见 SKILL.md 的降级方案"
  find "$OUTDIR" -maxdepth 1 -name "source*.srt" -exec mv {} "$OUTDIR/subs/" \; 2>/dev/null || true
  find "$OUTDIR" -maxdepth 1 -name "source*.info.json" -exec mv {} "$OUTDIR/meta/" \; 2>/dev/null || true
  VIDEO_FILE=$(find "$OUTDIR" -maxdepth 1 -name "source.*" \( -iname "*.mp4" -o -iname "*.mkv" -o -iname "*.webm" -o -iname "*.mov" \) | head -n1)
else
  echo "[1/5] 检测到本地文件路径，直接使用..."
  EXT="${INPUT##*.}"
  cp "$INPUT" "$OUTDIR/source.${EXT}"
  VIDEO_FILE="$OUTDIR/source.${EXT}"
fi

if [[ -z "${VIDEO_FILE:-}" || ! -f "$VIDEO_FILE" ]]; then
  echo "❌ 未找到视频文件，请检查 URL / 网络 / 登录态，或改用本地文件路径重试"
  exit 1
fi
echo "视频文件: $VIDEO_FILE"

# ---- 2. 元数据 ----
echo "[2/5] 读取元数据..."
ffprobe -v error -select_streams v:0 \
  -show_entries stream=width,height,r_frame_rate,duration,codec_name \
  -of default=noprint_wrappers=1 "$VIDEO_FILE" | tee "$OUTDIR/meta/video_stream.txt"

# ---- 3. 场景检测抽帧（含开场首帧，避免漏掉第一个镜头）----
echo "[3/5] 场景切换检测抽帧 (阈值=${THRESHOLD})..."
ffmpeg -y -i "$VIDEO_FILE" \
  -vf "select='eq(n\,0)+gt(scene\,${THRESHOLD})',showinfo" \
  "${FPS_ARGS[@]}" "$OUTDIR/frames/shot_%04d.jpg" \
  -loglevel info 2> "$OUTDIR/meta/scene_log.txt"
grep -o "pts_time:[0-9.]*" "$OUTDIR/meta/scene_log.txt" | cut -d: -f2 > "$OUTDIR/frames/timestamps.txt"
SHOT_COUNT=$(ls "$OUTDIR"/frames/shot_*.jpg 2>/dev/null | wc -l | tr -d ' ')
echo "检测到 ${SHOT_COUNT} 个镜头切换点。frames/shot_0001.jpg, shot_0002.jpg... 与 frames/timestamps.txt 按行号一一对应（第 N 行 = 第 N 张图的起始时间，单位秒）。"

# ---- 4. 可选：补充等间隔抽帧 ----
if [[ -n "$INTERVAL" ]]; then
  echo "[4/5] 额外按 ${INTERVAL} 秒间隔补抽帧，用于覆盖场景检测可能漏掉的慢切/推拉摇移镜头..."
  mkdir -p "$OUTDIR/frames_interval"
  ffmpeg -y -i "$VIDEO_FILE" -vf "fps=1/${INTERVAL}" "$OUTDIR/frames_interval/t_%04d.jpg" -loglevel error
  echo "已生成，第 N 张对应时间 ≈ (N-1) × ${INTERVAL} 秒。"
else
  echo "[4/5] 跳过等间隔补抽（未指定 -i）。如果第3步测出的镜头数明显偏少，重新运行并加上 -i 2 再看。"
fi

# ---- 5. 提取音频 ----
echo "[5/5] 提取音频..."
ffmpeg -y -i "$VIDEO_FILE" -vn -c:a libmp3lame -q:a 2 "$OUTDIR/audio/audio.mp3" -loglevel error

echo ""
echo "✅ 完成。输出目录结构："
echo "  $OUTDIR/frames/shot_XXXX.jpg      — 关键帧（按场景切换抽取）"
echo "  $OUTDIR/frames/timestamps.txt     — 每张关键帧对应的时间码（秒，按行号对应文件序号）"
echo "  $OUTDIR/frames_interval/          — 等间隔补充帧（仅当使用了 -i 参数）"
echo "  $OUTDIR/audio/audio.mp3           — 完整音轨"
echo "  $OUTDIR/subs/                     — 平台字幕（若有，SRT 格式）"
echo "  $OUTDIR/meta/                     — 元数据、场景检测日志、原始 info.json"
echo ""
echo "下一步：用宿主的读图能力逐张查看 frames/ 里的关键帧，对照 SKILL.md 第四步的分析维度清单展开分析。"
