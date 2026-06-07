"""항상 위에 떠 있는 반투명 자막 오버레이 + 설정창 + 트레이 아이콘 (PyQt6).

- 프레임 없음 / 항상 위 / 반투명 배경
- 화면 하단 중앙에 배치, 드래그로 이동 (옮긴 위치 유지)
- 확정 자막은 밝게, 중간(임시) 자막은 흐리게 표시
- 최대 N줄 높이로 고정, 넘치면 자동 스크롤(최신이 아래)
- 트레이 아이콘 우클릭 → 설정 / 종료
- 설정창: 글자 크기 / 글자 색 / 한번에 보이는 줄 수
- 설정은 QSettings 로 저장되어 재실행 시 유지된다.
- ESC: 종료
"""

import html

from PyQt6 import QtCore, QtGui, QtWidgets

DEFAULT_FONT_SIZE = 28
DEFAULT_COLOR = "#FFFFFF"
INTERIM_COLOR = "#9FE3FF"
DEFAULT_MAX_LINES = 5
HISTORY_LIMIT = 200  # 메모리 보호용 자막 보관 한계


class Bridge(QtCore.QObject):
    """워커 스레드 → UI 스레드로 안전하게 자막을 전달하는 신호."""

    new_text = QtCore.pyqtSignal(str, bool)  # (text, is_final)
    status = QtCore.pyqtSignal(str)


def make_tray_icon() -> QtGui.QIcon:
    """파일 없이 런타임에 트레이 아이콘을 그린다 ('자' 글자 배지)."""
    pm = QtGui.QPixmap(64, 64)
    pm.fill(QtCore.Qt.GlobalColor.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    p.setPen(QtCore.Qt.PenStyle.NoPen)
    p.setBrush(QtGui.QColor("#2D7DFF"))
    p.drawRoundedRect(4, 4, 56, 56, 16, 16)
    p.setPen(QtGui.QColor("white"))
    f = QtGui.QFont("Malgun Gothic", 30)
    f.setBold(True)
    p.setFont(f)
    p.drawText(pm.rect(), QtCore.Qt.AlignmentFlag.AlignCenter, "자")
    p.end()
    return QtGui.QIcon(pm)


class CaptionOverlay(QtWidgets.QWidget):
    def __init__(self, bridge: Bridge, settings: QtCore.QSettings):
        super().__init__()
        self._settings = settings
        self._drag_pos = None
        self._user_moved = False

        self.font_size = int(settings.value("font_size", DEFAULT_FONT_SIZE))
        self.color = str(settings.value("color", DEFAULT_COLOR))
        self.max_lines = int(settings.value("max_lines", DEFAULT_MAX_LINES))

        self._history = []  # 확정 자막들
        self._interim = ""  # 진행 중(중간) 자막
        self._status = "자막 대기 중..."

        self.setWindowFlags(
            QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
            | QtCore.Qt.WindowType.Tool
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)

        self.view = QtWidgets.QTextEdit(self)
        self.view.setReadOnly(True)
        self.view.setFrameStyle(QtWidgets.QFrame.Shape.NoFrame)
        self.view.setVerticalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.view.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        # 텍스트 영역이 마우스를 가로채지 않게 → 창 어디를 잡아도 드래그 이동 가능
        self.view.setAttribute(
            QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)

        self._apply_style()
        self._resize_and_place()
        self._render()

        bridge.new_text.connect(self._on_text)
        bridge.status.connect(self._on_status)

    # ---- 스타일 / 크기 ----
    def _apply_style(self):
        self.view.setStyleSheet(
            "QTextEdit {"
            " background-color: rgba(0, 0, 0, 170);"
            " border-radius: 14px;"
            " padding: 10px 18px;"
            " border: none;"
            "}"
        )
        f = QtGui.QFont("Malgun Gothic", self.font_size)
        f.setBold(True)
        self.view.setFont(f)

    def _line_height(self):
        return QtGui.QFontMetrics(self.view.font()).lineSpacing()

    def _resize_and_place(self, keep_position=False):
        screen = QtWidgets.QApplication.primaryScreen().availableGeometry()
        w = int(screen.width() * 0.7)
        h = self._line_height() * self.max_lines + 36  # 패딩 포함
        old = self.geometry()
        self.setFixedSize(w, h)
        if keep_position and self._user_moved:
            # 높이가 바뀌어도 아래쪽 가장자리를 고정
            new_y = old.y() + old.height() - h
            self.move(old.x(), new_y)
        elif not self._user_moved:
            x = screen.x() + (screen.width() - w) // 2
            y = screen.y() + screen.height() - h - 80
            self.move(x, y)

    # ---- 자막 렌더링 ----
    def _render(self):
        lines = []
        if self._history:
            for t in self._history:
                lines.append(
                    f'<span style="color:{self.color}">{html.escape(t)}</span>'
                )
        if self._interim:
            lines.append(
                f'<span style="color:{INTERIM_COLOR}">{html.escape(self._interim)}</span>'
            )
        if not lines:
            lines.append(
                f'<span style="color:{INTERIM_COLOR}">● {html.escape(self._status)}</span>'
            )
        self.view.setHtml("<br>".join(lines))
        # 최신이 보이도록 맨 아래로 스크롤
        sb = self.view.verticalScrollBar()
        sb.setValue(sb.maximum())

    @QtCore.pyqtSlot(str, bool)
    def _on_text(self, text: str, is_final: bool):
        if not text:
            return
        if is_final:
            self._history.append(text)
            if len(self._history) > HISTORY_LIMIT:
                self._history = self._history[-HISTORY_LIMIT:]
            self._interim = ""
        else:
            self._interim = text
        self._render()

    @QtCore.pyqtSlot(str)
    def _on_status(self, msg: str):
        self._status = msg
        if not self._history and not self._interim:
            self._render()

    # ---- 설정 적용 (설정창에서 호출) ----
    def set_font_size(self, size: int):
        self.font_size = size
        self._settings.setValue("font_size", size)
        self._apply_style()
        self._resize_and_place(keep_position=True)
        self._render()

    def set_color(self, color: str):
        self.color = color
        self._settings.setValue("color", color)
        self._render()

    def set_max_lines(self, n: int):
        self.max_lines = n
        self._settings.setValue("max_lines", n)
        self._resize_and_place(keep_position=True)
        self._render()

    # ---- 드래그 이동 / 종료 ----
    def mousePressEvent(self, e):
        if e.button() == QtCore.Qt.MouseButton.LeftButton:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag_pos is not None:
            self._user_moved = True
            self.move(e.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, e):
        self._drag_pos = None

    def keyPressEvent(self, e):
        if e.key() == QtCore.Qt.Key.Key_Escape:
            QtWidgets.QApplication.quit()


class SettingsDialog(QtWidgets.QDialog):
    """글자 크기 / 글자 색 / 보이는 줄 수 / 자막 저장 폴더 설정창."""

    def __init__(self, overlay: CaptionOverlay, logger=None):
        super().__init__()
        self.overlay = overlay
        self.logger = logger
        self.setWindowTitle("자막 설정")
        self.setWindowFlag(QtCore.Qt.WindowType.WindowStaysOnTopHint, True)

        form = QtWidgets.QFormLayout(self)

        # 글자 크기
        self.size_spin = QtWidgets.QSpinBox()
        self.size_spin.setRange(10, 80)
        self.size_spin.setValue(overlay.font_size)
        self.size_spin.setSuffix(" pt")
        self.size_spin.valueChanged.connect(self.overlay.set_font_size)
        form.addRow("글자 크기", self.size_spin)

        # 글자 색
        self.color_btn = QtWidgets.QPushButton()
        self._update_color_btn(overlay.color)
        self.color_btn.clicked.connect(self._pick_color)
        form.addRow("글자 색", self.color_btn)

        # 보이는 줄 수
        self.lines_spin = QtWidgets.QSpinBox()
        self.lines_spin.setRange(1, 15)
        self.lines_spin.setValue(overlay.max_lines)
        self.lines_spin.setSuffix(" 줄")
        self.lines_spin.valueChanged.connect(self.overlay.set_max_lines)
        form.addRow("보이는 줄 수", self.lines_spin)

        # 자막 저장 폴더
        if self.logger is not None:
            self.folder_btn = QtWidgets.QPushButton(self.logger.folder)
            self.folder_btn.setStyleSheet("text-align: left; padding: 6px;")
            self.folder_btn.clicked.connect(self._pick_folder)
            form.addRow("자막 저장 폴더", self.folder_btn)

        close_btn = QtWidgets.QPushButton("닫기")
        close_btn.clicked.connect(self.close)
        form.addRow(close_btn)

    def _update_color_btn(self, color: str):
        self.color_btn.setText(color)
        self.color_btn.setStyleSheet(
            f"background-color: {color}; color: black; padding: 6px;"
        )

    def _pick_color(self):
        col = QtWidgets.QColorDialog.getColor(
            QtGui.QColor(self.overlay.color), self, "글자 색 선택"
        )
        if col.isValid():
            name = col.name()
            self._update_color_btn(name)
            self.overlay.set_color(name)

    def _pick_folder(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(
            self, "자막 저장 폴더 선택", self.logger.folder
        )
        if folder:
            self.logger.set_folder(folder)
            self.overlay._settings.setValue("log_folder", folder)
            self.folder_btn.setText(folder)
