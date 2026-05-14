"""
视频转字幕工具类
功能：
1. 从视频中提取音频（MP3）
2. 调用 Whisper ASR Web 服务进行语音识别
3. 生成 SRT 字幕文件

依赖安装：
pip install requests python-dotenv

环境变量配置 (.env 文件):
WHISPER_SERVICE_URL=http://localhost:9000/asr  # Whisper ASR 服务地址
WHISPER_LANGUAGE=en                              # 默认语言 (可选)
WHISPER_TASK=transcribe                          # 任务类型: transcribe/translate (可选)
WHISPER_KEEP_AUDIO=false                         # 是否保留音频文件 (可选)
TEMP_DIR=temp                                    # 临时文件目录 (可选)
OUTPUT_DIR=output                                # 输出目录 (可选)
"""

import os
import re
import json
import subprocess
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass

# 加载 .env 环境变量
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # 如果没有 python-dotenv，继续使用环境变量


@dataclass
class WhisperSegment:
    """Whisper 识别结果片段"""
    id: int
    start: float
    end: float
    text: str


@dataclass
class WhisperResult:
    """Whisper 识别完整结果"""
    text: str
    segments: List[WhisperSegment]
    language: str


class VideoToSRT:
    """视频转字幕处理类"""

    def __init__(
        self,
        whisper_service_url: Optional[str] = None,
        temp_dir: Optional[str] = None,
        output_dir: Optional[str] = None
    ):
        """
        初始化视频转字幕工具

        Args:
            whisper_service_url: Whisper ASR Web 服务地址，
                不传则从环境变量 WHISPER_SERVICE_URL 读取，默认: http://localhost:9000/asr
            temp_dir: 临时文件目录，
                不传则从环境变量 TEMP_DIR 读取，默认: temp
            output_dir: 输出目录，
                不传则从环境变量 OUTPUT_DIR 读取，默认: output
        """
        # 辅助函数：去除值后面的注释
        def _clean_env_value(value: Optional[str]) -> Optional[str]:
            if not value:
                return value
            # 去除 # 后面的注释，以及首尾空白
            return value.split("#", 1)[0].strip() or None

        self.whisper_service_url = (
            whisper_service_url
            or _clean_env_value(os.getenv("WHISPER_SERVICE_URL"))
            or "http://localhost:9000/asr"
        )
        self.temp_dir = Path(
            temp_dir
            or _clean_env_value(os.getenv("TEMP_DIR"))
            or "temp"
        )
        self.output_dir = Path(
            output_dir
            or _clean_env_value(os.getenv("OUTPUT_DIR"))
            or "output"
        )

        # 创建目录
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def extract_audio_from_video(
        self,
        video_path: str,
        output_audio_path: Optional[str] = None,
        bitrate: str = "128k",
        force_reextract: bool = False
    ) -> str:
        """
        从视频中提取音频（MP3）

        Args:
            video_path: 视频文件路径
            output_audio_path: 输出音频文件路径（可选）
            bitrate: MP3 比特率
            force_reextract: 强制重新提取（即使文件已存在）

        Returns:
            输出音频文件路径
        """
        video_path = Path(video_path)

        if not video_path.exists():
            raise FileNotFoundError(f"视频文件不存在: {video_path}")

        # 确定输出音频路径
        if output_audio_path is None:
            output_audio_path = self.temp_dir / f"{video_path.stem}.mp3"
        else:
            output_audio_path = Path(output_audio_path)

        # 检查文件是否已存在
        if output_audio_path.exists() and not force_reextract:
            print(f"🎬 音频文件已存在，直接使用: {output_audio_path.name}")
            return str(output_audio_path)

        print(f"🎬 从视频提取音频: {video_path.name}")

        # 使用 FFmpeg 提取音频
        try:
            result = subprocess.run([
                "ffmpeg", "-y", "-i", str(video_path),
                "-vn", "-acodec", "libmp3lame", "-ab", bitrate, "-ar", "44100", "-ac", "2",
                str(output_audio_path)
            ], capture_output=True, text=True)

            if result.returncode != 0:
                error_msg = result.stderr[:500] if result.stderr else "未知错误"
                raise RuntimeError(f"FFmpeg 提取音频失败: {error_msg}")

        except FileNotFoundError:
            raise RuntimeError("未找到 FFmpeg，请确保已安装 FFmpeg 并添加到 PATH")

        print(f"✅ 音频提取完成: {output_audio_path.name}")
        return str(output_audio_path)

    def transcribe_with_whisper(
        self,
        audio_path: str,
        language: Optional[str] = None,
        task: str = "transcribe",
        initial_prompt: Optional[str] = None,
        word_timestamps: bool = False
    ) -> WhisperResult:
        """
        调用 Whisper ASR Web 服务进行语音识别

        Args:
            audio_path: 音频文件路径
            language: 语言代码（如 'en', 'zh'），None 为自动检测
            task: 任务类型 ('transcribe' 或 'translate')
            initial_prompt: 初始提示词
            word_timestamps: 是否返回词级时间戳

        Returns:
            WhisperResult 对象
        """
        try:
            import requests
        except ImportError:
            raise ImportError("请安装 requests: pip install requests")

        audio_path = Path(audio_path)

        if not audio_path.exists():
            raise FileNotFoundError(f"音频文件不存在: {audio_path}")

        print(f"🎤 调用 Whisper ASR 服务: {self.whisper_service_url}")

        # 准备请求参数 - whisper-asr-webservice 格式
        files = {"audio_file": open(str(audio_path), "rb")}
        params = {
            "task": task,
            "output": "json"
        }

        if language:
            params["language"] = language
        else:
            print(f"🔍 未指定语言，将由 Whisper 服务自动检测")

        if initial_prompt:
            params["initial_prompt"] = initial_prompt

        print(f"📤 查询参数: {params}")

        try:
            response = requests.post(
                self.whisper_service_url,
                files=files,
                params=params,  # 参数通过 query 传递
                timeout=3600  # 较长超时，大文件可能需要更久
            )

            files["audio_file"].close()

            if response.status_code != 200:
                print(f"⚠️  Whisper 服务返回状态码: {response.status_code}")
                print(f"⚠️  响应内容: {response.text[:200]}")
                raise RuntimeError(f"Whisper 服务返回错误 {response.status_code}: {response.text}")

            # 先打印响应内容用于调试
            print(f"📥 Whisper 服务响应内容（前200字符）: {response.text[:200]}")

            # 解析响应
            try:
                result_json = response.json()
            except Exception as e:
                print(f"⚠️  JSON 解析失败，响应内容: {response.text}")
                raise RuntimeError(f"Whisper 服务返回了无效的 JSON: {response.text[:100]}") from e

            # 转换为 WhisperResult 对象
            segments = []
            for seg in result_json.get("segments", []):
                segments.append(WhisperSegment(
                    id=seg.get("id", 0),
                    start=seg.get("start", 0.0),
                    end=seg.get("end", 0.0),
                    text=seg.get("text", "").strip()
                ))

            result = WhisperResult(
                text=result_json.get("text", ""),
                segments=segments,
                language=result_json.get("language", "")
            )

            print(f"✅ 识别完成，语言: {result.language}, 片段数: {len(result.segments)}")
            return result

        except requests.exceptions.Timeout:
            raise RuntimeError("Whisper 服务请求超时")
        except requests.exceptions.ConnectionError:
            raise RuntimeError(f"无法连接到 Whisper 服务: {self.whisper_service_url}")
        except Exception as e:
            raise RuntimeError(f"Whisper 识别失败: {e}")

    def save_as_srt(
        self,
        result: WhisperResult,
        output_srt_path: str,
        video_path: Optional[str] = None
    ) -> str:
        """
        将识别结果保存为 SRT 字幕文件

        Args:
            result: WhisperResult 对象
            output_srt_path: 输出 SRT 文件路径或目录
            video_path: 原视频路径（如果 output_srt_path 是目录，用来生成文件名）

        Returns:
            输出 SRT 文件路径
        """
        output_srt_path = Path(output_srt_path)

        # 如果是目录，自动生成文件名
        if output_srt_path.is_dir():
            if video_path:
                output_srt_path = output_srt_path / f"{Path(video_path).stem}.srt"
            else:
                output_srt_path = output_srt_path / "output.srt"
            print(f"📁 输出是目录，自动生成文件名: {output_srt_path.name}")

        # 确保父目录存在
        output_srt_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_srt_path, "w", encoding="utf-8") as f:
            for idx, seg in enumerate(result.segments, 1):
                # 写入序号
                f.write(f"{idx}\n")

                # 写入时间戳
                start_str = self._seconds_to_srt_time(seg.start)
                end_str = self._seconds_to_srt_time(seg.end)
                f.write(f"{start_str} --> {end_str}\n")

                # 写入文本
                f.write(f"{seg.text}\n\n")

        print(f"✅ SRT 字幕已保存: {output_srt_path.name}")
        return str(output_srt_path)

    def convert(
        self,
        input_video: str,
        output_srt: Optional[str] = None,
        language: Optional[str] = None,
        task: str = "transcribe",
        keep_audio: bool = False,
        initial_prompt: Optional[str] = None,
        force_reextract: bool = False
    ) -> str:
        """
        将输入视频文件转换为输出字幕文件（简单直接接口）

        Args:
            input_video: 输入视频文件路径
            output_srt: 输出 SRT 字幕文件路径，None 时自动保存到 output_dir
            language: 语言代码（如 'en', 'zh'），None 为自动检测
            task: 任务类型 ('transcribe' 转录 或 'translate' 翻译)
            keep_audio: 是否保留提取的音频文件
            initial_prompt: 初始提示词
            force_reextract: 强制重新提取音频（即使已存在）

        Returns:
            输出 SRT 字幕文件路径
        """
        result = self.process_video(
            video_path=input_video,
            output_srt_path=output_srt,
            language=language,
            task=task,
            keep_audio=keep_audio,
            initial_prompt=initial_prompt,
            force_reextract=force_reextract
        )
        return result["srt_path"]

    def process_video(
        self,
        video_path: str,
        output_srt_path: Optional[str] = None,
        language: Optional[str] = None,
        task: str = "transcribe",
        keep_audio: bool = False,
        initial_prompt: Optional[str] = None,
        force_reextract: bool = False
    ) -> Dict[str, str]:
        """
        完整处理流程：视频 -> 音频 -> Whisper识别 -> SRT字幕

        Args:
            video_path: 视频文件路径
            output_srt_path: 输出 SRT 文件路径（可选）
            language: 语言代码
            task: 任务类型 ('transcribe' 或 'translate')
            keep_audio: 是否保留提取的音频文件
            initial_prompt: 初始提示词
            force_reextract: 强制重新提取音频（即使已存在）

        Returns:
            包含路径信息的字典
        """
        video_path = Path(video_path)

        if not video_path.exists():
            raise FileNotFoundError(f"视频文件不存在: {video_path}")

        print("=" * 60)
        print("🎬 视频转字幕处理")
        print("=" * 60)

        # 确定输出路径
        if not output_srt_path:
            output_srt_path = self.output_dir / f"{video_path.stem}.srt"

        output_srt_path = Path(output_srt_path)

        # Step 1: 提取音频
        print("\n📌 Step 1: 从视频提取音频...")
        audio_path = self.extract_audio_from_video(
            str(video_path),
            force_reextract=force_reextract
        )

        # Step 2: Whisper 识别
        print("\n📌 Step 2: Whisper ASR 识别...")
        result = self.transcribe_with_whisper(
            audio_path,
            language=language,
            task=task,
            initial_prompt=initial_prompt
        )

        # Step 3: 保存 SRT
        print("\n📌 Step 3: 保存 SRT 字幕...")
        self.save_as_srt(result, str(output_srt_path), str(video_path))

        # 清理临时音频文件
        if not keep_audio and Path(audio_path).exists():
            Path(audio_path).unlink()
            print(f"🗑️ 临时音频文件已清理")

        print("\n" + "=" * 60)
        print("✅ 处理完成！")
        print(f"   SRT 字幕: {output_srt_path}")
        print("=" * 60)

        return {
            "video_path": str(video_path),
            "audio_path": audio_path,
            "srt_path": str(output_srt_path),
            "language": result.language
        }

    @staticmethod
    def _seconds_to_srt_time(seconds: float) -> str:
        """
        秒数转换为 SRT 时间戳格式

        Args:
            seconds: 秒数

        Returns:
            SRT 时间戳格式字符串 (HH:MM:SS,mmm)
        """
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millisecs = int((seconds - int(seconds)) * 1000)

        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millisecs:03d}"

    @staticmethod
    def _srt_time_to_seconds(time_str: str) -> float:
        """
        SRT 时间戳转换为秒数

        Args:
            time_str: SRT 时间戳格式字符串

        Returns:
            秒数
        """
        time_str = time_str.strip().replace(",", ".")
        parts = time_str.split(":")

        if len(parts) == 3:
            h, m, s = parts
            return int(h) * 3600 + int(m) * 60 + float(s)
        elif len(parts) == 2:
            m, s = parts
            return int(m) * 60 + float(s)
        else:
            return float(time_str)

    def load_srt(self, srt_path: str) -> List[WhisperSegment]:
        """
        加载 SRT 文件

        Args:
            srt_path: SRT 文件路径

        Returns:
            WhisperSegment 列表
        """
        srt_path = Path(srt_path)

        if not srt_path.exists():
            raise FileNotFoundError(f"SRT 文件不存在: {srt_path}")

        segments = []
        with open(srt_path, "r", encoding="utf-8") as f:
            content = f.read().strip()

        blocks = re.split(r"\n\n+", content)
        for block in blocks:
            lines = [l.strip() for l in block.strip().split("\n") if l.strip()]
            if len(lines) < 3:
                continue

            try:
                idx = int(lines[0])
            except ValueError:
                continue

            if "-->" not in lines[1]:
                continue

            start_str, end_str = lines[1].split("-->")
            start = self._srt_time_to_seconds(start_str.strip())
            end = self._srt_time_to_seconds(end_str.strip())
            text = " ".join(lines[2:]).strip()

            if text:
                segments.append(WhisperSegment(
                    id=idx,
                    start=start,
                    end=end,
                    text=text
                ))

        return segments


def main():
    """命令行接口"""
    import argparse

    # 辅助函数：去除值后面的注释
    def _clean_env_value(value: Optional[str]) -> Optional[str]:
        if not value:
            return value
        # 去除 # 后面的注释，以及首尾空白
        return value.split("#", 1)[0].strip() or None

    parser = argparse.ArgumentParser(
        description="视频转字幕工具 - 使用 Whisper ASR Web 服务"
    )

    parser.add_argument(
        "input_video",
        help="输入视频文件路径"
    )
    parser.add_argument(
        "output_srt",
        nargs="?",
        help="输出 SRT 字幕文件路径（可选，默认保存到 output_dir）"
    )
    parser.add_argument(
        "--service", "-s",
        default=_clean_env_value(os.getenv("WHISPER_SERVICE_URL")) or "http://localhost:9000/asr",
        help="Whisper ASR Web 服务地址 (默认: 来自环境变量 WHISPER_SERVICE_URL 或 http://localhost:9000/asr)"
    )
    parser.add_argument(
        "--language", "-l",
        default=_clean_env_value(os.getenv("WHISPER_LANGUAGE")),
        help="语言代码 (如 'en', 'zh'), 不指定则自动检测 (可通过环境变量 WHISPER_LANGUAGE 设置)"
    )
    parser.add_argument(
        "--task", "-t",
        default=_clean_env_value(os.getenv("WHISPER_TASK")) or "transcribe",
        choices=["transcribe", "translate"],
        help="任务类型: transcribe(转录) 或 translate(翻译) (可通过环境变量 WHISPER_TASK 设置)"
    )
    parser.add_argument(
        "--keep-audio", "-k",
        action="store_true",
        help="保留提取的音频文件 (可通过环境变量 WHISPER_KEEP_AUDIO=true 设置)"
    )
    parser.add_argument(
        "--force-reextract", "-f",
        action="store_true",
        help="强制重新提取音频（即使文件已存在）"
    )
    parser.add_argument(
        "--temp-dir",
        default=_clean_env_value(os.getenv("TEMP_DIR")) or "temp",
        help="临时文件目录 (默认: 来自环境变量 TEMP_DIR 或 temp)"
    )
    parser.add_argument(
        "--output-dir",
        default=_clean_env_value(os.getenv("OUTPUT_DIR")) or "output",
        help="默认输出目录 (默认: 来自环境变量 OUTPUT_DIR 或 output)"
    )

    args = parser.parse_args()

    # 创建处理器
    processor = VideoToSRT(
        whisper_service_url=args.service,
        temp_dir=args.temp_dir,
        output_dir=args.output_dir
    )

    # 处理视频
    try:
        # 处理 keep_audio：参数优先，然后环境变量
        keep_audio = args.keep_audio
        if not keep_audio:
            env_val = _clean_env_value(os.getenv("WHISPER_KEEP_AUDIO"))
            keep_audio = env_val and env_val.lower() in ("true", "1", "yes")

        srt_path = processor.convert(
            input_video=args.input_video,
            output_srt=args.output_srt,
            language=args.language,
            task=args.task,
            keep_audio=keep_audio,
            force_reextract=args.force_reextract
        )
        print(f"\n✅ 成功！字幕文件: {srt_path}")
        return 0
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        return 1


if __name__ == "__main__":
    # 使用示例
    # 方式0: 使用 .env 配置 (无需传参)
    processor = VideoToSRT()  # 参数从 .env 文件读取，没有则用默认值

    # 方式1: 简单直接 - 指定输入视频和输出字幕文件
    processor.convert(
        input_video='/Users/iox/Desktop/msagent/source/全网最全！60分钟全面掌握Claude Code～【附完整文档】.mp4',
        output_srt='',
        language='zh',  # 可选：指定语言，None 自动检测
        initial_prompt='这是一段 claudes code 的使用教学视频， 使用中文普通话录制。claudes code 是一个基于 ai 的开发平台，里面可能会出现很多基于开发相关的术语， 比如 skills plugin， skill， plugin， 插件, memory, 如果有读音类似，或是出现语句不通顺的地方时，请考虑选择正确的读音内容。'
    )
    # 方式2: 完整处理 - 更多参数控制
    # result = processor.process_video(
    #     video_path="/path/to/video.mp4",
    #     output_srt_path="/path/to/output.srt",
    #     language="en",
    #     task="transcribe",  # 或 "translate"
    #     keep_audio=False,
    #     initial_prompt=None
    # )
    # print(f"SRT 已生成: {result['srt_path']}")

    # 方式3: 命令行调用（见 main 函数）
    # 执行: python video_to_srt.py video.mp4 output.srt -l en
    # import sys
    # if len(sys.argv) > 1 and not sys.argv[0].endswith("unittest"):
    #     sys.exit(main())
