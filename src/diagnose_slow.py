#!/usr/bin/env python3
"""诊断：长音频下 VAD 的切句行为，以及识别耗时随音频长度的变化。
用来查清会议中途"卡住"的根因。
"""
import time
import wave

import numpy as np
import sherpa_onnx

from asr_engine import BLOCK, SAMPLE_RATE, SV_DIR, VAD_DIR


def read_wav(path):
    with wave.open(path, "rb") as w:
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


x = read_wav("/tmp/long.wav")
print(f"测试音频：{len(x)/SAMPLE_RATE:.1f} 秒连续语音（几乎无长停顿）")

rec = sherpa_onnx.OfflineRecognizer.from_sense_voice(
    model=str(SV_DIR / "model.int8.onnx"),
    tokens=str(SV_DIR / "tokens.txt"),
    num_threads=2, use_itn=True, language="auto", debug=False,
)

cfg = sherpa_onnx.VadModelConfig()
cfg.silero_vad.model = str(VAD_DIR / "silero_vad.onnx")
cfg.silero_vad.threshold = 0.45
cfg.silero_vad.min_silence_duration = 0.35
cfg.silero_vad.min_speech_duration = 0.20
cfg.silero_vad.max_speech_duration = 12.0
cfg.sample_rate = SAMPLE_RATE
vad = sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=180)

segs = []
for i in range(0, len(x), BLOCK):
    vad.accept_waveform(x[i : i + BLOCK])
    while not vad.empty():
        segs.append(np.asarray(vad.front.samples, dtype=np.float32))
        vad.pop()
vad.flush()
while not vad.empty():
    segs.append(np.asarray(vad.front.samples, dtype=np.float32))
    vad.pop()

print(f"\n=== VAD 切句结果（配置为 max_speech_duration = 12 秒）===")
for i, s in enumerate(segs, 1):
    print(f"  第{i}段：{len(s)/SAMPLE_RATE:.1f} 秒")
print(f"  → 共切出 {len(segs)} 段")

print("\n=== 识别耗时随音频长度变化（临时结果每次刷新的成本）===")
for sec in [2, 5, 10, 15, 20, 30, 40, 53]:
    n = min(sec, int(len(x) / SAMPLE_RATE))
    seg = x[: n * SAMPLE_RATE]
    if len(seg) == 0:
        continue
    t = time.time()
    st = rec.create_stream()
    st.accept_waveform(SAMPLE_RATE, seg)
    rec.decode_stream(st)
    cost = (time.time() - t) * 1000
    flag = "  ← 超过 500ms 刷新间隔！" if cost > 500 else ""
    print(f"  {len(seg)/SAMPLE_RATE:5.1f} 秒音频 → 识别 {cost:6.0f} ms{flag}")
