# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

msagent is an English teaching video automatic translation and dubbing pipeline. It translates English videos to Chinese with dubbing, supporting local LLM translation, Edge-TTS speech synthesis, and checkpoint resumption.

## Key Files

### Core Pipeline

- **`video_localizer.py`** - Core processing module (7-step pipeline)
  - Video → Audio extraction (not implemented yet, uses existing subtitles)
  - Subtitle parsing (.srt/.vtt)
  - Sentence merging (look-ahead algorithm)
  - Translation (Ollama local or DashScope cloud)
  - TTS with Edge-TTS
  - Audio stitching
  - Video merging

- **`video_to_srt.py`** - Video to SRT subtitle tool
  - Extracts audio from video
  - Calls Whisper ASR web service for speech recognition
  - Generates SRT subtitle files

### Batch Processing

- **`aiagent_csharp_videos_batch_process.py`** - Batch processing entry point
- **`temp_batch_process.py`** - Another batch processing script
- **`check_video_list.py`** - Parses video_list.html to generate task queue
- **`export_aiagent_csharp.py`** - Exports finished products to upload directory with seq numbering

### Task Queue

- **`aiagent_csharp_task_queue.py`** - Auto-generated task queue (119 videos)

### Test Files

- **`test_video_localizer.py`** - Single video test
- **`test_load_srt.py`** - SRT parsing test
- **`test_video_to_srt.py`** - Tests for video_to_srt.py

## Project Structure

```
msagent/
├── video_localizer.py              # Core processing module
├── video_to_srt.py                 # Video to SRT tool (new!)
├── aiagent_csharp_videos_batch_process.py  # Batch processing
├── export_aiagent_csharp.py       # Export finished products
├── check_video_list.py             # Parse video list HTML
├── aiagent_csharp_task_queue.py    # Task queue
├── temp_batch_process.py           # Alternative batch processing
├── test_video_localizer.py         # Single video test
├── test_video_to_srt.py           # Tests for video_to_srt.py
├── test_load_srt.py               # SRT parsing test
├── video_list.html                # Video list HTML source
├── requirements.txt               # Python dependencies
├── .env                           # Environment variables
├── source/                         # Source videos and subtitles
├── temp/                           # Temporary files (per-video directories)
├── output/                         # Final output
└── upload/                         # Export directory
```

## Common Tasks

### Install Dependencies

```bash
pip install -r requirements.txt
```

### Run Tests

```bash
# Run video_to_srt tests
python -m pytest test_video_to_srt.py -v
# Or using unittest
python -m unittest test_video_to_srt.py -v
```

### Run Single Video Processing (Legacy Pipeline)

Edit `test_video_localizer.py` with paths, then:

```bash
python test_video_localizer.py
```

### Run Video to SRT Tool

```bash
# Basic usage - auto-saves to output_dir
python video_to_srt.py "video.mp4"

# Specify output file
python video_to_srt.py "video.mp4" "output.srt"

# Specify service URL and language
python video_to_srt.py "video.mp4" "output.srt" --service "http://192.168.0.80:9000/asr" --language "zh"
```

### Run Batch Processing

```bash
python aiagent_csharp_videos_batch_process.py
```

### Regenerate Task Queue

After modifying `video_list.html`:

```bash
python check_video_list.py
```

### Export Finished Products

```bash
python export_aiagent_csharp.py
```

## Key Concepts

### The 7-Step Pipeline (video_localizer.py)

1. Read subtitle file (SRT or VTT)
2. Merge fragmented sentences using look-ahead algorithm
3. Translate to Chinese (Ollama local or DashScope cloud)
4. Calculate TTS speed parameters
5. Synthesize Chinese speech with Edge-TTS
6. Stitch audio with Pydub
7. Merge video with FFmpeg

### Terminology Preservation

The system preserves technical terms during translation:
- Framework: Agent, Framework, LLM, API, SDK, Semantic Kernel
- Platforms: GitHub, NuGet, Azure, OpenAI, Anthropic
- Languages: C#, .NET, Python, JavaScript, TypeScript
- Formats: JSON, XML, HTML, CSS
- Tools: CLI, IDE, Debug, CI/CD

### Whisper ASR Integration (video_to_srt.py)

- Uses `whisper-asr-webservice` at `/asr` endpoint
- File field: `audio_file`
- Parameters via query string: `task`, `language`, `output`
- Supports output formats: json, srt, vtt, txt, tsv

### Environment Variables (.env)

```bash
OLLAMA_BASE_URL=http://localhost:11434
DASHSCOPE_API_KEY=your_key (optional)
WHISPER_SERVICE_URL=http://192.168.0.80:9000/asr
WHISPER_LANGUAGE=zh
WHISPER_TASK=transcribe
WHISPER_KEEP_AUDIO=false
TEMP_DIR=temp
OUTPUT_DIR=output
```

## External Dependencies

- **FFmpeg** - Video/audio processing (required)
- **Ollama** - Local LLM deployment (optional)

## Development Tips

1. **Checkpoint Resumption**: Each video's status is saved in `temp/{video_name}/checkpoint.json`
2. **Use Resume**: Set `resume_from_checkpoint=True` to skip completed steps
3. **Debug Mode**: Use debug flags in Config class to troubleshoot
4. **Terminology List**: Edit `PRESERVE_TERMS` in video_localizer.py to add more preserved terms
