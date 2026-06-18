"""RealtimeTranslator — Pipeline completo."""

import argparse
import atexit
import signal
import subprocess
import time
import threading
import sounddevice as sd
from config import SAMPLE_RATE, BLACKHOLE_2CH, BLACKHOLE_16CH, find_device
from audio_capture import AudioCapture
from transcriber import RealtimeTranscriber
from translator import TextTranslator
from tts import TextToSpeech


def get_system_volume() -> int | None:
    """Get current macOS system volume (0-100). Returns None if unavailable."""
    result = subprocess.run(
        ["osascript", "-e", "output volume of (get volume settings)"],
        capture_output=True, text=True,
    )
    try:
        return int(result.stdout.strip())
    except (ValueError, TypeError):
        return None


def set_system_volume(volume: int):
    """Set macOS system volume (0-100)."""
    subprocess.run(
        ["osascript", "-e", f"set volume output volume {volume}"],
        capture_output=True,
    )


class TranslatorPipeline:
    """Orchestrates the full translation pipeline."""

    def __init__(self, subtitle=True, audio_in=False, mic_out=False,
                 source_lang="EN", target_lang="PT-BR"):
        self.subtitle = subtitle
        self.audio_in = audio_in
        self.mic_out = mic_out
        self.source_lang = source_lang
        self.target_lang = target_lang

        # Determine STT languages
        self.stt_lang_in = "en-US" if source_lang == "EN" else "pt-BR"
        self.stt_lang_out = "pt-BR" if source_lang == "EN" else "en-US"

        # TTS language (what we generate)
        self.tts_lang_in = "pt" if target_lang.startswith("PT") else "en"
        self.tts_lang_out = "en" if target_lang.startswith("PT") else "pt"

        # Labels
        self.src_label = source_lang[:2]
        self.tgt_label = target_lang[:2]

        # Components
        self._incoming_capture = None
        self._incoming_transcriber = None
        self._outgoing_capture = None
        self._outgoing_transcriber = None
        self._translator_in = None
        self._translator_out = None
        self._tts_in = None
        self._tts_out = None
        self._tts_lock = threading.Lock()
        self._running = False
        self._original_volume = None

    def start(self):
        """Start the pipeline."""
        self._running = True

        # Lower system volume if audio-in mode (so original audio is quieter)
        if self.audio_in:
            self._original_volume = get_system_volume()
            if self._original_volume is not None:
                set_system_volume(10)
                print(f"  [Volume] {self._original_volume}% -> 10% (original audio lowered)")
            else:
                print("  [Volume] Multi-Output Device detected — volume control manual")

        # Translation engines
        self._translator_in = TextTranslator(
            source_lang=self.source_lang,
            target_lang=self.target_lang,
        )

        # --- INCOMING PATH (what others say → translate for you) ---
        if self.subtitle or self.audio_in:
            if self.audio_in:
                self._tts_in = TextToSpeech(language=self.tts_lang_in)

            self._incoming_transcriber = RealtimeTranscriber(
                language=self.stt_lang_in,
                on_transcript=self._on_incoming_transcript,
            )
            self._incoming_transcriber.start()
            time.sleep(0.5)

            self._incoming_capture = AudioCapture(BLACKHOLE_2CH)
            self._incoming_capture.start(self._incoming_transcriber.send)

        # --- OUTGOING PATH (what you say → translate for others) ---
        if self.mic_out:
            self._translator_out = TextTranslator(
                source_lang=self.target_lang.split("-")[0] if "-" in self.target_lang else self.target_lang,
                target_lang="EN-US" if self.source_lang == "EN" else "PT-BR",
            )
            self._tts_out = TextToSpeech(language=self.tts_lang_out)

            self._outgoing_transcriber = RealtimeTranscriber(
                language=self.stt_lang_out,
                on_transcript=self._on_outgoing_transcript,
            )
            self._outgoing_transcriber.start()
            time.sleep(0.5)

            # Use default microphone
            default_in = sd.default.device[0]
            self._outgoing_capture = AudioCapture.__new__(AudioCapture)
            self._outgoing_capture.device_index = default_in
            self._outgoing_capture.sample_rate = SAMPLE_RATE
            self._outgoing_capture.channels = 1
            self._outgoing_capture.block_size = 4096
            self._outgoing_capture.stream = None
            self._outgoing_capture._callback = None
            self._outgoing_capture.start(self._outgoing_transcriber.send)

    def _on_incoming_transcript(self, text, is_final):
        """Handle incoming audio transcription (what others say)."""
        if not is_final or not text.strip():
            return

        translated = self._translator_in.translate(text)

        if self.subtitle:
            print(f"  [{self.src_label}] {text}")
            print(f"  [{self.tgt_label}] {translated}")
            print()

        if self.audio_in and translated:
            threading.Thread(
                target=self._speak_to_device_safe,
                args=(self._tts_in, translated, sd.default.device[1]),
                daemon=True,
            ).start()

    def _on_outgoing_transcript(self, text, is_final):
        """Handle outgoing audio transcription (what you say)."""
        if not is_final or not text.strip():
            return

        translated = self._translator_out.translate(text)

        if self.subtitle:
            print(f"  [MIC {self.tgt_label}] {text}")
            print(f"  [MIC {self.src_label}] {translated}")
            print()

        if translated:
            # Send to virtual mic (BlackHole 16ch)
            virtual_mic = find_device(BLACKHOLE_16CH, kind="output")
            if virtual_mic is not None:
                threading.Thread(
                    target=self._speak_to_device_safe,
                    args=(self._tts_out, translated, virtual_mic),
                    daemon=True,
                ).start()

    def _speak_safe(self, tts, text):
        """Thread-safe TTS playback."""
        with self._tts_lock:
            try:
                tts.speak(text)
            except Exception as e:
                print(f"  [TTS Error] {e}")

    def _speak_to_device_safe(self, tts, text, device):
        """Thread-safe TTS to specific device."""
        with self._tts_lock:
            try:
                tts.speak_to_device(text, device)
            except Exception as e:
                print(f"  [TTS Error] {e}")

    def stop(self):
        """Stop all components and restore system state."""
        self._running = False

        if self._incoming_capture:
            self._incoming_capture.stop()
        if self._outgoing_capture:
            self._outgoing_capture.stop()
        if self._incoming_transcriber:
            self._incoming_transcriber.stop()
        if self._outgoing_transcriber:
            self._outgoing_transcriber.stop()

        # Restore original volume
        if self._original_volume is not None:
            set_system_volume(self._original_volume)
            print(f"  [Volume] Restored to {self._original_volume}%")

        print("\n[Pipeline] Stopped.")


def main():
    parser = argparse.ArgumentParser(description="RealtimeTranslator")
    parser.add_argument("--subtitle", action="store_true", default=True,
                        help="Show subtitles (default: on)")
    parser.add_argument("--no-subtitle", action="store_true",
                        help="Disable subtitles")
    parser.add_argument("--audio-in", action="store_true",
                        help="Translate incoming audio (hear translation)")
    parser.add_argument("--mic-out", action="store_true",
                        help="Translate your mic (others hear translation)")
    parser.add_argument("--direction", choices=["en-pt", "pt-en"], default="en-pt",
                        help="Translation direction (default: en-pt)")
    args = parser.parse_args()

    subtitle = not args.no_subtitle
    if args.direction == "en-pt":
        source_lang, target_lang = "EN", "PT-BR"
    else:
        source_lang, target_lang = "PT", "EN-US"

    # Show config
    modes = []
    if subtitle:
        modes.append("Legenda")
    if args.audio_in:
        modes.append("Audio Traduzido")
    if args.mic_out:
        modes.append("Mic Traduzido")

    print(f"\n{'='*50}")
    print(f"  REALTIME TRANSLATOR")
    print(f"  Direcao: {source_lang} -> {target_lang}")
    print(f"  Modos: {', '.join(modes) if modes else 'Nenhum'}")
    print(f"  Ctrl+C para parar")
    print(f"{'='*50}\n")

    pipeline = TranslatorPipeline(
        subtitle=subtitle,
        audio_in=args.audio_in,
        mic_out=args.mic_out,
        source_lang=source_lang,
        target_lang=target_lang,
    )

    # Graceful shutdown — restore volume even on crash
    def on_signal(sig, frame):
        pipeline.stop()
        exit(0)

    signal.signal(signal.SIGINT, on_signal)
    atexit.register(pipeline.stop)

    pipeline.start()

    # Keep running
    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
