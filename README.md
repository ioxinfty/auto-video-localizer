# 英文教学视频自动翻译配音流水线

将英文视频自动翻译为中文配音，支持本地 LLM 翻译、Edge-TTS 语音合成、断点续传。

- `video_localizer.py` 核心处理模块， 
- `test_video_localizer` 处理单视频例子
- `temp_batch_process` 批量处理例子， 从目录载入待处理视频及字幕
- `aiagent_csharp_videos_batch_process` 批量处理例子，从 youtube 页面中解析出文件名

## 项目结构

```
msagent/
├── video_localizer.py              # 核心处理模块（7步流水线）
├── aiagent_csharp_videos_batch_process.py  # 批量处理入口， 调用 aiagent_csharp_task_queue.py 之类的任务
├── check_video_list.py             # 解析 video_list.html 生成任务队列， 保存为 task_queue.py 供 aiagent_csharp_videos_batch_process.py 调用
├── aiagent_csharp_task_queue.py    # 自动生成的任务队列（119个视频）
├── temp_batch_process.py           # 另一个批量处理脚本
├── test_video_localizer.py         # 单视频测试
├── test_load_srt.py               # SRT 解析测试
├── video_list.html                # 视频列表（源数据）, youtube 视频合集 f12 复制 html 保存
├── requirements.txt               # Python 依赖

source/                             # 原始视频和字幕
└── aiagent_youtube/
    └── AI in C# (Microsoft Agent Framework)/
        ├── *.mkv                  # 原始视频
        └── *.vtt                  # YouTube 自动字幕

temp/                               # 临时文件（按视频分目录）
└── {视频名}/
    ├── checkpoint.json             # 断点续传记录
    ├── english.srt                # 英文字幕（转换后）
    ├── chinese.srt               # 中文字幕
    ├── chinese_audio.wav         # 中文配音音频
    ├── original_audio.wav        # 原视频音频（用于BGM）
    ├── pure_video.mp4            # 无音频的纯净视频
    ├── processing_data.json      # 完整处理数据
    └── audio/                    # TTS 逐句音频
        └── *.wav

output/                            # 最终成品
└── aiagent_c#/
    └── {视频名}/
        ├── *.cn.mp4              # 中文配音视频
        ├── *.chs.srt             # 中文字幕
        └── *.en.srt             # 英文字幕
```

## 流水线 7 步详解

```
原始视频 ──→ 提取音频（暂未实现） ──→ 字幕解析 ──→ 句子拼接 ──→ 翻译 ──→ TTS ──→ 音频拼接 ──→ 视频合并 ──→ 最终视频
             
```

### Step 1: 读取字幕文件
解析 `.srt` 或 `.vtt` 格式字幕为结构化片段列表。

### Step 2: 拼接碎片句（**前瞻探测算法**）
Whisper/VTT 生成的字幕常被切成碎片（如每行几个词），本步骤将其合并为完整句子。

**核心算法**：
```
当前片段无结束符?
  → 前探 seg[i+1], seg[i+2], ...
    ├─ 发现强结束符(.!?) → 合并到那里断句
    ├─ 发现弱结束符(,) → 检查下一句是否紧接强结束句且总长≤120字符
    │                   └─ 若是 → 跳过逗号继续前探
    │                   └─ 若否 → 在逗号处断句
    ├─ 停顿 > 2秒 → 兜底断句
    └─ 到末尾 → 全部合并且断句
当前片段有强结束符? → 直接成单句
```

**标点分类**：
| 类型 | 符号 |
|------|------|
| 强结束符 | `. ! ? 。 ！ ？ ' " ) ]` |
| 弱结束符 | `, ; : 、 ， ； ： — - / \` |

超过 150 字的长句按逗号进一步拆分。

### Step 3: 翻译为中文
- **本地模式**: Ollama (`qwen2.5:14b` 或 `qwen2.5:7b`)
- **云端模式**: 阿里云 DashScope

**术语保留机制**: 专业术语（Agent, Framework, LLM, API, SDK, C#, .NET, NuGet, Azure 等）在翻译时强制保留不替换。

### Step 4: 计算 TTS 语速参数
根据中文字数和原字幕时长计算每句的语速比例，确保配音与原音同步。

**策略**：
- 语速范围: 0.77x ~ 1.05x（最多加速30%或减速5%）
- 正常语速约 5.2 字/秒

### Step 5: Edge-TTS 合成中文语音
使用微软 Edge-TTS 免费语音合成。

**可选语音**：
- 男声: `zh-CN-YunxiNeural`（云希，推荐）、`zh-CN-YunyangNeural`（云扬）
- 女声: `zh-CN-XiaoxiaoNeural`（晓晓）、`zh-CN-XiaomoNeural`（晓墨）

### Step 6: Pydub 拼接音频
将逐句 TTS 音频按时间轴拼接为完整 WAV 文件。

**策略**: 按原字幕时间戳定位，短了补静音，长了保留完整内容。

### Step 7: FFmpeg 合并视频
将中文配音音频与原视频合并为最终成品。

**可选功能**：
- 保留原视频 BGM（可调音量 0~1.0）
- 移除原视频人声
- 烧录字幕到视频

## 配置参数

### 翻译配置
| 参数 | 默认值 | 说明 |
|------|--------|------|
| `use_local_llm` | `True` | True=本地Ollama，False=阿里云DashScope |
| `ollama_base_url` | `localhost:11434` | Ollama 服务器地址 |
| `ollama_model` | `qwen2.5:14b` | Ollama 模型 |
| `dashscope_key` | 空 | 阿里云 API Key |

### TTS 配置
| 参数 | 默认值 | 说明 |
|------|--------|------|
| `tts_voice` | `zh-CN-YunxiNeural` | TTS 语音角色 |
| `chinese_reading_speed` | `5.2` | 中文朗读速度（字/秒） |
| `speed_ratio_min` | `0.77` | 最小语速（最多加速30%） |
| `speed_ratio_max` | `1.05` | 最大语速（最多减速5%） |
| `tts_delay` | `0.2` | TTS 请求延时（秒） |

### 音频/视频配置
| 参数 | 默认值 | 说明 |
|------|--------|------|
| `keep_original_bgm` | `False` | 是否保留原视频背景音乐 |
| `bgm_volume` | `0.25` | BGM 音量（0.0-1.0） |
| `keep_original_voice` | `False` | 是否保留原视频人声 |
| `burn_subtitles` | `False` | 是否烧录字幕到视频 |

### 断点续传配置
| 参数 | 默认值 | 说明 |
|------|--------|------|
| `enable_checkpoint` | `True` | 是否启用断点续传 |
| `resume_from_checkpoint` | `True` | 是否跳过已完成步骤 |

### 调试开关
| 参数 | 默认值 | 说明 |
|------|--------|------|
| `debug_breakpoint_after_parse` | `False` | 解析完成后中断 |
| `debug_log_merge` | `False` | 打印合并过程详细日志 |
| `debug_breakpoint_after_merge` | `False` | 合并完成后中断 |

## 依赖安装

```bash
pip install -r requirements.txt
```

**requirements.txt 包含**：
- `edge-tts` - 微软 TTS 语音合成（免费，无需 API Key）
- `pydub` - 音频处理
- `ollama` - 本地 LLM 翻译
- `dashscope` - 阿里云翻译（可选）
- `python-dotenv` - 环境变量
- `lxml` - HTML 解析

**外部依赖**（需自行安装）：
- [Ollama](https://ollama.ai/) - 本地部署 LLM
- FFmpeg - 视频/音频处理

## 使用方法

### 1. 配置环境变量

创建 `.env` 文件：
```bash
OLLAMA_BASE_URL=http://localhost:11434
# 或使用阿里云
# DASHSCOPE_API_KEY=your_api_key_here
```

### 2. 启动 Ollama

```bash
ollama serve
ollama pull qwen2.5:14b
```

### 3. 批量处理视频

```bash
python aiagent_csharp_videos_batch_process.py
```

### 4. 单视频测试

修改 `test_video_localizer.py` 中的路径，然后运行：
```bash
python test_video_localizer.py
```

### 5. 重新生成任务队列

修改 `video_list.html` 后，运行：
```bash
python check_video_list.py
```

## 断点续传

每个视频的处理状态保存在 `temp/{视频名}/checkpoint.json`。中断后可设置 `resume_from_checkpoint=True` 跳过已完成步骤，从断点继续。

如需全部重新生成，设置：
```python
config.resume_from_checkpoint = False
```

## 术语保留

以下专业术语在翻译时强制保留不替换：

- **框架**: Agent, Framework, LLM, API, SDK, Semantic Kernel
- **平台**: GitHub, NuGet, Azure, OpenAI, Anthropic, Google, Amazon
- **语言**: C#, .NET, Python, JavaScript, TypeScript
- **格式**: JSON, XML, HTML, CSS, YAML, TOML
- **协议**: REST, gRPC, GraphQL, WebSocket, HTTP, TCP
- **工具**: CLI, IDE, Debug, Test, CI/CD, Docker, Kubernetes

## 工作原理图

```
┌─────────────────────────────────────────────────────────────────┐
│                        原始视频 + 字幕                            │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ Step 1: 解析字幕 (.vtt/.srt → 碎片片段列表)                       │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ Step 2: 前瞻探测算法（碎片 → 完整句子）                            │
│   • 探测强/弱结束符                                               │
│   • 停顿兜底                                                     │
│   • 长句拆分                                                     │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ Step 3: LLM 翻译（英文 → 中文）                                   │
│   • Ollama 本地 / 阿里云 DashScope                               │
│   • 术语保留机制                                                 │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ Step 4: 计算语速（按时间轴同步）                                   │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ Step 5: Edge-TTS 语音合成                                        │
│   • 微软免费语音                                                 │
│   • 逐句生成 WAV                                                 │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ Step 6: 音频拼接（Pydub）                                        │
│   • 按时间轴拼接                                                 │
│   • 短了补静音                                                   │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ Step 7: 视频合并（FFmpeg）                                       │
│   • 配音 + 原视频/BGM                                            │
│   • 可选烧录字幕                                                 │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                     中文配音视频 + 双语字幕                        │
└─────────────────────────────────────────────────────────────────┘
```
