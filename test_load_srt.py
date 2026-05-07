import sys
sys.path.insert(0, ".")

from video_localizer import load_subtitle

srt = "source/aiagent_youtube/AI in C# (Microsoft Agent Framework)/DevUI Introduction - AI in C# (Microsoft Agent Framework).1080p.en.vtt"

segs = load_subtitle(srt)
print(f"片段数: {len(segs)}")
if segs:
    print(f"第一个: {segs[0]}")
    print(f"最后一个: {segs[-1]}")
