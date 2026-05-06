"""
英文教学视频 → 中文配音
完整流水线脚本

依赖安装：
pip install edge-tts pydub ollama faster-whisper python-dotenv

使用说明：
1. 配置好 Ollama（本地翻译）或 DashScope API Key（云端翻译）
2. 配置好 Edge-TTS（无需 API Key，直接用）
3. 修改底部的 MAIN 函数中的路径，运行即可
"""

import re
import json
import os
import shutil
import subprocess
import asyncio
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, asdict


# ============================================================
# 配置区
# ============================================================

@dataclass
class Config:
    """全局配置"""
    # Ollama 远程地址（留空使用默认 localhost:11434）
    ollama_base_url: str = ""

    # 翻译方式：True=本地 Ollama，False=阿里云 DashScope API
    use_local_llm: bool = True

    # Ollama 配置
    ollama_model: str = "qwen2.5:7b"  # 或 qwen2.5:3b（更快）

    # DashScope API Key（use_local_llm=False 时需要）
    dashscope_key: str = ""

    # TTS 配置
    tts_voice: str = "zh-CN-XiaoxiaoNeural"  # 晓晓，教学推荐
    # 其他可选：zh-CN-YunxiNeural（云希，男声）
    #           zh-CN-XiaoyiNeural（晓伊，女声，年轻）

    # 中文朗读速率（字/秒），用于估算时长
    chinese_reading_speed: float = 4.5  # 正常语速

    # 语速比例限制（防止失真）
    speed_ratio_min: float = 0.6
    speed_ratio_max: float = 1.4

    # Edge-TTS 延时（秒），避免请求过快被限
    tts_delay: float = 0.2

    # 音频格式
    audio_format: str = "wav"  # wav 或 mp3

    # BGM 配置
    keep_original_bgm: bool = True  # 是否保留原视频背景音乐
    bgm_volume: float = 0.25  # BGM 音量（0.0-1.0）

    # 临时文件目录
    temp_dir: str = "temp"


# ============================================================
# 第一步：读取 SRT 文件
# ============================================================

def load_srt(srt_path: str) -> list[dict]:
    """
    读取 SRT/VTT 文件，返回结构化列表
    """
    segments = []
    with open(srt_path, "r", encoding="utf-8") as f:
        content = f.read().strip()

    # 跳过 WEBVTT 头
    if content.startswith("WEBVTT"):
        # 跳过 WEBVTT 头和空行
        lines = content.split("\n")
        start_idx = 0
        for i, line in enumerate(lines):
            if line.strip() and not line.startswith("WEBVTT"):
                start_idx = i
                break
        content = "\n".join(lines[start_idx:])

    blocks = re.split(r"\n\n+", content)
    for block in blocks:
        lines = [l.strip() for l in block.strip().split("\n") if l.strip()]
        if len(lines) < 3:
            continue

        # 解析序号（可能是纯数字，也可能在 NOTE 块里）
        try:
            idx = int(lines[0])
        except ValueError:
            continue

        # 解析时间戳 "00:00:00.710 --> 00:00:03.730"
        time_line = lines[1]
        if "-->" not in time_line:
            continue

        start_str, end_str = time_line.split("-->")
        start = srt_time_to_seconds(start_str.strip())
        end = srt_time_to_seconds(end_str.strip())
        text = " ".join(lines[2:]).strip()

        if text:
            segments.append({
                "index": idx,
                "start": round(start, 3),
                "end": round(end, 3),
                "text": text,
                "duration": round(end - start, 3)
            })

    return segments


def srt_time_to_seconds(t: str) -> float:
    """SRT/VTT 时间戳 → 秒数"""
    t = t.strip().replace(",", ".")
    parts = t.split(":")

    if len(parts) == 3:
        h, m, s = parts
        s_parts = s.split(".")
        return int(h) * 3600 + int(m) * 60 + float(s)
    elif len(parts) == 2:
        m, s = parts
        s_parts = s.split(".")
        return int(m) * 60 + float(s)
    else:
        return float(t)


def seconds_to_srt_time(seconds: float) -> str:
    """秒数 → SRT 时间戳"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def save_srt(segments: list[dict], output_path: str):
    """保存为 SRT 文件"""
    with open(output_path, "w", encoding="utf-8") as f:
        for i, seg in enumerate(segments, 1):
            f.write(f"{i}\n")
            f.write(f"{seconds_to_srt_time(seg['start'])} --> {seconds_to_srt_time(seg['end'])}\n")
            f.write(f"{seg['text']}\n\n")


# ============================================================
# 第二步：拼接碎片句 → 完整句子
# ============================================================

def merge_segments_to_sentences(segments: list[dict]) -> list[dict]:
    """
    将 Whisper 的碎片段拼接成完整句子。

    判断依据：
    1. 段尾是否有句末标点（. ! ? 。！？ " ' ）
    2. 或段尾是否为常见缩写（如 Dr. Mr. e.g.）
    3. 连续多段但中间有明显停顿（>1秒），也视为分隔
    """
    sentences = []
    current = None
    last_end = 0

    for seg in segments:
        if current is None:
            current = {
                "seg_indices": [seg["index"]],
                "text": seg["text"],
                "start": seg["start"],
                "end": seg["end"],
            }
        else:
            current["seg_indices"].append(seg["index"])
            current["text"] += " " + seg["text"]
            current["end"] = seg["end"]

        # 判断是否句末
        text = seg["text"].strip()

        # 检查是否有句末标点
        is_sentence_end = _is_sentence_ending(text)

        # 检查是否有明显停顿（超过 1 秒视为分隔）
        pause_duration = seg["start"] - last_end
        if pause_duration > 1.5 and len(current["text"].split()) > 5:
            is_sentence_end = True

        last_end = seg["end"]

        if is_sentence_end:
            current["text"] = current["text"].strip()
            sentences.append(current)
            current = None

    # 兜底：最后一段没有句末标点
    if current is not None:
        current["text"] = current["text"].strip()
        sentences.append(current)

    return sentences


def _is_sentence_ending(text: str) -> bool:
    """判断文本是否以句末标点结尾"""
    text = text.strip()

    # 空文本不算
    if not text:
        return False

    # 常见句末标点
    if text[-1] in (".", "!", "?", "。", "！", "？"):
        return True

    # 常见缩写（不算句末）
    common_abbrevs = (
        "dr", "mr", "mrs", "ms", "prof", "vs", "etc",
        "e.g", "i.e", "inc", "llc", "co", "ltd",
        "a.m", "p.m", "U.S", "U.K", "API", "LLM", "AI",
        "HTML", "CSS", "JSON", "XML", "API", "SDK"
    )

    # 检查是否以 "X." 结尾且 X 是常见缩写
    if len(text) > 2 and text[-1] == ".":
        word_before = text.split()[-1].rstrip(".")
        if word_before.lower() in common_abbrevs:
            return False

    return False


# ============================================================
# 第三步：翻译（Ollama 本地 或 DashScope 云端）
# ============================================================

def translate_sentences(sentences: list[dict], config: Config) -> list[dict]:
    """翻译完整句子"""
    if config.use_local_llm:
        return _translate_with_ollama(sentences, config)
    else:
        return _translate_with_dashscope(sentences, config)


def _translate_with_ollama(sentences: list[dict], config: Config) -> list[dict]:
    """使用本地 Ollama + Qwen 翻译（支持远程地址）"""
    try:
        import ollama
    except ImportError:
        raise ImportError("请安装 ollama: pip install ollama")

    # 远程 Ollama 地址（如 http://192.168.0.80:11434）
    base_url = config.ollama_base_url or os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    client = ollama.Client(host=base_url)

    BATCH_SIZE = 10
    output_dir = Path(getattr(config, 'output_dir', 'output'))
    debug_dir = output_dir / "debug_translations"
    debug_dir.mkdir(parents=True, exist_ok=True)

    for i in range(0, len(sentences), BATCH_SIZE):
        batch = sentences[i:i + BATCH_SIZE]
        batch_idx = i // BATCH_SIZE + 1
        batch_texts = "\n".join([
            f"{j + 1}. {s['text']}"
            for j, s in enumerate(batch)
        ])

        prompt = f"""你是一个专业的英文教学视频字幕翻译专家。请将以下英文字幕翻译为中文，要求：

1. 保持教学语气，自然、清晰、易懂
2. 保留专业术语不翻译（如 Agent, LLM, API, SDK, GitHub, NuGet 等）
3. 适当增补语气词，使中文听起来更自然（如"好"、"那么"、"我们来看"）
4. 按序号逐条返回，格式：序号. 中文翻译
5. 不要添加多余解释

{'-' * 40}
{batch_texts}
{'-' * 40}
返回示例：
1. 让我们开始使用 Agent 框架
2. 现在我们已经建立了原始连接
"""

        try:
            print(f"  📤 发送翻译请求到 {base_url} (批次 {batch_idx})...")
            response = client.chat(
                model=config.ollama_model,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0.3}  # 降低随机性，保持翻译一致
            )

            raw_response = response['message']['content']
            # 保存原始响应用于调试
            debug_file = debug_dir / f"batch_{batch_idx:03d}_raw.txt"
            debug_file.write_text(raw_response, encoding='utf-8')

            translations = _parse_translations(raw_response, len(batch))

            # 检查解析结果
            success_count = sum(1 for t in translations if t and "（翻译失败）" not in t)
            print(f"  📥 批次 {batch_idx} 解析完成: {success_count}/{len(batch)} 条成功")

            if len(translations) != len(batch):
                print(f"  ⚠️ 警告: 解析数量不匹配! 期望 {len(batch)}, 实际 {len(translations)}")

            for j, t in enumerate(translations):
                sentences[i + j]["cn_text"] = t.strip()
                if "（翻译失败）" in t:
                    print(f"  ❌ 翻译 [{i + j + 1}/{len(sentences)}]: 解析失败 - {batch[j]['text'][:30]}...")
                else:
                    print(f"  ✅ 翻译 [{i + j + 1}/{len(sentences)}]: {t[:40]}...")

            # 保存解析后的结果
            parsed_file = debug_dir / f"batch_{batch_idx:03d}_parsed.txt"
            parsed_file.write_text("\n".join([f"{j+1}. {t}" for j, t in enumerate(translations)]), encoding='utf-8')

        except Exception as e:
            print(f"  ⚠️ 翻译批次 {batch_idx} 失败: {e}")
            for j in range(len(batch)):
                sentences[i + j]["cn_text"] = f"[翻译失败] {batch[j]['text']}"

    return sentences


def _translate_with_dashscope(sentences: list[dict], config: Config) -> list[dict]:
    """使用阿里云 DashScope Qwen API 翻译"""
    try:
        import dashscope
        from dashscope import Generation
    except ImportError:
        raise ImportError("请安装 dashscope: pip install dashscope")

    if not config.dashscope_key:
        raise ValueError("请设置 DashScope API Key")

    dashscope.api_key = config.dashscope_key
    BATCH_SIZE = 10
    output_dir = Path(getattr(config, 'output_dir', 'output'))
    debug_dir = output_dir / "debug_translations"
    debug_dir.mkdir(parents=True, exist_ok=True)

    for i in range(0, len(sentences), BATCH_SIZE):
        batch = sentences[i:i + BATCH_SIZE]
        batch_idx = i // BATCH_SIZE + 1
        batch_texts = "\n".join([f"{j + 1}. {s['text']}" for j, s in enumerate(batch)])

        prompt = f"翻译为教学语气中文，保留专业术语：\n{batch_texts}"

        try:
            print(f"  📤 发送翻译请求到 DashScope (批次 {batch_idx})...")
            response = Generation.call(
                model="qwen-turbo",
                prompt=prompt
            )

            raw_response = response.output.text
            # 保存原始响应用于调试
            debug_file = debug_dir / f"batch_{batch_idx:03d}_raw.txt"
            debug_file.write_text(raw_response, encoding='utf-8')

            translations = _parse_translations(raw_response, len(batch))

            # 检查解析结果
            success_count = sum(1 for t in translations if t and "（翻译失败）" not in t)
            print(f"  📥 批次 {batch_idx} 解析完成: {success_count}/{len(batch)} 条成功")

            for j, t in enumerate(translations):
                sentences[i + j]["cn_text"] = t.strip()
                if "（翻译失败）" in t:
                    print(f"  ❌ 翻译 [{i + j + 1}/{len(sentences)}]: 解析失败 - {batch[j]['text'][:30]}...")
                else:
                    print(f"  ✅ 翻译 [{i + j + 1}/{len(sentences)}]: {t[:40]}...")

            # 保存解析后的结果
            parsed_file = debug_dir / f"batch_{batch_idx:03d}_parsed.txt"
            parsed_file.write_text("\n".join([f"{j+1}. {t}" for j, t in enumerate(translations)]), encoding='utf-8')

        except Exception as e:
            print(f"  ⚠️ 翻译批次 {batch_idx} 失败: {e}")
            for j in range(len(batch)):
                sentences[i + j]["cn_text"] = f"[翻译失败] {batch[j]['text']}"

    return sentences


def _parse_translations(raw: str, expected_count: int) -> list[str]:
    """解析 LLM 返回的多条翻译结果"""
    results = []
    raw = raw.strip()

    print(f"  🔍 解析翻译结果 (期望 {expected_count} 条):")

    # 方法1: 匹配 "1. 中文" 或 "1: 中文" 格式
    matched_lines = []
    for line in raw.split("\n"):
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^\s*(\d+)[\.\:\、\—\-]\s*(.+)$", line)
        if m:
            translation = m.group(2).strip()
            matched_lines.append((m.group(1), translation))
            results.append(translation)
            print(f"    方法1匹配 [{m.group(1)}]: {translation[:50]}...")

    print(f"  📊 方法1匹配: {len(matched_lines)}/{expected_count} 条")

    # 方法2: 如果方法1匹配不足，尝试提取所有非空行
    if len(results) < expected_count:
        print(f"  📊 尝试方法2补充...")
        lines = []
        for line in raw.split("\n"):
            line = line.strip()
            if not line:
                continue
            # 跳过纯数字行、markdown标记行
            if re.match(r"^[\d\s]+$", line):
                continue
            if line.startswith("```") or line.startswith("【") or line.startswith("["):
                continue
            # 移除可能的引号包裹
            line = re.sub(r'^["""\'""\']+|["""\'""\']+$', '', line)
            if line:
                lines.append(line)
                print(f"    方法2补充: {line[:50]}...")

        # 补充缺失的结果
        added = 0
        for line in lines[:expected_count]:
            if line not in results:
                results.append(line)
                added += 1
        print(f"  📊 方法2补充: {added} 条")

    # 确保数量正确
    while len(results) < expected_count:
        results.append("（翻译失败）")
        print(f"  ⚠️ 补充分配失败标记")

    final_count = sum(1 for r in results if r and "（翻译失败）" not in r)
    print(f"  ✅ 最终解析结果: {final_count}/{expected_count} 有效")

    return results[:expected_count]


# ============================================================
# 第四步：计算 TTS 语速参数
# ============================================================

def prepare_tts_tasks(sentences: list[dict], config: Config) -> list[dict]:
    """
    为每个句子计算目标时长和语速参数
    """
    for s in sentences:
        original_duration = s["end"] - s["start"]
        cn_chars = len(re.sub(r"\s+", "", s.get("cn_text", "")))

        # 估算中文朗读时长
        estimated_cn_duration = cn_chars / config.chinese_reading_speed

        # 计算语速比例
        if original_duration > 0:
            speed_ratio = estimated_cn_duration / original_duration
        else:
            speed_ratio = 1.0

        # 限制范围，防止失真
        speed_ratio = max(config.speed_ratio_min, min(speed_ratio, config.speed_ratio_max))

        s["cn_chars"] = cn_chars
        s["original_duration"] = original_duration
        s["estimated_cn_duration"] = estimated_cn_duration
        s["speed_ratio"] = round(speed_ratio, 2)

    return sentences


# ============================================================
# 第五步：Edge-TTS 合成中文语音
# ============================================================

async def synthesize_with_edgetts(sentences: list[dict], config: Config, output_dir: str):
    """
    使用 Edge-TTS 逐句合成中文语音
    """
    try:
        import edge_tts
    except ImportError:
        raise ImportError("请安装 edge_tts: pip install edge-tts")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    for idx, s in enumerate(sentences):
        seg_idx = s["seg_indices"][0]
        out_path = output_dir / f"{seg_idx:04d}.wav"

        # 跳过已合成的（断点续传）
        if out_path.exists() and out_path.stat().st_size > 1000:
            s["cn_audio_path"] = str(out_path)
            print(f"  ⏭️ 跳过 [{idx + 1}/{len(sentences)}]: 已存在")
            continue

        # 将 speed_ratio 转换为 Edge-TTS rate 参数
        # speed_ratio > 1 → 中文比原音长 → 需要加速
        # speed_ratio < 1 → 中文比原音短 → 需要减速
        rate_percent = int((1.0 / s["speed_ratio"] - 1) * 100)
        rate_str = f"{rate_percent:+}%" if rate_percent != 0 else "0%"

        # 限制范围
        rate_percent = max(-50, min(rate_percent, 50))
        rate_str = f"{rate_percent:+}%"

        try:
            communicate = edge_tts.Communicate(
                s.get("cn_text", ""),
                voice=config.tts_voice,
                rate=rate_str,
                volume="+0%"
            )
            await communicate.save(str(out_path))

            s["cn_audio_path"] = str(out_path)
            print(f"  ✅ [{idx + 1}/{len(sentences)}] rate={rate_str}: {s.get('cn_text', '')[:35]}...")

        except Exception as e:
            print(f"  ❌ TTS 失败 [{idx + 1}]: {e}")
            s["cn_audio_path"] = None

        # 延时，避免请求过快
        await asyncio.sleep(config.tts_delay)

    return sentences


def sync_synthesize_with_edgetts(sentences: list[dict], config: Config, output_dir: str):
    """同步封装"""
    return asyncio.run(synthesize_with_edgetts(sentences, config, output_dir))


# ============================================================
# 第六步：Pydub 拼接音频 + 时间轴对齐
# ============================================================

def stitch_audio_segments(sentences: list[dict], config: Config, output_path: str) -> str:
    """
    将所有 TTS 音频片段按时间轴拼接成完整音频

    策略：
    1. 每个句子在其原始时间段内播放
    2. 如果中文语音比原时段短 → 前面正常说，后面补静音
    3. 如果中文语音比原时段长 → TTS 已调速，或截断超出的部分
    """
    from pydub import AudioSegment
    from pydub.effects import speedup

    print("\n📦 拼接音频片段...")

    # 创建空白音频（用于累积）
    # 先确定总时长（最后一个句子的结束时间）
    total_duration = max(s["end"] for s in sentences)
    print(f"  总时长: {total_duration:.2f} 秒")

    # 创建全零音频（毫秒）
    combined: Optional[AudioSegment] = None

    for idx, s in enumerate(sentences):
        start_ms = int(s["start"] * 1000)
        end_ms = int(s["end"] * 1000)
        seg_duration_ms = end_ms - start_ms

        if s.get("cn_audio_path") and Path(s["cn_audio_path"]).exists():
            audio: AudioSegment = AudioSegment.from_file(s["cn_audio_path"])
        else:
            # 没有音频，生成静音
            audio = AudioSegment.silent(duration=seg_duration_ms)
            print(f"  ⚠️ [{idx + 1}] 无音频，使用静音填充")
            s["final_audio_path"] = None
            continue

        # 如果音频比目标时段短 → 补静音
        if len(audio) < seg_duration_ms:
            silence = AudioSegment.silent(duration=seg_duration_ms - len(audio))
            audio = audio + silence

        # 如果音频比目标时段长 → 截断
        elif len(audio) > seg_duration_ms:
            audio = audio[:seg_duration_ms]

        # 累积到总音频
        if combined is None:
            # 填充开头空白（如果有）
            if start_ms > 0:
                silence = AudioSegment.silent(duration=start_ms)
                combined = silence + audio
            else:
                combined = audio
        else:
            # 填充当前片段之前的空白（如果有）
            current_len = len(combined)
            if start_ms > current_len:
                silence = AudioSegment.silent(duration=start_ms - current_len)
                combined = combined + silence

            combined = combined + audio

        s["final_audio_path"] = s["cn_audio_path"]

    # 确保总时长足够
    if combined:
        total_ms = int(total_duration * 1000)
        if len(combined) < total_ms:
            silence = AudioSegment.silent(duration=total_ms - len(combined))
            combined = combined + silence

        # 导出
        combined.export(output_path, format=config.audio_format)
        print(f"  ✅ 音频拼接完成: {output_path}")
        return output_path
    else:
        raise ValueError("没有可拼接的音频片段")


# ============================================================
# 第七步：提取原视频音频、混合 BGM、合并视频
# ============================================================

def process_video(
    video_path: str,
    chinese_audio_path: str,
    output_path: str,
    config: Config,
    chinese_srt_path: str = None,
    english_srt_path: str = None
):
    """
    使用 FFmpeg 合并视频和中文音频

    流程：
    1. 提取原视频的音频轨道（作为 BGM 参考）
    2. 如果保留 BGM → 降低音量后与中文音频混合
    3. 用新音频轨道替换原视频的音频
    4. 可选：烧录字幕到视频
    """
    video_path = Path(video_path)
    chinese_audio_path = Path(chinese_audio_path)
    output_path = Path(output_path)

    temp_dir = Path(config.temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)

    original_audio_path = temp_dir / "original_audio.wav"
    mixed_audio_path = temp_dir / "mixed_audio.wav"

    print(f"\n🎬 处理视频: {video_path.name}")

    # Step 1: 提取原视频音频
    print("  1️⃣ 提取原音频轨道...")
    subprocess.run([
        "ffmpeg", "-y", "-i", str(video_path),
        "-vn", "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "2",
        str(original_audio_path)
    ], capture_output=True)

    # Step 2: 混合音频
    if config.keep_original_bgm and original_audio_path.exists():
        print(f"  2️⃣ 混合中文配音 + 背景音乐 (BGM音量={config.bgm_volume})...")

        # 中文音频在前，背景音乐叠加
        # 中文音频音量 100%，BGM 音量降低
        subprocess.run([
            "ffmpeg", "-y",
            "-i", str(chinese_audio_path),
            "-i", str(original_audio_path),
            "-filter_complex",
            f"[0:a]volume=1.0[voice];[1:a]volume={config.bgm_volume}[bgm];"
            f"[voice][bgm]amix=inputs=2:duration=first:dropout_transition=2[out]",
            "-map", "[out]",
            "-ar", "44100", "-ac", "2",
            str(mixed_audio_path)
        ], capture_output=True)

        audio_to_use = mixed_audio_path
    else:
        print("  2️⃣ 不保留背景音乐，直接使用中文配音...")
        audio_to_use = chinese_audio_path

    # Step 3: 合并视频和音频，可选烧录字幕
    print("  3️⃣ 合并视频 + 音频 → 最终成品...")

    ffmpeg_args = ["ffmpeg", "-y"]

    if chinese_srt_path and Path(chinese_srt_path).exists():
        # 烧录中文字幕
        print("  📝 烧录中文字幕...")
        ffmpeg_args.extend([
            "-i", str(video_path),
            "-i", str(audio_to_use),
            "-vf", f"subtitles='{chinese_srt_path}':force_style='FontSize=24,PrimaryColour=&HFFFFFF&,OutlineColour=&H000000&,Outline=2'",
        ])
    else:
        ffmpeg_args.extend([
            "-i", str(video_path),
            "-i", str(audio_to_use),
        ])

    ffmpeg_args.extend([
        "-c:v", "libx264" if chinese_srt_path else "copy",  # 烧录字幕需要重新编码视频
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        str(output_path)
    ])

    result = subprocess.run(ffmpeg_args, capture_output=True)

    if result.returncode == 0:
        print(f"  ✅ 视频生成完成: {output_path}")
    else:
        print(f"  ❌ FFmpeg 失败: {result.stderr.decode()[:500]}")

    # Step 4: 复制字幕文件到 output 目录（与视频同名，方便播放器识别）
    if chinese_srt_path and Path(chinese_srt_path).exists():
        video_name = output_path.stem  # 不含扩展名的视频名
        cn_srt_dest = output_path.parent / f"{video_name}.chs.srt"
        shutil.copy(chinese_srt_path, cn_srt_dest)
        print(f"  📋 中文字幕已复制: {cn_srt_dest}")

    if english_srt_path and Path(english_srt_path).exists():
        video_name = output_path.stem
        en_srt_dest = output_path.parent / f"{video_name}.en.srt"
        shutil.copy(english_srt_path, en_srt_dest)
        print(f"  📋 英文字幕已复制: {en_srt_dest}")

    return str(output_path)


# ============================================================
# 主程序入口
# ============================================================

def main(
    video_path: str,
    srt_path: str,
    output_dir: str = "output",
    config: Optional[Config] = None
):
    """
    完整流水线：
    SRT → 拼接整句 → 翻译 → TTS → 音频拼接 → 视频合并
    """
    if config is None:
        config = Config()

    video_path = Path(video_path)
    srt_path = Path(srt_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    temp_dir = output_dir / config.temp_dir
    audio_dir = temp_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("📺 英文视频中文配音流水线")
    print("=" * 60)
    print(f"  视频: {video_path}")
    print(f"  字幕: {srt_path}")
    print(f"  输出: {output_dir}")
    print("=" * 60)

    # Step 1: 读取 SRT
    print("\n📖 Step 1: 读取 SRT 文件...")
    segments = load_srt(str(srt_path))
    print(f"  读取到 {len(segments)} 个字幕片段")

    # Step 2: 拼接成完整句子
    print("\n🔗 Step 2: 拼接碎片句为完整句子...")
    sentences = merge_segments_to_sentences(segments)
    print(f"  合并为 {len(sentences)} 个完整句子")
    for i, s in enumerate(sentences[:3]):
        print(f"    句 {i + 1}: {s['text'][:60]}...")

    # Step 3: 翻译
    print("\n🌐 Step 3: 翻译为中文...")
    if config.use_local_llm:
        base_url = config.ollama_base_url or os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        print(f"  使用 Ollama ({config.ollama_model}) @ {base_url}")
    else:
        print("  使用阿里云 DashScope API")
    sentences = translate_sentences(sentences, config)

    # 保存翻译结果
    trans_srt = output_dir / "translations.srt"
    save_srt(sentences, str(trans_srt))
    print(f"  翻译结果已保存: {trans_srt}")

    # Step 4: 计算 TTS 语速
    print("\n⚡ Step 4: 计算 TTS 语速参数...")
    sentences = prepare_tts_tasks(sentences, config)

    # 统计
    speed_stats = {"加速": 0, "正常": 0, "减速": 0}
    for s in sentences:
        r = s["speed_ratio"]
        if r > 1.05:
            speed_stats["加速"] += 1
        elif r < 0.95:
            speed_stats["减速"] += 1
        else:
            speed_stats["正常"] += 1
    print(f"  统计: {speed_stats['减速']} 句需减速, {speed_stats['正常']} 句正常, {speed_stats['加速']} 句需加速")

    # Step 5: TTS 合成
    print("\n🔊 Step 5: Edge-TTS 合成中文语音...")
    print(f"  语音: {config.tts_voice}, 延时: {config.tts_delay}s")
    sentences = sync_synthesize_with_edgetts(sentences, config, str(audio_dir))

    # Step 6: 拼接音频
    print("\n📦 Step 6: 拼接音频片段...")
    chinese_audio = output_dir / "chinese_audio.wav"
    stitch_audio_segments(sentences, config, str(chinese_audio))

    # Step 7: 合并视频
    if video_path.exists():
        print("\n🎬 Step 7: 合并视频和音频...")

        # 生成中文字幕文件（用于烧录和复制）
        chinese_srt_path = temp_dir / "chinese.srt"
        chinese_segments = []
        for s in sentences:
            cn_text = s.get("cn_text", "")
            if cn_text and "（翻译失败）" not in cn_text and "[翻译失败]" not in cn_text:
                chinese_segments.append({
                    "start": s["start"],
                    "end": s["end"],
                    "text": cn_text
                })
        if chinese_segments:
            save_srt(chinese_segments, str(chinese_srt_path))

        # 生成英文字幕文件（从原文翻译后恢复）
        english_srt_path = temp_dir / "english.srt"
        english_segments = []
        for s in sentences:
            en_text = s.get("text", "")
            if en_text:
                english_segments.append({
                    "start": s["start"],
                    "end": s["end"],
                    "text": en_text
                })
        if english_segments:
            save_srt(english_segments, str(english_srt_path))

        final_video = output_dir / video_path.with_suffix(".cn.mp4").name
        process_video(
            str(video_path), str(chinese_audio), str(final_video), config,
            chinese_srt_path=str(chinese_srt_path) if chinese_segments else None,
            english_srt_path=str(english_srt_path) if english_segments else None
        )
    else:
        print("\n⚠️ 视频文件不存在，跳过视频合并步骤")
        print(f"  中文音频已生成: {chinese_audio}")

    # 保存完整数据
    data_path = output_dir / "processing_data.json"
    with open(data_path, "w", encoding="utf-8") as f:
        # 转换 Path 对象为字符串
        serializable = []
        for s in sentences:
            d = dict(s)
            for k, v in d.items():
                if isinstance(v, Path):
                    d[k] = str(v)
            serializable.append(d)
        json.dump(serializable, f, ensure_ascii=False, indent=2)
    print(f"\n💾 处理数据已保存: {data_path}")

    print("\n" + "=" * 60)
    print("✅ 流水线完成！")
    print("=" * 60)


# ============================================================
# 入口
# ============================================================

if __name__ == "__main__":
    # 示例配置
    config = Config(
        use_local_llm=True,          # True=本地 Ollama，False=云端 DashScope
        ollama_base_url="http://192.168.0.80:11434",  # 远程 Ollama 地址
        ollama_model="qwen2.5:14b",    # 或 qwen2.5:3b（更快）
        tts_voice="zh-CN-XiaoxiaoNeural",
        tts_delay=0.2,
        keep_original_bgm=True,
        bgm_volume=0.25,
    )

    # 视频和字幕路径（放在 source 目录中）
    source_dir = '/Users/iox/Desktop/msagent/source'
    video_path = f'{source_dir}/[中文字幕]使用微软 Agent Framework 框架进行 C# Ai 开发/[P1]1. Welcome.mp4'
    srt_path = f'{source_dir}/AI in C# using the Microsoft Agent Framework 2026.1/1 - Introduction to the course/1. Welcome.en_US.srt'
    output_dir = "/Users/iox/Desktop/msagent/output"

    main(
        video_path=video_path,
        srt_path=srt_path,
        output_dir=output_dir,
        config=config
    )
