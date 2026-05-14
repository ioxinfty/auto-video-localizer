#Requires -Version 7.0
<#
.SYNOPSIS
    批量烧录中文字幕到 MP4 视频

.DESCRIPTION
    读取 aiagent_c# 目录中的所有 MP4 视频，
    查找同名的 .chs.srt 字幕文件并烧录进视频，
    输出到 aiagent_c#_baked_subtitles 目录。

.PARAMETER SourceDir
    源视频目录（默认: /Users/iox/Desktop/msagent/upload/aiagent_c#）

.PARAMETER OutputDir
    输出目录（默认: /Users/iox/Desktop/msagent/upload/aiagent_c#_baked_subtitles）
#>


param(
    [string]$SourceDir = $null,
    [string]$OutputDir = $null
)

$errorActionPreference = "Stop"

# ──────────────────────────────────────────────
# 前置检查
# ──────────────────────────────────────────────

# 设置默认源目录为脚本所在目录下的 upload/aiagent_c#
if (-not $SourceDir) {
    $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    $SourceDir = Join-Path $scriptDir "upload/aiagent_c#"
}


if (-not (Test-Path -LiteralPath $SourceDir)) {
    throw "[错误] 源目录不存在: $SourceDir"
}

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    throw "[错误] 未找到 ffmpeg，请先安装: brew install ffmpeg"
}

if (-not $OutputDir) {
    $OutputDir = Join-Path (Split-Path $SourceDir -Parent) "$((Split-Path $SourceDir -Leaf))_baked_subtitles"
}


# 创建输出目录
if (-not (Test-Path -LiteralPath $OutputDir)) {
    New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
    Write-Host "[INFO] 创建输出目录: $OutputDir"
}

# 查找所有 MP4 文件
$videoFiles = Get-ChildItem -LiteralPath $SourceDir -Include "*.mp4" -File
if ($videoFiles.Count -eq 0) {
    throw "[错误] 源目录中没有找到 MP4 文件: $SourceDir"
}

Write-Host "========================================"
Write-Host "  字幕烧录批处理"
Write-Host "========================================"
Write-Host "  源目录: $SourceDir"
Write-Host "  输出目录: $OutputDir"
Write-Host "  字幕透明度: 10%（Alpha=0x1A）"
Write-Host "  找到视频: $($videoFiles.Count) 个"
Write-Host "========================================`n"

# 字幕样式参数
# Alpha 不透明度计算：0x00=完全不透明，0xFF=完全透明
# ABGR 格式: &HAABBGGRR
#   PrimaryColour: 白色 100%不透明 = &H00FFFFFF
#   BackColour:    背景框 黑色 75%不透明(25%透明) = &HC0000000
# 注意：字体名中的空格需要转义为 \（ffmpeg 要求）
# 参数说明：
#   FontName          → 字体：PingFang SC（空格需转义为 \）
#   FontSize=11       → 字体大小 11（较小字号，适合多行字幕）
#   PrimaryColour     → 文字颜色：白色，10% 透明（Alpha=0x1A）
#   OutlineColour     → 描边颜色（Outline=0 时无效果）
#   BackColour        → 背景框颜色：黑色，75% 不透明（半透明黑底）
#   Outline=0         → 无描边
#   Shadow=1          → 阴影深度 1 像素
#   BorderStyle=4     → 样式：背景框 + 描边 + 阴影
#   Alignment=2       → 位置：底部居中
#   MarginV=8         → 距离底部 8 像素（更贴近底部）
$SUBTITLE_STYLE = "FontName=PingFang\ SC,FontSize=11,PrimaryColour=&H1AFFFFFF,OutlineColour=&H00000000,BackColour=&HC0000000,Outline=0,Shadow=1,BorderStyle=4,Alignment=2,MarginV=8"


$successCount = 0
$failCount = 0
$failList = @()

foreach ($video in $videoFiles) {
    $videoPath = $video.FullName # MP4 文件
    $srtPath = [System.IO.Path]::ChangeExtension($videoPath, ".chs.srt") # 中文字幕文件
    $outName = $video.Name # 输出文件名
    $outPath = Join-Path $OutputDir -ChildPath $outName # 输出文件

    # 检查输出文件是否已存在（跳过已处理的）
    if (Test-Path -LiteralPath $outPath) {
        Write-Host "[跳过] 已存在: $outName" -ForegroundColor Cyan
        $successCount++
        continue
    }

    # 检查字幕文件是否存在
    if (-not (Test-Path -LiteralPath $srtPath)) {
        throw "字幕文件不存在: $srtPath"
    }
    
    Write-Host "[处理] $($video.Name) ..."

    # 创建临时文件（避免 # 和空格导致路径问题）
    $tmpSrt = "/tmp/subtitle_baked_$([System.Guid]::NewGuid().ToString('N')).srt"
    $tmpVideo = "/tmp/video_baked_$([System.Guid]::NewGuid().ToString('N')).mp4"
    $tmpOutput = "/tmp/video_output_$([System.Guid]::NewGuid().ToString('N')).mp4"
    Copy-Item -LiteralPath $srtPath -Destination $tmpSrt -Force
    if (-not (Test-Path -LiteralPath $tmpSrt)) {
        throw "临时 SRT 文件复制失败: $tmpSrt (源文件: $srtPath)"
    }
    
    # 创建视频文件的符号链接（避免复制大文件）
    New-Item -ItemType SymbolicLink -Path $tmpVideo -Target $videoPath -Force | Out-Null

    try {
        # 构建 ffmpeg 命令（使用临时文件路径）
        # 使用 PowerShell 调用操作符 & 直接执行，更好地控制参数传递
        $vfArg = "subtitles='${tmpSrt}':force_style='${SUBTITLE_STYLE}'"
        
        # 使用 & 直接调用 ffmpeg，参数作为数组传递
        $ffmpegArgs = @(
            "-y",
            "-hide_banner",
            "-loglevel", "error",
            "-i", $tmpVideo,
            "-vf", $vfArg,
            "-c:v", "mpeg4",
            "-q:v", "3",
            "-c:a", "copy",
            $tmpOutput
        )
        
        # 执行 ffmpeg 并捕获错误输出
        $stderrFile = "$tmpSrt.stderr.txt"
        & ffmpeg @ffmpegArgs 2> $stderrFile
        
        # 检查 ffmpeg 退出码
        if ($LASTEXITCODE -ne 0) {
            $stderr = ""
            if (Test-Path -LiteralPath $stderrFile) {
                $stderr = Get-Content -LiteralPath $stderrFile -Raw -ErrorAction SilentlyContinue
            }
            throw "ffmpeg 返回非零退出码: $LASTEXITCODE`n$stderr"
        }

        # 验证临时输出文件
        if (-not (Test-Path -LiteralPath $tmpOutput) -or (Get-Item -LiteralPath $tmpOutput).Length -eq 0) {
            throw "输出文件为空或未生成: $tmpOutput"
        }

        # 将临时输出文件复制到最终路径
        Copy-Item -LiteralPath $tmpOutput -Destination $outPath -Force

        $outSize = [math]::Round((Get-Item -LiteralPath $outPath).Length / 1MB, 1)
        Write-Host "[完成] $($video.Name) → ${outSize} MB" -ForegroundColor Green
        $successCount++

    }
    catch {
        Write-Host "[失败] $($video.Name): $_" -ForegroundColor Red
        throw "[终止] 处理 $($video.Name) 时发生错误，脚本已停止。"
    }
    finally {
        # 清理临时文件
        Remove-Item -LiteralPath $tmpSrt -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $tmpVideo -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $tmpOutput -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath "$tmpSrt.stderr.txt" -Force -ErrorAction SilentlyContinue
    }

    # throw "[终止] 处理 $($video.Name) 时发生错误，脚本已停止。"

}

# ──────────────────────────────────────────────
# 汇总报告
# ──────────────────────────────────────────────
Write-Host "`n========================================"
Write-Host "  处理完成"
Write-Host "========================================"
Write-Host "  成功: $successCount"
Write-Host "  失败/跳过: $failCount"
Write-Host "========================================"

if ($failCount -gt 0) {
    Write-Host "`n失败列表:"
    foreach ($item in $failList) {
        Write-Host "  $item" -ForegroundColor Yellow
    }
    throw "[终止] 有 $failCount 个文件处理失败，请查看上方错误信息。"
}

Write-Host "`n全部完成！输出目录: $OutputDir" -ForegroundColor Green
