"""设置对话框：API key、目标语言、音源、字体大小、模型名。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QFormLayout, QLineEdit, QComboBox, QSpinBox,
    QDialogButtonBox, QLabel,
)

from config import Config, LANGUAGES, DEFAULT_MODEL


class SettingsDialog(QDialog):
    def __init__(self, cfg: Config, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置 — Live Translate")
        self.setMinimumWidth(460)
        self.cfg = cfg

        self.ed_key = QLineEdit(cfg.api_key)
        self.ed_key.setEchoMode(QLineEdit.Password)
        self.ed_key.setPlaceholderText("在 aistudio.google.com 免费获取")

        self.cb_lang = QComboBox()
        for code, name in LANGUAGES:
            self.cb_lang.addItem(name, code)
        idx = self.cb_lang.findData(cfg.target_language)
        self.cb_lang.setCurrentIndex(max(0, idx))

        self.cb_source = QComboBox()
        self.cb_source.addItem("系统声音（视频/会议）", "system")
        self.cb_source.addItem("麦克风", "mic")
        self.cb_source.setCurrentIndex(0 if cfg.audio_source == "system" else 1)

        self.sp_font = QSpinBox()
        self.sp_font.setRange(10, 48)
        self.sp_font.setValue(cfg.font_size)

        self.ed_model = QLineEdit(cfg.model)
        self.ed_model.setPlaceholderText(DEFAULT_MODEL)

        hint = QLabel(
            '免费 API key: <a href="https://aistudio.google.com/apikey">aistudio.google.com/apikey</a>'
            "<br>国内网络需要系统代理（程序自动读取 HTTPS_PROXY 环境变量）"
        )
        hint.setOpenExternalLinks(True)
        hint.setTextInteractionFlags(Qt.TextBrowserInteraction)

        form = QFormLayout(self)
        form.addRow("API Key:", self.ed_key)
        form.addRow("翻译成:", self.cb_lang)
        form.addRow("音频来源:", self.cb_source)
        form.addRow("字体大小:", self.sp_font)
        form.addRow("模型:", self.ed_model)
        form.addRow(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def accept(self) -> None:
        self.cfg.api_key = self.ed_key.text().strip()
        self.cfg.target_language = self.cb_lang.currentData()
        self.cfg.audio_source = self.cb_source.currentData()
        self.cfg.font_size = self.sp_font.value()
        self.cfg.model = self.ed_model.text().strip() or DEFAULT_MODEL
        self.cfg.save()
        super().accept()
