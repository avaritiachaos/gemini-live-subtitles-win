"""字幕历史：内存存储 + 查看窗口 + 导出 txt / srt。

App 在每句 turnComplete 时调用 store.add()；窗口打开时实时追加。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPlainTextEdit, QPushButton,
    QFileDialog, QMessageBox, QLabel,
)


@dataclass
class Entry:
    start: float   # time.time() 该句开始
    end: float     # 该句结束
    text: str      # 译文
    orig: str = "" # 原文（双语开启时才有）


def _fmt_clock(t: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(t))


def _fmt_srt(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


class HistoryStore:
    def __init__(self) -> None:
        self.entries: list[Entry] = []
        self.on_add: Optional[Callable[[Entry], None]] = None

    def add(self, entry: Entry) -> None:
        self.entries.append(entry)
        if self.on_add is not None:
            self.on_add(entry)

    def clear(self) -> None:
        self.entries.clear()

    def to_txt(self) -> str:
        lines = []
        for e in self.entries:
            lines.append(f"[{_fmt_clock(e.start)}] {e.text}")
            if e.orig.strip():
                lines.append(f"    {e.orig}")
        return "\n".join(lines) + "\n"

    def to_srt(self) -> str:
        if not self.entries:
            return ""
        base = self.entries[0].start
        blocks = []
        for i, e in enumerate(self.entries, 1):
            start = max(0.0, e.start - base)
            end = max(start + 0.5, e.end - base)
            body = e.text if not e.orig.strip() else f"{e.orig}\n{e.text}"
            blocks.append(f"{i}\n{_fmt_srt(start)} --> {_fmt_srt(end)}\n{body}\n")
        return "\n".join(blocks)


class HistoryWindow(QWidget):
    def __init__(self, store: HistoryStore):
        super().__init__()
        self.store = store
        self.setWindowTitle("翻译历史 — Live Translate")
        self.resize(560, 480)

        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setFont(QFont("Microsoft YaHei", 11))

        self.count_label = QLabel("")

        btn_txt = QPushButton("导出 TXT")
        btn_srt = QPushButton("导出 SRT")
        btn_clear = QPushButton("清空")
        btn_txt.clicked.connect(lambda: self._export("txt"))
        btn_srt.clicked.connect(lambda: self._export("srt"))
        btn_clear.clicked.connect(self._clear)

        bar = QHBoxLayout()
        bar.addWidget(self.count_label)
        bar.addStretch(1)
        bar.addWidget(btn_txt)
        bar.addWidget(btn_srt)
        bar.addWidget(btn_clear)

        layout = QVBoxLayout(self)
        layout.addWidget(self.view)
        layout.addLayout(bar)

        self.store.on_add = self._on_add

    # ---- 展示 ----

    def showEvent(self, event):
        self._reload()
        super().showEvent(event)

    def _reload(self) -> None:
        self.view.setPlainText(self.store.to_txt() if self.store.entries else "（暂无记录）")
        self._update_count()
        self._scroll_bottom()

    def _on_add(self, e: Entry) -> None:
        if not self.isVisible():
            return
        if len(self.store.entries) == 1:
            self.view.setPlainText("")
        line = f"[{_fmt_clock(e.start)}] {e.text}"
        if e.orig.strip():
            line += f"\n    {e.orig}"
        self.view.appendPlainText(line)
        self._update_count()
        self._scroll_bottom()

    def _update_count(self) -> None:
        self.count_label.setText(f"共 {len(self.store.entries)} 句")

    def _scroll_bottom(self) -> None:
        sb = self.view.verticalScrollBar()
        sb.setValue(sb.maximum())

    # ---- 操作 ----

    def _export(self, fmt: str) -> None:
        if not self.store.entries:
            QMessageBox.information(self, "导出", "还没有可导出的记录")
            return
        default = time.strftime(f"字幕_%Y%m%d_%H%M%S.{fmt}")
        path, _ = QFileDialog.getSaveFileName(
            self, "导出", default, f"{fmt.upper()} (*.{fmt})"
        )
        if not path:
            return
        content = self.store.to_txt() if fmt == "txt" else self.store.to_srt()
        try:
            with open(path, "w", encoding="utf-8-sig") as f:
                f.write(content)
        except OSError as e:
            QMessageBox.warning(self, "导出失败", str(e))
            return
        QMessageBox.information(self, "导出", f"已保存到\n{path}")

    def _clear(self) -> None:
        if QMessageBox.question(self, "清空", "确定清空全部历史记录？") == QMessageBox.Yes:
            self.store.clear()
            self._reload()
