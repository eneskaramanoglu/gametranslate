"""Açılışta hedef pencere seçimi (başlığa göre otomatik öneri)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout

from ..overlay import win32


class WindowPickerDialog(QDialog):
    def __init__(self, keywords: list[str], preselect_title: str = "", parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self.setWindowTitle("Yakalanacak pencereyi seç — gta-tr-subs")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        self.resize(640, 420)
        self._keywords = keywords
        self._preselect = preselect_title
        self.selected: win32.WindowInfo | None = None

        hint = QLabel(
            "Oyunun göründüğü pencereyi seç (PS Remote Play, chiaki-ng veya OBS Projector). "
            "★ işaretliler otomatik önerilerdir. Pencere görünmüyorsa uygulamayı açıp Yenile'ye bas."
        )
        hint.setWordWrap(True)
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _item: self._accept())
        refresh = QPushButton("Yenile")
        refresh.clicked.connect(self.reload)
        ok = QPushButton("Seç")
        ok.setDefault(True)
        ok.clicked.connect(self._accept)
        cancel = QPushButton("İptal")
        cancel.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addWidget(refresh)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(ok)
        layout = QVBoxLayout(self)
        layout.addWidget(hint)
        layout.addWidget(self.list)
        layout.addLayout(buttons)
        self.reload()

    def reload(self) -> None:
        self.list.clear()
        windows = win32.list_windows()
        ranked = sorted(windows, key=lambda w: (win32.suggestion_rank(w.title, self._keywords), w.title.casefold()))
        chosen_row = 0 if ranked else -1
        for row, window in enumerate(ranked):
            suggested = win32.suggestion_rank(window.title, self._keywords) < len(self._keywords)
            item = QListWidgetItem(("★ " if suggested else "   ") + window.title)
            item.setData(Qt.ItemDataRole.UserRole, window)
            self.list.addItem(item)
            if self._preselect and self._preselect.casefold() in window.title.casefold():
                chosen_row = row
        if chosen_row >= 0:
            self.list.setCurrentRow(chosen_row)

    def _accept(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        self.selected = item.data(Qt.ItemDataRole.UserRole)
        self.accept()
