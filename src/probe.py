#!/usr/bin/env python3
"""环境探针：确认本机 sherpa-onnx 的 API 形态，避免凭猜测写调用。"""
import inspect
import sys

try:
    import sherpa_onnx
except Exception as e:
    print("IMPORT FAIL:", e)
    sys.exit(1)

print("sherpa_onnx version:", getattr(sherpa_onnx, "__version__", "unknown"))
print()

try:
    print("from_sense_voice signature:")
    print("  ", inspect.signature(sherpa_onnx.OfflineRecognizer.from_sense_voice))
except Exception as e:
    print("from_sense_voice 取签名失败:", e)
print()

for name in ("VoiceActivityDetector", "VadModelConfig", "SileroVadModelConfig"):
    cls = getattr(sherpa_onnx, name, None)
    if cls is None:
        print(f"{name}: 不存在")
        continue
    members = [m for m in dir(cls) if not m.startswith("_")]
    print(f"{name}: {members}")
    try:
        print(f"  __init__: {inspect.signature(cls.__init__)}")
    except Exception:
        pass
print()

probe_pcm = getattr(sherpa_onnx, "read_wave", None)
print("read_wave 可用:", probe_pcm is not None)

try:
    import sounddevice as sd
    print("sounddevice:", sd.__version__)
    d = sd.query_devices(kind="input")
    print("默认输入设备:", d.get("name"), "| 采样率", d.get("default_samplerate"))
except Exception as e:
    print("sounddevice 查询失败:", e)

import numpy as np
print("numpy:", np.__version__)
