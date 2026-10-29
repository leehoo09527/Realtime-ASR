#!/usr/bin/env python3
"""验证「逐句实时落盘」——重点是进程被强杀（来不及 close）时内容还在不在。"""
import shutil
import time
from pathlib import Path

from asr_engine import AsrEngine

D = Path("/tmp/asr-test-data")
shutil.rmtree(D, ignore_errors=True)

print("=== 场景 1：正常开始 → 说两句 → 停止 ===")
e = AsrEngine(data_dir=str(D))
p = e._open_session_file()
print("会话文件:", p)
e._write_line("大家好，今天我们开个短会。")
e._write_line("第一，本周的进度要抓紧。")
e._close_session_file("结束")
print(p.read_text(encoding="utf-8"))

time.sleep(1.1)  # 让下一轮的文件名不同秒

print("=== 场景 2：说了话但没点停止，窗口直接被强杀 ===")
e2 = AsrEngine(data_dir=str(D))
p2 = e2._open_session_file()
e2._write_line("这句是在被强杀之前说出来的")
# 故意不调用 _close_session_file，模拟进程被 kill
content = p2.read_text(encoding="utf-8")
print(content)
assert "被强杀之前" in content, "内容没有落盘！"
print("→ 未关闭文件也已落盘，内容没丢 ✓")

print()
print("=== 数据目录结构 ===")
for f in sorted(D.rglob("*")):
    print(" ", f.relative_to(D), f"({f.stat().st_size} bytes)" if f.is_file() else "")
