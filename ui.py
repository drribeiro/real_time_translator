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
from PyQt6.QtCore import Qt, pyqtSignal, QObject, QRectF, QPropertyAnimation, pyqtProperty, QEasingCurve
from PyQt6.QtGui import QFont, QIcon, QPixmap, QPainter, QColor, QAction, QBrush, QPen

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
from app_logger import get_logger, setup_logger
from dotenv import load_dotenv, set_key

PRESETS_FILE = os.path.join(os.path.dirname(__file__), "presets.json")
SESSIONS_DIR = os.path.join(os.path.dirname(__file__), "sessions")
ENV_FILE = os.path.join(os.path.dirname(__file__), ".env")


class AudioLevelMeter(QWidget):
    """Animated audio level meter with bouncing bars."""

    def __init__(self, color="#00cc66", label="", bar_count=12, parent=None):
        super().__init__(parent)
        self._color = QColor(color)
        self._label = label
        self._level = 0.0  # 0.0 to 1.0
        self._peak = 0.0
        self._bar_count = bar_count
        self.setFixedHeight(28)
        self.setMinimumWidth(120)

    def set_level(self, level: float):
        """Set level 0.0 to 1.0."""
        self._level = max(0.0, min(1.0, level))
        self._peak = max(self._level, self._peak * 0.95)  # slow peak decay
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()

        # Label
        label_w = 0
        if self._label:
            p.setPen(QPen(QColor("#888")))
            p.setFont(QFont("SF Pro", 9))
            label_w = p.fontMetrics().horizontalAdvance(self._label) + 6
            p.drawText(0, h // 2 + 4, self._label)

        # Bars area
        bar_area_w = w - label_w
        bar_w = max(2, (bar_area_w - (self._bar_count - 1) * 2) // self._bar_count)
        bar_h = h - 4
        active_bars = int(self._level * self._bar_count)
        peak_bar = int(self._peak * self._bar_count)

        for i in range(self._bar_count):
            x = label_w + i * (bar_w + 2)
            rect = QRectF(x, 2, bar_w, bar_h)

            if i < active_bars:
                # Color gradient: green -> yellow -> red
                t = i / self._bar_count
                if t < 0.6:
                    c = self._color
                elif t < 0.8:
                    c = QColor("#f1c40f")
                else:
                    c = QColor("#e74c3c")
                p.setBrush(QBrush(c))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(rect, 2, 2)
            else:
                # Inactive bar
                p.setBrush(QBrush(QColor("#2a2a30")))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(rect, 2, 2)

            # Peak indicator
            if i == peak_bar and peak_bar > 0:
                p.setBrush(QBrush(QColor(255, 255, 255, 180)))
                p.drawRoundedRect(QRectF(x, 2, bar_w, 3), 1, 1)

        p.end()


class ToggleSwitch(QWidget):
    """macOS-style toggle switch widget."""
    toggled = pyqtSignal(bool)

    def __init__(self, label="", color="#2d8cf0", checked=False, parent=None):
        super().__init__(parent)
        self._checked = checked
        self._color = QColor(color)
        self._label = label
        self._knob_x = 1.0 if not checked else 20.0
        self._anim = None
        self.setFixedSize(self._calc_width(), 32)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def _calc_width(self):
        if self._label:
            font = QFont("SF Pro", 12)
            font.setBold(True)
            from PyQt6.QtGui import QFontMetrics
            fm = QFontMetrics(font)
            text_w = fm.horizontalAdvance(self._label)
            return 44 + 14 + text_w + 4
        return 44

    def isChecked(self):
        return self._checked

    def setChecked(self, val):
        if self._checked == val:
            return
        self._checked = val
        self._animate()

    def setEnabled(self, val):
        super().setEnabled(val)
        self.update()

    def _get_knob_x(self):
        return self._knob_x

    def _set_knob_x(self, val):
        self._knob_x = val
        self.update()

    knob_position = pyqtProperty(float, _get_knob_x, _set_knob_x)

    def _animate(self):
        self._anim = QPropertyAnimation(self, b"knob_position")
        self._anim.setDuration(150)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._anim.setStartValue(self._knob_x)
        self._anim.setEndValue(20.0 if self._checked else 1.0)
        self._anim.start()

    def mousePressEvent(self, e):
        if not self.isEnabled():
            return
        self._checked = not self._checked
        self._animate()
        self.toggled.emit(self._checked)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Track
        track_rect = QRectF(0, 2, 42, 24)
        if self._checked and self.isEnabled():
            p.setBrush(QBrush(self._color))
        elif self.isEnabled():
            p.setBrush(QBrush(QColor("#444")))
        else:
            p.setBrush(QBrush(QColor("#333")))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(track_rect, 12, 12)

        # Knob
        knob_y = 4.0
        knob_size = 20.0
        if self.isEnabled():
            p.setBrush(QBrush(QColor("white")))
        else:
            p.setBrush(QBrush(QColor("#666")))
        p.drawEllipse(QRectF(self._knob_x + 1, knob_y, knob_size, knob_size))

        # Label
        if self._label:
            if self.isEnabled():
                p.setPen(QPen(QColor("#ccc")))
            else:
                p.setPen(QPen(QColor("#555")))
            font = QFont("SF Pro", 12)
            font.setBold(True)
            p.setFont(font)
            p.drawText(54, 18, self._label)

        p.end()


class PipelineSignals(QObject):
    new_subtitle = pyqtSignal(str, str, str)  # (original, translated, source)
    status_changed = pyqtSignal(str)
    error = pyqtSignal(str)
    audio_level = pyqtSignal(float)   # incoming audio level 0-1
    mic_level = pyqtSignal(float)     # mic audio level 0-1


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
        self.setMinimumSize(620, 520)
        self.resize(720, 600)
        self.setStyleSheet(self.DIALOG_STYLE)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_keys_tab(), "API Keys")
        self._tabs.addTab(self._build_voice_tab(), "Voz")
        self._tabs.addTab(self._build_storage_tab(), "Armazenamento")
        self._tabs.addTab(self._build_presets_tab(), "Presets")
        self._tabs.addTab(self._build_performance_tab(), "Performance")
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
        path_group = QGroupBox("Pasta de Transcricoes")
        pg_layout = QVBoxLayout()

        info = QLabel("Transcricoes e traducoes sao salvas como arquivos .txt nesta pasta:")
        info.setWordWrap(True)
        pg_layout.addWidget(info)

        path_row = QHBoxLayout()
        self._sessions_path_input = QLineEdit()
        self._sessions_path_input.setText(os.getenv("SESSIONS_PATH", SESSIONS_DIR))
        path_row.addWidget(self._sessions_path_input)

        browse_btn = QPushButton("Procurar")
        browse_btn.setFixedWidth(80)
        browse_btn.clicked.connect(lambda: self._browse_path(self._sessions_path_input))
        path_row.addWidget(browse_btn)

        open_btn = QPushButton("Abrir")
        open_btn.setFixedWidth(60)
        open_btn.clicked.connect(lambda: subprocess.Popen(["open", self._sessions_path_input.text()]))
        path_row.addWidget(open_btn)

        pg_layout.addLayout(path_row)

        fmt_info = QLabel("Formato: YYYY-MM-DD_HH-MM-SS_IN-OUT.txt")
        fmt_info.setStyleSheet("color: #666; font-size: 10px;")
        pg_layout.addWidget(fmt_info)

        path_group.setLayout(pg_layout)
        layout.addWidget(path_group)

        # Logs path
        logs_group = QGroupBox("Pasta de Logs (debug)")
        lg_layout = QVBoxLayout()

        logs_info = QLabel("Logs de execucao do app (erros, conexoes, eventos). Util para debug:")
        logs_info.setWordWrap(True)
        lg_layout.addWidget(logs_info)

        logs_row = QHBoxLayout()
        default_logs = os.getenv("LOGS_PATH", os.path.join(os.path.dirname(__file__), "logs"))
        self._logs_path_input = QLineEdit()
        self._logs_path_input.setText(default_logs)
        logs_row.addWidget(self._logs_path_input)

        logs_browse = QPushButton("Procurar")
        logs_browse.setFixedWidth(80)
        logs_browse.clicked.connect(lambda: self._browse_path(self._logs_path_input))
        logs_row.addWidget(logs_browse)

        logs_open = QPushButton("Abrir")
        logs_open.setFixedWidth(60)
        logs_open.clicked.connect(lambda: subprocess.Popen(["open", self._logs_path_input.text()]))
        logs_row.addWidget(logs_open)

        lg_layout.addLayout(logs_row)

        self._enable_logs = QCheckBox("Ativar logs de debug")
        self._enable_logs.setChecked(os.getenv("ENABLE_LOGS", "false").lower() == "true")
        self._enable_logs.setStyleSheet("color: #aaa; font-size: 12px;")
        lg_layout.addWidget(self._enable_logs)

        logs_group.setLayout(lg_layout)
        layout.addWidget(logs_group)

        layout.addStretch()
        return tab

    # ==================== TAB: PRESETS ====================

    # ==================== TAB: PERFORMANCE ====================

    def _build_performance_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # Audio buffer
        buf_group = QGroupBox("Captura de Audio")
        bg = QFormLayout()

        self._block_size_combo = QComboBox()
        self._block_size_combo.addItems(["1024 (64ms - menor delay)", "2048 (128ms - recomendado)", "4096 (256ms - mais estavel)"])
        saved_block = int(os.getenv("BLOCK_SIZE", "2048"))
        idx = {1024: 0, 2048: 1, 4096: 2}.get(saved_block, 1)
        self._block_size_combo.setCurrentIndex(idx)
        bg.addRow("Buffer size:", self._block_size_combo)

        buf_info = QLabel("Menor buffer = menor delay, mas pode causar falhas de audio em maquinas lentas")
        buf_info.setStyleSheet("color: #666; font-size: 10px;")
        buf_info.setWordWrap(True)
        bg.addRow("", buf_info)

        buf_group.setLayout(bg)
        layout.addWidget(buf_group)

        # STT settings
        stt_group = QGroupBox("Speech-to-Text (Deepgram)")
        sg = QFormLayout()

        self._endpointing_spin = QComboBox()
        self._endpointing_spin.addItems(["100ms (agressivo)", "150ms (recomendado)", "200ms (equilibrado)", "300ms (conservador)", "500ms (muito conservador)"])
        saved_ep = int(os.getenv("ENDPOINTING_MS", "150"))
        ep_idx = {100: 0, 150: 1, 200: 2, 300: 3, 500: 4}.get(saved_ep, 1)
        self._endpointing_spin.setCurrentIndex(ep_idx)
        sg.addRow("Endpointing:", self._endpointing_spin)

        ep_info = QLabel("Tempo de silencio (ms) para considerar fim de frase. Menor = frases mais curtas e rapidas")
        ep_info.setStyleSheet("color: #666; font-size: 10px;")
        ep_info.setWordWrap(True)
        sg.addRow("", ep_info)

        self._interim_check = QCheckBox("Mostrar texto parcial na legenda (interim results)")
        self._interim_check.setChecked(os.getenv("INTERIM_SUBTITLES", "true").lower() == "true")
        self._interim_check.setStyleSheet("color: #aaa; font-size: 12px;")
        sg.addRow("", self._interim_check)

        interim_info = QLabel("Mostra texto na legenda antes de finalizar a frase. Mais responsivo, mas texto pode mudar")
        interim_info.setStyleSheet("color: #666; font-size: 10px;")
        interim_info.setWordWrap(True)
        sg.addRow("", interim_info)

        self._stt_model_combo = QComboBox()
        self._stt_model_combo.addItems(["nova-2 (recomendado)", "nova-3 (mais recente)"])
        saved_model = os.getenv("STT_MODEL", "nova-2")
        self._stt_model_combo.setCurrentIndex(1 if saved_model == "nova-3" else 0)
        sg.addRow("Modelo:", self._stt_model_combo)

        stt_group.setLayout(sg)
        layout.addWidget(stt_group)

        # Translation settings
        trl_group = QGroupBox("Traducao")
        tg = QFormLayout()

        self._translate_interim = QCheckBox("Traduzir textos parciais (mais rapido, usa mais API)")
        self._translate_interim.setChecked(os.getenv("TRANSLATE_INTERIM", "false").lower() == "true")
        self._translate_interim.setStyleSheet("color: #aaa; font-size: 12px;")
        tg.addRow("", self._translate_interim)

        trl_info = QLabel("Se ativado, traduz cada resultado parcial do STT. Legendas mais rapidas mas consome mais quota do DeepL")
        trl_info.setStyleSheet("color: #666; font-size: 10px;")
        trl_info.setWordWrap(True)
        tg.addRow("", trl_info)

        trl_group.setLayout(tg)
        layout.addWidget(trl_group)

        # Latency estimate
        est_group = QGroupBox("Estimativa de Latencia")
        eg = QVBoxLayout()
        self._latency_label = QLabel("")
        self._latency_label.setStyleSheet("color: #2d8cf0; font-size: 12px;")
        self._latency_label.setWordWrap(True)
        eg.addWidget(self._latency_label)
        est_group.setLayout(eg)
        layout.addWidget(est_group)

        # Update estimate on change
        self._block_size_combo.currentIndexChanged.connect(self._update_latency_estimate)
        self._endpointing_spin.currentIndexChanged.connect(self._update_latency_estimate)
        self._update_latency_estimate()

        layout.addStretch()
        return tab

    def _update_latency_estimate(self):
        block_sizes = [1024, 2048, 4096]
        endpointings = [100, 150, 200, 300, 500]
        block = block_sizes[self._block_size_combo.currentIndex()]
        ep = endpointings[self._endpointing_spin.currentIndex()]

        buffer_ms = block / 16  # 16 samples per ms at 16kHz
        stt_ms = 200 + ep  # base STT + endpointing wait
        translate_ms = 200  # avg DeepL
        tts_ms = 600  # avg macOS say

        subtitle_total = buffer_ms + stt_ms + translate_ms
        audio_total = subtitle_total + tts_ms

        self._latency_label.setText(
            f"Legenda: ~{int(subtitle_total)}ms  |  "
            f"Audio traduzido: ~{int(audio_total)}ms\n"
            f"(Buffer: {int(buffer_ms)}ms + STT: ~{int(stt_ms)}ms + "
            f"Traducao: ~{translate_ms}ms + TTS: ~{tts_ms}ms)"
        )

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

    def _browse_path(self, line_edit):
        path = QFileDialog.getExistingDirectory(self, "Selecionar pasta", line_edit.text())
        if path:
            line_edit.setText(path)

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

        # Save storage paths
        set_key(ENV_FILE, "SESSIONS_PATH", self._sessions_path_input.text().strip())
        set_key(ENV_FILE, "LOGS_PATH", self._logs_path_input.text().strip())
        set_key(ENV_FILE, "ENABLE_LOGS", "true" if self._enable_logs.isChecked() else "false")

        # Save performance settings
        block_sizes = ["1024", "2048", "4096"]
        endpointings = ["100", "150", "200", "300", "500"]
        set_key(ENV_FILE, "BLOCK_SIZE", block_sizes[self._block_size_combo.currentIndex()])
        set_key(ENV_FILE, "ENDPOINTING_MS", endpointings[self._endpointing_spin.currentIndex()])
        set_key(ENV_FILE, "INTERIM_SUBTITLES", "true" if self._interim_check.isChecked() else "false")
        set_key(ENV_FILE, "TRANSLATE_INTERIM", "true" if self._translate_interim.isChecked() else "false")
        stt_models = ["nova-2", "nova-3"]
        set_key(ENV_FILE, "STT_MODEL", stt_models[self._stt_model_combo.currentIndex()])

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
        return self._sessions_path_input.text().strip()

    def get_logs_path(self) -> str:
        return self._logs_path_input.text().strip()

    def get_enable_logs(self) -> bool:
        return self._enable_logs.isChecked()

    def get_presets(self) -> dict:
        return self._presets


class FloatingSubtitle(QMainWindow):
    """Transparent floating subtitle overlay that shows on screen."""

    MAX_LINES = 4

    def __init__(self):
        super().__init__()
        self._drag_pos = None
        self._lines = []  # list of (original, translated)
        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        screen = QApplication.primaryScreen().geometry()
        w = int(screen.width() * 0.75)
        self.setMinimumSize(400, 100)
        self.resize(w, 200)
        self.move(
            (screen.width() - w) // 2,
            screen.height() - 250,
        )

        central = QWidget()
        central.setObjectName("floatingSub")
        self.setCentralWidget(central)

        layout = QVBoxLayout(central)
        layout.setContentsMargins(20, 12, 20, 12)
        layout.setSpacing(4)

        self._text_area = QTextEdit()
        self._text_area.setReadOnly(True)
        self._text_area.setStyleSheet(
            "QTextEdit { background: transparent; border: none; color: #fff; }"
        )
        self._text_area.setFont(QFont("SF Pro", 14))
        self._text_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._text_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(self._text_area)

        # Resize grip at bottom-right
        grip = QLabel()
        grip.setFixedSize(16, 16)
        grip.setStyleSheet("color: rgba(255,255,255,0.3); font-size: 12px;")
        grip.setText("⟡")
        grip.setAlignment(Qt.AlignmentFlag.AlignRight)
        layout.addWidget(grip, 0, Qt.AlignmentFlag.AlignRight)

        self.setStyleSheet("""
            #floatingSub {
                background: rgba(0, 0, 0, 0.70);
                border-radius: 14px;
                border: 1px solid rgba(255, 255, 255, 0.08);
            }
        """)

        # Enable resize from edges
        self._resize_edge = None
        self._resize_margin = 8

    def update_text(self, original: str, translated: str):
        self._lines.append((original, translated))
        if len(self._lines) > self.MAX_LINES:
            self._lines = self._lines[-self.MAX_LINES:]
        self._render_lines()

    def _render_lines(self):
        html = ""
        for orig, trans in self._lines:
            html += f'<p style="margin: 2px 0;"><span style="color: rgba(200,200,200,0.6); font-size: 12px;">{orig}</span></p>'
            html += f'<p style="margin: 2px 0 8px 0;"><span style="color: rgba(255,255,255,0.95); font-size: 16px; font-weight: bold;">{trans}</span></p>'
        self._text_area.setHtml(html)
        sb = self._text_area.verticalScrollBar()
        sb.setValue(sb.maximum())

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            edge = self._hit_test_edge(e.position())
            if edge:
                self._resize_edge = edge
                self._drag_pos = e.globalPosition().toPoint()
            else:
                self._resize_edge = None
                self._drag_pos = e.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, e):
        if not self._drag_pos:
            # Update cursor on hover
            edge = self._hit_test_edge(e.position())
            if edge in ("left", "right"):
                self.setCursor(Qt.CursorShape.SizeHorCursor)
            elif edge in ("top", "bottom"):
                self.setCursor(Qt.CursorShape.SizeVerCursor)
            elif edge:
                self.setCursor(Qt.CursorShape.SizeFDiagCursor)
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)
            return

        if self._resize_edge:
            delta = e.globalPosition().toPoint() - self._drag_pos
            self._drag_pos = e.globalPosition().toPoint()
            geo = self.geometry()

            if "right" in self._resize_edge:
                geo.setRight(geo.right() + delta.x())
            if "left" in self._resize_edge:
                geo.setLeft(geo.left() + delta.x())
            if "bottom" in self._resize_edge:
                geo.setBottom(geo.bottom() + delta.y())
            if "top" in self._resize_edge:
                geo.setTop(geo.top() + delta.y())

            if geo.width() >= self.minimumWidth() and geo.height() >= self.minimumHeight():
                self.setGeometry(geo)
        elif e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, e):
        self._drag_pos = None
        self._resize_edge = None

    def _hit_test_edge(self, pos):
        m = self._resize_margin
        w, h = self.width(), self.height()
        x, y = pos.x(), pos.y()

        edges = ""
        if y < m:
            edges += "top"
        elif y > h - m:
            edges += "bottom"
        if x < m:
            edges += "left"
        elif x > w - m:
            edges += "right"
        return edges or None


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
        self._interim_subtitles = os.getenv("INTERIM_SUBTITLES", "true").lower() == "true"
        self._translate_interim = os.getenv("TRANSLATE_INTERIM", "false").lower() == "true"
        self._endpointing_ms = int(os.getenv("ENDPOINTING_MS", "150"))
        self._stt_model = os.getenv("STT_MODEL", "nova-2")

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
        )
        screen = QApplication.primaryScreen().geometry()
        h = int(screen.height() * 0.90)
        self.setMinimumWidth(400)
        self.resize(screen.width(), h)
        self.move(0, (screen.height() - h) // 2)

        central = QWidget()
        central.setObjectName("central")
        self.setCentralWidget(central)

        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ==================== TITLE BAR ====================
        title_bar = QWidget()
        title_bar.setObjectName("titleBar")
        title_bar.setFixedHeight(44)
        tb = QHBoxLayout(title_bar)
        tb.setContentsMargins(16, 0, 12, 0)

        self._status_dot = QLabel()
        self._status_dot.setFixedSize(12, 12)
        self._status_dot.setStyleSheet("background: #666; border-radius: 6px;")
        tb.addWidget(self._status_dot)

        title = QLabel("  RealtimeTranslator")
        title.setStyleSheet("color: #eee; font-size: 14px; font-weight: bold; letter-spacing: 0.5px;")
        tb.addWidget(title)
        tb.addStretch()

        cfg_btn = QPushButton("Config")
        cfg_btn.setFixedSize(60, 28)
        cfg_btn.setStyleSheet(
            "QPushButton { color: #bbb; background: #3a3a40; border: 1px solid #555; border-radius: 6px; font-size: 11px; font-weight: bold; }"
            "QPushButton:hover { color: white; background: #4a4a50; }"
        )
        cfg_btn.clicked.connect(self._open_settings)
        tb.addWidget(cfg_btn)

        layout.addWidget(title_bar)

        # ==================== PRESET SECTION ====================
        preset_section = QWidget()
        preset_section.setObjectName("presetSection")
        ps = QVBoxLayout(preset_section)
        ps.setContentsMargins(20, 14, 20, 14)
        ps.setSpacing(10)

        # Preset header
        ps_header = QHBoxLayout()
        ps_title = QLabel("PRESET")
        ps_title.setStyleSheet("color: #2d8cf0; font-size: 11px; font-weight: bold; letter-spacing: 2px;")
        ps_header.addWidget(ps_title)
        ps_header.addStretch()
        ps.addLayout(ps_header)

        # Preset controls
        ps_row = QHBoxLayout()
        ps_row.setSpacing(10)

        self._preset_combo = QComboBox()
        self._preset_combo.addItem("-- Nenhum preset --")
        for name in self._presets:
            self._preset_combo.addItem(name)
        self._preset_combo.setMinimumWidth(220)
        self._preset_combo.currentIndexChanged.connect(self._on_preset_selected)
        ps_row.addWidget(self._preset_combo)

        # "Criar preset" — visible when no preset selected
        self._btn_create_preset = QPushButton("Criar preset")
        self._btn_create_preset.setFixedHeight(30)
        self._btn_create_preset.setStyleSheet(self._small_btn_style("#27ae60"))
        self._btn_create_preset.clicked.connect(self._save_preset_as)
        ps_row.addWidget(self._btn_create_preset)

        # "Salvar" — visible when a preset is selected
        self._btn_save_preset = QPushButton("Salvar")
        self._btn_save_preset.setFixedHeight(30)
        self._btn_save_preset.setToolTip("Sobrescrever o preset selecionado")
        self._btn_save_preset.setStyleSheet(self._small_btn_style("#2d8cf0"))
        self._btn_save_preset.clicked.connect(self._save_preset)
        self._btn_save_preset.hide()
        ps_row.addWidget(self._btn_save_preset)

        # "Salvar como..." — visible when a preset is selected
        self._btn_saveas_preset = QPushButton("Salvar como...")
        self._btn_saveas_preset.setFixedHeight(30)
        self._btn_saveas_preset.setStyleSheet(self._small_btn_style("#8e44ad"))
        self._btn_saveas_preset.clicked.connect(self._save_preset_as)
        self._btn_saveas_preset.hide()
        ps_row.addWidget(self._btn_saveas_preset)

        # "Excluir" — visible when a preset is selected
        self._btn_del_preset = QPushButton("Excluir")
        self._btn_del_preset.setFixedHeight(30)
        self._btn_del_preset.setStyleSheet(self._small_btn_style("#c0392b"))
        self._btn_del_preset.clicked.connect(self._delete_preset)
        self._btn_del_preset.hide()
        ps_row.addWidget(self._btn_del_preset)

        ps_row.addStretch()
        ps.addLayout(ps_row)

        # Preset summary
        self._preset_summary = QLabel("")
        self._preset_summary.setStyleSheet("color: #666; font-size: 11px; padding: 2px 0;")
        self._preset_summary.setWordWrap(True)
        ps.addWidget(self._preset_summary)

        # Audio level meters
        meters_row = QHBoxLayout()
        meters_row.setSpacing(16)

        self._audio_meter = AudioLevelMeter(color="#00cc66", label="Audio")
        meters_row.addWidget(self._audio_meter)

        self._mic_meter = AudioLevelMeter(color="#e74c3c", label="Mic")
        meters_row.addWidget(self._mic_meter)

        meters_row.addStretch()
        ps.addLayout(meters_row)

        layout.addWidget(preset_section)

        layout.addWidget(self._sep())

        # ==================== LANGUAGE + DEVICE SECTION ====================
        config_section = QWidget()
        config_section.setObjectName("configSection")
        cs = QVBoxLayout(config_section)
        cs.setContentsMargins(20, 14, 20, 14)
        cs.setSpacing(12)

        # Section title
        cs_title = QLabel("IDIOMAS E DISPOSITIVO")
        cs_title.setStyleSheet("color: #27ae60; font-size: 11px; font-weight: bold; letter-spacing: 2px; margin-bottom: 25px;")
        cs.addWidget(cs_title)

        # Language row
        lang_row = QHBoxLayout()
        lang_row.setSpacing(12)

        lang_row.addWidget(self._field_label("Idioma de entrada"))
        self._lang_in = QComboBox()
        for name in LANGUAGES:
            self._lang_in.addItem(name)
        self._lang_in.setCurrentText("English")
        self._lang_in.setMinimumWidth(160)
        lang_row.addWidget(self._lang_in)

        arrow = QLabel("  ->  ")
        arrow.setStyleSheet("color: #555; font-size: 16px; font-weight: bold;")
        lang_row.addWidget(arrow)

        lang_row.addWidget(self._field_label("Idioma de saida"))
        self._lang_out = QComboBox()
        for name in LANGUAGES:
            self._lang_out.addItem(name)
        self._lang_out.setCurrentText("Portugues (BR)")
        self._lang_out.setMinimumWidth(160)
        lang_row.addWidget(self._lang_out)

        lang_row.addStretch()
        cs.addLayout(lang_row)

        # Mic row
        mic_row = QHBoxLayout()
        mic_row.setSpacing(12)

        mic_row.addWidget(self._field_label("Microfone"))
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
        self._mic_combo.setMinimumWidth(260)
        mic_row.addWidget(self._mic_combo)

        mic_row.addStretch()
        cs.addLayout(mic_row)

        layout.addWidget(config_section)

        layout.addWidget(self._sep())

        # ==================== VOLUME SECTION ====================
        vol_section = QWidget()
        vol_section.setObjectName("configSection")
        vs = QVBoxLayout(vol_section)
        vs.setContentsMargins(24, 16, 24, 16)
        vs.setSpacing(6)

        vs_title = QLabel("VOLUMES E VELOCIDADE")
        vs_title.setStyleSheet("color: #e07c3a; font-size: 11px; font-weight: bold; letter-spacing: 2px; margin-bottom: 25px;")
        vs.addWidget(vs_title)

        # Speaker Original
        v1_header = QHBoxLayout()
        v1_label = QLabel("Speaker Original")
        v1_label.setStyleSheet("color: #e07c3a; font-size: 14px; font-weight: bold;")
        v1_header.addWidget(v1_label)
        v1_header.addStretch()
        self._vol_original_lbl = QLabel(f"{self._original_vol}%")
        self._vol_original_lbl.setStyleSheet("color: #e07c3a; font-size: 14px; font-weight: bold;")
        v1_header.addWidget(self._vol_original_lbl)
        self._mute_original_btn = ToggleSwitch("Mute", "#c0392b", checked=True)
        self._mute_original_btn.toggled.connect(lambda _: self._on_mute_original())
        v1_header.addWidget(self._mute_original_btn)
        vs.addLayout(v1_header)

        self._vol_original = QSlider(Qt.Orientation.Horizontal)
        self._vol_original.setRange(0, 100)
        self._vol_original.setValue(self._original_vol)
        self._vol_original.setFixedHeight(28)
        self._vol_original.setStyleSheet(self._slider_style("#e07c3a"))
        self._vol_original.valueChanged.connect(self._on_original_vol_changed)
        vs.addWidget(self._vol_original)
        vs.addSpacing(14)

        # Voz Tradutor
        v2_header = QHBoxLayout()
        v2_label = QLabel("Voz Tradutor")
        v2_label.setStyleSheet("color: #2d8cf0; font-size: 14px; font-weight: bold;")
        v2_header.addWidget(v2_label)
        v2_header.addStretch()
        self._vol_tts_lbl = QLabel(f"{self._tts_vol}%")
        self._vol_tts_lbl.setStyleSheet("color: #2d8cf0; font-size: 14px; font-weight: bold;")
        v2_header.addWidget(self._vol_tts_lbl)
        vs.addLayout(v2_header)

        self._vol_tts = QSlider(Qt.Orientation.Horizontal)
        self._vol_tts.setRange(0, 100)
        self._vol_tts.setValue(self._tts_vol)
        self._vol_tts.setFixedHeight(28)
        self._vol_tts.setStyleSheet(self._slider_style("#2d8cf0"))
        self._vol_tts.valueChanged.connect(self._on_tts_vol_changed)
        vs.addWidget(self._vol_tts)
        vs.addSpacing(14)

        # Velocidade da Fala
        v3_header = QHBoxLayout()
        v3_label = QLabel("Velocidade da Fala")
        v3_label.setStyleSheet("color: #27ae60; font-size: 14px; font-weight: bold;")
        v3_header.addWidget(v3_label)
        v3_header.addStretch()
        self._speed_lbl = QLabel(f"{self._tts_speed}")
        self._speed_lbl.setStyleSheet("color: #27ae60; font-size: 14px; font-weight: bold;")
        v3_header.addWidget(self._speed_lbl)
        vs.addLayout(v3_header)

        self._speed_slider = QSlider(Qt.Orientation.Horizontal)
        self._speed_slider.setRange(100, 400)
        self._speed_slider.setValue(self._tts_speed)
        self._speed_slider.setFixedHeight(28)
        self._speed_slider.setStyleSheet(self._slider_style("#27ae60"))
        self._speed_slider.valueChanged.connect(self._on_speed_changed)
        vs.addWidget(self._speed_slider)

        layout.addWidget(vol_section)

        layout.addWidget(self._sep())

        # ==================== MODES + ACTIONS ====================
        action_section = QWidget()
        action_section.setObjectName("actionSection")
        acts = QVBoxLayout(action_section)
        acts.setContentsMargins(20, 14, 20, 14)
        acts.setSpacing(12)

        acts_title = QLabel("FUNCOES")
        acts_title.setStyleSheet("color: #8e44ad; font-size: 11px; font-weight: bold; letter-spacing: 2px; margin-bottom: 25px;")
        acts.addWidget(acts_title)

        # All toggles in one row
        func_row = QHBoxLayout()
        func_row.setSpacing(16)

        self._btn_subtitle = ToggleSwitch("Legenda", "#2d8cf0")
        self._btn_subtitle.toggled.connect(self._on_subtitle_toggled)
        func_row.addWidget(self._btn_subtitle)

        self._btn_floating = ToggleSwitch("Legenda Flutuante", "#9b59b6")
        self._btn_floating.setEnabled(False)
        self._btn_floating.toggled.connect(self._on_floating_toggled)
        func_row.addWidget(self._btn_floating)

        self._btn_audio_in = ToggleSwitch("Audio In", "#e07c3a")
        func_row.addWidget(self._btn_audio_in)

        self._btn_mic_out = ToggleSwitch("Mic Out", "#8e44ad")
        func_row.addWidget(self._btn_mic_out)

        self._chk_save_transcription = ToggleSwitch("Salvar Transcricao", "#16a085")
        func_row.addWidget(self._chk_save_transcription)

        self._chk_save_translation = ToggleSwitch("Salvar Traducao", "#16a085")
        func_row.addWidget(self._chk_save_translation)

        func_row.addStretch()
        acts.addLayout(func_row)

        # Session label
        label_row = QHBoxLayout()
        label_row.setSpacing(10)
        sess_label = QLabel("Nome da sessao:")
        sess_label.setStyleSheet("color: #888; font-size: 12px;")
        label_row.addWidget(sess_label)
        self._session_name = QLineEdit()
        self._session_name.setPlaceholderText("Ex: Reuniao com cliente, Daily standup...")
        self._session_name.setStyleSheet(
            "QLineEdit { background: #2a2a30; color: #ddd; border: 1px solid #444; "
            "border-radius: 8px; padding: 8px 12px; font-size: 13px; }"
        )
        label_row.addWidget(self._session_name)
        acts.addLayout(label_row)

        # Start / Pause buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self._btn_start = QPushButton("INICIAR")
        self._btn_start.setFixedHeight(42)
        self._btn_start.setMinimumWidth(160)
        self._btn_start.setStyleSheet(
            "QPushButton { background: #27ae60; color: white; border: none; border-radius: 8px; "
            "font-size: 16px; font-weight: bold; letter-spacing: 1px; }"
            "QPushButton:hover { background: #2ecc71; }"
        )
        self._btn_start.clicked.connect(self._toggle_start)
        btn_row.addWidget(self._btn_start)

        self._btn_pause = QPushButton("Pause")
        self._btn_pause.setFixedHeight(42)
        self._btn_pause.setFixedWidth(90)
        self._btn_pause.setCheckable(True)
        self._btn_pause.setEnabled(False)
        self._btn_pause.setStyleSheet(self._btn_style("#444", "#ff9500"))
        self._btn_pause.clicked.connect(self._toggle_pause)
        btn_row.addWidget(self._btn_pause)

        btn_row.addStretch()
        acts.addLayout(btn_row)

        layout.addWidget(action_section)

        layout.addWidget(self._sep())

        # ==================== SUBTITLE AREA ====================
        self._subtitle_area = QTextEdit()
        self._subtitle_area.setReadOnly(True)
        self._subtitle_area.setObjectName("subtitleArea")
        self._subtitle_area.setFont(QFont("SF Pro", 14))
        self._subtitle_area.setMinimumHeight(120)
        layout.addWidget(self._subtitle_area)

        # ==================== STATUS BAR ====================
        sb_w = QWidget()
        sb_w.setObjectName("statusBar")
        sb_w.setFixedHeight(32)
        sbl = QHBoxLayout(sb_w)
        sbl.setContentsMargins(16, 0, 16, 0)

        self._status_label = QLabel("Pronto — configure e clique INICIAR")
        self._status_label.setStyleSheet("color: #888; font-size: 11px;")
        sbl.addWidget(self._status_label)

        self._output_label = QLabel(f"Output: {self._output_device_name or '?'}")
        self._output_label.setStyleSheet("color: #555; font-size: 10px;")
        sbl.addStretch()
        sbl.addWidget(self._output_label)

        layout.addWidget(sb_w)

        # ==================== STYLESHEET ====================
        self.setStyleSheet("""
            #central {
                background: #16161c;
            }
            #titleBar {
                background: rgba(30, 30, 36, 0.98);
                border-top-left-radius: 14px; border-top-right-radius: 14px;
            }
            #presetSection {
                background: rgba(28, 28, 34, 0.95);
            }
            #configSection, #actionSection {
                background: rgba(26, 26, 32, 0.92);
            }
            #subtitleArea {
                background: transparent; color: #eee; border: none; padding: 16px;
                selection-background-color: #2d8cf0;
            }
            #statusBar {
                background: rgba(18, 18, 22, 0.95);
                border-bottom-left-radius: 14px; border-bottom-right-radius: 14px;
            }
            QComboBox {
                background: #2a2a30; color: #ddd; border: 1px solid #444;
                border-radius: 8px; padding: 6px 12px; font-size: 13px;
                min-height: 20px;
            }
            QComboBox::drop-down { border: none; width: 20px; }
            QComboBox QAbstractItemView {
                background: #2a2a30; color: #ddd; selection-background-color: #2d8cf0;
                border: 1px solid #444; border-radius: 4px;
            }
            QCheckBox { spacing: 6px; }
            QCheckBox::indicator {
                width: 16px; height: 16px; border-radius: 4px;
                border: 1px solid #555; background: #2a2a30;
            }
            QCheckBox::indicator:checked {
                background: #2d8cf0; border: 1px solid #2d8cf0;
            }
            QFrame[frameShape="4"] { color: #2a2a30; }
        """)

    # ==================== UI HELPERS ====================

    def _lbl(self, text):
        l = QLabel(text)
        l.setStyleSheet("color: #999; font-size: 12px;")
        return l

    def _field_label(self, text):
        l = QLabel(text)
        l.setStyleSheet("color: #888; font-size: 12px;")
        l.setFixedWidth(120)
        return l

    def _sep(self):
        s = QFrame()
        s.setFrameShape(QFrame.Shape.HLine)
        s.setStyleSheet("color: #2a2a30;")
        return s

    def _toggle_btn(self, text, color="#2d8cf0"):
        btn = QPushButton(text)
        btn.setCheckable(True)
        btn.setFixedHeight(34)
        btn.setMinimumWidth(90)
        btn.setStyleSheet(f"""
            QPushButton {{ background: #2a2a30; color: #888; border: 1px solid #444;
                border-radius: 8px; padding: 4px 16px; font-size: 13px; font-weight: bold; }}
            QPushButton:hover {{ background: #333; color: #bbb; }}
            QPushButton:checked {{ background: {color}; color: white; border: 1px solid {color}; }}
        """)
        return btn

    def _slider_style(self, color):
        return f"""
            QSlider::groove:horizontal {{ height: 8px; background: #333; border-radius: 4px; }}
            QSlider::handle:horizontal {{ width: 20px; height: 20px; margin: -6px 0; background: {color}; border-radius: 10px; }}
            QSlider::sub-page:horizontal {{ background: {color}; border-radius: 4px; }}
        """

    def _btn_style(self, off, on):
        return f"""
            QPushButton {{ background: {off}; color: #ccc; border: 1px solid #555;
                border-radius: 8px; padding: 6px 16px; font-size: 13px; font-weight: bold; }}
            QPushButton:hover {{ background: #555; }}
            QPushButton:checked {{ background: {on}; color: white; border: 1px solid {on}; }}
        """

    def _small_btn_style(self, color):
        return f"""
            QPushButton {{ background: {color}; color: white; border: none;
                border-radius: 6px; padding: 4px 14px; font-size: 12px; font-weight: bold; }}
            QPushButton:hover {{ background: {color}cc; }}
        """

    def _set_dot(self, color):
        self._status_dot.setStyleSheet(f"background: {color}; border-radius: 6px;")

    # ==================== SIGNALS ====================

    def _connect_signals(self):
        self.signals.new_subtitle.connect(self._add_subtitle)
        self.signals.status_changed.connect(self._set_status)
        self.signals.error.connect(self._set_error)
        self.signals.audio_level.connect(self._audio_meter.set_level)
        self.signals.mic_level.connect(self._mic_meter.set_level)

    def _add_subtitle(self, original, translated, source="AUDIO"):
        src = self._lang_in.currentText()[:2].upper()
        tgt = self._lang_out.currentText()[:2].upper()
        source_tag = f'<span style="color: #e07c3a;">[{source}]</span> ' if source == "MIC" else ""

        self._subtitle_area.append(
            f'{source_tag}<span style="color: #888; font-size: 12px;">[{src}] {original}</span>'
        )
        self._subtitle_area.append(
            f'{source_tag}<span style="color: #fff; font-size: 15px; font-weight: bold;">[{tgt}] {translated}</span>'
        )
        self._subtitle_area.append("")
        bar = self._subtitle_area.verticalScrollBar()
        bar.setValue(bar.maximum())

        # Update floating subtitle
        self._floating_sub.update_text(
            f"[{source}] [{src}] {original}",
            f"[{tgt}] {translated}",
        )

        # Log to file
        self._log_entry(src, original, tgt, translated, source=source)

    def _set_status(self, text):
        self._status_label.setText(text)
        self._status_label.setStyleSheet("color: #888; font-size: 11px;")
        get_logger().info(text)

    def _set_error(self, text):
        self._status_label.setText(f"ERRO: {text}")
        self._status_label.setStyleSheet("color: #ff5555; font-size: 11px;")
        get_logger().error(text)


    # ==================== PRESETS ====================

    def _get_current_config(self) -> dict:
        return {
            "lang_in": self._lang_in.currentText(),
            "lang_out": self._lang_out.currentText(),
            "mic": self._mic_combo.currentText(),
            "subtitle": self._btn_subtitle.isChecked(),
            "floating": self._btn_floating.isChecked(),
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
            self._on_subtitle_toggled(config["subtitle"])
        if "floating" in config:
            self._btn_floating.setChecked(config["floating"])
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

            # Reload performance settings from env
            self._interim_subtitles = os.getenv("INTERIM_SUBTITLES", "true").lower() == "true"
            self._translate_interim = os.getenv("TRANSLATE_INTERIM", "false").lower() == "true"
            self._endpointing_ms = int(os.getenv("ENDPOINTING_MS", "150"))
            self._stt_model = os.getenv("STT_MODEL", "nova-2")

            # Setup logger
            from app_logger import setup_logger
            setup_logger(dialog.get_logs_path(), dialog.get_enable_logs())

            # Update presets (may have been renamed/deleted)
            self._presets = dialog.get_presets()
            self._refresh_preset_combo()
            self._rebuild_tray_menu()

            self.signals.status_changed.emit("Configuracoes salvas")

    def _on_preset_selected(self, index):
        has_preset = index > 0
        self._update_preset_buttons(has_preset)
        if not has_preset:
            self._preset_summary.setText("")
            return
        name = self._preset_combo.currentText()
        if name in self._presets:
            self._apply_config(self._presets[name])
            self._update_preset_summary(name)

    def _update_preset_buttons(self, has_preset):
        """Show/hide preset buttons based on selection."""
        self._btn_create_preset.setVisible(not has_preset)
        self._btn_save_preset.setVisible(has_preset)
        self._btn_saveas_preset.setVisible(has_preset)
        self._btn_del_preset.setVisible(has_preset)

    def _refresh_preset_combo(self):
        """Rebuild the preset dropdown from current presets dict."""
        self._preset_combo.blockSignals(True)
        self._preset_combo.clear()
        self._preset_combo.addItem("-- Nenhum preset --")
        for name in self._presets:
            self._preset_combo.addItem(name)
        self._preset_combo.blockSignals(False)

    def _save_preset(self):
        """Overwrite the currently selected preset."""
        name = self._preset_combo.currentText()
        if name == "-- Nenhum preset --":
            self._save_preset_as()
            return
        self._presets[name] = self._get_current_config()
        save_presets(self._presets)
        self._update_preset_summary(name)
        self.signals.status_changed.emit(f"Preset '{name}' atualizado")

    def _save_preset_as(self):
        """Save current config as a new preset."""
        name, ok = QInputDialog.getText(self, "Salvar como", "Nome do novo preset:")
        if ok and name.strip():
            name = name.strip()
            self._presets[name] = self._get_current_config()
            save_presets(self._presets)
            if self._preset_combo.findText(name) < 0:
                self._preset_combo.addItem(name)
            self._preset_combo.setCurrentText(name)
            self._update_preset_summary(name)
            self.signals.status_changed.emit(f"Preset '{name}' criado")

    def _update_preset_summary(self, name):
        """Show a summary of the selected preset."""
        if name not in self._presets:
            self._preset_summary.setText("")
            return
        cfg = self._presets[name]
        modes = []
        if cfg.get("subtitle"): modes.append("Legenda")
        if cfg.get("audio_in"): modes.append("Audio In")
        if cfg.get("mic_out"): modes.append("Mic Out")
        self._preset_summary.setText(
            f"{cfg.get('lang_in', '?')} -> {cfg.get('lang_out', '?')}  |  "
            f"{', '.join(modes) if modes else 'Nenhum modo'}  |  "
            f"Vol: {cfg.get('original_vol', '?')}% / {cfg.get('tts_vol', '?')}%  |  "
            f"Vel: {cfg.get('tts_speed', '?')}"
        )

    def _delete_preset(self):
        name = self._preset_combo.currentText()
        if name == "-- Nenhum preset --":
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

    def _on_subtitle_toggled(self, checked):
        """When Legenda is toggled, enable/disable Legenda Flutuante."""
        self._btn_floating.setEnabled(checked)
        if checked:
            self._btn_floating.setChecked(True)
        else:
            self._btn_floating.setChecked(False)
            self._floating_sub.hide()

    def _on_floating_toggled(self, checked):
        """Show/hide floating subtitle."""
        if checked:
            self._floating_sub.show()
        else:
            self._floating_sub.hide()
        self._rebuild_tray_menu()

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
            QMessageBox.warning(
                self, "Nenhuma funcao selecionada",
                "Ative pelo menos uma funcao antes de iniciar:\n"
                "Legenda, Audio In ou Mic Out"
            )
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
        if self._btn_floating.isChecked():
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
        self._session_name.setEnabled(enabled)

    def _get_selected_mic_index(self) -> int:
        mic_name = self._mic_combo.currentText()
        for dev_idx, dev_name in self._mic_devices:
            if dev_name == mic_name:
                return dev_idx
        return sd.default.device[0]

    # ==================== LOGGING ====================

    def _open_log_file(self):
        sessions_dir = self._sessions_path or SESSIONS_DIR
        preset_name = self._preset_combo.currentText()
        if preset_name and preset_name != "-- Nenhum preset --":
            # Sanitize preset name for folder
            safe_name = "".join(c if c.isalnum() or c in " _-" else "_" for c in preset_name).strip()
            sessions_dir = os.path.join(sessions_dir, safe_name)
        os.makedirs(sessions_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        src = self._lang_in.currentText()[:2]
        tgt = self._lang_out.currentText()[:2]
        session_name = self._session_name.text().strip()
        if session_name:
            safe_session = "".join(c if c.isalnum() or c in " _-" else "_" for c in session_name).strip()
            filename = f"{safe_session}_{ts}_{src}-{tgt}.txt"
        else:
            filename = f"{ts}_{src}-{tgt}.txt"
        path = os.path.join(sessions_dir, filename)
        self._log_file = open(path, "w", encoding="utf-8")

        # Header
        preset_name = self._preset_combo.currentText()
        preset_tag = preset_name if preset_name != "-- Nenhum preset --" else "Manual"
        modes = []
        if self._btn_subtitle.isChecked(): modes.append("Legenda")
        if self._btn_audio_in.isChecked(): modes.append("Audio In")
        if self._btn_mic_out.isChecked(): modes.append("Mic Out")

        self._log_file.write(f"# RealtimeTranslator Session\n")
        if session_name:
            self._log_file.write(f"# Sessao: {session_name}\n")
        self._log_file.write(f"# Data: {datetime.now().isoformat()}\n")
        self._log_file.write(f"# Preset: {preset_tag}\n")
        self._log_file.write(f"# Idiomas: {self._lang_in.currentText()} -> {self._lang_out.currentText()}\n")
        self._log_file.write(f"# Modos: {', '.join(modes)}\n\n")
        self.signals.status_changed.emit(f"Log: {filename}")

    def _close_log_file(self):
        if self._log_file:
            self._log_file.close()
            self._log_file = None

    def _log_entry(self, src_label, original, tgt_label, translated, source="AUDIO"):
        if not self._log_file:
            return
        ts = datetime.now().strftime("%H:%M:%S")
        if self._save_transcription:
            self._log_file.write(f"[{ts}] [{source}] [{src_label}] {original}\n")
        if self._save_translation:
            self._log_file.write(f"[{ts}] [{source}] [{tgt_label}] {translated}\n")
        if self._save_transcription or self._save_translation:
            self._log_file.write("\n")
            self._log_file.flush()

    # ==================== PIPELINE ====================

    def _start_pipeline(self):
        log = get_logger()
        log.info("Pipeline starting...")
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
                        endpointing_ms=self._endpointing_ms,
                    )
                    self._incoming_transcriber.start()
                    time.sleep(0.5)

                    self._incoming_capture = AudioCapture(BLACKHOLE_2CH)
                    if audio_in:
                        self._start_passthrough(output_dev)
                        self._incoming_capture.start(self._on_audio_data)
                    else:
                        self._incoming_capture.start(self._on_audio_data_subtitle_only)

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
                        endpointing_ms=self._endpointing_ms,
                    )
                    self._outgoing_transcriber.start()
                    time.sleep(0.5)

                # Always capture mic for level meter (+ transcription if mic_out)
                self._outgoing_capture = AudioCapture.__new__(AudioCapture)
                self._outgoing_capture.device_index = mic_dev
                self._outgoing_capture.sample_rate = SAMPLE_RATE
                self._outgoing_capture.channels = 1
                self._outgoing_capture.block_size = 4096
                self._outgoing_capture.stream = None
                self._outgoing_capture._callback = None
                self._outgoing_capture.start(self._on_mic_data)

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

    def _on_audio_data_subtitle_only(self, audio_bytes):
        """Audio callback for subtitle-only mode (no passthrough)."""
        if self._incoming_transcriber:
            self._incoming_transcriber.send(audio_bytes)
        # Emit level
        data = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32)
        rms = np.sqrt(np.mean(data ** 2))
        self.signals.audio_level.emit(min(1.0, rms / 10000.0))

    def _on_audio_data(self, audio_bytes):
        """Audio callback for audio-in mode (with passthrough)."""
        if self._incoming_transcriber:
            self._incoming_transcriber.send(audio_bytes)

        # Calculate and emit audio level
        data = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32)
        rms = np.sqrt(np.mean(data ** 2))
        self.signals.audio_level.emit(min(1.0, rms / 10000.0))

        vol = self._original_vol / 100.0
        if vol > 0 and self._passthrough_stream:
            normalized = (data / 32767.0 * vol).reshape(-1, 1)
            try:
                self._passthrough_stream.write(normalized)
            except Exception:
                pass

    def _on_mic_data(self, audio_bytes):
        """Mic audio callback — sends to transcriber + emits level."""
        if self._outgoing_transcriber:
            self._outgoing_transcriber.send(audio_bytes)
        data = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32)
        rms = np.sqrt(np.mean(data ** 2))
        self.signals.mic_level.emit(min(1.0, rms / 10000.0))

    def _stop_pipeline(self):
        self._stop_passthrough()
        self._audio_meter.set_level(0)
        self._mic_meter.set_level(0)

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
        if not text.strip():
            return

        # Interim subtitles (show partial text immediately, no translation)
        if not is_final:
            if self._interim_subtitles and self._btn_subtitle.isChecked():
                self.signals.new_subtitle.emit(text, "...", "AUDIO")
            if self._translate_interim and self._btn_subtitle.isChecked():
                try:
                    translated = self._translator_in.translate(text)
                    self.signals.new_subtitle.emit(text, translated, "AUDIO")
                except Exception:
                    pass
            return

        # Final result — always translate and process
        try:
            translated = self._translator_in.translate(text)
            if self._btn_subtitle.isChecked():
                self.signals.new_subtitle.emit(text, translated, "AUDIO")
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
                self.signals.new_subtitle.emit(text, translated, "MIC")
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
        self._tray.setIcon(self._load_app_icon())
        self._tray.setToolTip("RealtimeTranslator")
        self._tray.activated.connect(self._on_tray_activated)

        # Set window icon too
        self.setWindowIcon(self._load_app_icon())

        self._rebuild_tray_menu()
        self._tray.show()

    def _load_app_icon(self):
        """Load the app icon from file."""
        icon_path = os.path.join(os.path.dirname(__file__), "icon.png")
        if os.path.exists(icon_path):
            return QIcon(icon_path)
        # Fallback: generate simple icon
        return self._create_fallback_icon()

    def _create_fallback_icon(self):
        """Fallback icon if icon.png is missing."""
        px = QPixmap(32, 32)
        px.fill(QColor(0, 0, 0, 0))
        painter = QPainter(px)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor("#2d8cf0"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(4, 4, 24, 24)
        painter.setPen(QColor("white"))
        painter.setFont(QFont("Arial", 14, QFont.Weight.Bold))
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

        # Floating subtitle toggle
        if self._floating_sub.isVisible():
            sub_action = menu.addAction("Ocultar legenda flutuante")
        else:
            sub_action = menu.addAction("Mostrar legenda flutuante")
        sub_action.triggered.connect(self._toggle_floating_subtitle)

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

    def _toggle_floating_subtitle(self):
        if self._floating_sub.isVisible():
            self._floating_sub.hide()
        else:
            self._floating_sub.show()
        self._rebuild_tray_menu()

    def _activate_preset_from_tray(self, preset_name):
        """Load a preset and start from tray menu."""
        if preset_name in self._presets:
            self._apply_config(self._presets[preset_name])
            if not self._running:
                self._start_all()
            self._rebuild_tray_menu()

    def _update_tray_icon(self):
        """Update tray tooltip based on state."""
        if self._running:
            self._tray.setToolTip("RealtimeTranslator — Ativo")
        else:
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
    # Set app name before QApplication init (macOS menu bar name)
    if sys.platform == "darwin":
        # This makes macOS show "RealtimeTranslator" instead of "Python" in the menu bar
        try:
            from Foundation import NSBundle
            bundle = NSBundle.mainBundle()
            info = bundle.localizedInfoDictionary() or bundle.infoDictionary()
            info["CFBundleName"] = "RealtimeTranslator"
        except ImportError:
            pass

    app = QApplication(sys.argv)
    app.setApplicationName("RealtimeTranslator")
    app.setApplicationDisplayName("RealtimeTranslator")
    app.setQuitOnLastWindowClosed(False)  # Keep running in tray

    # Init logger from saved settings
    setup_logger(
        os.getenv("LOGS_PATH", os.path.join(os.path.dirname(__file__), "logs")),
        os.getenv("ENABLE_LOGS", "false").lower() == "true",
    )
    get_logger().info("App started")

    window = TranslatorWindow()
    window.setup_tray()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
