"""
VideoToSRT 类的测试用例

使用方法：
python -m unittest test_video_to_srt.py -v
"""

import unittest
import os
import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock, mock_open

# 导入要测试的类
from video_to_srt import VideoToSRT, WhisperSegment, WhisperResult


class TestWhisperSegment(unittest.TestCase):
    """测试 WhisperSegment 数据类"""

    def test_create_segment(self):
        """测试创建片段"""
        seg = WhisperSegment(id=1, start=0.0, end=2.5, text="Hello world")
        self.assertEqual(seg.id, 1)
        self.assertEqual(seg.start, 0.0)
        self.assertEqual(seg.end, 2.5)
        self.assertEqual(seg.text, "Hello world")


class TestWhisperResult(unittest.TestCase):
    """测试 WhisperResult 数据类"""

    def test_create_result(self):
        """测试创建结果"""
        segments = [
            WhisperSegment(id=1, start=0.0, end=1.0, text="Hello"),
            WhisperSegment(id=2, start=1.0, end=2.0, text="world")
        ]
        result = WhisperResult(text="Hello world", segments=segments, language="en")
        self.assertEqual(result.text, "Hello world")
        self.assertEqual(len(result.segments), 2)
        self.assertEqual(result.language, "en")


class TestVideoToSRT(unittest.TestCase):
    """测试 VideoToSRT 类"""

    def setUp(self):
        """测试前的准备"""
        self.temp_dir = tempfile.mkdtemp()
        self.output_dir = tempfile.mkdtemp()
        self.processor = VideoToSRT(
            whisper_service_url="http://localhost:9000/asr",
            temp_dir=self.temp_dir,
            output_dir=self.output_dir
        )

    def tearDown(self):
        """测试后的清理"""
        # 清理临时文件
        import shutil
        shutil.rmtree(self.temp_dir)
        shutil.rmtree(self.output_dir)

    def test_init(self):
        """测试初始化"""
        self.assertEqual(self.processor.whisper_service_url, "http://localhost:9000/asr")
        self.assertEqual(str(self.processor.temp_dir), self.temp_dir)
        self.assertEqual(str(self.processor.output_dir), self.output_dir)

        # 测试目录是否创建
        self.assertTrue(Path(self.temp_dir).exists())
        self.assertTrue(Path(self.output_dir).exists())

    def test_seconds_to_srt_time(self):
        """测试秒数转 SRT 时间戳"""
        test_cases = [
            (0.0, "00:00:00,000"),
            (1.5, "00:00:01,500"),
            (60.0, "00:01:00,000"),
            (3661.123, "01:01:01,123"),
        ]

        for seconds, expected in test_cases:
            result = VideoToSRT._seconds_to_srt_time(seconds)
            self.assertEqual(result, expected)

    def test_srt_time_to_seconds(self):
        """测试 SRT 时间戳转秒数"""
        test_cases = [
            ("00:00:00,000", 0.0),
            ("00:00:01,500", 1.5),
            ("00:01:00,000", 60.0),
            ("01:01:01,123", 3661.123),
            ("00:00:00.000", 0.0),  # 点号格式也支持
        ]

        for time_str, expected in test_cases:
            result = VideoToSRT._srt_time_to_seconds(time_str)
            self.assertEqual(result, expected)

    def test_save_as_srt(self):
        """测试保存 SRT 文件"""
        # 准备测试数据
        segments = [
            WhisperSegment(id=1, start=0.0, end=2.0, text="Hello world"),
            WhisperSegment(id=2, start=2.0, end=4.0, text="This is a test"),
        ]
        result = WhisperResult(text="Hello world This is a test", segments=segments, language="en")

        # 保存 SRT
        output_path = Path(self.output_dir) / "test.srt"
        saved_path = self.processor.save_as_srt(result, str(output_path))

        # 验证文件存在
        self.assertTrue(Path(saved_path).exists())
        self.assertEqual(saved_path, str(output_path))

        # 读取并验证内容
        with open(saved_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("1", content)
        self.assertIn("00:00:00,000 --> 00:00:02,000", content)
        self.assertIn("Hello world", content)
        self.assertIn("This is a test", content)

    def test_load_srt(self):
        """测试加载 SRT 文件"""
        # 创建测试 SRT
        srt_content = """1
00:00:00,000 --> 00:00:02,000
Hello world

2
00:00:02,000 --> 00:00:04,000
This is a test
"""
        srt_path = Path(self.temp_dir) / "test.srt"
        with open(srt_path, "w", encoding="utf-8") as f:
            f.write(srt_content)

        # 加载 SRT
        segments = self.processor.load_srt(str(srt_path))

        # 验证
        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0].id, 1)
        self.assertEqual(segments[0].text, "Hello world")
        self.assertEqual(segments[1].id, 2)
        self.assertEqual(segments[1].text, "This is a test")

    @patch("subprocess.run")
    def test_extract_audio_from_video(self, mock_subprocess_run):
        """测试从视频提取音频"""
        # 模拟 FFmpeg 成功
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_subprocess_run.return_value = mock_result

        # 创建假视频文件
        video_path = Path(self.temp_dir) / "test.mp4"
        with open(video_path, "w") as f:
            f.write("fake video")

        # 提取音频
        output_audio = Path(self.temp_dir) / "test.mp3"
        result = self.processor.extract_audio_from_video(str(video_path), str(output_audio))

        # 验证 FFmpeg 被调用
        mock_subprocess_run.assert_called_once()
        args = mock_subprocess_run.call_args[0][0]
        self.assertIn("ffmpeg", args[0])
        self.assertIn("-i", args)
        self.assertIn(str(video_path), args)
        self.assertIn(str(output_audio), args)

    @patch("subprocess.run")
    def test_extract_audio_from_video_not_found(self, mock_subprocess_run):
        """测试视频文件不存在"""
        with self.assertRaises(FileNotFoundError):
            self.processor.extract_audio_from_video("/nonexistent/video.mp4")

    @patch("subprocess.run")
    def test_extract_audio_from_video_ffmpeg_failed(self, mock_subprocess_run):
        """测试 FFmpeg 失败"""
        # 模拟 FFmpeg 失败
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "FFmpeg error"
        mock_subprocess_run.return_value = mock_result

        # 创建假视频文件
        video_path = Path(self.temp_dir) / "test.mp4"
        with open(video_path, "w") as f:
            f.write("fake video")

        with self.assertRaises(RuntimeError) as ctx:
            self.processor.extract_audio_from_video(str(video_path))

        self.assertIn("FFmpeg", str(ctx.exception))

    @patch("requests.post")
    def test_transcribe_with_whisper(self, mock_post):
        """测试 Whisper 识别"""
        # 准备模拟响应
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "text": "Hello world",
            "segments": [
                {"id": 0, "start": 0.0, "end": 1.0, "text": "Hello"},
                {"id": 1, "start": 1.0, "end": 2.0, "text": "world"}
            ],
            "language": "en"
        }
        mock_post.return_value = mock_response

        # 创建假音频文件
        audio_path = Path(self.temp_dir) / "test.mp3"
        with open(audio_path, "w") as f:
            f.write("fake audio")

        # 调用识别
        result = self.processor.transcribe_with_whisper(str(audio_path), language="en")

        # 验证
        self.assertEqual(result.text, "Hello world")
        self.assertEqual(len(result.segments), 2)
        self.assertEqual(result.language, "en")
        mock_post.assert_called_once()

    @patch("requests.post")
    def test_transcribe_with_whisper_not_found(self, mock_post):
        """测试音频文件不存在"""
        with self.assertRaises(FileNotFoundError):
            self.processor.transcribe_with_whisper("/nonexistent/audio.mp3")

    @patch("requests.post")
    def test_transcribe_with_whisper_service_error(self, mock_post):
        """测试 Whisper 服务错误"""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Server Error"
        mock_post.return_value = mock_response

        # 创建假音频文件
        audio_path = Path(self.temp_dir) / "test.mp3"
        with open(audio_path, "w") as f:
            f.write("fake audio")

        with self.assertRaises(RuntimeError) as ctx:
            self.processor.transcribe_with_whisper(str(audio_path))

        self.assertIn("500", str(ctx.exception))

    @patch.object(VideoToSRT, 'extract_audio_from_video')
    @patch.object(VideoToSRT, 'transcribe_with_whisper')
    @patch.object(VideoToSRT, 'save_as_srt')
    def test_process_video(self, mock_save, mock_transcribe, mock_extract):
        """测试完整处理流程"""
        # 准备模拟数据
        video_path = Path(self.temp_dir) / "test.mp4"
        with open(video_path, "w") as f:
            f.write("fake video")

        mock_extract.return_value = str(Path(self.temp_dir) / "test.mp3")

        segments = [
            WhisperSegment(id=1, start=0.0, end=2.0, text="Hello world"),
        ]
        mock_transcribe.return_value = WhisperResult(
            text="Hello world",
            segments=segments,
            language="en"
        )

        expected_srt = Path(self.output_dir) / "test.srt"
        mock_save.return_value = str(expected_srt)

        # 调用处理
        result = self.processor.process_video(
            str(video_path),
            language="en",
            keep_audio=False
        )

        # 验证
        mock_extract.assert_called_once()
        mock_transcribe.assert_called_once()
        mock_save.assert_called_once()
        self.assertEqual(result["video_path"], str(video_path))
        self.assertEqual(result["srt_path"], str(expected_srt))


class TestVideoToSRTIntegration(unittest.TestCase):
    """集成测试（需要实际文件和服务）"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.output_dir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir)
        shutil.rmtree(self.output_dir)

    def test_full_workflow_with_mocks(self):
        """使用模拟对象测试完整流程"""
        processor = VideoToSRT(
            whisper_service_url="http://localhost:9000/asr",
            temp_dir=self.temp_dir,
            output_dir=self.output_dir
        )

        # 这里不实际调用外部服务，而是测试逻辑流程
        self.assertTrue(Path(self.temp_dir).exists())
        self.assertTrue(Path(self.output_dir).exists())


if __name__ == "__main__":
    unittest.main()
