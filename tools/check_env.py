#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""环境自检 —— 在 Windows 上第一步就跑这个。

用法（在包根目录）：
    python tools\\check_env.py

它只做检查和一次真实识别，不改任何东西。任何一项红灯都会在末尾汇总，
方便直接定位是「环境没装好」「模型没放好」还是「麦克风权限没开」。

退出码：0 全绿 / 1 模型或引擎不可用 / 2 推理失败
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"

# 让 asr_engine 认识本包自带的 models/（打包后由 PyInstaller 处理，不需要这个）
if (ROOT / "models" / "sense-voice").is_dir():
    os.environ.setdefault("ASR_MODEL_ROOT", str(ROOT / "models"))

sys.path.insert(0, str(SRC))

# Windows 控制台默认 GBK，中文/✓ 会炸；尽力切到 UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OK, BAD, WARN = "[ OK ]", "[FAIL]", "[WARN]"
problems: list[str] = []
warnings: list[str] = []


def line(tag: str, text: str):
    print(f"{tag} {text}")


def fail(text: str):
    line(BAD, text)
    problems.append(text)


def warn(text: str):
    line(WARN, text)
    warnings.append(text)


def head(title: str):
    print(f"\n=== {title} ===")


def main() -> int:
    head("运行环境")
    print(f"Python      : {sys.version.split()[0]}  ({sys.executable})")
    print(f"平台        : {sys.platform} / {os.name} / {os.cpu_count()} 核")
    print(f"CPU 架构    : {os.environ.get('PROCESSOR_ARCHITECTURE', 'n/a')}")
    print(f"包根目录    : {ROOT}")

    head("依赖包")
    from importlib.metadata import version as dist_version
    for mod, pipname in [
        ("numpy", "numpy"),
        ("sherpa_onnx", "sherpa-onnx"),
        ("sounddevice", "sounddevice"),
        ("webview", "pywebview"),
    ]:
        try:
            __import__(mod)
            try:
                v = dist_version(pipname)
            except Exception:
                v = "?"
            line(OK, f"{pipname:<12} {v}")
        except Exception as e:
            fail(f"{pipname} 导入失败：{type(e).__name__}: {e}")

    if sys.platform == "win32":
        try:
            import clr  # noqa: F401  pythonnet
            line(OK, "pythonnet   可导入（pywebview 的 Windows 后端依赖）")
        except Exception as e:
            fail(f"pythonnet 不可用：{e}（pip install pythonnet）")

        # WebView2 Runtime：没有它界面窗口起不来
        try:
            import winreg
            keys = [
                (winreg.HKEY_LOCAL_MACHINE,
                 r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
                (winreg.HKEY_LOCAL_MACHINE,
                 r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
                (winreg.HKEY_CURRENT_USER,
                 r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
            ]
            ver = None
            for hive, path in keys:
                try:
                    with winreg.OpenKey(hive, path) as k:
                        ver = winreg.QueryValueEx(k, "pv")[0]
                    break
                except OSError:
                    continue
            if ver:
                line(OK, f"WebView2    已安装 {ver}")
            else:
                warn("没查到 WebView2 Runtime；Win11 一般自带，老 Win10 需手动装")
        except Exception as e:
            warn(f"WebView2 检测跳过：{e}")

    import shutil
    ff = shutil.which("ffmpeg")
    line(OK if ff else WARN, f"ffmpeg      {ff or '未安装（只影响「保存音频并结束」的 m4a 压缩，会退回 wav）'}")

    head("模型文件")
    import asr_engine as ae
    print(f"模型目录    : {ae.MODEL_ROOT}")
    need = [
        (ae.SV_DIR / "model.int8.onnx", 200 * 1024 * 1024),
        (ae.SV_DIR / "tokens.txt", 100 * 1024),
        (ae.VAD_DIR / "silero_vad.onnx", 1 * 1024 * 1024),
    ]
    for p, minimum in need:
        if not p.exists():
            fail(f"缺少 {p}")
        elif p.stat().st_size < minimum:
            fail(f"文件偏小，可能下载不全：{p}（{p.stat().st_size/1048576:.1f} MB）")
        else:
            line(OK, f"{p.name:<20} {p.stat().st_size/1048576:>7.1f} MB")

    head("音频设备")
    try:
        import sounddevice as sd
        line(OK, f"PortAudio   {sd.get_portaudio_version()[1]}")
        ins = [d for d in sd.query_devices() if d["max_input_channels"] > 0]
        line(OK if ins else WARN, f"输入设备    {len(ins)} 个")
        for d in ins[:5]:
            print(f"        · {d['name'][:60]}  ({d['max_input_channels']}ch, 默认 {d['default_samplerate']:.0f} Hz)")
        try:
            dev = sd.query_devices(kind="input")
            print(f"        默认输入：{dev['name']}")
        except Exception as e:
            warn(f"取默认输入设备失败：{e}")
        # 真正打开一次 16k 单声道，验证设备支持（不开麦克风权限的机器这里会报错）
        try:
            with sd.InputStream(samplerate=16000, channels=1, dtype="float32", blocksize=512):
                pass
            line(OK, "16000 Hz 单声道采集可用")
        except Exception as e:
            fail(f"打开 16k 单声道失败：{e}（检查系统麦克风权限 / 隐私设置）")
    except Exception as e:
        fail(f"sounddevice 不可用：{type(e).__name__}: {e}")

    head("真实识别（用包内测试音频）")
    sample = ROOT / "sample" / "sample_16k.wav"
    if not sample.exists():
        warn(f"没有测试音频 {sample}，跳过识别测试")
    elif problems:
        warn("前面有红灯，跳过识别测试（先把环境修好）")
    else:
        try:
            from realtime_asr import build_recognizer, recognize, read_wav_16k

            t0 = time.time()
            rec = build_recognizer(num_threads=4)
            load_s = time.time() - t0
            samples, sr = read_wav_16k(str(sample))
            t1 = time.time()
            text = recognize(rec, samples)
            cost = time.time() - t1
            audio_s = len(samples) / sr
            line(OK, f"模型加载 {load_s:.2f}s")
            print(f"        音频 {audio_s:.1f}s → 识别 {cost:.2f}s，实时倍率 {audio_s/max(cost,1e-6):.1f}x")
            print(f"        结果：{text}")
            if not text:
                fail("识别结果为空（模型能加载但没出字，检查音频格式/模型版本）")
        except Exception as e:
            fail(f"识别失败：{type(e).__name__}: {e}")
            return 2

    head("汇总")
    if problems:
        print(f"有 {len(problems)} 项必须解决：")
        for p in problems:
            print(f"  · {p}")
        print("\n排查顺序建议：模型齐全 → 依赖装全 → 麦克风权限/WebView2。")
        return 1
    if warnings:
        print(f"通过（{len(warnings)} 条提醒，不影响构建）：")
        for w in warnings:
            print(f"  · {w}")
    else:
        print("全部通过，可以开始构建。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
