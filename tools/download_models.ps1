# 下载语音模型（约 242 MB，走 hf-mirror 国内镜像）
#
# 用法（在包根目录，PowerShell）：
#     powershell -ExecutionPolicy Bypass -File tools\download_models.ps1
#
# 下完得到：
#     models\sense-voice\model.int8.onnx   228 MB  SenseVoice-small int8
#     models\sense-voice\tokens.txt        309 KB
#     models\silero-vad\silero_vad.onnx    1.7 MB  Silero VAD
#
# 如果这里下载不通，改从 Mac 上直接拷贝这两个目录（可通过 ASR_MODEL_ROOT 指定路径），一样能用。

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Base = "https://hf-mirror.com"
$SvRepo = "$Base/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/resolve/main"

$Sv = Join-Path $Root "models\sense-voice"
$Vad = Join-Path $Root "models\silero-vad"
New-Item -ItemType Directory -Force -Path $Sv, $Vad | Out-Null

$files = @(
    @{ Url = "$SvRepo/model.int8.onnx"; Out = "$Sv\model.int8.onnx"; Min = 200MB },
    @{ Url = "$SvRepo/tokens.txt";      Out = "$Sv\tokens.txt";      Min = 100KB },
    @{ Url = "$Base/csukuangfj/vad/resolve/main/silero_vad.onnx"; Out = "$Vad\silero_vad.onnx"; Min = 1MB }
)

foreach ($f in $files) {
    $name = Split-Path -Leaf $f.Out
    if ((Test-Path $f.Out) -and ((Get-Item $f.Out).Length -ge $f.Min)) {
        Write-Host "[跳过] $name 已存在且大小正常"
        continue
    }
    Write-Host "[下载] $name ..."
    # curl.exe 是 Win10 1803+ 自带的，断点续传+进度条都靠它，比 Invoke-WebRequest 稳
    & curl.exe -fL --retry 3 --retry-delay 2 -C - -o $f.Out $f.Url
    if ($LASTEXITCODE -ne 0) { throw "下载失败：$($f.Url)" }
    $size = (Get-Item $f.Out).Length
    if ($size -lt $f.Min) { throw "文件不完整：$($f.Out)（$([math]::Round($size/1MB,1)) MB）" }
    Write-Host ("[完成] {0}  {1:N1} MB" -f $name, ($size/1MB))
}

Write-Host "`n模型齐了，接着跑：python tools\check_env.py"
