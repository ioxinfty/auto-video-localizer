"""
导出 aiagent_c# 视频及字幕到 upload 目录
根据 aiagent_csharp_task_queue.py 中的 seq 编号重命名文件
"""
import os
import shutil
import sys
from pathlib import Path
from typing import Optional

# 获取脚本所在目录
SCRIPT_DIR = Path(__file__).parent

# 添加当前目录到 sys.path，以便导入 aiagent_csharp_task_queue
sys.path.insert(0, str(SCRIPT_DIR))

from aiagent_csharp_task_queue import task_queue

# 路径配置（使用相对路径）
OUTPUT_DIR = SCRIPT_DIR / 'output' / 'aiagent_c#'
UPLOAD_DIR = SCRIPT_DIR / 'upload' / 'aiagent_c#'

# 创建目标目录
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def clean_filename(name: str) -> str:
    """清理文件名中的非法字符"""
    illegal_chars = ['/', '\\', ':', '*', '?', '"', '<', '>', '|']
    cleaned = name
    for char in illegal_chars:
        cleaned = cleaned.replace(char, ' -')
    # 替换其他可能导致问题的字符
    cleaned = cleaned.replace('⧸', '-')
    cleaned = cleaned.replace('：', '-')
    cleaned = cleaned.replace('？', '')
    return cleaned.strip()


def normalize_for_matching(text: str) -> str:
    """归一化特殊字符用于文件夹名匹配

    video_localizer.py 处理时或文件系统会将部分特殊字符转义：
      / → ⧸ (fraction slash)
      : → ： (full-width colon)
      ? → ？ (full-width question mark)
    """
    normalized = text
    char_map = {
        '/': '⧸',
        ':': '：',
        '?': '？',
    }
    for original, escaped in char_map.items():
        normalized = normalized.replace(original, escaped)
    return normalized


def find_video_folder(title: str) -> Optional[Path]:
    """根据 title 查找对应的输出文件夹（支持字符归一化 + 多分辨率回退）"""
    normalized_title = normalize_for_matching(title)

    # 策略1: 精确匹配 — 原始 title + 分辨率后缀
    for suffix in ['.1080p', '.720p']:
        folder_path = OUTPUT_DIR / f"{title}{suffix}"
        if folder_path.exists():
            return folder_path

    # 策略2: 归一化精确匹配 — 转义后的 title + 分辨率后缀
    for suffix in ['.1080p', '.720p']:
        folder_path = OUTPUT_DIR / f"{normalized_title}{suffix}"
        if folder_path.exists():
            return folder_path

    # 策略3: 遍历目录做子串模糊匹配（最后手段）
    for folder in OUTPUT_DIR.iterdir():
        if not folder.is_dir():
            continue
        if title in folder.name or normalized_title in folder.name:
            return folder

    return None


def export_video(task: dict, seq: int):
    """导出单个视频及其字幕"""
    title = task['title']
    seq_str = f"{seq:03d}"  # 格式化为 001, 002, ...
    
    # 查找视频文件夹
    video_folder = find_video_folder(title)
    if not video_folder:
        print(f"  [警告] 未找到视频文件夹: {title}")
        return False
    
    # 清理后的标题（用于文件名）
    clean_title = clean_filename(title)
    
    # 查找并复制文件
    files_copied = 0
    
    for file_path in video_folder.iterdir():
        if not file_path.is_file():
            continue
        
        file_name = file_path.name
        
        # 视频文件
        if file_name.endswith('.cn.mp4'):
            new_name = f"{seq_str}. {clean_title}.mp4"
            dest_path = UPLOAD_DIR / new_name
            if dest_path.exists():
                print(f"  [跳过] {new_name} (已存在)")
            else:
                shutil.copy2(file_path, dest_path)
                print(f"  [视频] {new_name}")
            files_copied += 1
        
        # 中文字幕
        elif file_name.endswith('.cn.chs.srt'):
            new_name = f"{seq_str}. {clean_title}.chs.srt"
            dest_path = UPLOAD_DIR / new_name
            if dest_path.exists():
                print(f"  [跳过] {new_name} (已存在)")
            else:
                shutil.copy2(file_path, dest_path)
                print(f"  [中文字幕] {new_name}")
            files_copied += 1
        
        # 英文字幕
        elif file_name.endswith('.cn.en.srt'):
            new_name = f"{seq_str}. {clean_title}.en.srt"
            dest_path = UPLOAD_DIR / new_name
            if dest_path.exists():
                print(f"  [跳过] {new_name} (已存在)")
            else:
                shutil.copy2(file_path, dest_path)
                print(f"  [英文字幕] {new_name}")
            files_copied += 1
    
    if files_copied == 0:
        print(f"  [警告] 文件夹中存在，但未找到预期的文件: {video_folder}")
        return False
    
    return True


def main():
    """主函数"""
    print("=" * 60)
    print("开始导出 aiagent_c# 视频及字幕")
    print(f"源目录: {OUTPUT_DIR}")
    print(f"目标目录: {UPLOAD_DIR}")
    print("=" * 60)
    
    success_count = 0
    failed_tasks = []
    
    for idx, task in task_queue.items():
        seq = task['seq']
        title = task['title']
        
        print(f"\n[{idx + 1}/{len(task_queue)}] 处理: {title} (seq={seq})")
        
        try:
            if export_video(task, seq):
                success_count += 1
            else:
                failed_tasks.append((seq, title))
        except Exception as e:
            print(f"  [错误] {e}")
            failed_tasks.append((seq, title))
    
    # 输出总结
    print("\n" + "=" * 60)
    print(f"导出完成!")
    print(f"成功: {success_count}/{len(task_queue)}")
    if failed_tasks:
        print(f"失败: {len(failed_tasks)} 个")
        for seq, title in failed_tasks:
            print(f"  - [{seq}] {title}")
    print("=" * 60)


if __name__ == '__main__':
    main()
