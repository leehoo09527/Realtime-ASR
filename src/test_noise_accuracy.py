#!/usr/bin/env python3
"""
噪音环境下的识别准确率实测。

做法：拿一段已知文本的干净语音，按不同信噪比（SNR）混入三类噪音，
再跑识别、比对原文、算字符错误率（CER）。CER 越低越好，0 为全对。

三类噪音：
  · 白噪声    —— 宽频稳态噪声
  · 粉红噪声  —— 低频更强，接近空调 / 机房环境底噪
  · 人声嘈杂  —— 多段人声错位叠加，最接近"会议室里旁边有人在说话"
"""
import re
import wave

import numpy as np
import sherpa_onnx

from asr_engine import SAMPLE_RATE, SV_DIR

REF = ("我们这个项目从去年开始立项，到现在差不多一年多的时间，"
       "中间经历了几次比较大的调整，最早的目标是做数据采集，"
       "后来发现采上来的数据质量不行，所以又加了一个治理的环节，"
       "现在整个链路基本打通了，从采集到清洗到入湖再到上层应用，"
       "每一个环节都有对应的工具支撑，接下来主要的工作是把这个链路标准化，"
       "让别的部门也能复用。")


def read_wav(path):
    with wave.open(path, "rb") as w:
        raw = w.readframes(w.getnframes())
        ch = w.getnchannels()
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return x


def normalize(s):
    return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", s)


def lev(a, b):
    if not a:
        return len(b)
    prev = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1,
                         prev[j - 1] + (a[i - 1] != b[j - 1]))
        prev = cur
    return prev[len(b)]


def cer(ref, hyp):
    r, h = normalize(ref), normalize(hyp)
    return (lev(r, h) / len(r)) if r else 0.0


rng = np.random.default_rng(20261014)

speech = read_wav("/tmp/clean.wav")
voices = [read_wav(f"/tmp/bab{i}.wav") for i in (1, 2, 3)]


def white_noise(n):
    return rng.normal(0, 1, n).astype(np.float32)


def pink_noise(n):
    w = rng.normal(0, 1, n)
    X = np.fft.rfft(w)
    f = np.fft.rfftfreq(n, 1 / SAMPLE_RATE)
    f[0] = f[1] if len(f) > 1 else 1.0
    y = np.fft.irfft(X / np.sqrt(f), n)
    return (y / (np.std(y) + 1e-9)).astype(np.float32)


def babble(n):
    out = np.zeros(n, dtype=np.float32)
    for v in voices:
        for _ in range(3):
            r = np.roll(v, int(rng.integers(0, len(v))))
            out += np.tile(r, n // len(r) + 1)[:n]
    return (out / (np.std(out) + 1e-9)).astype(np.float32)


def mix_at_snr(s, noise, snr_db):
    ps = float(np.mean(s ** 2)) + 1e-12
    pn = float(np.mean(noise ** 2)) + 1e-12
    noise = noise * np.sqrt(ps / (10 ** (snr_db / 10)) / pn)
    return np.clip(s + noise, -1, 1).astype(np.float32)


def add_reverb(x, rt60):
    """简易房间混响：指数衰减冲激响应卷积。rt60 = 混响时间（秒）。
    会议室里真正的杀手是它 —— 声音在墙面来回反射，把字与字糊在一起。"""
    n = max(int(rt60 * SAMPLE_RATE), 2)
    ir = np.exp(-6.9 * np.arange(n) / n)   # 衰减 60dB 所需时间 = rt60
    ir /= ir.sum()
    y = np.convolve(x, ir)[: len(x)]
    return (y / (np.std(y) + 1e-9) * np.std(x)).astype(np.float32)


rec = sherpa_onnx.OfflineRecognizer.from_sense_voice(
    model=str(SV_DIR / "model.int8.onnx"),
    tokens=str(SV_DIR / "tokens.txt"),
    num_threads=4, use_itn=True, language="auto", debug=False,
)


def recognize(x):
    st = rec.create_stream()
    st.accept_waveform(SAMPLE_RATE, x.astype(np.float32))
    rec.decode_stream(st)
    return st.result.text.strip()


print(f"参考文本 {len(normalize(REF))} 字\n")
print(f"{'场景':<14}{'CER':>8}   识别结果开头")
print("-" * 72)

base = recognize(speech)
print(f"{'干净(无噪音)':<14}{cer(REF, base)*100:>7.1f}%   {base[:26]}")

for name, gen in [("白噪声", white_noise), ("粉红噪声", pink_noise),
                  ("人声嘈杂", babble)]:
    for snr in (20, 15, 10, 5, 0):
        mixed = mix_at_snr(speech, gen(len(speech)), snr)
        text = recognize(mixed)
        print(f"{name+' '+str(snr)+'dB':<14}{cer(REF, text)*100:>7.1f}%   {text[:26]}")
    print()

print("=== 混响（会议室真实杀手）===")
print(f"{'场景':<20}{'CER':>8}   识别结果开头")
print("-" * 72)
for rt in (0.3, 0.6, 1.0):
    y = add_reverb(speech, rt)
    text = recognize(y)
    label = {0.3: "小会议室", 0.6: "中会议室", 1.0: "大/空房间"}[rt]
    print(f"{label+'(RT60 '+str(rt)+'s)':<20}{cer(REF, text)*100:>7.1f}%   {text[:26]}")

print()
print("=== 最接近真实会议：混响 + 旁边有人说话 ===")
for rt in (0.3, 0.6):
    for snr in (15, 10):
        y = add_reverb(speech, rt)
        y = mix_at_snr(y, babble(len(y)), snr)
        text = recognize(y)
        print(f"{'混响'+str(rt)+'s + 嘈杂'+str(snr)+'dB':<20}{cer(REF, text)*100:>7.1f}%   {text[:26]}")
