#!/usr/bin/env python3
"""
本地实时语音识别 · 桌面应用

双击打开 → 点「开始」→ 实时出字 → 点「停止」
识别全程在本机完成，音频不出设备。

界面用系统原生 WKWebView 渲染，识别逻辑在 asr_engine.py。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import webview

from asr_engine import AsrEngine, MODEL_ROOT, models_present, resource_root

FROZEN = getattr(sys, "frozen", False)

# ui/ 等只读资源：开发时在源码目录，打包后在 .app 包内
HERE = resource_root()

# 结果输出目录：.app 包内不可写，打包后落到用户文档目录
if FROZEN:
    DATA_DIR = Path(os.path.expanduser("~/Documents/实时语音转写记录"))
else:
    DATA_DIR = HERE / "data"   # 数据目录：识别结果与录音都落这里

DIAG_LOG = DATA_DIR / "启动诊断.log"


def _set_clipboard(text: str) -> None:
    """把文本放进系统剪贴板（跨平台，中文不乱码）。

    - macOS：pbcopy（读 UTF-8 stdin，中文没问题）
    - Windows：走 PowerShell 的 Set-Clipboard 并以 UTF-8 传 stdin。
      不要用 clip.exe —— 它按 OEM 代码页解码，中文路径会变成乱码。
    - 其他：有 xclip/xsel 就用，最后兜底 tkinter。
    """
    if sys.platform == "darwin":
        subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)
        return
    if sys.platform == "win32":
        ps = ("[Console]::InputEncoding=[Text.Encoding]::UTF8;"
              "$t=[Console]::In.ReadToEnd(); Set-Clipboard -Value $t.TrimEnd()")
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            input=text.encode("utf-8"),
            check=True,
        )
        return
    for cmd in (["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]):
        try:
            subprocess.run(cmd, input=text.encode("utf-8"), check=True)
            return
        except Exception:
            continue
    import tkinter
    r = tkinter.Tk()
    r.withdraw()
    r.clipboard_clear()
    r.clipboard_append(text)
    r.update()
    r.destroy()


def write_startup_diag():
    """每次启动记一行诊断：模型从哪读、资源是否齐全。

    应用在别人机器上跑不起来时，把 DATA_DIR/启动诊断.log 发回来就能定位。
    写失败不影响使用，直接忽略。
    """
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        import asr_engine
        rows = [
            f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} ---",
            f"打包运行 = {FROZEN}",
            f"程序位置 = {sys.executable}",
            f"资源目录 = {HERE}",
            f"界面文件 = {(HERE / 'ui' / 'index.html').exists()}",
            f"模型目录 = {MODEL_ROOT}",
            f"模型齐全 = {models_present()}",
            f"sherpa   = {(asr_engine.SV_DIR / 'model.int8.onnx').exists()}",
            f"vad      = {(asr_engine.VAD_DIR / 'silero_vad.onnx').exists()}",
        ]
        # 音频库自检。sounddevice 在引擎里是延迟导入的，
        # 「界面能打开」并不代表音频库可用，所以这里单独验一次。
        try:
            import sounddevice as sd
            ins = [d for d in sd.query_devices() if d["max_input_channels"] > 0]
            rows.append(f"音频库   = OK（PortAudio {sd.get_portaudio_version()[1]}，输入设备 {len(ins)} 个）")
        except Exception as exc:
            rows.append(f"音频库   = 失败 {type(exc).__name__}: {exc}")
        with open(DIAG_LOG, "a", encoding="utf-8") as fh:
            fh.write("\n".join(rows) + "\n")
    except Exception:
        pass


class Api:
    """暴露给前端的接口。前端通过 window.pywebview.api.<方法>() 调用。"""

    def __init__(self):
        self.window = None
        self.engine: AsrEngine | None = None
        self.finals: list[str] = []
        self.started_at: float | None = None

    # ---------------------------------------------------------- 与前端通信
    def _emit(self, kind: str, payload: dict):
        if self.window is None:
            return
        try:
            data = json.dumps(payload, ensure_ascii=False)
            self.window.evaluate_js(f"window.onAsr && window.onAsr('{kind}', {data})")
        except Exception:
            pass  # 窗口已关闭

    def _on_partial(self, text: str, elapsed: float):
        self._emit("partial", {"text": text, "elapsed": round(elapsed, 1)})

    def _on_final(self, text: str, elapsed: float):
        self.finals.append(text)
        self._emit(
            "final",
            {
                "text": text,
                "elapsed": round(elapsed, 1),
                "index": len(self.finals),
                "clock": time.strftime("%H:%M:%S"),
            },
        )

    def _on_status(self, state: str, info: str):
        self._emit("status", {"state": state, "info": info})

    def _ensure_engine(self):
        if self.engine is None:
            self.engine = AsrEngine(
                on_partial=self._on_partial,
                on_final=self._on_final,
                on_status=self._on_status,
                data_dir=str(DATA_DIR),
            )

    # ---------------------------------------------------------- 前端可调用的方法
    def boot(self):
        """界面加载完成后调用：把模型预热好，避免点开始时卡顿。"""
        if not models_present():
            self._emit("status", {"state": "error",
                                  "info": f"缺少模型文件，请检查 {MODEL_ROOT}"})
            return {"ok": False}
        self._ensure_engine()
        threading.Thread(target=self.engine.load, daemon=True).start()
        return {"ok": True, "data_dir": str(DATA_DIR)}

    def start(self):
        self._ensure_engine()
        self.finals.clear()
        self.started_at = time.time()
        ok = self.engine.start()
        if not ok:
            self._emit("status", {"state": "error", "info": "启动失败，请查看模型与麦克风"})
        return {"ok": ok}

    def stop(self, keep_audio=False):
        """结束本轮。keep_audio=True 时保留录音（自动压缩成 m4a）。"""
        if self.engine:
            self.engine.stop(keep_audio=bool(keep_audio))
        return {"ok": True}

    def pause(self):
        """暂停识别并释放麦克风，本轮不结束（结果继续写同一个文件）。"""
        if self.engine:
            self.engine.pause()
        return {"ok": True}

    def resume(self):
        if self.engine:
            self.engine.resume()
        return {"ok": True}

    def save_transcript(self):
        """把本轮定稿内容另存一份（自动落盘的结果文件已经实时存在数据目录里）。"""
        if not self.finals:
            return {"ok": False, "msg": "还没有内容可以保存"}
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        path = DATA_DIR / f"整理稿-{time.strftime('%Y%m%d-%H%M%S')}.txt"
        path.write_text("\n".join(self.finals), encoding="utf-8")
        return {"ok": True, "path": str(path)}

    def copy_path(self):
        """把本轮结果文件的完整路径放进剪贴板。

        识别出来的口语原文基本没法直接用（没有分段、没有删冗余、错别字也在），
        所以这里复制的是「路径」——粘给大模型做洗稿 / 会议纪要，
        或者喂给脚本，比复制一堆正文更有用。
        """
        path = str(self.engine.session_file) if (self.engine and self.engine.session_file) else ""
        if not path:
            return {"ok": False, "msg": "还没开始转录，没有结果文件"}
        if not Path(path).exists():
            return {"ok": False, "msg": f"结果文件不存在：{path}", "path": path}
        try:
            _set_clipboard(path)
            return {"ok": True, "path": path}
        except Exception as e:
            return {"ok": False, "msg": f"复制失败：{e}", "path": path}

    def copy_all(self):
        """把本轮定稿内容放进系统剪贴板（界面上已不再用按钮暴露，留作接口）。"""
        text = "\n".join(self.finals)
        if not text:
            return {"ok": False, "msg": "还没有内容可以复制"}
        try:
            _set_clipboard(text)
            return {"ok": True, "count": len(self.finals)}
        except Exception as e:
            return {"ok": False, "msg": f"复制失败：{e}"}

    def open_data_dir(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(str(DATA_DIR))            # noqa: S606  Windows 专用
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(DATA_DIR)])
        else:
            subprocess.Popen(["xdg-open", str(DATA_DIR)])
        return {"ok": True}

    def current_file(self):
        """当前这一轮的结果文件（前端用来显示「结果存到哪了」）。"""
        if self.engine and self.engine.session_file:
            return {"ok": True, "path": str(self.engine.session_file)}
        return {"ok": False, "path": ""}

    def quit_app(self):
        if self.engine:
            self.engine.stop()
        if self.window:
            self.window.destroy()
        return {"ok": True}


def main():
    write_startup_diag()
    api = Api()
    window = webview.create_window(
        "实时语音转写",
        str(HERE / "ui" / "index.html"),
        js_api=api,
        width=960,
        height=700,
        min_size=(700, 520),
        background_color="#F6F6F4",
    )
    api.window = window

    def on_closing():
        # 关窗口前给引擎最多 2 秒收尾，把还没定稿的音频落盘
        if api.engine:
            api.engine.stop()
            api.engine.wait(timeout=2)

    window.events.closing += on_closing
    webview.start()


if __name__ == "__main__":
    main()
