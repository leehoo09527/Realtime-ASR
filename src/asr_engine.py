#!/usr/bin/env python3
"""
语音识别引擎：把「麦克风采集 + VAD 断句 + SenseVoice 识别」封装成可复用的类。

界面层只管调用 start() / stop()，并通过回调拿结果，不碰识别细节。

回调协议：
    on_partial(text, elapsed)   一句话还没说完时的临时结果（会反复刷新）
    on_final(text, elapsed)     一句话定稿（不会再变）
    on_status(state, info)      状态变化：loading / ready / running / stopped / error
"""
from __future__ import annotations

import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path
from typing import Callable

import numpy as np
import sherpa_onnx

SAMPLE_RATE = 16000
BLOCK = 512                 # silero VAD 窗口 @16kHz = 32ms
MAX_PARTIAL_SEC = 60.0      # 临时结果识别时的音频上限（兜底）
FORCE_SPLIT_SEC = 15.0      # 应用层强制切句：单句超过此长度就定稿，防止无限增长拖死循环
BACKLOG_LIMIT = 20          # 音频队列积压块数上限（约 0.64 秒），超过就跳过临时刷新优先保音频


def _candidate_roots() -> list[Path]:
    """资源可能落在的根目录，按优先级排列。

    PyInstaller 的 macOS .app 里，datas 实际被放进 Contents/Resources，
    而 sys._MEIPASS 通常指向 Contents/Frameworks —— 两者都得试，
    否则会出现「包里有模型却读不到」的怪现象。
    """
    roots: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(Path(meipass))
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable).resolve()
        roots += [
            exe.parent,                        # .app/Contents/MacOS
            exe.parent.parent / "Resources",   # .app/Contents/Resources ← 实际位置
            exe.parent.parent / "Frameworks",  # .app/Contents/Frameworks
            exe.parent.parent,                 # .app/Contents
        ]
    roots.append(Path(__file__).resolve().parent)   # 开发模式：源码目录

    seen: set[Path] = set()
    out: list[Path] = []
    for r in roots:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def _find_dir(rel: str) -> Path | None:
    """在候选根目录里找第一个存在的子目录。"""
    for root in _candidate_roots():
        p = root / rel
        if p.is_dir():
            return p
    return None


def _resolve_model_root() -> Path:
    """模型目录。

    - 环境变量 ASR_MODEL_ROOT：显式指定（交接包 / 自定义部署用）
    - 打包后：.app 包内随附的 models/（开箱即用，无需联网下载）
    - 开发时：~/.cache/workbuddy-asr
    """
    env = os.environ.get("ASR_MODEL_ROOT")
    if env and (Path(env) / "sense-voice").is_dir():
        return Path(env)
    bundled = _find_dir("models/sense-voice")
    if bundled:
        return bundled.parent
    return Path(os.path.expanduser("~/.cache/workbuddy-asr"))


def resource_root() -> Path:
    """只读资源（ui/ 等）所在目录。"""
    ui = _find_dir("ui")
    return ui.parent if ui else Path(__file__).resolve().parent


MODEL_ROOT = _resolve_model_root()
SV_DIR = MODEL_ROOT / "sense-voice"
VAD_DIR = MODEL_ROOT / "silero-vad"


def models_present() -> bool:
    return all(
        f.exists()
        for f in [
            SV_DIR / "model.int8.onnx",
            SV_DIR / "tokens.txt",
            VAD_DIR / "silero_vad.onnx",
        ]
    )


def onnx_path(p: Path) -> str:
    """把模型路径转成 ONNX Runtime 能可靠打开的字符串。

    Windows 上 sherpa-onnx 的 C++ 层按窄字符打开文件，路径里有中文时
    （用户名是中文，或者程序装在中文目录下）可能直接报「模型文件不存在」。
    遇到非 ASCII 路径就退到 Windows 8.3 短路径（纯 ASCII），是目前最省事的解法。
    其他平台原样返回。
    """
    s = str(p)
    if sys.platform != "win32":
        return s
    try:
        s.encode("ascii")
        return s
    except UnicodeEncodeError:
        pass
    try:
        import ctypes
        from ctypes import wintypes

        fn = ctypes.windll.kernel32.GetShortPathNameW
        fn.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        fn.restype = wintypes.DWORD
        buf = ctypes.create_unicode_buffer(1024)
        n = fn(s, buf, 1024)
        if n and n < 1024:
            return buf.value
    except Exception:
        pass
    return s


class AsrEngine:
    def __init__(
        self,
        on_partial: Callable[[str, float], None] | None = None,
        on_final: Callable[[str, float], None] | None = None,
        on_status: Callable[[str, str], None] | None = None,
        language: str = "auto",
        threads: int = 4,
        vad_threshold: float = 0.45,
        min_silence: float = 0.35,
        max_speech: float = 12.0,
        partial_every: float = 0.5,
        force_split: float = FORCE_SPLIT_SEC,
        data_dir: str | None = None,
    ):
        self.on_partial = on_partial or (lambda t, e: None)
        self.on_final = on_final or (lambda t, e: None)
        self.on_status = on_status or (lambda s, i: None)

        self.language = language
        self.threads = threads
        self.vad_threshold = vad_threshold
        self.min_silence = min_silence
        self.max_speech = max_speech
        self.partial_every = partial_every
        self.force_split = force_split
        if data_dir:
            self.data_dir = Path(data_dir)
        elif getattr(sys, "frozen", False):
            # 打包后包内不可写，落到文档目录
            self.data_dir = Path(os.path.expanduser("~/Documents/实时语音转写记录"))
        else:
            self.data_dir = Path(__file__).parent / "data"

        self._rec = None
        self._vad = None
        self._thread: threading.Thread | None = None
        self._stop_evt = threading.Event()
        self._pause_evt = threading.Event()
        self._lock = threading.Lock()
        self.session_count = 0
        self.last_wav: str | None = None
        self.session_file: Path | None = None   # 本轮结果文件（txt）
        self._fh = None                          # 打开的文件句柄，逐句实时写
        self._keep_audio = False                 # 结束时是否保留录音（默认不留）

    # ------------------------------------------------------------ 模型
    def load(self) -> bool:
        """加载模型。可以提前调用，避免点「开始」时卡一下。"""
        if self._rec is not None:
            return True
        if not models_present():
            self.on_status("error", f"缺少模型文件，请检查 {MODEL_ROOT}")
            return False

        self.on_status("loading", "正在加载模型…")
        t0 = time.time()
        self._rec = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=onnx_path(SV_DIR / "model.int8.onnx"),
            tokens=onnx_path(SV_DIR / "tokens.txt"),
            num_threads=self.threads,
            use_itn=True,               # 自动加标点
            language=self.language,
            debug=False,
        )
        self._vad = self._build_vad()
        self.on_status("ready", f"模型就绪（{time.time() - t0:.1f}s）")
        return True

    def _build_vad(self):
        cfg = sherpa_onnx.VadModelConfig()
        cfg.silero_vad.model = onnx_path(VAD_DIR / "silero_vad.onnx")
        cfg.silero_vad.threshold = self.vad_threshold
        cfg.silero_vad.min_silence_duration = self.min_silence
        cfg.silero_vad.min_speech_duration = 0.20
        cfg.silero_vad.max_speech_duration = self.max_speech
        cfg.sample_rate = SAMPLE_RATE
        return sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=60)

    def _recognize(self, samples: np.ndarray) -> str:
        if samples is None or len(samples) == 0:
            return ""
        stream = self._rec.create_stream()
        stream.accept_waveform(SAMPLE_RATE, samples.astype(np.float32))
        self._rec.decode_stream(stream)
        return stream.result.text.strip()

    # ------------------------------------------------------------ 落盘
    def _open_session_file(self) -> Path:
        """
        每轮开始按时间建一个结果文件：<数据目录>/<年-月-日>/<时-分-秒>.txt
        逐句实时追加，窗口被误关闭也只丢没说完的半句。
        """
        now = time.localtime()
        folder = self.data_dir / time.strftime("%Y-%m-%d", now)
        folder.mkdir(parents=True, exist_ok=True)
        self.session_file = folder / f"{time.strftime('%H-%M-%S', now)}.txt"
        self._fh = open(self.session_file, "a", encoding="utf-8")
        self._write_line(f"# 实时语音转写  {time.strftime('%Y-%m-%d %H:%M:%S', now)}")
        return self.session_file

    def _write_line(self, line: str):
        """写入后立即 flush + fsync——即使进程被强杀，已定稿的内容也不会丢。"""
        if self._fh is None:
            return
        try:
            self._fh.write(line + "\n")
            self._fh.flush()
            os.fsync(self._fh.fileno())
        except Exception:
            pass

    def _close_session_file(self, reason: str = ""):
        if self._fh is None:
            return
        if reason:
            try:
                self._fh.write(f"# {reason}  {time.strftime('%H:%M:%S')}\n")
                self._fh.flush()
                os.fsync(self._fh.fileno())
            except Exception:
                pass
        try:
            self._fh.close()
        except Exception:
            pass
        self._fh = None

    # ------------------------------------------------------------ 生命周期
    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def paused(self) -> bool:
        return self._pause_evt.is_set()

    def pause(self):
        """暂停识别并释放麦克风。手头还没定稿的半句会先落盘，不会丢。"""
        self._pause_evt.set()

    def resume(self):
        self._pause_evt.clear()

    def start(self) -> bool:
        with self._lock:
            if self.running:
                return True
            if not self.load():
                return False
            self._stop_evt.clear()
            self._pause_evt.clear()
            self._keep_audio = False
            self.last_wav = None
            self._open_session_file()
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
            return True

    def stop(self, keep_audio: bool = False):
        """结束本轮。只有 keep_audio=True 才保留录音（默认不留，省空间）。"""
        self._keep_audio = bool(keep_audio)
        self._stop_evt.set()

    def wait(self, timeout: float | None = None):
        if self._thread:
            self._thread.join(timeout)

    # ------------------------------------------------------------ 主循环
    def _loop(self):
        try:
            import sounddevice as sd
        except Exception as e:
            self.on_status("error", f"缺少录音依赖: {e}")
            return

        audio_q: queue.Queue = queue.Queue()

        def callback(indata, frames, time_info, status):
            audio_q.put(indata[:, 0].copy())

        session: list[np.ndarray] = []
        tail = np.zeros(0, dtype=np.float32)
        sent_start = time.time()
        last_partial = 0.0
        final_count = 0

        def commit_tail():
            """把当前还没定稿的音频定稿（暂停 / 停止收尾用）。"""
            nonlocal tail, final_count, sent_start, last_partial
            if len(tail) > SAMPLE_RATE * 0.3:
                text = self._recognize(tail)
                if text:
                    final_count += 1
                    self._write_line(text)
                    self.on_final(text, time.time() - sent_start)
            tail = np.zeros(0, dtype=np.float32)
            sent_start = time.time()
            last_partial = 0.0

        try:
            self.session_count += 1
            fname = self.session_file.name if self.session_file else ""
            self.on_status("running", f"正在聆听 · {fname}")

            while not self._stop_evt.is_set():
                # ---- 暂停：手头这句先定稿，释放麦克风，等恢复 ----
                if self._pause_evt.is_set():
                    commit_tail()
                    self._write_line(f"# 暂停 {time.strftime('%H:%M:%S')}")
                    self.on_status("paused", "已暂停（麦克风已释放）")
                    while self._pause_evt.is_set() and not self._stop_evt.is_set():
                        time.sleep(0.1)
                    if self._stop_evt.is_set():
                        break
                    self._write_line(f"# 继续 {time.strftime('%H:%M:%S')}")
                    self.on_status("running", f"正在聆听 · {fname}")
                    continue

                # ---- 正常聆听 ----
                with sd.InputStream(
                    samplerate=SAMPLE_RATE,
                    channels=1,
                    dtype="float32",
                    blocksize=BLOCK,
                    callback=callback,
                ):
                    # 丢掉暂停期间积压的音频，避免前后接不上
                    while True:
                        try:
                            audio_q.get_nowait()
                        except queue.Empty:
                            break

                    while not self._stop_evt.is_set() and not self._pause_evt.is_set():
                        try:
                            block = audio_q.get(timeout=0.2)
                        except queue.Empty:
                            continue

                        session.append(block)
                        tail = np.concatenate([tail, block])
                        self._vad.accept_waveform(block)

                        # VAD 判定的「说完了」→ 定稿。
                        # 识别统一用自上次定稿以来的累积音频，避免被 VAD 裁掉开头、也避免重复。
                        committed = False
                        while not self._vad.empty():
                            self._vad.pop()
                            committed = True

                        if committed or len(tail) >= SAMPLE_RATE * self.force_split:
                            # 应用层兜底：VAD 在噪音 / 连续说话时可能迟迟不切句，
                            # 单句无限增长会让识别耗时线性上升，最终把整条循环拖死。
                            commit_tail()
                            continue

                        now = time.time()
                        # 积压时优先保证音频完整：跳过临时刷新，把算力让给定稿
                        if (audio_q.qsize() < BACKLOG_LIMIT
                                and len(tail) >= SAMPLE_RATE * 0.7
                                and now - last_partial >= self.partial_every):
                            last_partial = now
                            # 用整段累积音频识别，长句开头也看得见；上限只作兜底
                            seg = tail[-int(SAMPLE_RATE * MAX_PARTIAL_SEC):]
                            text = self._recognize(seg)
                            if text:
                                self.on_partial(text, now - sent_start)

        except Exception as e:  # 麦克风被占用、权限被拒等
            self.on_status("error", f"录音失败: {e}")
            return
        finally:
            # 收尾：把还没定稿的音频啃完
            try:
                self._vad.flush()
                if not self._vad.empty():
                    while not self._vad.empty():
                        self._vad.pop()
                    text = self._recognize(tail)
                    if text:
                        final_count += 1
                        self._write_line(text)
                        self.on_final(text, time.time() - sent_start)
                    tail = np.zeros(0, dtype=np.float32)
                if len(tail) > SAMPLE_RATE * 0.3:
                    text = self._recognize(tail)
                    if text:
                        final_count += 1
                        self._write_line(text)
                        self.on_final(text, time.time() - sent_start)
            except Exception:
                pass

            if session and self._keep_audio:
                self.last_wav = self._save_audio(session)
            self._close_session_file("结束")
            if self.last_wav:
                self.on_status(
                    "stopped",
                    f"已结束（{final_count} 句）· 音频已存 {Path(self.last_wav).name}",
                )
            else:
                self.on_status("stopped", f"已结束（本轮 {final_count} 句）")

    def _save_audio(self, blocks: list[np.ndarray]) -> str | None:
        """
        保存本轮录音。先落 wav，再用系统自带的 afconvert 压成 AAC(m4a)：
            原始 wav ≈ 110 MB/小时 → AAC 64kbps ≈ 28 MB/小时（约 1/4）
        压缩失败就保留 wav，绝不因为转码问题把录音弄丢。
        """
        if self.session_file is not None:
            base = self.session_file.with_suffix("")
        else:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            base = self.data_dir / f"session-{time.strftime('%Y%m%d-%H%M%S')}"

        wav_path = base.with_suffix(".wav")
        data = np.concatenate(blocks) if blocks else np.zeros(0, dtype=np.float32)
        pcm = (np.clip(data, -1.0, 1.0) * 32767.0).astype(np.int16)
        with wave.open(str(wav_path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(pcm.tobytes())

        m4a_path = base.with_suffix(".m4a")
        try:
            if self._compress_audio(wav_path, m4a_path):
                try:
                    wav_path.unlink()
                except Exception:
                    pass
                return str(m4a_path)
        except Exception:
            pass
        return str(wav_path)

    @staticmethod
    def _compress_audio(wav_path: Path, m4a_path: Path) -> bool:
        """把 wav 压成 AAC(m4a)：原始 wav ≈ 110 MB/小时 → AAC 64kbps ≈ 28 MB/小时。

        各平台用的工具不同（macOS 自带 afconvert，Windows 一般是 ffmpeg，
        Linux 一般也是 ffmpeg）。任何一个环节缺工具/失败都返回 False，
        由调用方保留 wav —— 绝不因为转码问题把录音弄丢。
        """
        if sys.platform == "darwin":
            if not shutil.which("afconvert"):
                return False
            cmd = ["afconvert", "-f", "m4af", "-d", "aac@22050", "-b", "64000",
                   "-c", "1", str(wav_path), str(m4a_path)]
        else:
            ff = shutil.which("ffmpeg")
            if not ff:
                return False
            cmd = [ff, "-hide_banner", "-loglevel", "error", "-y",
                   "-i", str(wav_path), "-c:a", "aac", "-b:a", "64k",
                   "-ac", "1", str(m4a_path)]

        try:
            r = subprocess.run(cmd, capture_output=True, timeout=600)
        except Exception:
            return False
        return r.returncode == 0 and m4a_path.exists() and m4a_path.stat().st_size > 0
