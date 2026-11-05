import html
import os
import sys
import tempfile

from PySide6.QtCore import QObject, QPoint, QRunnable, QSettings, QSize, Qt, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFrame, QHBoxLayout, QLabel, QMainWindow, QPlainTextEdit,
    QPushButton, QTextBrowser, QToolButton, QVBoxLayout, QWidget,
)

try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
except ImportError:  # audio is optional
    QMediaPlayer = None

from . import google_api
from .languages import AUTO, LANGUAGES, name_of

APP_NAME = "Translator"
MAX_CHARS = 5000
TRANSLATE_DELAY_MS = 500

STYLE = """
QMainWindow, QWidget#root { background: #f8f9fa; }
QComboBox {
    font-size: 15px; color: #1a73e8; font-weight: 600; padding: 8px 12px;
    border: 1px solid #dadce0; border-radius: 8px; background: white; min-width: 220px;
}
QComboBox:hover { background: #f1f6fe; }
QComboBox::drop-down { border: none; width: 32px; }
QComboBox::down-arrow { image: url(ARROW); width: 12px; height: 12px; }
QComboBox QAbstractItemView { font-size: 14px; color: #202124; font-weight: normal; selection-background-color: #e8f0fe; selection-color: #1a73e8; }
QFrame#card { background: white; border: 1px solid #dadce0; border-radius: 12px; }
QFrame#card[target="true"] { background: #f5f5f5; }
QPlainTextEdit { border: none; background: transparent; font-size: 22px; color: #202124; }
QLabel#roman { color: #5f6368; font-size: 14px; }
QLabel#counter { color: #5f6368; font-size: 12px; }
QLabel#hint { color: #5f6368; font-size: 14px; padding: 2px 4px; }
QLabel#hint a { color: #1a73e8; }
QToolButton {
    border: none; border-radius: 20px; font-size: 18px; color: #5f6368;
    min-width: 40px; min-height: 40px;
}
QToolButton:hover { background: #e8eaed; }
QToolButton:disabled { color: #c4c7c5; }
QPushButton#swap {
    border: none; border-radius: 20px; font-size: 22px; color: #5f6368;
    min-width: 44px; min-height: 44px;
}
QPushButton#swap:hover { background: #e8eaed; }
QPushButton#swap:disabled { color: #c4c7c5; }
QTextBrowser#dict {
    background: white; border: 1px solid #dadce0; border-radius: 12px;
    padding: 10px; font-size: 14px; color: #202124;
}
QLabel#status { color: #d93025; font-size: 13px; padding: 2px 4px; }
"""


def make_arrow(path: str):
    pix = QPixmap(24, 24)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor("#5f6368"))
    p.setPen(Qt.NoPen)
    p.drawPolygon([QPoint(5, 9), QPoint(19, 9), QPoint(12, 16)])
    p.end()
    pix.save(path, "PNG")


def make_icon() -> QIcon:
    pix = QPixmap(64, 64)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor("#1a73e8"))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(4, 4, 56, 56, 14, 14)
    p.setPen(QColor("white"))
    p.setFont(QFont("Segoe UI", 22, QFont.Bold))
    p.drawText(pix.rect(), Qt.AlignCenter, "文A")
    p.end()
    return QIcon(pix)


class _Signals(QObject):
    done = Signal(int, object)
    failed = Signal(int, str)


class Task(QRunnable):
    """Runs fn(*args) on a worker thread and reports back with a request id."""

    def __init__(self, request_id, fn, *args):
        super().__init__()
        self.request_id, self.fn, self.args = request_id, fn, args
        self.signals = _Signals()

    def run(self):
        try:
            self.signals.done.emit(self.request_id, self.fn(*self.args))
        except google_api.TranslationError as e:
            self.signals.failed.emit(self.request_id, str(e))
        except Exception as e:  # never let a worker crash the app
            self.signals.failed.emit(self.request_id, f"Error: {e}")


def tool_button(text: str, tip: str) -> QToolButton:
    b = QToolButton()
    b.setText(text)
    b.setToolTip(tip)
    b.setCursor(Qt.PointingHandCursor)
    return b


class TranslatorWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(make_icon())
        self.settings = QSettings(APP_NAME, APP_NAME)
        self.pool = QThreadPool.globalInstance()
        self.request_id = 0
        self.request_id_audio = 0
        self.detected_lang = ""
        self.last = None  # last Translation
        self.temp_dir = tempfile.TemporaryDirectory(prefix="translator-")

        self.timer = QTimer(self, singleShot=True, interval=TRANSLATE_DELAY_MS)
        self.timer.timeout.connect(self.translate_now)

        self._build_ui()
        self._setup_audio()
        self._restore_settings()

    # ---------- UI ----------

    def _build_ui(self):
        root = QWidget(objectName="root")
        outer = QVBoxLayout(root)
        outer.setContentsMargins(24, 20, 24, 16)
        outer.setSpacing(12)

        # Language bar
        self.source_combo = QComboBox()
        self.source_combo.addItem("Detect language", AUTO)
        self.target_combo = QComboBox()
        for code, name in LANGUAGES:
            self.source_combo.addItem(name, code)
            self.target_combo.addItem(name, code)
        for combo in (self.source_combo, self.target_combo):
            combo.setMaxVisibleItems(20)
            combo.setCursor(Qt.PointingHandCursor)
            combo.currentIndexChanged.connect(self.on_language_changed)

        self.swap_btn = QPushButton("⇄", objectName="swap")
        self.swap_btn.setToolTip("Swap languages (Ctrl+Shift+S)")
        self.swap_btn.setCursor(Qt.PointingHandCursor)
        self.swap_btn.clicked.connect(self.swap)

        bar = QHBoxLayout()
        bar.addWidget(self.source_combo, 1)
        bar.addWidget(self.swap_btn)
        bar.addWidget(self.target_combo, 1)
        outer.addLayout(bar)

        # Source card
        self.source_edit = QPlainTextEdit()
        self.source_edit.setPlaceholderText("Enter text")
        self.source_edit.textChanged.connect(self.on_text_changed)
        self.source_roman = QLabel(objectName="roman", wordWrap=True)
        self.source_roman.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.clear_btn = tool_button("✕", "Clear text")
        self.clear_btn.clicked.connect(self.source_edit.clear)
        self.listen_src_btn = tool_button("🔊", "Listen")
        self.listen_src_btn.clicked.connect(lambda: self.speak("source"))
        self.counter = QLabel(f"0 / {MAX_CHARS:,}", objectName="counter")

        src_tools = QHBoxLayout()
        src_tools.addWidget(self.listen_src_btn)
        src_tools.addStretch()
        src_tools.addWidget(self.counter)
        src_tools.addWidget(self.clear_btn)
        source_card = self._card([self.source_edit, self.source_roman], src_tools)

        # Target card
        self.target_edit = QPlainTextEdit(readOnly=True)
        self.target_edit.setPlaceholderText("Translation")
        self.target_roman = QLabel(objectName="roman", wordWrap=True)
        self.target_roman.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.listen_tgt_btn = tool_button("🔊", "Listen")
        self.listen_tgt_btn.clicked.connect(lambda: self.speak("target"))
        self.copy_btn = tool_button("⧉", "Copy translation")
        self.copy_btn.clicked.connect(self.copy_translation)

        tgt_tools = QHBoxLayout()
        tgt_tools.addWidget(self.listen_tgt_btn)
        tgt_tools.addStretch()
        tgt_tools.addWidget(self.copy_btn)
        target_card = self._card([self.target_edit, self.target_roman], tgt_tools)
        target_card.setProperty("target", True)

        cards = QHBoxLayout()
        cards.setSpacing(16)
        cards.addWidget(source_card, 1)
        cards.addWidget(target_card, 1)
        outer.addLayout(cards, 3)

        # "Did you mean" / detected-language hint, errors, dictionary
        self.hint = QLabel(objectName="hint", textFormat=Qt.RichText)
        self.hint.linkActivated.connect(self.on_hint_link)
        self.hint.hide()
        outer.addWidget(self.hint)

        self.status = QLabel(objectName="status", wordWrap=True)
        self.status.hide()
        outer.addWidget(self.status)

        self.dict_view = QTextBrowser(objectName="dict")
        self.dict_view.setOpenLinks(False)
        self.dict_view.anchorClicked.connect(lambda url: self.source_edit.setPlainText(url.path()))
        self.dict_view.hide()
        outer.addWidget(self.dict_view, 2)

        self.setCentralWidget(root)
        arrow = os.path.join(self.temp_dir.name, "arrow.png")
        make_arrow(arrow)
        self.setStyleSheet(STYLE.replace("ARROW", arrow.replace("\\", "/")))

        QShortcut(QKeySequence("Ctrl+Return"), self, self.translate_now)
        QShortcut(QKeySequence("Ctrl+Shift+S"), self, self.swap)
        QShortcut(QKeySequence("Ctrl+Shift+C"), self, self.copy_translation)
        QShortcut(QKeySequence("Escape"), self, self.source_edit.clear)

        self._update_buttons()

    def _card(self, widgets, tools) -> QFrame:
        card = QFrame(objectName="card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 12, 8, 6)
        for w in widgets:
            lay.addWidget(w)
        lay.setStretch(0, 1)
        lay.addLayout(tools)
        return card

    def _setup_audio(self):
        self.audio_file = None
        self.player = None
        if QMediaPlayer is None:
            return
        self.player = QMediaPlayer(self)
        self.audio_out = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_out)

    # ---------- settings ----------

    def _restore_settings(self):
        self.resize(self.settings.value("size", QSize(1100, 640)))
        for combo, key, default in ((self.source_combo, "source", AUTO), (self.target_combo, "target", "en")):
            i = combo.findData(self.settings.value(key, default))
            combo.blockSignals(True)
            combo.setCurrentIndex(max(i, 0))
            combo.blockSignals(False)

    def closeEvent(self, event):
        self.settings.setValue("size", self.size())
        self.settings.setValue("source", self.source_lang())
        self.settings.setValue("target", self.target_lang())
        if self.player:
            self.player.stop()
            self.player.setSource(QUrl())
        self.temp_dir.cleanup()
        super().closeEvent(event)

    # ---------- helpers ----------

    def source_lang(self) -> str:
        return self.source_combo.currentData()

    def target_lang(self) -> str:
        return self.target_combo.currentData()

    def _update_buttons(self):
        has_src = bool(self.source_edit.toPlainText().strip())
        has_tgt = bool(self.target_edit.toPlainText().strip())
        self.clear_btn.setVisible(has_src)
        self.listen_src_btn.setEnabled(has_src and self.player is not None)
        self.listen_tgt_btn.setEnabled(has_tgt and self.player is not None)
        self.copy_btn.setEnabled(has_tgt)
        self.swap_btn.setEnabled(self.source_lang() != AUTO or bool(self.detected_lang))

    def _set_detected(self, code: str):
        self.detected_lang = code
        self.source_combo.setItemText(0, f"{name_of(code)} - Detected" if code else "Detect language")

    def _show_error(self, message: str):
        self.status.setText(message)
        self.status.setVisible(bool(message))

    def _reset_results(self):
        self.target_edit.clear()
        self.source_roman.clear()
        self.target_roman.clear()
        self.hint.hide()
        self.dict_view.hide()
        self._set_detected("")
        self.last = None

    # ---------- translation ----------

    def on_text_changed(self):
        text = self.source_edit.toPlainText()
        if len(text) > MAX_CHARS:
            cursor = self.source_edit.textCursor()
            pos = min(cursor.position(), MAX_CHARS)
            self.source_edit.blockSignals(True)
            self.source_edit.setPlainText(text[:MAX_CHARS])
            self.source_edit.blockSignals(False)
            cursor = self.source_edit.textCursor()
            cursor.setPosition(pos)
            self.source_edit.setTextCursor(cursor)
            text = text[:MAX_CHARS]
        self.counter.setText(f"{len(text):,} / {MAX_CHARS:,}")

        if not text.strip():
            self.timer.stop()
            self.request_id += 1  # drop any in-flight response
            self._reset_results()
            self._show_error("")
        else:
            self.timer.start()
        self._update_buttons()

    def on_language_changed(self):
        if self.source_lang() != AUTO:
            self._set_detected("")
        self._update_buttons()
        self.translate_now()

    def translate_now(self):
        self.timer.stop()
        text = self.source_edit.toPlainText()
        if not text.strip():
            return
        self.request_id += 1
        if not self.target_edit.toPlainText():
            self.target_edit.setPlaceholderText("Translating...")
        task = Task(self.request_id, google_api.translate, text, self.source_lang(), self.target_lang())
        task.signals.done.connect(self.on_translated)
        task.signals.failed.connect(self.on_failed)
        self.pool.start(task)

    def on_translated(self, request_id: int, result: google_api.Translation):
        if request_id != self.request_id:
            return  # a newer request superseded this one
        self.last = result
        self._show_error("")
        self.target_edit.setPlaceholderText("Translation")
        self.target_edit.setPlainText(result.text)
        self.source_roman.setText(result.source_romanization)
        self.source_roman.setVisible(bool(result.source_romanization))
        self.target_roman.setText(result.target_romanization)
        self.target_roman.setVisible(bool(result.target_romanization))

        if self.source_lang() == AUTO:
            self._set_detected(result.source_lang)

        if result.did_you_mean:
            self.hint.setText(f'Did you mean: <a href="spell"><b><i>{html.escape(result.did_you_mean)}</i></b></a>')
            self.hint.show()
        else:
            self.hint.hide()

        self._show_dictionary(result)
        self._update_buttons()

    def on_failed(self, request_id: int, message: str):
        if request_id != self.request_id:
            return
        self.target_edit.setPlaceholderText("Translation")
        self._show_error(message)

    def on_hint_link(self, link: str):
        if link == "spell" and self.last:
            self.source_edit.setPlainText(self.last.did_you_mean)
            self.source_edit.moveCursor(self.source_edit.textCursor().MoveOperation.End)

    def _show_dictionary(self, result: google_api.Translation):
        if not result.dictionary:
            self.dict_view.hide()
            return
        word = html.escape(self.source_edit.toPlainText().strip())
        parts = [f'<p style="font-size:16px">Translations of <b>{word}</b></p>']
        for entry in result.dictionary:
            parts.append(f'<p style="color:#1a73e8;font-weight:600;margin-top:10px">{html.escape(entry.part_of_speech.capitalize())}</p>')
            parts.append('<table cellspacing="0" cellpadding="3">')
            for translation, reverse in entry.words[:8]:
                links = ", ".join(f'<a href="text:{html.escape(r)}" style="color:#5f6368">{html.escape(r)}</a>' for r in reverse)
                parts.append(f'<tr><td style="padding-right:24px">{html.escape(translation)}</td><td>{links}</td></tr>')
            parts.append("</table>")
        self.dict_view.setHtml("".join(parts))
        self.dict_view.show()

    # ---------- actions ----------

    def swap(self):
        if not self.swap_btn.isEnabled():
            return
        src = self.detected_lang if self.source_lang() == AUTO else self.source_lang()
        tgt = self.target_lang()
        translated = self.target_edit.toPlainText()
        for combo in (self.source_combo, self.target_combo):
            combo.blockSignals(True)
        self.source_combo.setCurrentIndex(self.source_combo.findData(tgt))
        self.target_combo.setCurrentIndex(max(self.target_combo.findData(src), 0))
        for combo in (self.source_combo, self.target_combo):
            combo.blockSignals(False)
        self._set_detected("")
        if translated:
            self.source_edit.setPlainText(translated)
            self.translate_now()
        self._update_buttons()

    def copy_translation(self):
        text = self.target_edit.toPlainText()
        if text:
            QApplication.clipboard().setText(text)
            self.copy_btn.setText("✓")
            QTimer.singleShot(1200, lambda: self.copy_btn.setText("⧉"))

    def speak(self, which: str):
        if self.player is None:
            return
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.stop()
            return
        if which == "source":
            text = self.source_edit.toPlainText()
            lang = self.detected_lang or self.source_lang()
            if lang == AUTO:
                lang = "en"
        else:
            text, lang = self.target_edit.toPlainText(), self.target_lang()
        if not text.strip():
            return
        self.request_id_audio += 1
        task = Task(self.request_id_audio, google_api.speech, text, lang)
        task.signals.done.connect(self.on_audio)
        task.signals.failed.connect(lambda _id, msg: self._show_error(msg))
        self.pool.start(task)

    def on_audio(self, request_id: int, audio: bytes):
        if request_id != self.request_id_audio:
            return
        old = self.audio_file
        fd, self.audio_file = tempfile.mkstemp(suffix=".mp3", dir=self.temp_dir.name)
        with os.fdopen(fd, "wb") as f:
            f.write(audio)
        self.player.setSource(QUrl.fromLocalFile(self.audio_file))
        self.player.play()
        if old:
            try:
                os.remove(old)
            except OSError:
                pass  # still locked by the player on Windows; cleaned up on exit


def main():
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setFont(QFont("Segoe UI", 10))
    window = TranslatorWindow()
    window.show()
    sys.exit(app.exec())
