"""悬浮字幕条 HUD：无边框、置顶、半透明，可拖动，hover 显示控制按钮。

支持双语显示（原文小字 + 译文大字）、自定义文字颜色/背景不透明度、
鼠标穿透模式（穿透时通过托盘或全局快捷键解除）。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QPoint, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QBrush
from PySide6.QtWidgets import (
    QWidget, QLabel, QHBoxLayout, QVBoxLayout, QPushButton, QSizeGrip,
    QApplication,
)

MAX_LINES = 2       # 屏幕上保留的译文行数
MAX_LINE_CHARS = 42  # 单行大致换行宽度（按 CJK 字符估算）
MAX_ORIG_CHARS = 80  # 原文行最多保留的尾部字符数

STATUS_COLORS = {
    "idle": "#888888",
    "connecting": "#e6b800",
    "connected": "#33cc66",
    "error": "#ff4d4d",
    "info": "#4da6ff",
}


class SubtitleHud(QWidget):
    startRequested = Signal()
    stopRequested = Signal()
    settingsRequested = Signal()
    historyRequested = Signal()
    lockRequested = Signal()
    quitRequested = Signal()

    def __init__(self, font_size: int = 20, width: int = 900,
                 text_color: str = "#FFFFFF", bg_opacity: int = 170,
                 show_original: bool = False):
        super().__init__()
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._drag_pos: QPoint | None = None
        self._lines: list[str] = []
        self._current = ""       # 正在生成的译文句
        self._orig_current = ""  # 正在生成的原文句
        self._is_running = False
        self._text_color = text_color
        self._bg_opacity = bg_opacity
        self._font_size = font_size
        self._show_original = show_original

        # 原文（小字，双语模式）
        self.orig_label = QLabel("")
        self.orig_label.setAlignment(Qt.AlignCenter)
        self.orig_label.setVisible(show_original)

        # 译文
        self.label = QLabel("点击 ▶ 开始同传字幕")
        self.label.setWordWrap(True)
        self.label.setAlignment(Qt.AlignCenter)

        self._apply_style()

        # 控制栏（hover 显示）
        self.btn_toggle = QPushButton("▶")
        self.btn_history = QPushButton("📜")
        self.btn_lock = QPushButton("🔒")
        self.btn_settings = QPushButton("⚙")
        self.btn_quit = QPushButton("✕")
        self.btn_history.setToolTip("历史记录 / 导出")
        self.btn_lock.setToolTip("鼠标穿透（用托盘或 Ctrl+Alt+L 解除）")
        for b in (self.btn_toggle, self.btn_history, self.btn_lock,
                  self.btn_settings, self.btn_quit):
            b.setFixedSize(30, 30)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(
                "QPushButton{color:white;background:rgba(255,255,255,30);"
                "border:none;border-radius:15px;font-size:14px;}"
                "QPushButton:hover{background:rgba(255,255,255,70);}"
            )
        self.status_label = QLabel("●")
        self.status_label.setStyleSheet(f"color:{STATUS_COLORS['idle']};background:transparent;")
        self.status_text = QLabel("")
        self.status_text.setStyleSheet("color:#bbbbbb;background:transparent;font-size:12px;")

        self.btn_toggle.clicked.connect(self._on_toggle)
        self.btn_history.clicked.connect(self.historyRequested)
        self.btn_lock.clicked.connect(self.lockRequested)
        self.btn_settings.clicked.connect(self.settingsRequested)
        self.btn_quit.clicked.connect(self.quitRequested)

        bar = QHBoxLayout()
        bar.setContentsMargins(8, 0, 8, 0)
        bar.addWidget(self.status_label)
        bar.addWidget(self.status_text)
        bar.addStretch(1)
        for b in (self.btn_toggle, self.btn_history, self.btn_lock,
                  self.btn_settings, self.btn_quit):
            bar.addWidget(b)
        self._bar_widget = QWidget()
        self._bar_widget.setLayout(bar)
        self._bar_widget.setVisible(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 6, 16, 10)
        layout.addWidget(self._bar_widget)
        layout.addWidget(self.orig_label)
        layout.addWidget(self.label)

        grip = QSizeGrip(self)
        grip.setFixedSize(14, 14)

        self.resize(width, 110)

    # ---- 外观 ----

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QColor(0, 0, 0, self._bg_opacity)
        p.setBrush(QBrush(c))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(self.rect(), 12, 12)

    def _apply_style(self) -> None:
        f = QFont("Microsoft YaHei", self._font_size)
        f.setWeight(QFont.DemiBold)
        self.label.setFont(f)
        self.label.setStyleSheet(f"color:{self._text_color};background:transparent;")
        of = QFont("Microsoft YaHei", max(9, int(self._font_size * 0.55)))
        self.orig_label.setFont(of)
        self.orig_label.setStyleSheet("color:#9fd0ff;background:transparent;")

    def apply_appearance(self, font_size: int, text_color: str,
                         bg_opacity: int, show_original: bool) -> None:
        self._font_size = font_size
        self._text_color = text_color
        self._bg_opacity = bg_opacity
        self._show_original = show_original
        self.orig_label.setVisible(show_original)
        if not show_original:
            self.orig_label.setText("")
        self._apply_style()
        self.update()

    def set_click_through(self, enabled: bool) -> None:
        # 改 window flag 会让窗口隐藏，需要重新 show
        self.setWindowFlag(Qt.WindowTransparentForInput, enabled)
        self.show()

    def place_default(self) -> None:
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(
            screen.x() + (screen.width() - self.width()) // 2,
            screen.y() + screen.height() - self.height() - 60,
        )

    # ---- 字幕内容 ----

    def append_text(self, text: str) -> None:
        self._current += text
        while len(self._current) > MAX_LINE_CHARS:
            self._push_line(self._current[:MAX_LINE_CHARS])
            self._current = self._current[MAX_LINE_CHARS:]
        self._render()

    def append_original(self, text: str) -> None:
        if not self._show_original:
            return
        self._orig_current += text
        shown = self._orig_current[-MAX_ORIG_CHARS:]
        self.orig_label.setText(("…" if len(self._orig_current) > MAX_ORIG_CHARS else "") + shown)

    def finish_sentence(self) -> None:
        if self._current.strip():
            self._push_line(self._current)
        self._current = ""
        self._orig_current = ""
        self._render()

    def clear_text(self) -> None:
        self._lines.clear()
        self._current = ""
        self._orig_current = ""
        self.label.setText("")
        self.orig_label.setText("")

    def _push_line(self, line: str) -> None:
        self._lines.append(line)
        if len(self._lines) > MAX_LINES:
            self._lines = self._lines[-MAX_LINES:]

    def _render(self) -> None:
        show = self._lines[-(MAX_LINES - 1):] if self._current else self._lines[-MAX_LINES:]
        parts = [ln for ln in show if ln.strip()]
        if self._current:
            parts.append(self._current)
        self.label.setText("\n".join(parts))

    # ---- 状态 ----

    def set_status(self, kind: str, message: str) -> None:
        self.status_label.setStyleSheet(
            f"color:{STATUS_COLORS.get(kind, '#888888')};background:transparent;"
        )
        self.status_text.setText(message)
        if kind == "error":
            self.label.setText(message)

    def set_running(self, running: bool, clear: bool = True) -> None:
        self._is_running = running
        self.btn_toggle.setText("■" if running else "▶")
        if running and clear:
            self.clear_text()
            self.label.setText("正在聆听…")

    def _on_toggle(self) -> None:
        if self._is_running:
            self.stopRequested.emit()
        else:
            self.startRequested.emit()

    # ---- 交互 ----

    def enterEvent(self, event):
        self._bar_widget.setVisible(True)

    def leaveEvent(self, event):
        self._bar_widget.setVisible(False)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
