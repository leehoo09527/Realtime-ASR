# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（Windows）。

用法（必须在**包根目录**执行，datas 的相对路径是相对本文件所在目录）：

    pyinstaller --noconfirm --distpath dist_win --workpath build_win asr-win.spec

要点：
- datas 里的 'src/ui' 和 'models' 都相对包根目录；
- hiddenimports 必须显式加 webview.platforms.winforms —— 它是动态导入的，collect_all 抓不到；
- 用 onedir（EXE + COLLECT）而不是 onefile：模型 228MB，onefile 每次启动都要解压到 temp，
  冷启动会多等好几秒，还容易踩中文临时目录的坑；
- 每次构建换一个全新的 dist_win / build_win 目录名，旧产物删不掉时会出现
  「构建成功但产物还是旧的」。
"""
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = []

# 界面 + 模型（模型不入本交接包，需先按 README.md 放进 models/）
datas += [('src/ui', 'ui')]
datas += [('models', 'models')]

for pkg in ('sherpa_onnx', 'sounddevice', 'webview'):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

hiddenimports += ['webview.platforms.winforms']

a = Analysis(
    ['src/app.py'],
    pathex=['src'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=['tkinter.test', 'test', 'unittest', 'pydoc_data'],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='实时语音转写',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # 正式包不弹黑框；第一次调试可以临时改 True
    disable_windowed_traceback=False,
    icon='tools/AppIcon.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='实时语音转写',
)
