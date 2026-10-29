#!/usr/bin/env python3
"""
本地实时语音识别 —— sherpa-onnx + SenseVoice + silero VAD

思路（模拟流式）：
  VAD 负责断句；说话过程中每 0.5 秒把"当前这句"整段重新识别一遍，输出临时文字；
  VAD 判定这句说完了，就定稿。全程离线，音频不出本机。

用法：
  # 麦克风实时转写（会议/访谈现场）
  python realtime_asr.py

  # 跑一段现成音频，验证识别效果（不需要麦克风）
  python realtime_asr.py --file ~/.cache/workbuddy-asr/sense-voice/zh.wav
"""
from __future__ import annotations

import argparse
import os
import queue
import sys
import time
import wave
from pathlib import Path

import numpy as np
import sherpa_onnx

SAMPLE_RATE = 16000
BLOCK = 512  # silero VAD 的窗口 @16kHz = 32ms
PARTIAL_WINDOW = 12.0  # 临时结果只看最近 12 秒，控制刷新延迟
# 模型目录解析与 asr_engine 保持一致（打包后指向 .app 包内的 models/）
from asr_engine import MODEL_ROOT, onnx_path  # noqa: E402

SV_DIR = MODEL_ROOT / "sense-voice"
VAD_DIR = MODEL_ROOT / "silero-vad"


# ---------------------------------------------------------------- 模型加载
def build_recognizer(num_threads: int = 2, language: str = "auto"):
    return sherpa_onnx.OfflineRecognizer.from_sense_voice(
        model=onnx_path(SV_DIR / "model.int8.onnx"),
        tokens=onnx_path(SV_DIR / "tokens.txt"),
        num_threads=num_threads,
        use_itn=True,          # 自动加标点
        language=language,     # auto / zh / en / ja / ko / yue
        debug=False,
    )


def build_vad(
    threshold: float = 0.45,
    min_silence: float = 0.35,
    min_speech: float = 0.20,
    max_speech: float = 12.0,
):
    cfg = sherpa_onnx.VadModelConfig()
    cfg.silero_vad.model = onnx_path(VAD_DIR / "silero_vad.onnx")
    cfg.silero_vad.threshold = threshold
    cfg.silero_vad.min_silence_duration = min_silence
    cfg.silero_vad.min_speech_duration = min_speech
    cfg.silero_vad.max_speech_duration = max_speech
    cfg.sample_rate = SAMPLE_RATE
    return sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=60)


def recognize(rec, samples: np.ndarray) -> str:
    """识别一段音频，返回文本。"""
    if samples is None or len(samples) == 0:
        return ""
    stream = rec.create_stream()
    stream.accept_waveform(SAMPLE_RATE, samples.astype(np.float32))
    rec.decode_stream(stream)
    return stream.result.text.strip()


# ---------------------------------------------------------------- 终端渲染
class Line:
    """在终端原地刷新临时结果，定稿后另起一行。"""

    def __init__(self):
        self._showing = False

    def partial(self, text: str, elapsed: float):
        if not text:
            return
        sys.stdout.write(f"\r\033[K  … {text}  [{elapsed:4.1f}s]")
        sys.stdout.flush()
        self._showing = True

    def commit(self, text: str, elapsed: float):
        if self._showing:
            sys.stdout.write("\r\033[K")
        sys.stdout.write(f"  ✓ {text}  [{elapsed:4.1f}s]\n")
        sys.stdout.flush()
        self._showing = False

    def clear(self):
        if self._showing:
            sys.stdout.write("\r\033[K")
            sys.stdout.flush()
            self._showing = False


# ---------------------------------------------------------------- 文件模式
def run_file(path: str, rec, vad, language: str):
    samples, sr = read_wav_16k(path)
    if sr != SAMPLE_RATE:
        samples = resample(samples, sr, SAMPLE_RATE)

    print(f"音频时长 {len(samples) / SAMPLE_RATE:.1f}s，开始分段识别…\n")

    t0 = time.time()
    line = Line()
    pending = np.zeros(0, dtype=np.float32)
    idx = 0
    seg_no = 0
    total_audio_ms = 0.0
    worst_ms = 0.0

    while idx < len(samples):
        chunk = samples[idx : idx + BLOCK]
        idx += BLOCK
        pending = np.concatenate([pending, chunk])
        vad.accept_waveform(chunk)

        if not vad.empty():
            while not vad.empty():
                vad.pop()  # VAD 只负责判定"这句说完了"，识别统一用累积音频
            t1 = time.time()
            text = recognize(rec, pending)
            cost = (time.time() - t1) * 1000
            worst_ms = max(worst_ms, cost)
            total_audio_ms += len(pending) / SAMPLE_RATE * 1000
            if text:
                seg_no += 1
                print(f"  [{seg_no:02d}] {text}   ({len(pending)/SAMPLE_RATE:.1f}s, 识别 {cost:.0f}ms)")
            pending = np.zeros(0, dtype=np.float32)

    # 收尾：把残留音频再啃一遍
    vad.flush()
    if not vad.empty():
        while not vad.empty():
            vad.pop()
        text = recognize(rec, pending)
        if text:
            seg_no += 1
            print(f"  [{seg_no:02d}] {text}   ({len(pending)/SAMPLE_RATE:.1f}s)")
        pending = np.zeros(0, dtype=np.float32)
    if len(pending) > SAMPLE_RATE * 0.3:
        text = recognize(rec, pending)
        if text:
            seg_no += 1
            print(f"  [{seg_no:02d}] {text}   ({len(pending)/SAMPLE_RATE:.1f}s)")

    wall = time.time() - t0
    audio_s = len(samples) / SAMPLE_RATE
    print()
    print(f"共 {seg_no} 段，音频 {audio_s:.1f}s，处理耗时 {wall:.1f}s"
          f"（实时倍率 {audio_s / wall:.1f}x），单次识别最慢 {worst_ms:.0f}ms")


def run_replay(path: str, rec, vad, language: str, partial_every: float):
    """按真实时间回放音频文件，验证"边说边出字"的实时体验（不占用麦克风）。"""
    samples, sr = read_wav_16k(path)
    if sr != SAMPLE_RATE:
        samples = resample(samples, sr, SAMPLE_RATE)

    print(f"实时回放 {len(samples)/SAMPLE_RATE:.1f}s 音频，模拟真实说话节奏…\n")

    line = Line()
    tail = np.zeros(0, dtype=np.float32)
    sent_start = time.time()
    last_partial = 0.0
    seg_no = 0
    t0 = time.time()
    idx = 0
    n_partial = 0

    while idx < len(samples):
        target_t = t0 + idx / SAMPLE_RATE
        sleep = target_t - time.time()
        if sleep > 0:
            time.sleep(sleep)

        block = samples[idx : idx + BLOCK]
        idx += BLOCK
        tail = np.concatenate([tail, block])

        vad.accept_waveform(block)

        committed = False
        while not vad.empty():
            vad.pop()  # VAD 只负责判定"这句说完了"，识别统一用累积音频
            committed = True

        if committed:
            txt = recognize(rec, tail)
            if txt:
                seg_no += 1
                line.commit(txt, time.time() - sent_start)
            tail = np.zeros(0, dtype=np.float32)
            sent_start = time.time()
            last_partial = 0.0
            continue

        now = time.time()
        if len(tail) >= SAMPLE_RATE * 0.7 and now - last_partial >= partial_every:
            last_partial = now
            n_partial += 1
            window = tail[-int(SAMPLE_RATE * PARTIAL_WINDOW) :]
            txt = recognize(rec, window)
            line.partial(txt, now - sent_start)

    vad.flush()
    if not vad.empty():
        while not vad.empty():
            vad.pop()
        txt = recognize(rec, tail)
        if txt:
            seg_no += 1
            line.commit(txt, time.time() - sent_start)
        tail = np.zeros(0, dtype=np.float32)
    if len(tail) > SAMPLE_RATE * 0.3:
        txt = recognize(rec, tail)
        if txt:
            seg_no += 1
            line.commit(txt, time.time() - sent_start)

    print(f"\n共 {seg_no} 句定稿，刷新了 {n_partial} 次中间结果，"
          f"音频 {len(samples)/SAMPLE_RATE:.1f}s / 总耗时 {time.time()-t0:.1f}s")


def read_wav_16k(path: str):
    with wave.open(path, "rb") as w:
        n, sr, ch, width = w.getnframes(), w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(n)
    dtype = {1: np.uint8, 2: np.int16, 4: np.int32}.get(width)
    if dtype is None:
        raise ValueError(f"不支持的位深: {width * 8} bit")
    data = np.frombuffer(raw, dtype=dtype).astype(np.float32)
    if dtype == np.int16:
        data /= 32768.0
    elif dtype == np.int32:
        data /= 2147483648.0
    elif dtype == np.uint8:
        data = (data - 128) / 128.0
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1)
    return data, sr


def resample(x: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    if sr_in == sr_out:
        return x
    n_out = int(round(len(x) * sr_out / sr_in))
    xp = np.linspace(0.0, 1.0, len(x), endpoint=False)
    fp = np.linspace(0.0, 1.0, n_out, endpoint=False)
    return np.interp(fp, xp, x).astype(np.float32)


# ---------------------------------------------------------------- 麦克风实时
def run_mic(rec, vad, language: str, output: str | None, partial_every: float):
    try:
        import sounddevice as sd
    except Exception as e:
        print("需要 sounddevice:", e)
        return 1

    audio_q: queue.Queue = queue.Queue()

    def callback(indata, frames, time_info, status):
        if status:
            print(status, file=sys.stderr)
        audio_q.put(indata[:, 0].copy())

    dev = sd.query_devices(kind="input")
    print(f"输入设备: {dev.get('name')}")
    print("开始识别，说话即可。按 Ctrl+C 结束。\n")

    line = Line()
    session = []                      # 完整录音
    tail = np.zeros(0, dtype=np.float32)   # 当前句累积
    sent_start = time.time()
    last_partial = 0.0
    seg_no = 0
    t0 = time.time()

    def flush_all():
        nonlocal seg_no
        vad.flush()
        if not vad.empty():
            while not vad.empty():
                vad.pop()
            if len(tail) > SAMPLE_RATE * 0.3:
                txt = recognize(rec, tail)
                if txt:
                    seg_no += 1
                    line.commit(txt, time.time() - sent_start)

    try:
        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=BLOCK,
            callback=callback,
        ):
            while True:
                block = audio_q.get()
                session.append(block)
                tail = np.concatenate([tail, block])

                vad.accept_waveform(block)

                # VAD 判定的一句话说完 → 定稿（识别用累积音频，避免被 VAD 裁掉开头）
                committed = False
                while not vad.empty():
                    vad.pop()
                    committed = True

                if committed:
                    txt = recognize(rec, tail)
                    if txt:
                        seg_no += 1
                        line.commit(txt, time.time() - sent_start)
                    tail = np.zeros(0, dtype=np.float32)
                    sent_start = time.time()
                    last_partial = 0.0
                    continue

                # 说话中：每 partial_every 秒刷新一次临时结果
                now = time.time()
                if len(tail) >= SAMPLE_RATE * 0.7 and now - last_partial >= partial_every:
                    last_partial = now
                    window = tail[-int(SAMPLE_RATE * PARTIAL_WINDOW) :]
                    txt = recognize(rec, window)
                    line.partial(txt, now - sent_start)

    except KeyboardInterrupt:
        line.clear()
        print("\n收尾中…")
        flush_all()
    finally:
        if output:
            save_wav(output, session)
            print(f"\n完整录音已保存: {output}")
        dur = len(session) * BLOCK / SAMPLE_RATE
        print(f"本次共 {seg_no} 句，录音时长约 {dur:.0f}s，"
              f"总耗时 {time.time() - t0:.0f}s")
    return 0


def save_wav(path: str, blocks: list[np.ndarray]):
    p = Path(os.path.expanduser(path))
    p.parent.mkdir(parents=True, exist_ok=True)
    data = np.concatenate(blocks) if blocks else np.zeros(0, dtype=np.float32)
    pcm = np.clip(data, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype(np.int16)
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm.tobytes())


# ---------------------------------------------------------------- 入口
def main():
    ap = argparse.ArgumentParser(description="本地实时语音识别（SenseVoice + silero VAD）")
    ap.add_argument("--file", help="改为识别一个音频文件（不占用麦克风）")
    ap.add_argument("--replay", action="store_true",
                    help="配合 --file：按真实时间回放，验证实时出字效果")
    ap.add_argument("--language", default="auto", help="auto/zh/en/ja/ko/yue，默认 auto")
    ap.add_argument("--threads", type=int, default=2, help="识别线程数，默认 2")
    ap.add_argument("--partial-every", type=float, default=0.5,
                    help="临时结果刷新间隔（秒），默认 0.5")
    ap.add_argument("--vad-threshold", type=float, default=0.45, help="VAD 灵敏度，默认 0.45")
    ap.add_argument("--min-silence", type=float, default=0.35, help="判定说完的静音时长，默认 0.35s")
    ap.add_argument("--max-speech", type=float, default=12.0,
                    help="单句最长时长，超时强制切句，默认 12s")
    ap.add_argument("--output", default=None, help="录音保存路径（默认自动带时间戳）")

    args = ap.parse_args()

    for f in [SV_DIR / "model.int8.onnx", SV_DIR / "tokens.txt", VAD_DIR / "silero_vad.onnx"]:
        if not f.exists():
            print(f"缺少模型文件: {f}")
            return 1

    t = time.time()
    rec = build_recognizer(num_threads=args.threads, language=args.language)
    vad = build_vad(threshold=args.vad_threshold, min_silence=args.min_silence,
                    max_speech=args.max_speech)
    print(f"模型加载完成，耗时 {time.time() - t:.2f}s\n")

    if args.file:
        if args.replay:
            run_replay(args.file, rec, vad, args.language, args.partial_every)
        else:
            run_file(args.file, rec, vad, args.language)
        return 0

    output = args.output
    if output is None:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        output = str(Path(__file__).parent / "recordings" / f"session-{stamp}.wav")
    return run_mic(rec, vad, args.language, output, args.partial_every)


if __name__ == "__main__":
    sys.exit(main())
