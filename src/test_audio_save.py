#!/usr/bin/env python3
"""验证两个结束路径：「结束」不留音频，「保存音频并结束」存压缩后的 m4a。"""
import shutil
import time
from pathlib import Path

from asr_engine import AsrEngine

D = Path("/tmp/asr-audio-test")
shutil.rmtree(D, ignore_errors=True)

print("=== 场景 1：保存音频并结束 ===")
e = AsrEngine(data_dir=str(D))
e.start()
time.sleep(6)
e.stop(keep_audio=True)
e.wait(30)
print("  录音文件:", e.last_wav)
if e.last_wav:
    p = Path(e.last_wav)
    print(f"  格式 {p.suffix}｜体积 {p.stat().st_size} bytes")

print()
print("=== 场景 2：直接结束（不留音频）===")
e2 = AsrEngine(data_dir=str(D))
e2.start()
time.sleep(5)
e2.stop(keep_audio=False)
e2.wait(12)
print("  录音文件:", e2.last_wav, "（None = 未保存，符合默认行为）")

print()
print("=== 数据目录实际内容 ===")
for f in sorted(D.rglob("*")):
    if f.is_file():
        print(f"  {f.name}  {f.stat().st_size} bytes")
