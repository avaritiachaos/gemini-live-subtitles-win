"""应用配置：dataclass + JSON 持久化到 %APPDATA%\\live-translate\\config.json"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict, fields

DEFAULT_MODEL = "models/gemini-3.5-live-translate-preview"
DEFAULT_API_BASE = "https://generativelanguage.googleapis.com"

LANGUAGES = [
    ("zh-CN", "中文（简体）"),
    ("zh-TW", "中文（繁体）"),
    ("en", "English"),
    ("ja", "日本語"),
    ("ko", "한국어"),
    ("es", "Español"),
    ("fr", "Français"),
    ("de", "Deutsch"),
    ("ru", "Русский"),
    ("pt", "Português"),
]


def config_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "live-translate")


def config_path() -> str:
    return os.path.join(config_dir(), "config.json")


@dataclass
class Config:
    api_key: str = ""
    api_base: str = DEFAULT_API_BASE
    model: str = DEFAULT_MODEL
    target_language: str = "zh-CN"
    system_prompt: str = ""        # 翻译提示词（可选），如"日语直播，口语化中文"
    response_modality: str = "AUDIO"  # AUDIO(默认,稳) | TEXT(实验,省配额)
    audio_source: str = "system"  # "system" | "mic"
    device_name: str = ""  # 系统音频模式下要监听的输出设备名；空 = 默认输出
    font_size: int = 20
    show_original: bool = False    # 双语字幕：显示原文转写
    vad_enabled: bool = True       # 静音时暂停上传音频（省 TPM 配额）
    vad_threshold: int = 200       # RMS 阈值（int16 幅度）
    click_through: bool = False    # 鼠标穿透
    text_color: str = "#FFFFFF"
    bg_opacity: int = 170          # 0-255
    hud_x: int = -1  # -1 = 屏幕底部居中
    hud_y: int = -1
    hud_width: int = 900

    def normalize(self) -> None:
        self.api_key = str(self.api_key or "").strip()
        self.api_base = str(self.api_base or DEFAULT_API_BASE).strip().rstrip("/") or DEFAULT_API_BASE
        self.model = str(self.model or DEFAULT_MODEL).strip() or DEFAULT_MODEL
        if self.target_language not in {code for code, _ in LANGUAGES}:
            self.target_language = "zh-CN"
        self.system_prompt = str(self.system_prompt or "").strip()
        self.response_modality = str(self.response_modality or "AUDIO").strip().upper()
        if self.response_modality not in ("AUDIO", "TEXT"):
            self.response_modality = "AUDIO"
        if self.audio_source not in ("system", "mic"):
            self.audio_source = "system"
        self.device_name = str(self.device_name or "").strip()
        self.show_original = bool(self.show_original)
        self.vad_enabled = bool(self.vad_enabled)
        self.click_through = bool(self.click_through)
        try:
            self.vad_threshold = max(0, min(5000, int(self.vad_threshold)))
        except (TypeError, ValueError):
            self.vad_threshold = 200
        self.text_color = str(self.text_color or "#FFFFFF").strip()
        if not (self.text_color.startswith("#") and len(self.text_color) in (4, 7)):
            self.text_color = "#FFFFFF"
        try:
            self.bg_opacity = max(0, min(255, int(self.bg_opacity)))
        except (TypeError, ValueError):
            self.bg_opacity = 170
        try:
            self.font_size = max(10, min(48, int(self.font_size)))
        except (TypeError, ValueError):
            self.font_size = 20
        try:
            self.hud_width = max(400, min(3000, int(self.hud_width)))
        except (TypeError, ValueError):
            self.hud_width = 900

    @classmethod
    def load(cls) -> "Config":
        cfg = cls()
        try:
            with open(config_path(), "r", encoding="utf-8") as f:
                data = json.load(f)
            known = {f.name for f in fields(cls)}
            for k, v in (data or {}).items():
                if k in known:
                    setattr(cfg, k, v)
        except (OSError, json.JSONDecodeError):
            pass
        cfg.normalize()
        return cfg

    def save(self) -> None:
        self.normalize()
        os.makedirs(config_dir(), exist_ok=True)
        tmp = config_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, ensure_ascii=False, indent=2)
        os.replace(tmp, config_path())
