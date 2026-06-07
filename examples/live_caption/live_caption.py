"""한국어 라이브 자막.

마이크 또는 시스템 소리를 받아 한국어 음성을 인식하고, 화면 위에 항상 떠 있는
반투명 자막으로 실시간 표시한다.

실행:
    python live_caption.py

흐름:
    1) GPU/CPU 선택  2) 입력 장치 선택  3) 언어 선택
    4) 모델 로딩  5) 오버레이 자막 표시 (ESC 로 종료)
"""

import sys
import threading

import audio_source
from asr_worker import AsrWorker


def _ask(prompt, options, default_idx=0):
    """번호 메뉴로 옵션 하나를 고르게 한다. options: list[(label, value)]."""
    print(prompt)
    for i, (label, _) in enumerate(options):
        mark = " (기본값)" if i == default_idx else ""
        print(f"  [{i}] {label}{mark}")
    while True:
        raw = input(f"선택 (Enter={default_idx}): ").strip()
        if raw == "":
            return options[default_idx][1]
        if raw.isdigit() and 0 <= int(raw) < len(options):
            return options[int(raw)][1]
        print("  잘못된 입력입니다. 다시 선택하세요.")


def choose_device_id():
    """GPU 사용 가능 여부를 확인하고 device_id 를 고른다 (-1=CPU, 0=GPU)."""
    gpu = False
    try:
        import torch

        gpu = torch.cuda.is_available()
    except Exception:
        pass

    if not gpu:
        print("GPU(CUDA) 미감지 → CPU 로 실행합니다.")
        return "-1"

    choice = _ask(
        "\n실행 장치를 선택하세요:",
        [("GPU (CUDA, 빠름)", "0"), ("CPU", "-1")],
        default_idx=0,
    )
    return choice


def choose_audio_device():
    devices = audio_source.list_devices()
    if not devices:
        print("사용 가능한 입력 장치를 찾지 못했습니다.")
        sys.exit(1)

    options = []
    default_idx = 0
    default_lb = audio_source.default_loopback_index()
    for d in devices:
        icon = "🔊 시스템 소리" if d["kind"] == "loopback" else "🎤 마이크"
        name = d["name"] or "(이름 표시 불가)"
        label = f"{icon} | {name} | {d['channels']}ch {d['rate']}Hz"
        options.append((label, d["index"]))
        if default_lb is not None and d["index"] == default_lb:
            default_idx = len(options) - 1

    return _ask("\n자막으로 만들 소리를 선택하세요:", options, default_idx=default_idx)


def choose_language():
    return _ask(
        "\n인식 언어를 선택하세요:",
        [
            ("한국어", "ko"),
            ("자동 감지", "auto"),
            ("영어", "en"),
            ("일본어", "ja"),
        ],
        default_idx=0,
    )


def main():
    print("=" * 50)
    print(" 한국어 라이브 자막")
    print("=" * 50)

    device_id = choose_device_id()
    audio_index = choose_audio_device()
    language = choose_language()

    # Qt 는 콘솔 선택이 끝난 뒤 임포트 (창이 먼저 뜨는 것 방지)
    from PyQt6 import QtCore, QtGui, QtWidgets
    from overlay import Bridge, CaptionOverlay, SettingsDialog, make_tray_icon
    from caption_log import CaptionLogger, default_folder

    app = QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # 트레이로 상주
    settings = QtCore.QSettings("FunASR", "LiveCaption")

    bridge = Bridge()
    overlay = CaptionOverlay(bridge, settings)
    overlay.show()

    # 자막 md 파일 저장 (확정 자막마다 한 줄씩 추가)
    log_folder = str(settings.value("log_folder", default_folder()))
    logger = CaptionLogger(log_folder)

    # 트레이 아이콘 + 우클릭 메뉴
    tray = QtWidgets.QSystemTrayIcon(make_tray_icon(), app)
    tray.setToolTip("한국어 라이브 자막")
    menu = QtWidgets.QMenu()
    dlg = {"win": None}

    def open_settings():
        if dlg["win"] is None:
            dlg["win"] = SettingsDialog(overlay, logger)
        dlg["win"].show()
        dlg["win"].raise_()
        dlg["win"].activateWindow()

    def toggle_overlay(checked):
        overlay.setVisible(checked)

    act_settings = menu.addAction("설정...")
    act_settings.triggered.connect(open_settings)
    act_show = menu.addAction("자막 보이기")
    act_show.setCheckable(True)
    act_show.setChecked(True)
    act_show.toggled.connect(toggle_overlay)
    menu.addSeparator()
    act_quit = menu.addAction("종료")
    act_quit.triggered.connect(app.quit)
    tray.setContextMenu(menu)
    tray.activated.connect(
        lambda reason: open_settings()
        if reason == QtWidgets.QSystemTrayIcon.ActivationReason.DoubleClick
        else None
    )
    tray.show()

    worker = AsrWorker(
        on_result=lambda text, is_final: bridge.new_text.emit(text, is_final),
        on_status=lambda msg: bridge.status.emit(msg),
        on_final=lambda ts, text: logger.append(ts, text),
        language=language,
        device_id=device_id,
    )
    worker.start()

    # 모델 로딩이 끝나면 오디오 캡처 시작 (UI 블로킹 없이 백그라운드 대기)
    state = {"src": None}

    def start_audio_when_ready():
        worker.wait_ready()
        src = audio_source.AudioSource(audio_index, on_audio=worker.feed)
        state["src"] = src
        src.start()
        bridge.status.emit("듣는 중... (말해보세요)")

    threading.Thread(target=start_audio_when_ready, daemon=True).start()

    exit_code = app.exec()

    worker.stop()
    if state["src"] is not None:
        state["src"].stop()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
