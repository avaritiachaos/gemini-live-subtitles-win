"""Live Translate — Gemini 实时同传字幕（Windows）

入口：装配 HUD、Gemini 客户端、音频采集、历史记录、托盘与全局快捷键。

全局快捷键：Ctrl+Alt+T 开始/停止，Ctrl+Alt+L 切换鼠标穿透。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import re
import sys
import time

from PySide6.QtCore import Qt, QAbstractNativeEventFilter, QTimer
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor, QFont, QAction
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon, QMenu

from config import Config
from audio_capture import AudioCapture, AudioCaptureError
from gemini_client import GeminiClient
from subtitle_hud import SubtitleHud, UnlockOverlay
from settings_dialog import SettingsDialog
from history import HistoryStore, HistoryWindow, Entry

WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
HOTKEY_TOGGLE = 1       # Ctrl+Alt+T
HOTKEY_CLICKTHROUGH = 2  # Ctrl+Alt+L

# 历史成句：说话停顿判定 / 过长强制切分
SENTENCE_IDLE_MS = 2500
SENTENCE_MAX_CHARS = 80
SENT_SPLIT = re.compile(r"(?<=[。！？!?…；;])")


class _HotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, callbacks: dict[int, callable]):
        super().__init__()
        self._callbacks = callbacks

    def nativeEventFilter(self, event_type, message):
        if event_type == b"windows_generic_MSG":
            msg = ctypes.wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY:
                cb = self._callbacks.get(int(msg.wParam))
                if cb is not None:
                    cb()
                    return True, 0
        return False, 0


def _make_tray_icon() -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(30, 30, 30, 230))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(0, 0, 64, 64, 14, 14)
    p.setPen(QColor("#4da6ff"))
    f = QFont("Microsoft YaHei", 32)
    f.setBold(True)
    p.setFont(f)
    p.drawText(pm.rect(), Qt.AlignCenter, "译")
    p.end()
    return QIcon(pm)


class App:
    def __init__(self):
        self.cfg = Config.load()
        self.client = GeminiClient()
        self.capture: AudioCapture | None = None
        self._pending_restart = False

        # 历史记录
        self.history = HistoryStore()
        self.history_window = HistoryWindow(self.history)
        self._sent_text = ""
        self._sent_orig = ""
        self._sent_start = 0.0
        # Live Translate 是连续流，几乎不发 turnComplete——用"停顿即成句"兜底
        self._flush_timer = QTimer()
        self._flush_timer.setSingleShot(True)
        self._flush_timer.setInterval(SENTENCE_IDLE_MS)
        self._flush_timer.timeout.connect(lambda: self._flush_history(break_hud=True))

        self.hud = SubtitleHud(
            font_size=self.cfg.font_size,
            width=self.cfg.hud_width,
            text_color=self.cfg.text_color,
            bg_opacity=self.cfg.bg_opacity,
            show_original=self.cfg.show_original,
        )
        if self.cfg.hud_x >= 0 and self.cfg.hud_y >= 0:
            self.hud.move(self.cfg.hud_x, self.cfg.hud_y)
        else:
            self.hud.place_default()

        self.hud.startRequested.connect(self.start_session)
        self.hud.stopRequested.connect(self.stop_session)
        self.hud.settingsRequested.connect(self.open_settings)
        self.hud.historyRequested.connect(self.show_history)
        self.hud.lockRequested.connect(lambda: self.set_click_through(True))
        self.hud.quitRequested.connect(self.quit)

        self.unlock_overlay = UnlockOverlay()
        self.unlock_overlay.clicked.connect(lambda: self.set_click_through(False))

        self.client.subtitle.connect(self._on_subtitle)
        self.client.original.connect(self._on_original)
        self.client.turnComplete.connect(self._on_turn_complete)
        self.client.status.connect(self.hud.set_status)
        self.client.stopped.connect(self._on_client_stopped)

        self._setup_tray()
        self._setup_hotkeys()

        self.hud.show()
        if self.cfg.click_through:
            self.set_click_through(True)

    # ---- 托盘 ----

    def _setup_tray(self) -> None:
        self.tray = QSystemTrayIcon(_make_tray_icon())
        self.tray.setToolTip("Live Translate — Gemini 同传字幕")
        menu = QMenu()
        self.act_toggle = QAction("开始翻译 (Ctrl+Alt+T)")
        self.act_toggle.triggered.connect(self.toggle_session)
        self.act_lock = QAction("鼠标穿透 (Ctrl+Alt+L)")
        self.act_lock.setCheckable(True)
        self.act_lock.setChecked(self.cfg.click_through)
        self.act_lock.triggered.connect(
            lambda checked: self.set_click_through(checked)
        )
        act_history = QAction("历史记录…")
        act_history.triggered.connect(self.show_history)
        act_settings = QAction("设置…")
        act_settings.triggered.connect(self.open_settings)
        act_quit = QAction("退出")
        act_quit.triggered.connect(self.quit)
        for a in (self.act_toggle, self.act_lock, act_history, act_settings):
            menu.addAction(a)
        menu.addSeparator()
        menu.addAction(act_quit)
        self._tray_menu = menu  # 持有引用，防止被回收
        self._tray_actions = (act_history, act_settings, act_quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.Trigger:  # 单击托盘：解除穿透并带回 HUD
            if self.cfg.click_through:
                self.set_click_through(False)
            self.hud.show()
            self.hud.raise_()

    # ---- 全局快捷键 ----

    def _setup_hotkeys(self) -> None:
        self._hotkey_filter = _HotkeyFilter({
            HOTKEY_TOGGLE: self.toggle_session,
            HOTKEY_CLICKTHROUGH: lambda: self.set_click_through(not self.cfg.click_through),
        })
        QApplication.instance().installNativeEventFilter(self._hotkey_filter)
        user32 = ctypes.windll.user32
        self._hotkeys_ok = []
        for hk_id, vk in ((HOTKEY_TOGGLE, ord("T")), (HOTKEY_CLICKTHROUGH, ord("L"))):
            if user32.RegisterHotKey(None, hk_id, MOD_CONTROL | MOD_ALT, vk):
                self._hotkeys_ok.append(hk_id)

    def _unregister_hotkeys(self) -> None:
        user32 = ctypes.windll.user32
        for hk_id in getattr(self, "_hotkeys_ok", []):
            user32.UnregisterHotKey(None, hk_id)

    # ---- 字幕 / 历史 ----

    def _on_subtitle(self, text: str) -> None:
        if not self._sent_text and not self._sent_orig:
            self._sent_start = time.time()
        self._sent_text += text
        self.hud.append_text(text)
        self._flush_timer.start()
        # 句子过长：在最后一个句末标点处切分入库，避免一整段憋成一条
        if len(self._sent_text) >= SENTENCE_MAX_CHARS:
            parts = SENT_SPLIT.split(self._sent_text)
            if len(parts) > 1:
                head = "".join(parts[:-1]).strip()
                if head:
                    self.history.add(Entry(
                        start=self._sent_start,
                        end=time.time(),
                        text=head,
                        orig=self._sent_orig.strip() if self.cfg.show_original else "",
                    ))
                    self._sent_text = parts[-1]
                    self._sent_orig = ""
                    self._sent_start = time.time()

    def _on_original(self, text: str) -> None:
        if not self._sent_text and not self._sent_orig:
            self._sent_start = time.time()
        self._sent_orig += text
        self.hud.append_original(text)
        self._flush_timer.start()

    def _flush_history(self, break_hud: bool = False) -> None:
        """把当前累积的句子写入历史（停顿、turnComplete、停止时调用）。"""
        self._flush_timer.stop()
        text = self._sent_text.strip()
        if text:
            self.history.add(Entry(
                start=self._sent_start,
                end=time.time(),
                text=text,
                orig=self._sent_orig.strip() if self.cfg.show_original else "",
            ))
        self._sent_text = ""
        self._sent_orig = ""
        if break_hud:
            self.hud.finish_sentence()

    def _on_turn_complete(self) -> None:
        self._flush_history(break_hud=True)

    def show_history(self) -> None:
        self.history_window.show()
        self.history_window.raise_()
        self.history_window.activateWindow()

    # ---- 鼠标穿透 ----

    def set_click_through(self, enabled: bool) -> None:
        self.cfg.click_through = enabled
        self.act_lock.setChecked(enabled)
        self.hud.set_click_through(enabled)
        if enabled:
            g = self.hud.frameGeometry()
            self.unlock_overlay.move(g.right() - 36, g.top() + 6)
            self.unlock_overlay.show()
            self.unlock_overlay.raise_()
            self.tray.showMessage(
                "鼠标穿透已开启",
                "点字幕条右上角 🔓、托盘图标或 Ctrl+Alt+L 解除",
                QSystemTrayIcon.Information, 3000,
            )
        else:
            self.unlock_overlay.hide()

    # ---- 会话控制 ----

    def toggle_session(self) -> None:
        if self.client.running:
            self.stop_session()
        else:
            self.start_session()

    def start_session(self, preserve_text: bool = False) -> None:
        if not self.cfg.api_key:
            self.open_settings()
            if not self.cfg.api_key:
                self.hud.set_status("error", "请先在设置中填入 API Key")
                return
        try:
            self.capture = AudioCapture(
                self.cfg.audio_source,
                self.client.send_audio,
                device_name=self.cfg.device_name if self.cfg.audio_source == "system" else "",
                vad_enabled=self.cfg.vad_enabled,
                vad_threshold=self.cfg.vad_threshold,
            )
            self.capture.start()
        except AudioCaptureError as e:
            self.capture = None
            self.hud.set_status("error", str(e))
            return
        self.client.start(
            api_key=self.cfg.api_key,
            api_base=self.cfg.api_base,
            model=self.cfg.model,
            target_lang=self.cfg.target_language,
            system_prompt=self.cfg.system_prompt,
            response_modality=self.cfg.response_modality,
        )
        self.hud.set_running(True, clear=not preserve_text)
        self.act_toggle.setText("停止翻译 (Ctrl+Alt+T)")

    def stop_session(self) -> None:
        self._flush_history(break_hud=False)
        if self.capture is not None:
            self.capture.stop()
            self.capture = None
        self.client.stop()
        self.hud.set_running(False)
        self.act_toggle.setText("开始翻译 (Ctrl+Alt+T)")

    def _on_client_stopped(self) -> None:
        # 客户端因错误自行退出时，同步停掉采集并复位按钮
        self._flush_history(break_hud=False)
        if self.capture is not None:
            self.capture.stop()
            self.capture = None
        self.hud.set_running(False, clear=False)
        self.act_toggle.setText("开始翻译 (Ctrl+Alt+T)")
        if self._pending_restart:
            self._pending_restart = False
            self.start_session(preserve_text=True)

    # ---- 设置 / 退出 ----

    def open_settings(self) -> None:
        # 会话保持运行；只有确定且改了会话相关项才无缝重启（保留字幕）
        before = (
            self.cfg.api_key, self.cfg.api_base, self.cfg.model,
            self.cfg.target_language, self.cfg.audio_source,
            self.cfg.device_name, self.cfg.vad_enabled, self.cfg.vad_threshold,
            self.cfg.system_prompt,
        )
        dlg = SettingsDialog(self.cfg, parent=self.hud)
        if not dlg.exec():
            return
        self.hud.apply_appearance(
            self.cfg.font_size, self.cfg.text_color,
            self.cfg.bg_opacity, self.cfg.show_original,
        )
        after = (
            self.cfg.api_key, self.cfg.api_base, self.cfg.model,
            self.cfg.target_language, self.cfg.audio_source,
            self.cfg.device_name, self.cfg.vad_enabled, self.cfg.vad_threshold,
            self.cfg.system_prompt,
        )
        if after != before and self.client.running:
            self._pending_restart = True
            self.stop_session()

    def quit(self) -> None:
        self._pending_restart = False
        self.stop_session()
        self._unregister_hotkeys()
        pos = self.hud.pos()
        self.cfg.hud_x, self.cfg.hud_y = pos.x(), pos.y()
        self.cfg.hud_width = self.hud.width()
        self.cfg.save()
        self.tray.hide()
        QApplication.quit()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Live Translate")
    app.setQuitOnLastWindowClosed(False)
    try:
        controller = App()
    except Exception as e:
        QMessageBox.critical(None, "启动失败", str(e))
        return 1
    app.aboutToQuit.connect(controller.stop_session)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
