"""PyQt6 floating UI for RealtimeTranslator."""

import json
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
import numpy as np
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QTextEdit, QSlider, QComboBox, QFrame,
    QCheckBox, QInputDialog, QMessageBox, QLineEdit, QDialog,
    QFormLayout, QDialogButtonBox, QGroupBox, QSystemTrayIcon, QMenu,
    QTabWidget, QFileDialog, QListWidget, QListWidgetItem,
)
from PyQt6.QtCore import Qt, pyqtSignal, QObject
from PyQt6.QtGui import QFont, QIcon, QPixmap, QPainter, QColor, QAction

import sounddevice as sd
from config import (
    SAMPLE_RATE, BLACKHOLE_2CH, BLACKHOLE_16CH,
    LANGUAGES, find_device, get_input_devices,
)
from audio_capture import AudioCapture
from audio_router import AudioRouter, get_current_output
from transcriber import RealtimeTranscriber
from translator import TextTranslator
from tts import TextToSpeech, OPENAI_VOICES
from dotenv import load_dotenv, set_key

PRESETS_FILE = os.path.join(os.path.dirname(__file__), "presets.json")
SESSIONS_DIR = os.path.join(os.path.dirname(__file__), "sessions")
ENV_FILE = os.path.join(os.path.dirname(__file__), ".env")


class PipelineSignals(QObject):
    new_subtitle = pyqtSignal(str, str)
    status_changed = pyqtSignal(str)
    error = pyqtSignal(str)


class SettingsDialog(QDialog):
    """Full settings dialog with tabs: API Keys, Voice, Storage, Presets."""

    DIALOG_STYLE = """
        QDialog { background: #1e1e24; color: #ddd; }
        QTabWidget::pane { border: 1px solid #444; background: #1e1e24; }
        QTabBar::tab { background: #2a2a2e; color: #aaa; padding: 8px 16px; border: 1px solid #444; border-bottom: none; border-top-left-radius: 6px; border-top-right-radius: 6px; margin-right: 2px; }
        QTabBar::tab:selected { background: #1e1e24; color: #fff; }
        QGroupBox { border: 1px solid #444; border-radius: 6px; margin-top: 10px; padding-top: 14px; color: #ccc; font-weight: bold; }
        QGroupBox::title { subcontrol-origin: margin; left: 10px; }
        QLabel { color: #aaa; font-size: 12px; }
        QLineEdit { background: #333; color: #eee; border: 1px solid #555; border-radius: 4px; padding: 6px; font-size: 12px; }
        QPushButton { background: #2d8cf0; color: white; border: none; border-radius: 4px; padding: 6px 14px; font-size: 12px; }
        QPushButton:hover { background: #3a9df5; }
        QComboBox { background: #333; color: #ddd; border: 1px solid #555; border-radius: 4px; padding: 4px 8px; font-size: 12px; }
        QComboBox::drop-down { border: none; }
        QComboBox QAbstractItemView { background: #333; color: #ddd; selection-background-color: #555; }
        QListWidget { background: #2a2a2e; color: #ddd; border: 1px solid #444; border-radius: 4px; font-size: 12px; }
        QListWidget::item { padding: 6px; }
        QListWidget::item:selected { background: #2d8cf0; }
    """

    def __init__(self, parent=None, presets=None):
        super().__init__(parent)
        self._presets = presets if presets is not None else {}
        self._presets_changed = False

        self.setWindowTitle("Configuracoes")
        self.setFixedSize(580, 480)
        self.setStyleSheet(self.DIALOG_STYLE)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_keys_tab(), "API Keys")
        self._tabs.addTab(self._build_voice_tab(), "Voz")
        self._tabs.addTab(self._build_storage_tab(), "Armazenamento")
        self._tabs.addTab(self._build_presets_tab(), "Presets")
        layout.addWidget(self._tabs)

        # Save / Cancel
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ==================== TAB: API KEYS ====================

    def _build_keys_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # Helper to create a key row
        def key_row(placeholder, env_var, signup_url):
            key_input = QLineEdit()
            key_input.setPlaceholderText(placeholder)
            key_input.setEchoMode(QLineEdit.EchoMode.Password)
            key_input.setText(os.getenv(env_var, ""))

            row = QHBoxLayout()
            row.addWidget(key_input)

            show_btn = QPushButton("Mostrar")
            show_btn.setFixedWidth(65)
            show_btn.clicked.connect(lambda: self._toggle_vis(key_input, show_btn))
            row.addWidget(show_btn)

            link_btn = QPushButton("Criar conta")
            link_btn.setFixedWidth(85)
            link_btn.clicked.connect(lambda: subprocess.Popen(["open", signup_url]))
            row.addWidget(link_btn)

            w = QWidget()
            w.setLayout(row)
            return key_input, w

        form = QFormLayout()

        self._deepgram_key, dg_w = key_row(
            "Cole sua chave Deepgram", "DEEPGRAM_API_KEY",
            "https://console.deepgram.com/signup")
        form.addRow("Deepgram (STT):", dg_w)

        info_dg = QLabel("Speech-to-Text em tempo real. Free: $200 credito (~550h)")
        info_dg.setStyleSheet("color: #666; font-size: 10px;")
        form.addRow("", info_dg)

        self._deepl_key, dl_w = key_row(
            "Cole sua chave DeepL", "DEEPL_API_KEY",
            "https://www.deepl.com/pro-api")
        form.addRow("DeepL (Traducao):", dl_w)

        info_dl = QLabel("Traducao de texto. Free: 500k chars/mes")
        info_dl.setStyleSheet("color: #666; font-size: 10px;")
        form.addRow("", info_dl)

        self._openai_key, oa_w = key_row(
            "Cole sua chave OpenAI (opcional)", "OPENAI_API_KEY",
            "https://platform.openai.com/signup")
        form.addRow("OpenAI (TTS):", oa_w)

        info_oa = QLabel("Text-to-Speech premium. Opcional — macOS say e gratis")
        info_oa.setStyleSheet("color: #666; font-size: 10px;")
        form.addRow("", info_oa)

        layout.addLayout(form)
        layout.addStretch()
        return tab

    # ==================== TAB: VOICE ====================

    def _build_voice_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # Engine selection
        engine_group = QGroupBox("Motor de Voz")
        eg_layout = QFormLayout()

        self._tts_engine = QComboBox()
        self._tts_engine.addItems(["macOS (say) — Gratis", "OpenAI TTS — Melhor qualidade"])
        self._tts_engine.currentIndexChanged.connect(self._on_engine_changed)
        eg_layout.addRow("Engine:", self._tts_engine)

        info_engine = QLabel("macOS say: gratis, qualidade razoavel | OpenAI: pago, qualidade excelente")
        info_engine.setStyleSheet("color: #666; font-size: 10px;")
        info_engine.setWordWrap(True)
        eg_layout.addRow("", info_engine)

        engine_group.setLayout(eg_layout)
        layout.addWidget(engine_group)

        # OpenAI options
        self._openai_group = QGroupBox("Opcoes OpenAI TTS")
        og_layout = QFormLayout()

        self._openai_voice_combo = QComboBox()
        self._openai_voice_combo.addItems(OPENAI_VOICES)
        og_layout.addRow("Voz:", self._openai_voice_combo)

        voice_info = QLabel("alloy: neutra | echo: grave | fable: expressiva | nova: feminina | onyx: masculina | shimmer: suave")
        voice_info.setStyleSheet("color: #666; font-size: 10px;")
        voice_info.setWordWrap(True)
        og_layout.addRow("", voice_info)

        self._openai_group.setLayout(og_layout)
        layout.addWidget(self._openai_group)

        # Test button
        test_row = QHBoxLayout()
        self._test_btn = QPushButton("Testar voz")
        self._test_btn.clicked.connect(self._test_voice)
        test_row.addWidget(self._test_btn)
        test_row.addStretch()
        layout.addLayout(test_row)

        layout.addStretch()

        # Load saved
        saved_engine = os.getenv("TTS_ENGINE", "macos")
        if saved_engine == "openai":
            self._tts_engine.setCurrentIndex(1)
        else:
            self._openai_group.setEnabled(False)
        self._openai_voice_combo.setCurrentText(os.getenv("OPENAI_VOICE", "nova"))

        return tab

    # ==================== TAB: STORAGE ====================

    def _build_storage_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # Sessions path
        path_group = QGroupBox("Pasta de Sessoes")
        pg_layout = QVBoxLayout()

        info = QLabel("Transcricoes e traducoes sao salvas como arquivos .txt nesta pasta:")
        info.setWordWrap(True)
        pg_layout.addWidget(info)

        path_row = QHBoxLayout()
        self._sessions_path = QLineEdit()
        self._sessions_path.setText(os.getenv("SESSIONS_PATH", SESSIONS_DIR))
        path_row.addWidget(self._sessions_path)

        browse_btn = QPushButton("Procurar")
        browse_btn.setFixedWidth(80)
        browse_btn.clicked.connect(self._browse_sessions_path)
        path_row.addWidget(browse_btn)

        open_btn = QPushButton("Abrir")
        open_btn.setFixedWidth(60)
        open_btn.clicked.connect(lambda: subprocess.Popen(["open", self._sessions_path.text()]))
        path_row.addWidget(open_btn)

        pg_layout.addLayout(path_row)

        # Format info
        fmt_info = QLabel("Formato: YYYY-MM-DD_HH-MM-SS_IN-OUT.txt")
        fmt_info.setStyleSheet("color: #666; font-size: 10px;")
        pg_layout.addWidget(fmt_info)

        path_group.setLayout(pg_layout)
        layout.addWidget(path_group)

        layout.addStretch()
        return tab

    # ==================== TAB: PRESETS ====================

    def _build_presets_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        info = QLabel("Gerencie seus presets de configuracao. Presets salvam: idiomas, modos, volumes, velocidade.")
        info.setWordWrap(True)
        layout.addWidget(info)

        # List
        self._preset_list = QListWidget()
        self._refresh_preset_list()
        layout.addWidget(self._preset_list)

        # Buttons
        btn_row = QHBoxLayout()

        rename_btn = QPushButton("Renomear")
        rename_btn.clicked.connect(self._rename_preset)
        btn_row.addWidget(rename_btn)

        dup_btn = QPushButton("Duplicar")
        dup_btn.clicked.connect(self._duplicate_preset)
        btn_row.addWidget(dup_btn)

        del_btn = QPushButton("Excluir")
        del_btn.setStyleSheet("QPushButton { background: #c0392b; } QPushButton:hover { background: #e74c3c; }")
        del_btn.clicked.connect(self._delete_preset)
        btn_row.addWidget(del_btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        # Detail view
        self._preset_detail = QTextEdit()
        self._preset_detail.setReadOnly(True)
        self._preset_detail.setMaximumHeight(120)
        self._preset_detail.setStyleSheet("background: #2a2a2e; color: #aaa; border: 1px solid #444; border-radius: 4px; font-size: 11px;")
        layout.addWidget(self._preset_detail)

        self._preset_list.currentRowChanged.connect(self._on_preset_detail)

        return tab

    def _refresh_preset_list(self):
        self._preset_list.clear()
        for name in self._presets:
            self._preset_list.addItem(name)

    def _on_preset_detail(self, row):
        if row < 0:
            self._preset_detail.clear()
            return
        name = self._preset_list.item(row).text()
        cfg = self._presets.get(name, {})
        lines = []
        lines.append(f"Idioma IN: {cfg.get('lang_in', '?')}")
        lines.append(f"Idioma OUT: {cfg.get('lang_out', '?')}")
        modes = []
        if cfg.get("subtitle"): modes.append("Legenda")
        if cfg.get("audio_in"): modes.append("Audio In")
        if cfg.get("mic_out"): modes.append("Mic Out")
        lines.append(f"Modos: {', '.join(modes) if modes else 'Nenhum'}")
        lines.append(f"Vol Original: {cfg.get('original_vol', '?')}%")
        lines.append(f"Vol Traducao: {cfg.get('tts_vol', '?')}%")
        lines.append(f"Velocidade: {cfg.get('tts_speed', '?')}")
        lines.append(f"Mic: {cfg.get('mic', '?')}")
        self._preset_detail.setText("\n".join(lines))

    def _rename_preset(self):
        item = self._preset_list.currentItem()
        if not item:
            return
        old_name = item.text()
        new_name, ok = QInputDialog.getText(self, "Renomear Preset", "Novo nome:", text=old_name)
        if ok and new_name.strip() and new_name.strip() != old_name:
            self._presets[new_name.strip()] = self._presets.pop(old_name)
            self._presets_changed = True
            self._refresh_preset_list()

    def _duplicate_preset(self):
        item = self._preset_list.currentItem()
        if not item:
            return
        name = item.text()
        new_name = f"{name} (copia)"
        self._presets[new_name] = dict(self._presets[name])
        self._presets_changed = True
        self._refresh_preset_list()

    def _delete_preset(self):
        item = self._preset_list.currentItem()
        if not item:
            return
        name = item.text()
        reply = QMessageBox.question(
            self, "Excluir Preset", f"Excluir '{name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            del self._presets[name]
            self._presets_changed = True
            self._refresh_preset_list()
            self._preset_detail.clear()

    # ==================== SHARED HELPERS ====================

    def _toggle_vis(self, line_edit, btn):
        if line_edit.echoMode() == QLineEdit.EchoMode.Password:
            line_edit.setEchoMode(QLineEdit.EchoMode.Normal)
            btn.setText("Ocultar")
        else:
            line_edit.setEchoMode(QLineEdit.EchoMode.Password)
            btn.setText("Mostrar")

    def _on_engine_changed(self, index):
        self._openai_group.setEnabled(index == 1)

    def _browse_sessions_path(self):
        path = QFileDialog.getExistingDirectory(self, "Selecionar pasta de sessoes",
                                                 self._sessions_path.text())
        if path:
            self._sessions_path.setText(path)

    def _test_voice(self):
        engine = "openai" if self._tts_engine.currentIndex() == 1 else "macos"
        api_key = self._openai_key.text().strip()

        if engine == "openai" and not api_key:
            QMessageBox.warning(self, "Aviso", "Insira a chave da OpenAI na aba API Keys.")
            return

        self._test_btn.setText("Testando...")
        self._test_btn.setEnabled(False)

        def test():
            try:
                tts = TextToSpeech(
                    language="pt", engine=engine,
                    openai_api_key=api_key,
                    openai_voice=self._openai_voice_combo.currentText(),
                )
                tts.speak("Ola, este e um teste de voz do tradutor em tempo real.")
            except Exception:
                pass
            self._test_btn.setText("Testar voz")
            self._test_btn.setEnabled(True)

        threading.Thread(target=test, daemon=True).start()

    # ==================== SAVE ====================

    def _save_and_accept(self):
        # Save API keys
        set_key(ENV_FILE, "DEEPGRAM_API_KEY", self._deepgram_key.text().strip())
        set_key(ENV_FILE, "DEEPL_API_KEY", self._deepl_key.text().strip())
        set_key(ENV_FILE, "OPENAI_API_KEY", self._openai_key.text().strip())

        # Save voice
        engine = "openai" if self._tts_engine.currentIndex() == 1 else "macos"
        set_key(ENV_FILE, "TTS_ENGINE", engine)
        set_key(ENV_FILE, "OPENAI_VOICE", self._openai_voice_combo.currentText())

        # Save storage path
        set_key(ENV_FILE, "SESSIONS_PATH", self._sessions_path.text().strip())

        # Reload env
        load_dotenv(ENV_FILE, override=True)

        # Update config module
        import config
        config.DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY", "")
        config.DEEPL_API_KEY = os.getenv("DEEPL_API_KEY", "")
        config.OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

        # Save presets if changed
        if self._presets_changed:
            save_presets(self._presets)

        self.accept()

    # ==================== GETTERS ====================

    def get_tts_engine(self) -> str:
        return "openai" if self._tts_engine.currentIndex() == 1 else "macos"

    def get_openai_voice(self) -> str:
        return self._openai_voice_combo.currentText()

    def get_sessions_path(self) -> str:
        return self._sessions_path.text().strip()

    def get_presets(self) -> dict:
        return self._presets


class FloatingSubtitle(QMainWindow):
    """Transparent floating subtitle overlay that shows on screen."""

    def __init__(self):
        super().__init__()
        self._drag_pos = None
        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumWidth(500)
        self.resize(700, 80)

        # Position at bottom center of screen
        screen = QApplication.primaryScreen().geometry()
        self.move(
            (screen.width() - 700) // 2,
            screen.height() - 140,
        )

        central = QWidget()
        central.setObjectName("floatingSub")
        self.setCentralWidget(central)

        layout = QVBoxLayout(central)
        layout.setContentsMargins(16, 8, 16, 8)

        self._original_label = QLabel("")
        self._original_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._original_label.setWordWrap(True)
        self._original_label.setStyleSheet("color: rgba(200,200,200,0.7); font-size: 13px;")
        layout.addWidget(self._original_label)

        self._translated_label = QLabel("")
        self._translated_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._translated_label.setWordWrap(True)
        self._translated_label.setStyleSheet("color: rgba(255,255,255,0.95); font-size: 18px; font-weight: bold;")
        layout.addWidget(self._translated_label)

        self.setStyleSheet("""
            #floatingSub {
                background: rgba(0, 0, 0, 0.65);
                border-radius: 12px;
            }
        """)

    def update_text(self, original: str, translated: str):
        self._original_label.setText(original)
        self._translated_label.setText(translated)
        self.adjustSize()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = e.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, e):
        if self._drag_pos and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, e):
        self._drag_pos = None


def load_presets() -> dict:
    if os.path.exists(PRESETS_FILE):
        with open(PRESETS_FILE) as f:
            return json.load(f)
    return {}


def save_presets(presets: dict):
    with open(PRESETS_FILE, "w") as f:
        json.dump(presets, f, indent=2)


class TranslatorWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.signals = PipelineSignals()
        self._drag_pos = None

        # State
        self._running = False
        self._paused = False
        self._original_vol = 0
        self._tts_vol = 80
        self._tts_speed = 220
        self._tts_engine = os.getenv("TTS_ENGINE", "macos")
        self._openai_voice = os.getenv("OPENAI_VOICE", "nova")

        # Logging
        self._log_file = None
        self._save_transcription = False
        self._save_translation = False

        # Audio router
        self._router = AudioRouter()
        self._output_device_name = None

        # Pipeline components
        self._incoming_capture = None
        self._incoming_transcriber = None
        self._outgoing_capture = None
        self._outgoing_transcriber = None
        self._translator_in = None
        self._translator_out = None
        self._tts_in = None
        self._tts_out = None
        self._tts_lock = threading.Lock()
        self._passthrough_stream = None

        # Floating subtitle overlay
        self._floating_sub = FloatingSubtitle()

        # Presets
        self._presets = load_presets()
        self._sessions_path = os.getenv("SESSIONS_PATH", SESSIONS_DIR)

        self._detect_output_device()
        self._setup_ui()
        self._connect_signals()

    def _detect_output_device(self):
        current = get_current_output()
        if current and "blackhole" not in current.lower():
            self._output_device_name = current
        else:
            devices = sd.query_devices()
            for i, dev in enumerate(devices):
                if dev["max_output_channels"] > 0 and "blackhole" not in dev["name"].lower():
                    self._output_device_name = dev["name"]
                    break

    def _get_output_device_index(self) -> int:
        if self._output_device_name:
            idx = find_device(self._output_device_name, kind="output")
            if idx is not None:
                return idx
        return sd.default.device[1]

    def _setup_ui(self):
        self.setWindowTitle("RealtimeTranslator")
        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumSize(620, 560)
        self.resize(700, 580)

        central = QWidget()
        central.setObjectName("central")
        self.setCentralWidget(central)

        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ==================== TITLE BAR ====================
        title_bar = QWidget()
        title_bar.setObjectName("titleBar")
        title_bar.setFixedHeight(40)
        tb = QHBoxLayout(title_bar)
        tb.setContentsMargins(12, 0, 12, 0)

        self._status_dot = QLabel()
        self._status_dot.setFixedSize(10, 10)
        self._status_dot.setStyleSheet("background: #666; border-radius: 5px;")
        tb.addWidget(self._status_dot)

        title = QLabel("  RealtimeTranslator")
        title.setStyleSheet("color: #ccc; font-size: 13px; font-weight: bold;")
        tb.addWidget(title)
        tb.addStretch()

        cfg_btn = QPushButton("Config")
        cfg_btn.setFixedSize(50, 26)
        cfg_btn.setStyleSheet(
            "QPushButton { color: #aaa; background: #333; border: 1px solid #555; border-radius: 4px; font-size: 11px; }"
            "QPushButton:hover { color: white; background: #444; }"
        )
        cfg_btn.clicked.connect(self._open_settings)
        tb.addWidget(cfg_btn)

        tb.addSpacing(8)

        for text, slot, hc in [("—", self.showMinimized, "white"), ("x", self._on_close, "#ff5555")]:
            b = QPushButton(text)
            b.setFixedSize(30, 30)
            b.setStyleSheet(f"QPushButton {{ color: #aaa; background: transparent; border: none; font-size: 16px; }} QPushButton:hover {{ color: {hc}; }}")
            b.clicked.connect(slot)
            tb.addWidget(b)

        layout.addWidget(title_bar)

        # ==================== PRESET ROW ====================
        preset_row = QWidget()
        preset_row.setObjectName("settingsRow")
        pr = QHBoxLayout(preset_row)
        pr.setContentsMargins(12, 8, 12, 4)
        pr.setSpacing(8)

        pr.addWidget(self._lbl("Preset:"))
        self._preset_combo = QComboBox()
        self._preset_combo.addItem("-- Selecionar --")
        for name in self._presets:
            self._preset_combo.addItem(name)
        self._preset_combo.setFixedWidth(180)
        self._preset_combo.currentIndexChanged.connect(self._on_preset_selected)
        pr.addWidget(self._preset_combo)

        btn_save_preset = QPushButton("Salvar")
        btn_save_preset.setFixedHeight(26)
        btn_save_preset.setStyleSheet(self._small_btn_style("#2d8cf0"))
        btn_save_preset.clicked.connect(self._save_preset)
        pr.addWidget(btn_save_preset)

        btn_del_preset = QPushButton("Excluir")
        btn_del_preset.setFixedHeight(26)
        btn_del_preset.setStyleSheet(self._small_btn_style("#c0392b"))
        btn_del_preset.clicked.connect(self._delete_preset)
        pr.addWidget(btn_del_preset)

        pr.addStretch()
        layout.addWidget(preset_row)

        # ==================== LANGUAGE ROW ====================
        lang_row = QWidget()
        lang_row.setObjectName("settingsRow")
        lr = QHBoxLayout(lang_row)
        lr.setContentsMargins(12, 4, 12, 4)
        lr.setSpacing(8)

        lr.addWidget(self._lbl("Idioma IN:"))
        self._lang_in = QComboBox()
        for name in LANGUAGES:
            self._lang_in.addItem(name)
        self._lang_in.setCurrentText("English")
        self._lang_in.setFixedWidth(140)
        lr.addWidget(self._lang_in)

        lr.addSpacing(12)

        lr.addWidget(self._lbl("Idioma OUT:"))
        self._lang_out = QComboBox()
        for name in LANGUAGES:
            self._lang_out.addItem(name)
        self._lang_out.setCurrentText("Portugues (BR)")
        self._lang_out.setFixedWidth(140)
        lr.addWidget(self._lang_out)

        lr.addStretch()
        layout.addWidget(lang_row)

        # ==================== DEVICE ROW ====================
        dev_row = QWidget()
        dev_row.setObjectName("settingsRow")
        dr = QHBoxLayout(dev_row)
        dr.setContentsMargins(12, 4, 12, 4)
        dr.setSpacing(8)

        dr.addWidget(self._lbl("Mic:"))
        self._mic_combo = QComboBox()
        self._mic_devices = get_input_devices()
        default_in = sd.default.device[0]
        default_idx = 0
        for i, (dev_idx, dev_name) in enumerate(self._mic_devices):
            if "blackhole" not in dev_name.lower():
                self._mic_combo.addItem(dev_name)
                if dev_idx == default_in:
                    default_idx = self._mic_combo.count() - 1
        self._mic_combo.setCurrentIndex(default_idx)
        self._mic_combo.setFixedWidth(200)
        dr.addWidget(self._mic_combo)

        dr.addStretch()
        layout.addWidget(dev_row)

        # ==================== VOLUME ROW 1: Speaker Original ====================
        vr1_w = QWidget()
        vr1_w.setObjectName("settingsRow")
        vr1 = QHBoxLayout(vr1_w)
        vr1.setContentsMargins(12, 6, 12, 2)
        vr1.setSpacing(8)

        l1 = QLabel("Speaker Original")
        l1.setStyleSheet("color: #e07c3a; font-size: 12px; font-weight: bold;")
        l1.setFixedWidth(130)
        vr1.addWidget(l1)

        self._vol_original = QSlider(Qt.Orientation.Horizontal)
        self._vol_original.setRange(0, 100)
        self._vol_original.setValue(self._original_vol)
        self._vol_original.setFixedWidth(160)
        self._vol_original.setStyleSheet(self._slider_style("#e07c3a"))
        self._vol_original.valueChanged.connect(self._on_original_vol_changed)
        vr1.addWidget(self._vol_original)

        self._vol_original_lbl = QLabel(f"{self._original_vol}%")
        self._vol_original_lbl.setStyleSheet("color: #e07c3a; font-size: 12px;")
        self._vol_original_lbl.setFixedWidth(35)
        vr1.addWidget(self._vol_original_lbl)

        self._mute_original_btn = QPushButton("MUTE")
        self._mute_original_btn.setCheckable(True)
        self._mute_original_btn.setChecked(True)
        self._mute_original_btn.setFixedSize(50, 24)
        self._mute_original_btn.setStyleSheet(
            "QPushButton { background: #c0392b; color: white; border: none; border-radius: 4px; font-size: 10px; font-weight: bold; }"
            "QPushButton:!checked { background: #555; color: #aaa; }"
        )
        self._mute_original_btn.clicked.connect(self._on_mute_original)
        vr1.addWidget(self._mute_original_btn)

        vr1.addStretch()
        layout.addWidget(vr1_w)

        # ==================== VOLUME ROW 2: Tradutor + Speed ====================
        vr2_w = QWidget()
        vr2_w.setObjectName("settingsRow")
        vr2 = QHBoxLayout(vr2_w)
        vr2.setContentsMargins(12, 2, 12, 4)
        vr2.setSpacing(8)

        l2 = QLabel("Voz Tradutor")
        l2.setStyleSheet("color: #2d8cf0; font-size: 12px; font-weight: bold;")
        l2.setFixedWidth(130)
        vr2.addWidget(l2)

        self._vol_tts = QSlider(Qt.Orientation.Horizontal)
        self._vol_tts.setRange(0, 100)
        self._vol_tts.setValue(self._tts_vol)
        self._vol_tts.setFixedWidth(160)
        self._vol_tts.setStyleSheet(self._slider_style("#2d8cf0"))
        self._vol_tts.valueChanged.connect(self._on_tts_vol_changed)
        vr2.addWidget(self._vol_tts)

        self._vol_tts_lbl = QLabel(f"{self._tts_vol}%")
        self._vol_tts_lbl.setStyleSheet("color: #2d8cf0; font-size: 12px;")
        self._vol_tts_lbl.setFixedWidth(35)
        vr2.addWidget(self._vol_tts_lbl)

        vr2.addSpacing(12)

        ls = QLabel("Velocidade:")
        ls.setStyleSheet("color: #27ae60; font-size: 12px; font-weight: bold;")
        vr2.addWidget(ls)

        self._speed_slider = QSlider(Qt.Orientation.Horizontal)
        self._speed_slider.setRange(100, 400)
        self._speed_slider.setValue(self._tts_speed)
        self._speed_slider.setFixedWidth(90)
        self._speed_slider.setStyleSheet(self._slider_style("#27ae60"))
        self._speed_slider.valueChanged.connect(self._on_speed_changed)
        vr2.addWidget(self._speed_slider)

        self._speed_lbl = QLabel(f"{self._tts_speed}")
        self._speed_lbl.setStyleSheet("color: #27ae60; font-size: 12px;")
        self._speed_lbl.setFixedWidth(30)
        vr2.addWidget(self._speed_lbl)

        vr2.addStretch()
        layout.addWidget(vr2_w)

        # ==================== SEPARATOR ====================
        layout.addWidget(self._sep())

        # ==================== MODE TOGGLES ====================
        ctrl_w = QWidget()
        ctrl_w.setObjectName("controls")
        cl = QHBoxLayout(ctrl_w)
        cl.setContentsMargins(12, 8, 12, 4)
        cl.setSpacing(8)

        self._btn_subtitle = self._toggle_btn("Legenda")
        cl.addWidget(self._btn_subtitle)

        self._btn_audio_in = self._toggle_btn("Audio In")
        cl.addWidget(self._btn_audio_in)

        self._btn_mic_out = self._toggle_btn("Mic Out")
        cl.addWidget(self._btn_mic_out)

        cl.addStretch()
        layout.addWidget(ctrl_w)

        # ==================== SAVE OPTIONS + START ====================
        action_w = QWidget()
        action_w.setObjectName("controls")
        al = QHBoxLayout(action_w)
        al.setContentsMargins(12, 4, 12, 8)
        al.setSpacing(8)

        self._chk_save_transcription = QCheckBox("Salvar Transcricao")
        self._chk_save_transcription.setStyleSheet("color: #aaa; font-size: 11px;")
        al.addWidget(self._chk_save_transcription)

        self._chk_save_translation = QCheckBox("Salvar Traducao")
        self._chk_save_translation.setStyleSheet("color: #aaa; font-size: 11px;")
        al.addWidget(self._chk_save_translation)

        al.addStretch()

        # PAUSE button
        self._btn_pause = QPushButton("Pause")
        self._btn_pause.setFixedSize(70, 34)
        self._btn_pause.setCheckable(True)
        self._btn_pause.setEnabled(False)
        self._btn_pause.setStyleSheet(self._btn_style("#555", "#ff9500"))
        self._btn_pause.clicked.connect(self._toggle_pause)
        al.addWidget(self._btn_pause)

        # START / STOP button
        self._btn_start = QPushButton("INICIAR")
        self._btn_start.setFixedSize(100, 34)
        self._btn_start.setStyleSheet(
            "QPushButton { background: #27ae60; color: white; border: none; border-radius: 6px; "
            "font-size: 14px; font-weight: bold; }"
            "QPushButton:hover { background: #2ecc71; }"
        )
        self._btn_start.clicked.connect(self._toggle_start)
        al.addWidget(self._btn_start)

        layout.addWidget(action_w)

        # ==================== SEPARATOR ====================
        layout.addWidget(self._sep())

        # ==================== SUBTITLE AREA ====================
        self._subtitle_area = QTextEdit()
        self._subtitle_area.setReadOnly(True)
        self._subtitle_area.setObjectName("subtitleArea")
        self._subtitle_area.setFont(QFont("SF Pro", 14))
        layout.addWidget(self._subtitle_area)

        # ==================== STATUS BAR ====================
        sb_w = QWidget()
        sb_w.setObjectName("statusBar")
        sb_w.setFixedHeight(28)
        sbl = QHBoxLayout(sb_w)
        sbl.setContentsMargins(12, 0, 12, 0)

        self._status_label = QLabel("Pronto — configure e clique INICIAR")
        self._status_label.setStyleSheet("color: #888; font-size: 11px;")
        sbl.addWidget(self._status_label)

        self._output_label = QLabel(f"Output: {self._output_device_name or '?'}")
        self._output_label.setStyleSheet("color: #666; font-size: 10px;")
        sbl.addStretch()
        sbl.addWidget(self._output_label)

        layout.addWidget(sb_w)

        # ==================== STYLESHEET ====================
        self.setStyleSheet("""
            #central {
                background: rgba(25, 25, 30, 0.92);
                border-radius: 12px; border: 1px solid #333;
            }
            #titleBar {
                background: rgba(35, 35, 40, 0.95);
                border-top-left-radius: 12px; border-top-right-radius: 12px;
            }
            #settingsRow, #controls {
                background: rgba(30, 30, 35, 0.9);
            }
            #subtitleArea {
                background: transparent; color: #eee; border: none; padding: 12px;
            }
            #statusBar {
                background: rgba(20, 20, 25, 0.9);
                border-bottom-left-radius: 12px; border-bottom-right-radius: 12px;
            }
            QComboBox {
                background: #333; color: #ddd; border: 1px solid #555;
                border-radius: 6px; padding: 4px 8px; font-size: 12px;
            }
            QComboBox::drop-down { border: none; }
            QComboBox QAbstractItemView {
                background: #333; color: #ddd; selection-background-color: #555;
            }
            QCheckBox { spacing: 5px; }
            QCheckBox::indicator {
                width: 14px; height: 14px; border-radius: 3px;
                border: 1px solid #555; background: #333;
            }
            QCheckBox::indicator:checked {
                background: #2d8cf0; border: 1px solid #2d8cf0;
            }
        """)

    # ==================== UI HELPERS ====================

    def _lbl(self, text):
        l = QLabel(text)
        l.setStyleSheet("color: #999; font-size: 12px;")
        return l

    def _sep(self):
        s = QFrame()
        s.setFrameShape(QFrame.Shape.HLine)
        s.setStyleSheet("color: #333;")
        return s

    def _toggle_btn(self, text):
        btn = QPushButton(text)
        btn.setCheckable(True)
        btn.setFixedHeight(32)
        btn.setStyleSheet(self._btn_style("#444", "#2d8cf0"))
        return btn

    def _slider_style(self, color):
        return f"""
            QSlider::groove:horizontal {{ height: 6px; background: #444; border-radius: 3px; }}
            QSlider::handle:horizontal {{ width: 14px; height: 14px; margin: -4px 0; background: {color}; border-radius: 7px; }}
            QSlider::sub-page:horizontal {{ background: {color}; border-radius: 3px; }}
        """

    def _btn_style(self, off, on):
        return f"""
            QPushButton {{ background: {off}; color: #ccc; border: 1px solid #555;
                border-radius: 6px; padding: 4px 14px; font-size: 12px; font-weight: bold; }}
            QPushButton:hover {{ background: #555; }}
            QPushButton:checked {{ background: {on}; color: white; border: 1px solid {on}; }}
        """

    def _small_btn_style(self, color):
        return f"""
            QPushButton {{ background: {color}; color: white; border: none;
                border-radius: 4px; padding: 2px 10px; font-size: 11px; }}
            QPushButton:hover {{ opacity: 0.8; }}
        """

    def _set_dot(self, color):
        self._status_dot.setStyleSheet(f"background: {color}; border-radius: 5px;")

    # ==================== SIGNALS ====================

    def _connect_signals(self):
        self.signals.new_subtitle.connect(self._add_subtitle)
        self.signals.status_changed.connect(self._set_status)
        self.signals.error.connect(self._set_error)

    def _add_subtitle(self, original, translated):
        src = self._lang_in.currentText()[:2].upper()
        tgt = self._lang_out.currentText()[:2].upper()
        self._subtitle_area.append(
            f'<span style="color: #888; font-size: 12px;">[{src}] {original}</span>'
        )
        self._subtitle_area.append(
            f'<span style="color: #fff; font-size: 15px; font-weight: bold;">[{tgt}] {translated}</span>'
        )
        self._subtitle_area.append("")
        bar = self._subtitle_area.verticalScrollBar()
        bar.setValue(bar.maximum())

        # Update floating subtitle
        self._floating_sub.update_text(
            f"[{src}] {original}",
            f"[{tgt}] {translated}",
        )

        # Log to file
        self._log_entry(src, original, tgt, translated)

    def _set_status(self, text):
        self._status_label.setText(text)
        self._status_label.setStyleSheet("color: #888; font-size: 11px;")

    def _set_error(self, text):
        self._status_label.setText(f"ERRO: {text}")
        self._status_label.setStyleSheet("color: #ff5555; font-size: 11px;")

    # ==================== DRAGGING ====================

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and e.position().y() < 40:
            self._drag_pos = e.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, e):
        if self._drag_pos and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, e):
        self._drag_pos = None

    # ==================== PRESETS ====================

    def _get_current_config(self) -> dict:
        return {
            "lang_in": self._lang_in.currentText(),
            "lang_out": self._lang_out.currentText(),
            "mic": self._mic_combo.currentText(),
            "subtitle": self._btn_subtitle.isChecked(),
            "audio_in": self._btn_audio_in.isChecked(),
            "mic_out": self._btn_mic_out.isChecked(),
            "original_vol": self._vol_original.value(),
            "tts_vol": self._vol_tts.value(),
            "tts_speed": self._speed_slider.value(),
            "save_transcription": self._chk_save_transcription.isChecked(),
            "save_translation": self._chk_save_translation.isChecked(),
        }

    def _apply_config(self, config: dict):
        if "lang_in" in config:
            self._lang_in.setCurrentText(config["lang_in"])
        if "lang_out" in config:
            self._lang_out.setCurrentText(config["lang_out"])
        if "mic" in config:
            idx = self._mic_combo.findText(config["mic"])
            if idx >= 0:
                self._mic_combo.setCurrentIndex(idx)
        if "subtitle" in config:
            self._btn_subtitle.setChecked(config["subtitle"])
        if "audio_in" in config:
            self._btn_audio_in.setChecked(config["audio_in"])
        if "mic_out" in config:
            self._btn_mic_out.setChecked(config["mic_out"])
        if "original_vol" in config:
            self._vol_original.setValue(config["original_vol"])
        if "tts_vol" in config:
            self._vol_tts.setValue(config["tts_vol"])
        if "tts_speed" in config:
            self._speed_slider.setValue(config["tts_speed"])
        if "save_transcription" in config:
            self._chk_save_transcription.setChecked(config["save_transcription"])
        if "save_translation" in config:
            self._chk_save_translation.setChecked(config["save_translation"])

    def _open_settings(self):
        dialog = SettingsDialog(self, presets=self._presets)
        if dialog.exec():
            self._tts_engine = dialog.get_tts_engine()
            self._openai_voice = dialog.get_openai_voice()
            self._sessions_path = dialog.get_sessions_path()

            # Update presets (may have been renamed/deleted)
            self._presets = dialog.get_presets()
            self._refresh_preset_combo()
            self._rebuild_tray_menu()

            self.signals.status_changed.emit("Configuracoes salvas")

    def _on_preset_selected(self, index):
        if index <= 0:
            return
        name = self._preset_combo.currentText()
        if name in self._presets:
            self._apply_config(self._presets[name])

    def _refresh_preset_combo(self):
        """Rebuild the preset dropdown from current presets dict."""
        self._preset_combo.blockSignals(True)
        self._preset_combo.clear()
        self._preset_combo.addItem("-- Selecionar --")
        for name in self._presets:
            self._preset_combo.addItem(name)
        self._preset_combo.blockSignals(False)

    def _save_preset(self):
        name, ok = QInputDialog.getText(self, "Salvar Preset", "Nome do preset:")
        if ok and name.strip():
            name = name.strip()
            self._presets[name] = self._get_current_config()
            save_presets(self._presets)
            # Update combo
            if self._preset_combo.findText(name) < 0:
                self._preset_combo.addItem(name)
            self._preset_combo.setCurrentText(name)
            self.signals.status_changed.emit(f"Preset '{name}' salvo")

    def _delete_preset(self):
        name = self._preset_combo.currentText()
        if name == "-- Selecionar --":
            return
        if name in self._presets:
            del self._presets[name]
            save_presets(self._presets)
            idx = self._preset_combo.findText(name)
            if idx >= 0:
                self._preset_combo.removeItem(idx)
            self._preset_combo.setCurrentIndex(0)
            self.signals.status_changed.emit(f"Preset '{name}' excluido")

    # ==================== UI EVENTS ====================

    def _on_original_vol_changed(self, v):
        self._original_vol = v
        self._vol_original_lbl.setText(f"{v}%")
        if v > 0 and self._mute_original_btn.isChecked():
            self._mute_original_btn.setChecked(False)

    def _on_mute_original(self):
        if self._mute_original_btn.isChecked():
            self._original_vol = 0
            self._vol_original.setValue(0)
        else:
            self._original_vol = 50
            self._vol_original.setValue(50)

    def _on_tts_vol_changed(self, v):
        self._tts_vol = v
        self._vol_tts_lbl.setText(f"{v}%")

    def _on_speed_changed(self, v):
        self._tts_speed = v
        self._speed_lbl.setText(f"{v}")
        if self._tts_in:
            self._tts_in.rate = v
        if self._tts_out:
            self._tts_out.rate = v

    def _toggle_pause(self):
        self._paused = self._btn_pause.isChecked()
        if self._paused:
            self._btn_pause.setText("Resume")
            self._stop_pipeline()
            self._set_dot("#ff9500")
            self.signals.status_changed.emit("Pausado")
        else:
            self._btn_pause.setText("Pause")
            self._start_pipeline()

    def _toggle_start(self):
        if self._running:
            self._stop_all()
        else:
            self._start_all()

    def _start_all(self):
        subtitle = self._btn_subtitle.isChecked()
        audio_in = self._btn_audio_in.isChecked()
        mic_out = self._btn_mic_out.isChecked()

        if not subtitle and not audio_in and not mic_out:
            self.signals.status_changed.emit("Selecione pelo menos um modo")
            return

        # Ask to clear subtitle area if it has content
        if self._subtitle_area.toPlainText().strip():
            reply = QMessageBox.question(
                self, "Limpar transcricao",
                "Deseja limpar a transcricao anterior?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._subtitle_area.clear()

        self._running = True
        self._save_transcription = self._chk_save_transcription.isChecked()
        self._save_translation = self._chk_save_translation.isChecked()

        # Open log file (always append — never lose data)
        if self._save_transcription or self._save_translation:
            self._open_log_file()

        # Update UI
        self._btn_start.setText("PARAR")
        self._btn_start.setStyleSheet(
            "QPushButton { background: #c0392b; color: white; border: none; border-radius: 6px; "
            "font-size: 14px; font-weight: bold; }"
            "QPushButton:hover { background: #e74c3c; }"
        )
        self._btn_pause.setEnabled(True)
        self._set_controls_enabled(False)

        self._start_pipeline()
        self._floating_sub.show()
        self._update_tray_icon()
        self._rebuild_tray_menu()

    def _stop_all(self):
        self._running = False
        self._paused = False
        self._btn_pause.setChecked(False)
        self._btn_pause.setText("Pause")
        self._btn_pause.setEnabled(False)

        self._stop_pipeline()
        self._close_log_file()
        self._floating_sub.hide()

        # Ask to clear subtitle area
        if self._subtitle_area.toPlainText().strip():
            reply = QMessageBox.question(
                self, "Limpar transcricao",
                "Deseja limpar a transcricao?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._subtitle_area.clear()

        # Update UI
        self._btn_start.setText("INICIAR")
        self._btn_start.setStyleSheet(
            "QPushButton { background: #27ae60; color: white; border: none; border-radius: 6px; "
            "font-size: 14px; font-weight: bold; }"
            "QPushButton:hover { background: #2ecc71; }"
        )
        self._set_controls_enabled(True)
        self._set_dot("#666")
        self.signals.status_changed.emit("Pronto — configure e clique INICIAR")
        self._update_tray_icon()
        self._rebuild_tray_menu()

    def _set_controls_enabled(self, enabled):
        self._lang_in.setEnabled(enabled)
        self._lang_out.setEnabled(enabled)
        self._mic_combo.setEnabled(enabled)
        self._btn_subtitle.setEnabled(enabled)
        self._btn_audio_in.setEnabled(enabled)
        self._btn_mic_out.setEnabled(enabled)
        self._chk_save_transcription.setEnabled(enabled)
        self._chk_save_translation.setEnabled(enabled)
        self._preset_combo.setEnabled(enabled)

    def _get_selected_mic_index(self) -> int:
        mic_name = self._mic_combo.currentText()
        for dev_idx, dev_name in self._mic_devices:
            if dev_name == mic_name:
                return dev_idx
        return sd.default.device[0]

    # ==================== LOGGING ====================

    def _open_log_file(self):
        sessions_dir = self._sessions_path or SESSIONS_DIR
        os.makedirs(sessions_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        src = self._lang_in.currentText()[:2]
        tgt = self._lang_out.currentText()[:2]
        filename = f"{ts}_{src}-{tgt}.txt"
        path = os.path.join(sessions_dir, filename)
        self._log_file = open(path, "w", encoding="utf-8")
        self._log_file.write(f"# RealtimeTranslator Session\n")
        self._log_file.write(f"# {datetime.now().isoformat()}\n")
        self._log_file.write(f"# {self._lang_in.currentText()} -> {self._lang_out.currentText()}\n\n")
        self.signals.status_changed.emit(f"Log: {filename}")

    def _close_log_file(self):
        if self._log_file:
            self._log_file.close()
            self._log_file = None

    def _log_entry(self, src_label, original, tgt_label, translated):
        if not self._log_file:
            return
        ts = datetime.now().strftime("%H:%M:%S")
        if self._save_transcription:
            self._log_file.write(f"[{ts}] [{src_label}] {original}\n")
        if self._save_translation:
            self._log_file.write(f"[{ts}] [{tgt_label}] {translated}\n")
        if self._save_transcription or self._save_translation:
            self._log_file.write("\n")
            self._log_file.flush()

    # ==================== PIPELINE ====================

    def _start_pipeline(self):
        self._set_dot("#ffcc00")
        self.signals.status_changed.emit("Conectando...")

        def start():
            try:
                lang_in = LANGUAGES[self._lang_in.currentText()]
                lang_out = LANGUAGES[self._lang_out.currentText()]
                mic_dev = self._get_selected_mic_index()
                output_dev = self._get_output_device_index()

                subtitle = self._btn_subtitle.isChecked()
                audio_in = self._btn_audio_in.isChecked()
                mic_out = self._btn_mic_out.isChecked()

                # --- AUDIO ROUTING ---
                if audio_in:
                    self._router.redirect_output_to_blackhole()
                if mic_out:
                    self._router.redirect_input_to_blackhole()

                # --- INCOMING PATH ---
                if subtitle or audio_in:
                    self._translator_in = TextTranslator(
                        source_lang=lang_in["deepl_src"],
                        target_lang=lang_out["deepl_tgt"],
                    )
                    if audio_in:
                        self._tts_in = TextToSpeech(
                            language=lang_out["tts"], rate=self._tts_speed,
                            engine=self._tts_engine,
                            openai_api_key=os.getenv("OPENAI_API_KEY", ""),
                            openai_voice=self._openai_voice,
                        )

                    self._incoming_transcriber = RealtimeTranscriber(
                        language=lang_in["stt"],
                        on_transcript=self._on_incoming,
                    )
                    self._incoming_transcriber.start()
                    time.sleep(0.5)

                    self._incoming_capture = AudioCapture(BLACKHOLE_2CH)
                    if audio_in:
                        self._start_passthrough(output_dev)
                        self._incoming_capture.start(self._on_audio_data)
                    else:
                        self._incoming_capture.start(self._incoming_transcriber.send)

                # --- OUTGOING PATH ---
                if mic_out:
                    self._translator_out = TextTranslator(
                        source_lang=lang_out["deepl_src"],
                        target_lang=lang_in["deepl_tgt"],
                    )
                    self._tts_out = TextToSpeech(
                        language=lang_in["tts"], rate=self._tts_speed,
                        engine=self._tts_engine,
                        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
                        openai_voice=self._openai_voice,
                    )

                    self._outgoing_transcriber = RealtimeTranscriber(
                        language=lang_out["stt"],
                        on_transcript=self._on_outgoing,
                    )
                    self._outgoing_transcriber.start()
                    time.sleep(0.5)

                    self._outgoing_capture = AudioCapture.__new__(AudioCapture)
                    self._outgoing_capture.device_index = mic_dev
                    self._outgoing_capture.sample_rate = SAMPLE_RATE
                    self._outgoing_capture.channels = 1
                    self._outgoing_capture.block_size = 4096
                    self._outgoing_capture.stream = None
                    self._outgoing_capture._callback = None
                    self._outgoing_capture.start(self._outgoing_transcriber.send)

                # Status
                self._set_dot("#00cc66")
                modes = []
                if subtitle: modes.append("Legenda")
                if audio_in: modes.append("Audio In")
                if mic_out: modes.append("Mic Out")
                self.signals.status_changed.emit(
                    f"Ativo: {', '.join(modes)} | "
                    f"{self._lang_in.currentText()} -> {self._lang_out.currentText()}"
                )
            except Exception as e:
                self.signals.error.emit(str(e))
                self._set_dot("#ff5555")
                self._router.restore()

        threading.Thread(target=start, daemon=True).start()

    def _start_passthrough(self, output_dev):
        self._passthrough_stream = sd.OutputStream(
            device=output_dev,
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=4096,
        )
        self._passthrough_stream.start()

    def _stop_passthrough(self):
        if self._passthrough_stream:
            try:
                self._passthrough_stream.stop()
                self._passthrough_stream.close()
            except Exception:
                pass
            self._passthrough_stream = None

    def _on_audio_data(self, audio_bytes):
        if self._incoming_transcriber:
            self._incoming_transcriber.send(audio_bytes)

        vol = self._original_vol / 100.0
        if vol > 0 and self._passthrough_stream:
            data = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32767.0
            data = (data * vol).reshape(-1, 1)
            try:
                self._passthrough_stream.write(data)
            except Exception:
                pass

    def _stop_pipeline(self):
        self._stop_passthrough()

        if self._incoming_capture:
            self._incoming_capture.stop()
            self._incoming_capture = None
        if self._outgoing_capture:
            self._outgoing_capture.stop()
            self._outgoing_capture = None
        if self._incoming_transcriber:
            self._incoming_transcriber.stop()
            self._incoming_transcriber = None
        if self._outgoing_transcriber:
            self._outgoing_transcriber.stop()
            self._outgoing_transcriber = None
        self._translator_in = None
        self._translator_out = None
        self._tts_in = None
        self._tts_out = None

        self._router.restore()

    # ==================== TRANSCRIPTION CALLBACKS ====================

    def _on_incoming(self, text, is_final):
        if not is_final or not text.strip():
            return
        try:
            translated = self._translator_in.translate(text)
            if self._btn_subtitle.isChecked():
                self.signals.new_subtitle.emit(text, translated)
            if self._btn_audio_in.isChecked() and translated:
                threading.Thread(
                    target=self._speak_in, args=(translated,), daemon=True
                ).start()
        except Exception as e:
            self.signals.error.emit(str(e))

    def _on_outgoing(self, text, is_final):
        if not is_final or not text.strip():
            return
        try:
            translated = self._translator_out.translate(text)
            if self._btn_subtitle.isChecked():
                self.signals.new_subtitle.emit(f"[MIC] {text}", f"[MIC] {translated}")
            virtual_mic = find_device(BLACKHOLE_16CH, kind="output")
            if translated and virtual_mic is not None:
                threading.Thread(
                    target=self._speak_out, args=(translated, virtual_mic), daemon=True
                ).start()
        except Exception as e:
            self.signals.error.emit(str(e))

    def _speak_in(self, text):
        with self._tts_lock:
            try:
                gain = self._tts_vol / 80.0
                dev = self._get_output_device_index()
                self._tts_in.speak_to_device(text, device_index=dev, gain=gain)
            except Exception as e:
                self.signals.error.emit(f"TTS: {e}")

    def _speak_out(self, text, device):
        with self._tts_lock:
            try:
                self._tts_out.speak_to_device(text, device)
            except Exception as e:
                self.signals.error.emit(f"TTS: {e}")

    # ==================== SYSTEM TRAY ====================

    def setup_tray(self):
        """Create system tray icon with menu."""
        self._tray = QSystemTrayIcon(self)
        self._tray.setIcon(self._create_tray_icon("#2d8cf0"))
        self._tray.setToolTip("RealtimeTranslator")
        self._tray.activated.connect(self._on_tray_activated)

        self._rebuild_tray_menu()
        self._tray.show()

    def _create_tray_icon(self, color):
        """Create a simple colored circle icon for the tray."""
        px = QPixmap(32, 32)
        px.fill(QColor(0, 0, 0, 0))
        painter = QPainter(px)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor(color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(4, 4, 24, 24)
        # "T" letter
        painter.setPen(QColor("white"))
        font = QFont("Arial", 14, QFont.Weight.Bold)
        painter.setFont(font)
        painter.drawText(px.rect(), Qt.AlignmentFlag.AlignCenter, "T")
        painter.end()
        return QIcon(px)

    def _rebuild_tray_menu(self):
        """Rebuild the tray right-click menu with current presets."""
        menu = QMenu()
        menu.setStyleSheet("""
            QMenu { background: #2a2a2e; color: #ddd; border: 1px solid #444; padding: 4px; }
            QMenu::item { padding: 6px 20px; }
            QMenu::item:selected { background: #2d8cf0; }
            QMenu::separator { height: 1px; background: #444; margin: 4px 8px; }
        """)

        # Show/Hide window
        show_action = menu.addAction("Mostrar janela")
        show_action.triggered.connect(self._show_window)

        menu.addSeparator()

        # Status
        if self._running:
            status = menu.addAction("● Ativo")
            status.setEnabled(False)

            stop_action = menu.addAction("Parar")
            stop_action.triggered.connect(self._stop_all)
        else:
            status = menu.addAction("○ Inativo")
            status.setEnabled(False)

        menu.addSeparator()

        # Presets
        presets_menu = menu.addMenu("Presets")
        if self._presets:
            for name in self._presets:
                action = presets_menu.addAction(name)
                action.triggered.connect(lambda checked, n=name: self._activate_preset_from_tray(n))
        else:
            no_presets = presets_menu.addAction("(nenhum salvo)")
            no_presets.setEnabled(False)

        menu.addSeparator()

        # Config
        config_action = menu.addAction("Configuracoes")
        config_action.triggered.connect(self._open_settings)

        menu.addSeparator()

        # Quit
        quit_action = menu.addAction("Sair")
        quit_action.triggered.connect(self._quit_app)

        self._tray.setContextMenu(menu)

    def _on_tray_activated(self, reason):
        # Only show context menu, never auto-open the window
        pass

    def _show_window(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def _activate_preset_from_tray(self, preset_name):
        """Load a preset and start from tray menu."""
        if preset_name in self._presets:
            self._apply_config(self._presets[preset_name])
            if not self._running:
                self._start_all()
            self._rebuild_tray_menu()

    def _update_tray_icon(self):
        """Update tray icon color based on state."""
        if self._running:
            self._tray.setIcon(self._create_tray_icon("#00cc66"))
            self._tray.setToolTip("RealtimeTranslator — Ativo")
        else:
            self._tray.setIcon(self._create_tray_icon("#2d8cf0"))
            self._tray.setToolTip("RealtimeTranslator")

    # ==================== CLOSE ====================

    def _on_close(self):
        """Close button hides to tray instead of quitting."""
        self.hide()

    def closeEvent(self, event):
        """Window close hides to tray."""
        event.ignore()
        self.hide()

    def _quit_app(self):
        """Actually quit the app (from tray menu)."""
        self._stop_pipeline()
        self._close_log_file()
        self._router.restore()
        self._tray.hide()
        QApplication.quit()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("RealtimeTranslator")
    app.setQuitOnLastWindowClosed(False)  # Keep running in tray

    window = TranslatorWindow()
    window.setup_tray()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
