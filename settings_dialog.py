"""设置对话框：API key、目标语言、音源与设备、双语/VAD、外观、模型名。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QFormLayout, QLineEdit, QComboBox, QSpinBox, QCheckBox,
    QDialogButtonBox, QLabel, QPushButton, QSlider, QHBoxLayout,
    QColorDialog, QPlainTextEdit,
)

from config import Config, LANGUAGES, DEFAULT_MODEL
from audio_capture import list_loopback_devices


class SettingsDialog(QDialog):
    def __init__(self, cfg: Config, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置 — Live Translate")
        self.setMinimumWidth(480)
        self.cfg = cfg
        self._text_color = cfg.text_color

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
        self.cb_source.currentIndexChanged.connect(self._update_device_enabled)

        # 监听设备（仅系统声音模式）：配合虚拟声卡可静音观看
        self.cb_device = QComboBox()
        self.cb_device.addItem("默认输出设备", "")
        for name in list_loopback_devices():
            self.cb_device.addItem(name, name)
        if cfg.device_name:
            i = self.cb_device.findData(cfg.device_name)
            if i < 0:
                self.cb_device.addItem(f"{cfg.device_name}（未找到）", cfg.device_name)
                i = self.cb_device.count() - 1
            self.cb_device.setCurrentIndex(i)

        self.ck_original = QCheckBox("双语字幕（显示原文转写）")
        self.ck_original.setChecked(cfg.show_original)

        self.ck_vad = QCheckBox("静音时暂停上传（节省免费配额）")
        self.ck_vad.setChecked(cfg.vad_enabled)

        self.sp_font = QSpinBox()
        self.sp_font.setRange(10, 48)
        self.sp_font.setValue(cfg.font_size)

        self.btn_color = QPushButton()
        self.btn_color.setFixedSize(60, 24)
        self.btn_color.clicked.connect(self._pick_color)
        self._paint_color_btn()

        self.sl_opacity = QSlider(Qt.Horizontal)
        self.sl_opacity.setRange(0, 100)
        self.sl_opacity.setValue(round(cfg.bg_opacity * 100 / 255))
        self.lb_opacity = QLabel()
        self.sl_opacity.valueChanged.connect(
            lambda v: self.lb_opacity.setText(f"{v}%")
        )
        self.lb_opacity.setText(f"{self.sl_opacity.value()}%")
        op_row = QHBoxLayout()
        op_row.addWidget(self.sl_opacity)
        op_row.addWidget(self.lb_opacity)

        self.ed_model = QLineEdit(cfg.model)
        self.ed_model.setPlaceholderText(DEFAULT_MODEL)

        self.ed_prompt = QPlainTextEdit(cfg.system_prompt)
        self.ed_prompt.setFixedHeight(64)
        self.ed_prompt.setPlaceholderText(
            "可选。例：音频来自日语直播，输出口语化中文，"
            "人名与专有名词保留常用译名，语气词可省略。"
        )

        hint = QLabel(
            '免费 API key: <a href="https://aistudio.google.com/apikey">aistudio.google.com/apikey</a>'
            "<br>国内网络需要系统代理（程序自动读取 HTTPS_PROXY 环境变量）"
            "<br>静音观看：装虚拟声卡后把视频输出指到它，上面选它作监听设备"
        )
        hint.setOpenExternalLinks(True)
        hint.setTextInteractionFlags(Qt.TextBrowserInteraction)

        form = QFormLayout(self)
        form.addRow("API Key:", self.ed_key)
        form.addRow("翻译成:", self.cb_lang)
        form.addRow("音频来源:", self.cb_source)
        form.addRow("监听设备:", self.cb_device)
        form.addRow(self.ck_original)
        form.addRow(self.ck_vad)
        form.addRow("字体大小:", self.sp_font)
        form.addRow("字幕颜色:", self.btn_color)
        form.addRow("背景不透明度:", op_row)
        form.addRow("模型:", self.ed_model)
        form.addRow("翻译提示词:", self.ed_prompt)
        form.addRow(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

        self._update_device_enabled()

    def _update_device_enabled(self) -> None:
        self.cb_device.setEnabled(self.cb_source.currentData() == "system")

    def _paint_color_btn(self) -> None:
        self.btn_color.setStyleSheet(
            f"background:{self._text_color};border:1px solid #999;border-radius:4px;"
        )

    def _pick_color(self) -> None:
        from PySide6.QtGui import QColor
        c = QColorDialog.getColor(QColor(self._text_color), self, "字幕颜色")
        if c.isValid():
            self._text_color = c.name()
            self._paint_color_btn()

    def accept(self) -> None:
        self.cfg.api_key = self.ed_key.text().strip()
        self.cfg.target_language = self.cb_lang.currentData()
        self.cfg.audio_source = self.cb_source.currentData()
        self.cfg.device_name = self.cb_device.currentData() or ""
        self.cfg.show_original = self.ck_original.isChecked()
        self.cfg.vad_enabled = self.ck_vad.isChecked()
        self.cfg.font_size = self.sp_font.value()
        self.cfg.text_color = self._text_color
        self.cfg.bg_opacity = round(self.sl_opacity.value() * 255 / 100)
        self.cfg.model = self.ed_model.text().strip() or DEFAULT_MODEL
        self.cfg.system_prompt = self.ed_prompt.toPlainText().strip()
        self.cfg.save()
        super().accept()
