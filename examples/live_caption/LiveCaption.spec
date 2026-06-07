# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — 라이브 자막(live_caption) 단독 exe 빌드.

funasr/torch/streaming-sensevoice 등 동적 의존성이 많아 collect_all 로 데이터·서브모듈을
최대한 끌어모은다. 콘솔 앱(시작 시 장치/언어를 input() 으로 선택)이라 console=True.
모델은 실행 시 다운로드되므로 번들에 포함하지 않는다(최초 실행에 인터넷 필요).
"""

import os
import sys

from PyInstaller.utils.hooks import collect_all

# clone 된 streaming-sensevoice 를 빌드 시 import 경로에 추가
_SSV = os.path.abspath(os.path.join("third_party_ssv"))
if _SSV not in sys.path:
    sys.path.insert(0, _SSV)

datas, binaries, hiddenimports = [], [], []
for pkg in [
    "funasr",
    "torchaudio",
    "hydra",
    "omegaconf",
    "streaming_sensevoice",
    "asr_decoder",
    "online_fbank",
    "modelscope",
    "jaconv",
    "jamo",
    "jieba",
    "kaldiio",
    "torch_complex",
]:
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception as e:  # 빌드 환경에 없는 선택 패키지는 건너뜀
        print(f"[spec] collect_all skip {pkg}: {e}")

a = Analysis(
    ["live_caption.py"],
    pathex=[_SSV],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LiveCaption",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # 시작 메뉴(장치/언어 선택)용 콘솔 필요
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="LiveCaption",
)
