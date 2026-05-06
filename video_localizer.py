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
# 断点续传管理
# ============================================================

CHECKPOINT_FILE = "checkpoint.json"

class CheckpointManager:
    """断点续传管理器"""

    def __init__(self, temp_dir: str, enable: bool = True):
        self.temp_dir = Path(temp_dir)
        self.checkpoint_file = self.temp_dir / CHECKPOINT_FILE
        self.enable = enable
        self.data = {}

        if enable and self.checkpoint_file.exists():
            try:
                self.data = json.loads(self.checkpoint_file.read_text(encoding='utf-8'))
                print(f"  📂 已加载检查点: {len(self.data)} 个步骤完成")
            except Exception:
                self.data = {}

    def save(self):
        """保存检查点"""
        if not self.enable:
            return
        try:
            self.checkpoint_file.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding='utf-8')
        except Exception as e:
            print(f"  ⚠️ 保存检查点失败: {e}")

    def set_completed(self, step: str, data: dict = None):
        """标记步骤已完成"""
        if not self.enable:
            return
        self.data[step] = {"completed": True, "data": data}
        self.save()

    def is_completed(self, step: str) -> bool:
        """检查步骤是否已完成"""
        if not self.enable:
            return False
        return self.data.get(step, {}).get("completed", False)

    def get_data(self, step: str) -> dict:
        """获取步骤数据"""
        return self.data.get(step, {}).get("data", {})

    def clear(self):
        """清除所有检查点"""
        if self.checkpoint_file.exists():
            self.checkpoint_file.unlink()
        self.data = {}


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

    # TTS 语音配置
    # 可用男声：zh-CN-YunxiNeural（云希，推荐）, zh-CN-YunyangNeural（云扬）
    # 可用女声：zh-CN-XiaoxiaoNeural（晓晓）, zh-CN-XiaoyiNeural（晓伊）, zh-CN-XiaomoNeural（晓墨）
    tts_voice: str = "zh-CN-YunxiNeural"  # 默认男声

    # 中文朗读速率（字/秒），用于估算时长
    # 正常语速约 5-6 字/秒，略微放慢便于学习
    chinese_reading_speed: float = 5.2

    # 语速比例限制（防止失真）
    speed_ratio_min: float = 0.77  # 最小语速（最多加速30%，1/1.3）
    speed_ratio_max: float = 1.05  # 最大语速（最多减速5%，1/0.95）

    # Edge-TTS 延时（秒），避免请求过快被限
    tts_delay: float = 0.2

    # 音频格式
    audio_format: str = "wav"  # wav 或 mp3

    # BGM 配置
    keep_original_bgm: bool = False  # 是否保留原视频背景音乐
    bgm_volume: float = 0.25  # BGM 音量（0.0-1.0）

    # 原语音处理
    keep_original_voice: bool = False  # 是否保留原视频人声（False=完全移除，只用中文配音）

    # 字幕烧录配置
    burn_subtitles: bool = False  # 是否烧录字幕到视频（False=使用外挂字幕）

    # 临时文件目录
    temp_dir: str = "temp"

    # 断点续传配置
    enable_checkpoint: bool = True  # 是否启用断点续传
    resume_from_checkpoint: bool = True  # 默认重用中间文件，跳过已完成部分


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
    4. 优先检查是否为未完成的短语（如介词 to/for/of 结尾）
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

        # 优先检查是否为未完成的短语（应该继续合并）
        if _is_incomplete_phrase(text):
            # 未完成短语，不断开
            last_end = seg["end"]
            continue

        # 检查是否有句末标点
        is_sentence_end = _is_sentence_ending(text)

        # 检查是否有明显停顿（超过 1.5 秒视为分隔）
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


def _is_incomplete_phrase(text: str) -> bool:
    """
    判断文本是否为未完成的短语（应该继续合并，不应断句）

    例如：
    - "access a large" (to 不完整)
    - "cost you a bit" (of 不完整)
    - "demoing primarily" (Azure 不完整)
    """
    text = text.strip().lower()

    if not text:
        return False

    # 检查是否以常见介词/连词结尾（表示句子未完成）
    incomplete_endings = (
        # 介词
        "to", "for", "of", "in", "on", "at", "by", "with", "from", "about",
        "into", "through", "during", "before", "after", "above", "below",
        "between", "under", "again", "further", "then", "once",
        # 不定式 to 后面的动词被打断
        "going to", "need to", "want to", "have to", "able to",
        # 冠词后的名词可能不完整
        "a", "an", "the",
        # 形容词后可能跟名词
        "large", "small", "big", "new", "old", "first", "last",
        # 其他常见未完成模式
        "primarily", "mainly", "mostly", "partially",
    )

    # 获取最后一个单词
    words = text.split()
    if not words:
        return False

    last_word = words[-1].rstrip(".,!?;:'\"")
    last_two = " ".join(words[-2:]) if len(words) >= 2 else last_word

    if last_word in incomplete_endings or last_two in incomplete_endings:
        return True

    # 检查是否以常见句法模式结尾
    # "... access a" 后面很可能跟名词
    if text.endswith(" access ") or text.endswith(" access a"):
        return True

    # "... need " 后面可能跟动词
    if text.endswith(" need "):
        return True

    # "... going to" 后面可能跟动词
    if text.endswith(" going to"):
        return True

    # "... large" 后面可能跟名词（如 language model）
    if text.endswith(" large") or text.endswith(" big") or text.endswith(" small"):
        return True

    # "... primarily" 后面可能跟名词（如 Azure OpenAI）
    if text.endswith(" primarily") or text.endswith(" mainly") or text.endswith(" mostly"):
        return True

    # "... bit" 后面可能跟 "of"
    if text.endswith(" bit"):
        return True

    return False


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

def translate_sentences(sentences: list[dict], config: Config, temp_dir: str = None) -> list[dict]:
    """翻译完整句子"""
    if config.use_local_llm:
        return _translate_with_ollama(sentences, config, temp_dir)
    else:
        return _translate_with_dashscope(sentences, config, temp_dir)


def _translate_with_ollama(sentences: list[dict], config: Config, temp_dir: str = None) -> list[dict]:
    """使用本地 Ollama + Qwen 翻译（支持远程地址）"""
    try:
        import ollama
    except ImportError:
        raise ImportError("请安装 ollama: pip install ollama")

    # 远程 Ollama 地址（如 http://192.168.0.80:11434）
    base_url = config.ollama_base_url or os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    client = ollama.Client(host=base_url)

    BATCH_SIZE = 10  # 每批翻译条数
    MAX_RETRIES = 2  # 最大重试次数

    # 使用 temp_dir 或默认 output/debug_translations
    work_dir = Path(temp_dir) if temp_dir else Path('output')
    debug_dir = work_dir / "debug_translations"

    # 每次运行清空调试目录
    if debug_dir.exists():
        shutil.rmtree(debug_dir)
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
2. 保留专业术语不翻译（如 Agent, LLM, API, SDK, GitHub, NuGet, C# 等）
3. 适当增补语气词，使中文听起来更自然（如"好"、"那么"、"我们来看"）
4. 按序号逐条返回，格式：序号. 中文翻译
5. 不要添加多余解释

{'-' * 40}
{batch_texts}
{'-' * 40}
返回示例：
1. 让我们开始使用 C# Agent 框架
2. 现在我们已经建立了原始连接
"""

        raw_response = None
        last_error = None

        for retry in range(MAX_RETRIES):
            try:
                retry_msg = f" (重试 {retry + 1}/{MAX_RETRIES})" if retry > 0 else ""
                print(f"  📤 发送翻译请求到 {base_url} (批次 {batch_idx}){retry_msg}...")
                response = client.chat(
                    model=config.ollama_model,
                    messages=[{"role": "user", "content": prompt}],
                    options={"temperature": 0.3}  # 降低随机性，保持翻译一致
                )

                raw_response = response['message']['content']
                break  # 成功则退出重试循环

            except Exception as e:
                last_error = e
                if retry < MAX_RETRIES - 1:
                    print(f"  ⚠️ 翻译请求失败: {e}，准备重试...")
                else:
                    print(f"  ❌ 翻译请求失败: {e}")

        if raw_response:
            # 保存原始响应用于调试
            debug_file = debug_dir / f"batch_{batch_idx:03d}_raw.txt"
            debug_file.write_text(raw_response, encoding='utf-8')

            translations = _parse_translations(raw_response, len(batch))

            # 检查解析结果
            success_count = sum(1 for t in translations if t and "（翻译失败）" not in t)
            print(f"  📥 批次 {batch_idx} 解析完成: {success_count}/{len(batch)} 条成功")

            # 先记录翻译结果
            for j, t in enumerate(translations):
                if j < len(batch):
                    sentences[i + j]["cn_text"] = t.strip()

            # 单独重试失败的翻译
            failed_indices = []
            for j, t in enumerate(translations):
                if j < len(batch) and "（翻译失败）" in t:
                    failed_indices.append(j)
                    print(f"  ❌ 翻译 [{i + j + 1}/{len(sentences)}] 失败: \"{batch[j]['text']}\"")

            # 批量重试失败的翻译
            for j in failed_indices:
                retry_translation = _retry_single_translation(client, batch[j]['text'], config)
                if retry_translation:
                    sentences[i + j]["cn_text"] = retry_translation
                    print(f"  🔄 重试成功 [{i + j + 1}]")
                    print(f"     EN: {batch[j]['text']}")
                    print(f"     CN: {retry_translation}")
                else:
                    print(f"  ❌ 重试失败 [{i + j + 1}]: \"{batch[j]['text']}\"")

            # 打印所有翻译结果
            for j in range(len(batch)):
                t = sentences[i + j].get("cn_text", "")
                status = "⚠️" if ("（翻译失败）" in t or "[翻译失败]" in t) else "✅"
                print(f"  {status} [{i + j + 1}/{len(sentences)}]")
                print(f"     EN: {batch[j]['text']}")
                print(f"     CN: {t}")

            # 保存解析后的结果
            parsed_file = debug_dir / f"batch_{batch_idx:03d}_parsed.txt"
            parsed_file.write_text("\n".join([f"{j+1}. {sentences[i + j].get('cn_text', '')}" for j in range(len(batch))]), encoding='utf-8')
        else:
            print(f"  ❌ 批次 {batch_idx} 翻译失败: {last_error}")
            for j in range(len(batch)):
                sentences[i + j]["cn_text"] = f"[翻译失败] {batch[j]['text']}"

    return sentences


def _retry_single_translation(client, text: str, config: Config) -> str:
    """单独重试翻译单条文本"""
    try:
        prompt = f"""翻译以下英文为中文教学语气，保留专业术语：

原文: {text}

只需返回翻译结果，不要其他解释："""

        response = client.chat(
            model=config.ollama_model,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.3}
        )

        result = response['message']['content'].strip()
        # 清理可能的引号
        result = re.sub(r'^["""\'""\']+|["""\'""\']+$', '', result)
        return result

    except Exception as e:
        print(f"    重试失败: {e}")
        return None


def _translate_with_dashscope(sentences: list[dict], config: Config, temp_dir: str = None) -> list[dict]:
    """使用阿里云 DashScope Qwen API 翻译"""
    try:
        import dashscope
        from dashscope import Generation
    except ImportError:
        raise ImportError("请安装 dashscope: pip install dashscope")

    if not config.dashscope_key:
        raise ValueError("请设置 DashScope API Key")

    dashscope.api_key = config.dashscope_key
    BATCH_SIZE = 10  # 每批翻译条数
    MAX_RETRIES = 2  # 最大重试次数

    # 使用 temp_dir 或默认 output
    work_dir = Path(temp_dir) if temp_dir else Path('output')
    debug_dir = work_dir / "debug_translations"

    # 每次运行清空调试目录
    if debug_dir.exists():
        shutil.rmtree(debug_dir)
    debug_dir.mkdir(parents=True, exist_ok=True)

    for i in range(0, len(sentences), BATCH_SIZE):
        batch = sentences[i:i + BATCH_SIZE]
        batch_idx = i // BATCH_SIZE + 1
        batch_texts = "\n".join([f"{j + 1}. {s['text']}" for j, s in enumerate(batch)])

        prompt = f"翻译为教学语气中文，保留专业术语：\n{batch_texts}"

        raw_response = None
        last_error = None

        for retry in range(MAX_RETRIES):
            try:
                retry_msg = f" (重试 {retry + 1}/{MAX_RETRIES})" if retry > 0 else ""
                print(f"  📤 发送翻译请求到 DashScope (批次 {batch_idx}){retry_msg}...")
                response = Generation.call(
                    model="qwen-turbo",
                    prompt=prompt
                )

                raw_response = response.output.text
                break  # 成功则退出重试循环

            except Exception as e:
                last_error = e
                if retry < MAX_RETRIES - 1:
                    print(f"  ⚠️ 翻译请求失败: {e}，准备重试...")
                else:
                    print(f"  ❌ 翻译请求失败: {e}")

        if raw_response:
            # 保存原始响应用于调试
            debug_file = debug_dir / f"batch_{batch_idx:03d}_raw.txt"
            debug_file.write_text(raw_response, encoding='utf-8')

            translations = _parse_translations(raw_response, len(batch))

            # 检查解析结果
            success_count = sum(1 for t in translations if t and "（翻译失败）" not in t)
            print(f"  📥 批次 {batch_idx} 解析完成: {success_count}/{len(batch)} 条成功")

            # 先记录翻译结果
            for j, t in enumerate(translations):
                if j < len(batch):
                    sentences[i + j]["cn_text"] = t.strip()

            # 单独重试失败的翻译
            failed_indices = []
            for j, t in enumerate(translations):
                if j < len(batch) and "（翻译失败）" in t:
                    failed_indices.append(j)
                    print(f"  ❌ 翻译 [{i + j + 1}/{len(sentences)}] 失败: \"{batch[j]['text']}\"")

            # 批量重试失败的翻译
            for j in failed_indices:
                retry_translation = _retry_single_translation_dashscope(batch[j]['text'], config)
                if retry_translation:
                    sentences[i + j]["cn_text"] = retry_translation
                    print(f"  🔄 重试成功 [{i + j + 1}]")
                    print(f"     EN: {batch[j]['text']}")
                    print(f"     CN: {retry_translation}")
                else:
                    print(f"  ❌ 重试失败 [{i + j + 1}]: \"{batch[j]['text']}\"")

            # 打印所有翻译结果
            for j in range(len(batch)):
                t = sentences[i + j].get("cn_text", "")
                status = "⚠️" if ("（翻译失败）" in t or "[翻译失败]" in t) else "✅"
                print(f"  {status} [{i + j + 1}/{len(sentences)}]")
                print(f"     EN: {batch[j]['text']}")
                print(f"     CN: {t}")

            # 保存解析后的结果
            parsed_file = debug_dir / f"batch_{batch_idx:03d}_parsed.txt"
            parsed_file.write_text("\n".join([f"{j+1}. {sentences[i + j].get('cn_text', '')}" for j in range(len(batch))]), encoding='utf-8')
        else:
            print(f"  ❌ 批次 {batch_idx} 翻译失败: {last_error}")
            for j in range(len(batch)):
                sentences[i + j]["cn_text"] = f"[翻译失败] {batch[j]['text']}"

    return sentences


def _retry_single_translation_dashscope(text: str, config: Config) -> str:
    """单独重试翻译单条文本（DashScope 版本）"""
    try:
        import dashscope
        from dashscope import Generation

        prompt = f"翻译为教学语气中文，保留专业术语：\n{text}"

        response = Generation.call(
            model="qwen-turbo",
            prompt=prompt
        )

        result = response.output.text.strip()
        # 清理可能的引号
        result = re.sub(r'^["""\'""\']+|["""\'""\']+$', '', result)
        return result

    except Exception as e:
        print(f"    重试失败: {e}")
        return None

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
            print(f"    匹配 [{m.group(1)}]: {translation[:50]}...")

    print(f"  📊 解析结果: {len(matched_lines)}/{expected_count} 条")

    # 确保数量正确，不足时标记失败
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

def preprocess_tts_text(text: str) -> str:
    """
    预处理 TTS 文本，确保特殊词汇发音正确
    """
    import re

    # C# 替换为 "C sharp"（edge-tts 不支持 SSML，直接替换文本）
    text = re.sub(
        r'C#([\d\.]*)',
        r'C sharp\1',
        text,
        flags=re.IGNORECASE
    )

    return text


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

        # 跳过已合成的（仅当 resume_from_checkpoint=True 时）
        if config.resume_from_checkpoint and out_path.exists() and out_path.stat().st_size > 1000:
            s["cn_audio_path"] = str(out_path)
            print(f"  ⏭️ 跳过 [{idx + 1}/{len(sentences)}]: 已存在")
            continue

        # 将 speed_ratio 转换为 Edge-TTS rate 参数
        # speed_ratio > 1 → 中文比原音长 → 需要加速
        # speed_ratio < 1 → 中文比原音短 → 需要减速
        rate_percent = int((1.0 / s["speed_ratio"] - 1) * 100)
        rate_str = f"{rate_percent:+}%" if rate_percent != 0 else "0%"

        # 限制范围：最多加速30%，最多减速5%
        rate_percent = max(-5, min(rate_percent, 30))
        rate_str = f"{rate_percent:+}%"

        # 预处理文本，确保 C# 等词汇发音正确
        raw_text = s.get("cn_text", "")
        processed_text = preprocess_tts_text(raw_text)

        try:
            communicate = edge_tts.Communicate(
                processed_text,
                voice=config.tts_voice,
                rate=rate_str,
                volume="+0%"
            )
            await communicate.save(str(out_path))

            s["cn_audio_path"] = str(out_path)
            print(f"  ✅ [{idx + 1}/{len(sentences)}] rate={rate_str}: {raw_text[:35]}...")

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
    3. 如果中文语音比原时段长 → 不截断，保留完整内容，时间轴自然后移
    """
    from pydub import AudioSegment
    from pydub.effects import speedup

    print("\n📦 拼接音频片段...")

    # 创建空白音频（用于累积）
    # 先确定总时长（最后一个句子的结束时间）
    total_duration = max(s["end"] for s in sentences)
    print(f"  原始总时长: {total_duration:.2f} 秒")

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
            print(f"  📝 [{idx + 1}] 音频较短 {len(audio)}ms < {seg_duration_ms}ms，补静音")

        # 如果音频比目标时段长 → 不截断，保留完整内容
        elif len(audio) > seg_duration_ms:
            overrun = len(audio) - seg_duration_ms
            print(f"  📝 [{idx + 1}] 音频较长 {len(audio)}ms > {seg_duration_ms}ms，保留完整内容（超出 {overrun}ms）")

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

    # 统计最终时长
    if combined:
        final_duration_ms = len(combined)
        final_duration_s = final_duration_ms / 1000
        if final_duration_s > total_duration:
            print(f"  ⚠️ 最终时长 {final_duration_s:.2f}s 比原时长 {total_duration:.2f}s 长 {final_duration_s - total_duration:.2f}s")

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
    english_srt_path: str = None,
    temp_dir: str = None
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

    temp_dir = Path(temp_dir) if temp_dir else Path(config.temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)

    original_audio_path = temp_dir / "original_audio.wav"
    mixed_audio_path = temp_dir / "mixed_audio.wav"

    print(f"\n🎬 处理视频: {video_path.name}")

    # Step 1: 提取原视频音频（用于 BGM）
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

    # 如果不保留原语音，需要移除原视频的音频流
    if not config.keep_original_voice:
        # 方案A: 先提取纯视频流（无音频）
        pure_video_path = temp_dir / "pure_video.mp4"
        subprocess.run([
            "ffmpeg", "-y", "-i", str(video_path),
            "-an",  # 移除音频
            "-c:v", "copy",
            str(pure_video_path)
        ], capture_output=True)
        video_input = str(pure_video_path)
        print("  🔇 已移除原视频音频轨道")
    else:
        video_input = str(video_path)

    if config.burn_subtitles and chinese_srt_path and Path(chinese_srt_path).exists():
        # 烧录中文字幕（仅当配置启用时）
        print("  📝 烧录中文字幕...")
        ffmpeg_args.extend([
            "-i", video_input,
            "-i", str(audio_to_use),
            "-vf", f"subtitles='{chinese_srt_path}':force_style='FontSize=24,PrimaryColour=&HFFFFFF&,OutlineColour=&H000000&,Outline=2'",
            "-map", "0:v:0",    # 只取主视频流（忽略封面等）
            "-map", "1:a",      # 使用新音频
        ])
    else:
        ffmpeg_args.extend([
            "-i", video_input,
            "-i", str(audio_to_use),
            "-map", "0:v:0",    # 只取主视频流（忽略封面等）
            "-map", "1:a",      # 使用新音频
        ])

    ffmpeg_args.extend([
        "-c:v", "libx264",  # 强制重新编码为 H.264，确保播放器兼容性
        "-preset", "fast",   # 编码速度：ultrafast, superfast, veryfast, faster, fast, medium, slow, slower, veryslow
        "-crf", "23",        # 视频质量（0-51，越低越好），23 为默认质量
        "-pix_fmt", "yuv420p",  # 强制转换为 yuv420p，确保兼容性
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        str(output_path)
    ])

    result = subprocess.run(ffmpeg_args, capture_output=True)

    if result.returncode == 0:
        print(f"  ✅ 视频生成完成: {output_path}")
    else:
        error_msg = result.stderr.decode()[:800]
        print(f"  ❌ FFmpeg 失败: {error_msg}")
        raise RuntimeError(f"视频生成失败: {error_msg}")

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

    支持断点续传：中断后可跳过已完成步骤

    目录结构：
    - output/              # 最终输出（视频、字幕）
      └── {video_name}/    # 按视频名分目录
          ├── *.cn.mp4     # 中文配音视频
          ├── *.chs.srt    # 中文字幕
          └── *.en.srt     # 英文字幕
    - temp/                 # 临时文件（按视频名分目录）
      └── {video_name}/
          ├── audio/       # TTS 生成的音频片段
          ├── *.wav        # 中间音频文件
          └── checkpoint.json  # 断点记录
    """
    if config is None:
        config = Config()

    video_path = Path(video_path)
    srt_path = Path(srt_path)
    output_dir = Path(output_dir)

    # 根据视频文件名创建子目录，避免多视频冲突
    video_name = video_path.stem  # 不含扩展名的视频名
    work_dir = video_name  # 用于 temp 目录的子目录名

    # 临时文件目录（按视频分）
    temp_base = Path(config.temp_dir)
    temp_dir = temp_base / work_dir
    audio_dir = temp_dir / "audio"

    # 创建目录 
    temp_dir.mkdir(parents=True, exist_ok=True)
    audio_dir.mkdir(parents=True, exist_ok=True)

    # 最终输出目录
    final_output_dir = output_dir / video_name
    final_output_dir.mkdir(parents=True, exist_ok=True)

    # 初始化断点续传管理器
    checkpoint = CheckpointManager(str(temp_dir), enable=config.enable_checkpoint)

    # 流水线执行状态
    pipeline_success = True

    # 如果不禁用断点续传且不禁用 resume，打印提示
    if config.enable_checkpoint and config.resume_from_checkpoint:
        print("  💾 断点续传已启用")

    print("=" * 60)
    print("📺 英文视频中文配音流水线")
    print("=" * 60)
    print(f"  视频: {video_path}")
    print(f"  字幕: {srt_path}")
    print(f"  临时目录: {temp_dir}")
    print(f"  输出目录: {final_output_dir}")
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
    checkpoint.set_completed("step2", {"count": len(sentences)})

    # Step 3: 翻译（检查实际文件是否存在）
    print("\n🌐 Step 3: 翻译为中文...")
    # 检查是否有有效翻译（所有句子都有cn_text且不是失败标记）
    has_valid_translations = all(
        s.get("cn_text") and "（翻译失败）" not in s.get("cn_text", "") and "[翻译失败]" not in s.get("cn_text", "")
        for s in sentences
    )
    if config.resume_from_checkpoint and has_valid_translations:
        print("  ⏩ 跳过（已存在翻译结果）")
    else:
        if config.use_local_llm:
            base_url = config.ollama_base_url or os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
            print(f"  使用 Ollama ({config.ollama_model}) @ {base_url}")
        else:
            print("  使用阿里云 DashScope API")
        sentences = translate_sentences(sentences, config, str(temp_dir))

    # Step 4: 计算 TTS 语速
    print("\n⚡ Step 4: 计算 TTS 语速参数...")
    sentences = prepare_tts_tasks(sentences, config)
    checkpoint.set_completed("step4", {"count": len(sentences)})

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

    # Step 5: TTS 合成（检查实际文件是否存在）
    print("\n🔊 Step 5: Edge-TTS 合成中文语音...")
    print(f"  语音: {config.tts_voice}, 延时: {config.tts_delay}s")

    # 检查是否所有音频文件都存在
    all_audio_exist = all(
        s.get("cn_audio_path") and Path(s["cn_audio_path"]).exists() and Path(s["cn_audio_path"]).stat().st_size > 1000
        for s in sentences
    )
    if config.resume_from_checkpoint and all_audio_exist:
        print("  ⏩ 跳过（已存在 TTS 音频）")
    else:
        sentences = sync_synthesize_with_edgetts(sentences, config, str(audio_dir))

    # Step 6: 拼接音频（检查实际文件是否存在）
    print("\n📦 Step 6: 拼接音频片段...")
    chinese_audio = temp_dir / "chinese_audio.wav"

    if config.resume_from_checkpoint and chinese_audio.exists() and chinese_audio.stat().st_size > 1000:
        print("  ⏩ 跳过（已存在拼接音频）")
    else:
        stitch_audio_segments(sentences, config, str(chinese_audio))

    # Step 7: 合并视频（检查实际文件是否存在）
    final_video = final_output_dir / f"{video_name}.cn.mp4"
    if video_path.exists():
        print("\n🎬 Step 7: 合并视频和音频...")

        if config.resume_from_checkpoint and final_video.exists() and final_video.stat().st_size > 1000:
            print("  ⏩ 跳过（视频已生成）")
        else:
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

            # 最终视频输出到 output/{video_name}/
            try:
                process_video(
                    str(video_path), str(chinese_audio), str(final_video), config,
                    chinese_srt_path=str(chinese_srt_path) if chinese_segments else None,
                    english_srt_path=str(english_srt_path) if english_segments else None,
                    temp_dir=str(temp_dir)
                )
                checkpoint.set_completed("step7", {"path": str(final_video)})
            except RuntimeError as e:
                print(f"\n  ❌ 视频生成失败: {e}")
                print("  ⚠️ 请检查 FFmpeg 错误信息，修复后重新运行（设置 resume_from_checkpoint=True 跳过已完成步骤）")
                pipeline_success = False
                # 不 return，继续保存数据但标记失败
    else:
        print("\n⚠️ 视频文件不存在，跳过视频合并步骤")
        print(f"  中文音频已生成: {chinese_audio}")

    # 保存完整数据到 temp 目录
    data_path = temp_dir / "processing_data.json"
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
    if pipeline_success:
        print("✅ 流水线完成！")
    else:
        print("❌ 流水线执行失败！请检查上方错误信息。")
    print("=" * 60)


# ============================================================
# 入口
# ============================================================

# ============================================================
# 批量处理队列
# ============================================================

def get_ms_agent_batch_queue(output_dir: str, skip_completed: bool = True) -> list[dict]:
    """
    获取微软 Agent Framework 课程的批量处理队列。
    
    Args:
        output_dir: 输出目录
        skip_completed: 是否跳过已完成的视频（通过检查 .cn.mp4 是否存在判断）
    返回格式: [{"seq": 序号, "video": 视频路径, "srt": 字幕路径}, ...]
    """
    import re
    from pathlib import Path

    source_dir = '/Users/iox/Desktop/msagent/source'
    video_base = f'{source_dir}/[中文字幕]使用微软 Agent Framework 框架进行 C# Ai 开发'
    srt_base = f'{source_dir}/AI in C# using the Microsoft Agent Framework 2026.1'

    # 1. 收集所有视频文件并提取序号
    video_files = {}
    for f in Path(video_base).glob("*.mp4"):
        m = re.match(r'\[P\d*\]?(\d+)\.\s*(.+)\.mp4$', f.name)
        if m:
            num = int(m.group(1))
            video_files[num] = str(f)

    # 2. 收集所有字幕文件并提取序号
    srt_files = {}
    for srt_path in Path(srt_base).rglob("*.en_US.srt"):
        m = re.match(r'(\d+)\.\s*(.+)\.en_US\.srt$', srt_path.name)
        if m:
            num = int(m.group(1))
            srt_files[num] = str(srt_path)

    # 3. 收集已完成的视频序号
    completed_seqs = set()
    if skip_completed:
        for out_path in Path(output_dir).iterdir():
            if out_path.is_dir():
                mp4_file = out_path / f"{out_path.name}.cn.mp4"
                if mp4_file.exists() and mp4_file.stat().st_size > 1000:
                    # 从目录名提取序号，如 "[P1]1. Welcome" -> 1
                    m = re.match(r'\[P\d*\]?(\d+)', out_path.name)
                    if m:
                        completed_seqs.add(int(m.group(1)))

    # 4. 按序号配对 (1 -> 52)，跳过已完成的
    task_list = []
    skipped = 0
    for num in range(1, 53):
        if num in video_files and num in srt_files:
            if skip_completed and num in completed_seqs:
                skipped += 1
                continue
            task_list.append({
                "seq": num,
                "video": video_files[num],
                "srt": srt_files[num],
            })
        else:
            print(f"⚠️ 序号 {num} 配对缺失: video={num in video_files}, srt={num in srt_files}")

    if skipped > 0:
        print(f"⏭️ 跳过 {skipped} 个已完成视频")

    return task_list


def run_batch_process(config: Config, output_dir: str):
    """
    批量处理队列中的所有视频。
    """
    task_list = get_ms_agent_batch_queue(output_dir)
    print(f"📋 共配对 {len(task_list)}/52 个任务")
    for t in task_list[:3]:
        print(f"   [{t['seq']}] {Path(t['video']).name} ↔ {Path(t['srt']).name}")
    if len(task_list) > 3:
        print(f"   ...")

    for idx, task in enumerate(task_list):
        seq = task["seq"]
        video_path = task["video"]
        srt_path = task["srt"]
        video_name = Path(video_path).stem  # 如 "[P1]1. Welcome"

        print(f"\n{'='*70}")
        print(f"🎬 进度: [{idx + 1}/{len(task_list)}] 序号 {seq}")
        print(f"📹 视频: {Path(video_path).name}")
        print(f"📄 字幕: {Path(srt_path).name}")
        print(f"{'='*70}")

        # 如果 resume_from_checkpoint=True，检查是否已有输出
        if config.resume_from_checkpoint:
            final_video = Path(output_dir) / video_name / f"{video_name}.cn.mp4"
            if final_video.exists() and final_video.stat().st_size > 1000:
                print(f"  ⏭️ 已存在输出文件，跳过: {final_video.name}")
                continue

        try:
            main(
                video_path=video_path,
                srt_path=srt_path,
                output_dir=output_dir,
                config=config,
            )
        except Exception as e:
            print(f"❌ [{seq}] 处理失败: {e}")
            import traceback
            traceback.print_exc()
            continue

    print(f"\n🎉 全部完成! 共处理 {len(task_list)} 个视频")


# ============================================================
# 入口
# ============================================================

if __name__ == "__main__":
    # 配置
    config = Config(
        use_local_llm=True,            # 翻译方式：True=本地 Ollama，False=云端 DashScope
        ollama_base_url="http://192.168.0.80:11434",  # Ollama 服务器地址
        ollama_model="qwen2.5:14b",     # Ollama 模型：qwen2.5:14b（质量高）, qwen2.5:7b（速度快）
        tts_voice="zh-CN-YunxiNeural",  # TTS 语音：YunxiNeural(云希男声，推荐), YunyangNeural(云扬), XiaoxiaoNeural(晓晓女声)
        tts_delay=0.2,                  # TTS 请求延时（秒），避免被限速
        keep_original_bgm=False,       # 是否保留原视频背景音乐
        bgm_volume=0.25,               # BGM 音量（0.0-1.0）
        keep_original_voice=False,     # 是否保留原视频人声：False=完全移除，True=保留原音
        burn_subtitles=False,          # 是否烧录字幕到视频：False=外挂字幕，True=烧录到视频
        enable_checkpoint=True,        # 是否启用断点续传
        resume_from_checkpoint=True,   # 是否从断点恢复：True=跳过已完成步骤，False=全部重新生成
        speed_ratio_max=1.0,          # 语速上限：1.0=不降速（最多保持原速），1.05=最多减速5%
    )

    output_dir = "/Users/iox/Desktop/msagent/output"

    # 批量处理
    run_batch_process(config, output_dir)
