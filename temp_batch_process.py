"""
批量处理入口

用于批量处理微软 Agent Framework 课程的翻译和配音任务。
"""

import os
from pathlib import Path
from dataclasses import dataclass

# 从 video_localizer 导入核心功能
from video_localizer import (
    Config,
    main,
    get_ms_agent_batch_queue,
)


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
    # 加载 .env 环境变量文件
    from dotenv import load_dotenv
    load_dotenv()

    # 配置
    config = Config(
        use_local_llm=True,            # 翻译方式：True=本地 Ollama，False=云端 DashScope
        ollama_base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),  # Ollama 服务器地址
        ollama_model="qwen2.5:14b",     # Ollama 模型：qwen2.5:14b（质量高）, qwen2.5:7b（速度快）
        tts_voice="zh-CN-YunxiNeural",  # TTS 语音：YunxiNeural(云希男声，推荐), YunyangNeural(云扬), XiaoxiaoNeural(晓晓女声)
        tts_delay=0.2,                  # TTS 请求延时（秒），避免被限速
        keep_original_bgm=False,        # 是否保留原视频背景音乐
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
