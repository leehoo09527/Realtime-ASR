#!/usr/bin/env python3
"""验证暂停/继续：不该丢内容，且结果文件里要留下「暂停」「继续」标记。"""
import shutil
import time
from pathlib import Path

from asr_engine import AsrEngine

D = Path("/tmp/asr-pause-test")
shutil.rmtree(D, ignore_errors=True)

states: list[str] = []
e = AsrEngine(data_dir=str(D), on_status=lambda s, i: states.append(s))

print("① 开始识别…")
e.start()
time.sleep(4)

print("② 暂停…")
e.pause()
time.sleep(1.5)
print("   paused =", e.paused, "｜线程仍在 =", e.running)
f = e.session_file
print("   暂停瞬间文件内容：")
print("   ", f.read_text(encoding="utf-8").replace("\n", "\n    "))

time.sleep(2)
print("③ 继续…")
e.resume()
time.sleep(3)

print("④ 停止…")
e.stop()
e.wait(6)

print()
print("=== 最终文件内容 ===")
print(f.read_text(encoding="utf-8"))
print("=== 状态序列 ===")
print(" → ".join(states))
