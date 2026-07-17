"""Live Translate — Gemini 实时同传字幕（Windows）

入口：装配 HUD、Gemini 客户端与音频采集。
"""
from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from config import Config
from audio_capture import AudioCapture, AudioCaptureError
from gemini_client import GeminiClient
from subtitle_hud import SubtitleHud
from settings_dialog import SettingsDialog


class App:
    def __init__(self):
        self.cfg = Config.load()
        self.client = GeminiClient()
        self.capture: AudioCapture | None = None

        self.hud = SubtitleHud(font_size=self.cfg.font_size, width=self.cfg.hud_width)
        if self.cfg.hud_x >= 0 and self.cfg.hud_y >= 0:
            self.hud.move(self.cfg.hud_x, self.cfg.hud_y)
        else:
            self.hud.place_default()

        self.hud.startRequested.connect(self.start_session)
        self.hud.stopRequested.connect(self.stop_session)
        self.hud.settingsRequested.connect(self.open_settings)
        self.hud.quitRequested.connect(self.quit)

        self.client.subtitle.connect(self.hud.append_text)
        self.client.turnComplete.connect(self.hud.finish_sentence)
        self.client.status.connect(self.hud.set_status)
        self.client.stopped.connect(self._on_client_stopped)

        self.hud.show()

    # ---- 会话控制 ----

    def start_session(self) -> None:
        if not self.cfg.api_key:
            self.open_settings()
            if not self.cfg.api_key:
                self.hud.set_status("error", "请先在设置中填入 API Key")
                return
        try:
            self.capture = AudioCapture(self.cfg.audio_source, self.client.send_audio)
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
        )
        self.hud.set_running(True)

    def stop_session(self) -> None:
        if self.capture is not None:
            self.capture.stop()
            self.capture = None
        self.client.stop()
        self.hud.set_running(False)

    def _on_client_stopped(self) -> None:
        # 客户端因错误自行退出时，同步停掉采集并复位按钮
        if self.capture is not None:
            self.capture.stop()
            self.capture = None
        self.hud.set_running(False)

    # ---- 设置 / 退出 ----

    def open_settings(self) -> None:
        was_running = self.client.running
        if was_running:
            self.stop_session()
        dlg = SettingsDialog(self.cfg, parent=self.hud)
        if dlg.exec():
            self.hud.set_font_size(self.cfg.font_size)
        if was_running:
            self.start_session()

    def quit(self) -> None:
        self.stop_session()
        pos = self.hud.pos()
        self.cfg.hud_x, self.cfg.hud_y = pos.x(), pos.y()
        self.cfg.hud_width = self.hud.width()
        self.cfg.save()
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
