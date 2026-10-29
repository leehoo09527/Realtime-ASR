#!/usr/bin/env python3
"""
界面自测：往窗口里注入几条识别结果，检查字幕区渲染效果。
不占用麦克风，纯 UI 验证。
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import webview

from app import Api

HERE = Path(__file__).parent
AUTO = "--auto" in sys.argv   # 自动开始真实识别，用于验证落盘与界面联动

SAMPLE = [
    ("大家好，今天我们开个短会。", 3.2),
    ("第一，本周的进度要抓紧，周五之前把方案定下来。", 7.5),
    ("第二，预算这块还需要再核对，尤其是服务器采购那部分。", 12.1),
]


class DemoApi(Api):
    def boot(self):
        result = super().boot()
        target = self._auto if AUTO else self._demo
        threading.Thread(target=target, daemon=True).start()
        return result

    def _auto(self):
        time.sleep(3)
        self.start()          # 真实开始识别
        time.sleep(6)
        self.pause()          # 进入暂停态，便于截图确认按钮与状态
        time.sleep(4)
        f = self.engine.session_file if self.engine else None
        print(f"[自测] 会话文件 = {f}", flush=True)
        print(f"[自测] paused = {self.engine.paused if self.engine else None}", flush=True)
        if f and f.exists():
            print("[自测] 暂停时文件内容：", flush=True)
            print(f.read_text(encoding="utf-8"), flush=True)

    def _demo(self):
        time.sleep(4)  # 等模型加载完、界面就绪
        for text, elapsed in SAMPLE:
            self._on_final(text, elapsed)
            time.sleep(0.7)
        # 模拟一次识别逐步变长：前面的字会稳定下来（正常色），尾部持续变化（蓝底）
        pieces = [
            "有个具出",
            "有个具出借话权限",
            "有个具出借话权限，还有这个",
            "有个具出借话权限，还有这个呃",
            "有个具出借话权限，还有这个呃其他的有。",
        ]
        for piece in pieces:
            self._on_partial(piece, 15.3)
            time.sleep(0.55)
        n = self.window.evaluate_js("document.querySelectorAll('.pending').length")
        m = self.window.evaluate_js("document.querySelectorAll('.unstable').length")
        print(f"[自测] 定稿行数={len(SAMPLE)}，临时行={n}，非稳态片段={m}", flush=True)

        # 查出实际生效的 user-select 值，确认 CSS 真的落到了元素上
        diag = self.window.evaluate_js("""
            (function () {
                var el = document.querySelector('.line .txt');
                if (!el) return 'no-element';
                var cs = getComputedStyle(el);
                return JSON.stringify({
                    std: cs.getPropertyValue('user-select'),
                    webkit: cs.getPropertyValue('-webkit-user-select'),
                    bodyStd: getComputedStyle(document.body).getPropertyValue('user-select')
                });
            })()
        """)
        print(f"[自测] 计算样式：{diag}", flush=True)

        # 直接试选中第一行文字，看能不能拿到选区（最实在的验证）
        picked = self.window.evaluate_js("""
            (function () {
                var el = document.querySelector('.line .txt');
                if (!el) return 'no-element';
                var r = document.createRange();
                r.selectNodeContents(el);
                var s = window.getSelection();
                s.removeAllRanges();
                s.addRange(r);
                return s.toString().length + '|' + s.toString().slice(0, 12);
            })()
        """)
        print(f"[自测] 选中第一行 → {picked}", flush=True)
        # 按钮应当选不中
        btnpick = self.window.evaluate_js("""
            (function () {
                var el = document.querySelector('.btn');
                var r = document.createRange();
                r.selectNodeContents(el);
                var s = window.getSelection();
                s.removeAllRanges();
                s.addRange(r);
                return s.toString().length;
            })()
        """)
        print(f"[自测] 选中按钮文字长度 = {btnpick}（期望 0）", flush=True)


def main():
    api = DemoApi()
    window = webview.create_window(
        "实时语音转写",
        str(HERE / "ui" / "index.html"),
        js_api=api,
        x=140,
        y=120,
        width=920,
        height=700,
        background_color="#F6F6F4",
    )
    api.window = window
    webview.start()


if __name__ == "__main__":
    main()
