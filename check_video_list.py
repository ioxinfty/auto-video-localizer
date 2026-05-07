#!/usr/bin/env python3
"""
解析 YouTube 播放列表 HTML 中的视频标题，与源目录中的视频、字幕、封面进行匹配，
生成 Markdown 报告，并输出 task_queue 供批量视频转换使用。

video_list.html 来源：YouTube 播放列表页面保存的完整 HTML
例如 https://www.youtube.com/playlist?list=PLhGl0l5La4sYXjYOBv7h9l7x6qNuW34Cx

输出:
- video_list_check_report.md: Markdown 匹配报告
- task_queue: list[tuple], 每项 (序号, 标题, 视频路径, 字幕路径, 封面路径), 缺失为 ''
"""

from pathlib import Path
from lxml import html

# ==================== 配置 ====================
SCRIPT_DIR = Path(__file__).resolve().parent
HTML_FILE = SCRIPT_DIR / "video_list.html"
SOURCE_DIR = SCRIPT_DIR / "source" / "aiagent_youtube" / "AI in C# (Microsoft Agent Framework)"
OUTPUT_FILE = SCRIPT_DIR / "video_list_check_report.md"

# ================================================================
# 步骤1: 解析 video_list.html, 用 xpath //a[@id='video-title'] 取出 title
# ================================================================

tree = html.fromstring(HTML_FILE.read_bytes())

# 优先尝试 a 标签，如果没有则尝试 span 标签
elements = tree.xpath("//a[@id='video-title']")
if not elements:
    elements = tree.xpath("//span[@id='video-title']")

titles = [elem.get("title") for elem in elements]

# ================================================================
# 步骤2: 扫描源目录中的视频(.mkv)、字幕(.vtt)、封面(.webp) 文件
# ================================================================

video_files = list(SOURCE_DIR.glob("*.mkv"))
subtitle_files = list(SOURCE_DIR.glob("*.vtt"))
cover_files = list(SOURCE_DIR.glob("*.webp"))


def get_stem(filename):
    """去掉 .1080p / .720p / .en 后缀得到纯标题部分"""
    stem = filename.stem
    for suffix in (".1080p", ".720p", ".en"):
        if stem.endswith(suffix):
            return stem[: -len(suffix)]
    return stem


def normalize(text):
    """统一全角/半角字符用于匹配"""
    return (
        text.replace("\uff1a", ":")   # ：→ :
        .replace("\uff1f", "?")       # ？→ ?
        .replace("\u29f8", "/")       # ⧸ → /
        .replace("\uff08", "(")        # （→ (
        .replace("\uff09", ")")        # ）→ )
    )


# 构建: 标题(标准化后) -> 视频文件路径
video_map = {}
for vf in video_files:
    key = normalize(get_stem(vf))
    video_map[key] = vf

# 构建: 标题(标准化后) -> 字幕文件路径
subtitle_map = {}
for sf in subtitle_files:
    key = normalize(get_stem(sf))
    subtitle_map[key] = sf

# 构建: 标题(标准化后) -> 封面文件路径
cover_map = {}
for cf in cover_files:
    key = normalize(get_stem(cf))
    cover_map[key] = cf


def find_match(title):
    """
    给定 HTML 标题, 在源目录中找对应的视频、字幕和封面。
    返回 (视频文件, 字幕文件, 封面文件, 备注)
    """
    t_norm = normalize(title)
    note_parts = []

    # --- 匹配视频 ---
    video = None
    if t_norm in video_map:
        video = video_map[t_norm]
    else:
        # 二次尝试: 忽略大小写/空格/连字符差异
        for vk, vv in video_map.items():
            vk_norm = vk.lower().replace(" ", "").replace("-", "").replace("_", "")
            t_norm_simple = t_norm.lower().replace(" ", "").replace("-", "").replace("_", "")
            if vk_norm == t_norm_simple:
                video = vv
                break
        # 三次尝试: 模糊匹配
        if not video:
            for vk, vv in video_map.items():
                if vk in t_norm or t_norm in vk:
                    if not video:
                        video = vv

    # 分析差异原因
    if not video:
        note_parts.append("❌ 缺少视频")
        candidates = []
        for vk, vv in video_map.items():
            if len(vk) == len(t_norm):
                diffs = [(a, b) for a, b in zip(vk, t_norm) if a != b]
                if len(diffs) <= 5:
                    candidates.append((vv, diffs))

        if candidates and len(candidates[0][1]) <= 5:
            best_v, best_diffs = candidates[0]
            diff_str = ", ".join([f"'{d[0]}'→'{d[1]}'" for d in best_diffs[:4]])
            note_parts[-1] = f"⚠️ 视频名有差异({diff_str})"
            video = best_v
    else:
        if ".720p" in video.stem:
            note_parts.append("ℹ️ 720p 分辨率")

    # --- 匹配字幕 ---
    subtitle = None
    s_key = normalize(get_stem(video)) if video else t_norm
    if s_key in subtitle_map:
        subtitle = subtitle_map[s_key]
    else:
        for sk, sv in subtitle_map.items():
            if sk in s_key or s_key in sk:
                subtitle = sv
                break
        if not subtitle:
            note_parts.append("❌ 缺少字幕")

    # --- 匹配封面 ---
    cover = None
    c_key = normalize(get_stem(video)) if video else t_norm
    if c_key in cover_map:
        cover = cover_map[c_key]
    else:
        for ck, cv in cover_map.items():
            if ck in c_key or c_key in ck:
                cover = cv
                break
        # 封面缺失不记为错误（可选），仅静默返回 None

    note = " | ".join(note_parts) if note_parts else "✅"
    return video, subtitle, cover, note


# ================================================================
# 步骤3: 输出 Markdown 表格 + 构建 task_queue
# ================================================================

lines = []
lines.append("# 视频文件匹配报告\n")
lines.append(f"从 HTML 中提取到 **{len(titles)}** 个视频标题，源目录中共有 "
             f"**{len(video_files)}** 个视频文件、**{len(subtitle_files)}** 个字幕文件、"
             f"**{len(cover_files)}** 个封面文件。\n")
lines.append("| 序号 | 标题 | 视频文件 | 字幕文件 | 封面文件 | 备注 |")
lines.append("|:---:|------|----------|----------|----------|:----|")

match_count = 0
issue_count = 0
no_video_count = 0
no_subtitle_count = 0

# ========== task_queue: 为批量转换准备的队列 ==========
# 每项: (序号, 标题名, 视频文件路径, 字幕文件路径, 封面文件路径)
# 缺失的字幕或封面保存为 '' (空字符串)
task_queue = []

for i, title in enumerate(titles, 1):
    video, subtitle, cover, note = find_match(title)

    video_name = video.name if video else "(无)"
    sub_name = subtitle.name if subtitle else "(无)"
    cover_name = cover.name if cover else "(无)"

    # 统计问题
    if "❌ 缺少视频" in note or "⚠️" in note:
        issue_count += 1
    if "(无)" == video_name:
        no_video_count += 1
    if "缺少字幕" in note:
        no_subtitle_count += 1
    if note == "✅":
        match_count += 1

    lines.append(f"| {i} | {title} | {video_name} | {sub_name} | {cover_name} | {note} |")

    # === 构建队列项 ===
    task_queue.append((
        i,
        title,
        str(video) if video else "",          # 视频路径，无则为 ''
        str(subtitle) if subtitle else "",     # 字幕路径，无则为 ''
        str(cover) if cover else "",           # 封面路径，无则为 ''
    ))

# 统计摘要
lines.append("\n## 📊 统计摘要\n")
lines.append(f"| 项目 | 数量 |")
lines.append("|:------|-----:|")
lines.append(f"| 总标题数 (HTML) | {len(titles)} |")
lines.append(f"| ✅ 完全匹配 | {match_count} |")
lines.append(f"| ⚠️ 有问题 | {issue_count} |")
if no_video_count:
    lines.append(f"| └─ ❌ 缺少视频 | {no_video_count} |")
if no_subtitle_count:
    lines.append(f"| └─ ❌ 缺少字幕 | {no_subtitle_count} |")

# 写入 Markdown 文件
OUTPUT_FILE.write_text("\n".join(lines), encoding="utf-8")
print(f"报告已写入: {OUTPUT_FILE}")
print(f"task_queue 共 {len(task_queue)} 项，可直接用于批量视频转换")

# 写入 task_queue.py (Python 字典，供批量处理脚本导入)
TASK_QUEUE_FILE = SCRIPT_DIR / "task_queue.py"
queue_dict = {
    i: {
        "seq": item[0],
        "title": item[1],
        "video": item[2],
        "subtitle": item[3],
        "cover": item[4],
    }
    for i, item in enumerate(task_queue)
}
TASK_QUEUE_FILE.write_text(f"# 自动生成，请勿手动修改\n\ntask_queue = {queue_dict}\n", encoding="utf-8")
print(f"task_queue 已保存: {TASK_QUEUE_FILE}")

# ==================== 调试：预览前 5 项 ====================
if __name__ == "__main__":
    print("\n--- task_queue 预览 (前 5 项) ---")
    for idx, item in enumerate(task_queue[:5], 1):
        seq, title, vpath, spath, cpath = item
        print(f"  [{idx}] 序号={seq}")
        print(f"      标题={title}")
        print(f"      视频={vpath or '(空)'}")
        print(f"      字幕={spath or '(空)'}")
        print(f"      封面={cpath or '(空)'}")
        print()
