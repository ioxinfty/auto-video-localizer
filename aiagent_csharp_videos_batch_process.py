"""
批量处理入口 - AI Agent C# 课程

从 task_queue.py 载入任务，批量执行视频转换。
输出到脚本所在目录下的 output/aiagent_c# 目录。
"""

import os
from pathlib import Path

from video_localizer import (
    Config,
    main as process_video,
)

# 从 task_queue 导入任务队列
from aiagent_csharp_task_queue import task_queue


def run_batch_process(config: Config, output_dir: str):
    """
    批量处理队列中的所有视频。
    """
    total = len(task_queue)
    print(f"📋 从 task_queue 加载 {total} 个任务")

    # 预览前 3 个任务
    for idx in range(min(3, total)):
        task = task_queue[idx]
        print(f"   [{task['seq']}] {task['title']}")
        print(f"       视频={task['video'] or '(空)'}")
        print(f"       字幕={task['subtitle'] or '(空)'}")
        print(f"       封面={task['cover'] or '(空)'}")
    if total > 3:
        print(f"   ... 共 {total} 个")

    success_count = 0
    skip_count = 0
    fail_count = 0

    for idx, task in enumerate(task_queue):
        seq = task["seq"]
        title = task["title"]
        video_path = task["video"]
        subtitle_path = task["subtitle"]
        cover_path = task["cover"]

        # 跳过没有视频的任务
        if not video_path:
            print(f"\n⚠️ [{idx + 1}/{total}] 序号 {seq} - 缺少视频，跳过")
            continue

        video_name = Path(video_path).stem  # 如 "xxx.1080p"

        print(f"\n{'=' * 70}")
        print(f"🎬 进度: [{idx + 1}/{total}] 序号 {seq}")
        print(f"📝 标题: {title}")
        print(f"📹 视频: {Path(video_path).name}")
        print(f"📄 字幕: {Path(subtitle_path).name if subtitle_path else '(无)'}")
        print(f"🖼️ 封面: {Path(cover_path).name if cover_path else '(无)'}")
        print(f"{'=' * 70}")

        # 断点续传：检查是否已有输出
        if config.resume_from_checkpoint:
            # 尝试多个可能的输出文件名
            possible_outputs = [
                Path(output_dir) / video_name / f"{video_name}.cn.mp4",
                Path(output_dir) / f"{video_name}.cn.mp4",
                Path(output_dir) / video_name / f"{video_name}.mp4",
            ]
            for final_video in possible_outputs:
                if final_video.exists() and final_video.stat().st_size > 1000:
                    print(f"  ⏭️ 已存在输出文件，跳过: {final_video}")
                    skip_count += 1
                    break
            else:
                # 没有找到已完成的输出，继续处理
                pass
        else:
            skip_count = 0  # 不启用断点续传时重置

        try:
            process_video(
                video_path=video_path,
                srt_path=subtitle_path if subtitle_path else None,
                output_dir=output_dir,
                config=config,
            )
            success_count += 1
        except Exception as e:
            print(f"❌ [{seq}] 处理失败: {e}")
            import traceback
            traceback.print_exc()
            fail_count += 1
            continue

    print(f"\n{'=' * 70}")
    print(f"🎉 全部完成!")
    print(f"   ✅ 成功: {success_count}")
    print(f"   ⏭️ 跳过: {skip_count}")
    print(f"   ❌ 失败: {fail_count}")
    print(f"   📁 输出目录: {output_dir}")
    print(f"{'=' * 70}")


# ============================================================
# 入口
# ============================================================

if __name__ == "__main__":
    # 加载 .env 环境变量文件
    from dotenv import load_dotenv
    load_dotenv()

    # 输出目录
    SCRIPT_DIR = Path(__file__).resolve().parent
    OUTPUT_DIR = SCRIPT_DIR / "output" / "aiagent_c#"

    # 确保输出目录存在
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # 配置
    config = Config(
        use_local_llm=True,            # 翻译方式：True=本地 Ollama，False=云端 DashScope
        ollama_base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
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

    # 批量处理
    run_batch_process(config, str(OUTPUT_DIR))
