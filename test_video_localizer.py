"""
测试 video_localizer 模块

用于验证视频翻译和 TTS 功能。
"""

import sys
import os
from pathlib import Path

# 加载 .env 环境变量文件
from dotenv import load_dotenv
load_dotenv()

# 将当前脚本所在目录添加到模块搜索路径
sys.path.insert(0, str(Path(__file__).parent))

from video_localizer import main, Config

# 配置
config = Config(
    use_local_llm=True,
    ollama_base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
    ollama_model="qwen2.5:14b",
    tts_voice="zh-CN-YunxiNeural",
    tts_delay=0.2,
    keep_original_bgm=False,
    bgm_volume=0.25,
    keep_original_voice=False,
    burn_subtitles=False,
    enable_checkpoint=True,
    resume_from_checkpoint=False,  # 测试时全部重新生成
    speed_ratio_max=1.0,
)

# 测试文件路径
video_path = '/Users/iox/Desktop/msagent/source/aiagent_youtube/AI in C# (Microsoft Agent Framework)/DevUI Introduction - AI in C# (Microsoft Agent Framework).1080p.mkv'
srt_path = '/Users/iox/Desktop/msagent/source/aiagent_youtube/AI in C# (Microsoft Agent Framework)/DevUI Introduction - AI in C# (Microsoft Agent Framework).1080p.en.vtt'
output_dir = "/Users/iox/Desktop/msagent/output"

if __name__ == "__main__":
    main(
        video_path=video_path,
        srt_path=srt_path,
        output_dir=output_dir,
        config=config,
    )
