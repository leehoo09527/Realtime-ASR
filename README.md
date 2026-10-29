# 实时语音转写 · Realtime ASR

基于 SenseVoice、sherpa-onnx 与 Silero VAD 的本地实时语音转文字工具。Python 负责音频采集和识别，pywebview 提供桌面界面。

支持麦克风实时字幕、暂停与继续、逐句保存转写文本、录音保存，以及命令行音频文件识别。模型准备完成后，识别在本机运行。

## 从源码启动

建议 Python 3.11 或 3.12（64 位）。macOS 是现有开发环境；Windows 提供源码启动和打包脚本，当前整理未重新验证 Windows 构建。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
./run.sh
```

requirements.txt 保留原 Windows 交接包的依赖版本，尚未在全新环境重新安装验证。macOS 的 pywebview 如提示缺少 Cocoa 后端，安装 `pywebview[cocoa]`。

## 模型

模型不提交到仓库。准备以下目录，并通过环境变量 `ASR_MODEL_ROOT` 指定其父目录；也可放进项目的 models/ 目录（启动脚本会自动使用）。兼容原缓存 `~/.cache/workbuddy-asr`。

```
models/
  sense-voice/model.int8.onnx
  sense-voice/tokens.txt
  silero-vad/silero_vad.onnx
```

模型来源：
- https://huggingface.co/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17
- https://huggingface.co/csukuangfj/vad

Windows 可执行 `powershell -ExecutionPolicy Bypass -File tools\download_models.ps1`，该脚本使用 hf-mirror 镜像。模型和第三方依赖的使用须遵守各自许可。

## 命令行

```bash
./run.sh --cli
./run.sh --cli --file /path/to/audio.wav
```

## Windows

安装 Python 后，创建 `.venv` 并安装 requirements.txt 中的依赖，准备模型，然后运行 `run_from_source.bat`。打包运行 `build_windows.bat`，输出到 dist_win/。需要系统 WebView2 运行环境。

## 数据与隐私

源码界面运行时，录音、转写文本及诊断日志保存在 src/data/；打包应用保存在用户 Documents/实时语音转写记录/。这些目录及模型、虚拟环境、构建产物均被 Git 忽略。请勿提交真实会议录音或转写记录。

## 检查

```bash
python -m compileall -q src tools
PYTHONPATH=src python src/test_persist.py
```

文本保存测试需要安装依赖，使用合成文字验证落盘，不验证真实识别质量。其他历史诊断脚本保存在 src/；部分需要模型、麦克风或图形界面，请阅读脚本后运行。

本次导入保留现有识别逻辑。音频保存历史脚本曾运行但未生成音频，因此音频采集与保存尚未验证通过，需在具有麦克风权限的环境另行检查。仓库暂为私有，尚未选择项目开源许可证。
