"""
视频转字幕工具类
功能：
1. 从视频中提取音频（MP3）
2. 调用 Whisper ASR Web 服务进行语音识别
3. 生成 SRT 字幕文件

依赖安装：
pip install requests
"""

import os
import re
import json
import subprocess
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass


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
        whisper_service_url: str = "http://localhost:9000/asr",
        temp_dir: str = "temp",
        output_dir: str = "output"
    ):
        """
        初始化视频转字幕工具

        Args:
            whisper_service_url: Whisper ASR Web 服务地址
            temp_dir: 临时文件目录
            output_dir: 输出目录
        """
        self.whisper_service_url = whisper_service_url
        self.temp_dir = Path(temp_dir)
        self.output_dir = Path(output_dir)

        # 创建目录
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def extract_audio_from_video(
        self,
        video_path: str,
        output_audio_path: Optional[str] = None,
        bitrate: str = "128k"
    ) -> str:
        """
        从视频中提取音频（MP3）

        Args:
            video_path: 视频文件路径
            output_audio_path: 输出音频文件路径（可选）
            bitrate: MP3 比特率

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

        # 准备请求参数
        files = {"audio_file": open(str(audio_path), "rb")}
        data = {
            "task": task,
            "word_timestamps": str(word_timestamps).lower(),
            "output": "json"
        }

        if language:
            data["language"] = language

        if initial_prompt:
            data["initial_prompt"] = initial_prompt

        try:
            response = requests.post(
                self.whisper_service_url,
                files=files,
                data=data,
                timeout=3600  # 较长超时，大文件可能需要更久
            )

            files["audio_file"].close()

            if response.status_code != 200:
                raise RuntimeError(f"Whisper 服务返回错误 {response.status_code}: {response.text}")

            # 解析响应
            result_json = response.json()

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
        output_srt_path: str
    ) -> str:
        """
        将识别结果保存为 SRT 字幕文件

        Args:
            result: WhisperResult 对象
            output_srt_path: 输出 SRT 文件路径

        Returns:
            输出 SRT 文件路径
        """
        output_srt_path = Path(output_srt_path)

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

    def process_video(
        self,
        video_path: str,
        output_srt_path: Optional[str] = None,
        language: Optional[str] = None,
        task: str = "transcribe",
        keep_audio: bool = False,
        initial_prompt: Optional[str] = None
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
        if output_srt_path is None:
            output_srt_path = self.output_dir / f"{video_path.stem}.srt"

        output_srt_path = Path(output_srt_path)

        # Step 1: 提取音频
        print("\n📌 Step 1: 从视频提取音频...")
        audio_path = self.extract_audio_from_video(str(video_path))

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
        self.save_as_srt(result, str(output_srt_path))

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


if __name__ == "__main__":
    # 使用示例
    processor = VideoToSRT(
        whisper_service_url="http://localhost:9000/asr",
        temp_dir="temp",
        output_dir="output"
    )

    # 完整处理视频
    # processor.process_video(
    #     video_path="/path/to/video.mp4",
    #     language="en",
    #     keep_audio=False
    # )
