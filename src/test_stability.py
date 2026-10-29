#!/usr/bin/env python3
"""
长时间连续说话的稳定性测试。

用伪造的麦克风（按实时速度回放 53.7 秒长音频）驱动完整引擎流程，
统计定稿段数、每段识别耗时、以及音频队列是否积压。

这正是"会议中途卡住"的复现场景：连续说话、VAD 迟迟不切句。
"""
import sys
import threading
import time
import wave
from pathlib import Path

import numpy as np

WAV = "/tmp/long.wav"
SR = 16000
BLOCK = 512

with wave.open(WAV, "rb") as w:
    raw = w.readframes(w.getnframes())
X = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
DURATION = len(X) / SR


class FakeInputStream:
    """按真实时间把音频文件喂给引擎的 callback，替代真实麦克风。"""

    callbacks_made = 0

    def __init__(self, samplerate=None, channels=None, dtype=None,
                 blocksize=None, callback=None, **kw):
        self.callback = callback
        self._stop = False
        self._thread = None
        FakeInputStream.callbacks_made += 1

    def __enter__(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *args):
        self._stop = True
        return False

    def _run(self):
        t0 = time.time()
        for i in range(0, len(X), BLOCK):
            if self._stop:
                return
            target = t0 + i / SR
            gap = target - time.time()
            if gap > 0:
                time.sleep(gap)
            chunk = X[i : i + BLOCK].reshape(-1, 1)
            self.callback(chunk, len(chunk), None, None)


import sounddevice
sounddevice.InputStream = FakeInputStream

sys.path.insert(0, str(Path(__file__).parent))
from asr_engine import AsrEngine  # noqa: E402

finals = []
costs = []
partials = 0


def on_final(text, elapsed):
    finals.append((elapsed, text))


def on_partial(text, elapsed):
    global partials
    partials += 1


e = AsrEngine(data_dir="/tmp/asr-stability", on_final=on_final, on_partial=on_partial)

_orig_recognize = e._recognize


def timed_recognize(samples):
    t = time.time()
    r = _orig_recognize(samples)
    costs.append((len(samples) / SR, (time.time() - t) * 1000))
    return r


e._recognize = timed_recognize

print(f"开始：回放 {DURATION:.1f} 秒连续语音（实时速度）…")
t0 = time.time()
e.start()
time.sleep(DURATION + 3)
e.stop()
e.wait(20)
wall = time.time() - t0

print(f"\n=== 结果 ===")
print(f"总耗时 {wall:.1f} 秒（音频 {DURATION:.1f} 秒）")
print(f"定稿 {len(finals)} 段，临时刷新 {partials} 次")
print()
print("各段时长与识别耗时：")
for i, (elapsed, text) in enumerate(finals, 1):
    print(f"  第{i}段 @{elapsed:5.1f}s  识别内容长度 {len(text)} 字")
print()
if costs:
    audio_lens = [c[0] for c in costs]
    ms = [c[1] for c in costs]
    print(f"识别次数 {len(costs)}")
    print(f"  音频长度  最大 {max(audio_lens):.1f}s / 平均 {sum(audio_lens)/len(audio_lens):.1f}s")
    print(f"  识别耗时  最大 {max(ms):.0f}ms / 平均 {sum(ms)/len(ms):.0f}ms")
    over = [m for m in ms if m > 500]
    print(f"  超过 500ms 的次数：{len(over)}" + ("  ← 会拖慢实时性" if over else "  ← 没有，很健康"))
