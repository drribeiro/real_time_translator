"""Real-time speech-to-text via Deepgram WebSocket streaming."""

import threading
import time
from deepgram import DeepgramClient
from deepgram.core.events import EventType
from deepgram.listen.v1.types.listen_v1results import ListenV1Results
from config import DEEPGRAM_API_KEY, SAMPLE_RATE, ENDPOINTING_MS, BLACKHOLE_2CH
from audio_capture import AudioCapture


class RealtimeTranscriber:
    """Connects to Deepgram WebSocket and streams audio for real-time transcription."""

    def __init__(self, api_key: str = DEEPGRAM_API_KEY, language: str = "en-US",
                 on_transcript=None, endpointing_ms: int = ENDPOINTING_MS,
                 model: str = "nova-2"):
        """
        Args:
            on_transcript: callback(text: str, is_final: bool)
            endpointing_ms: silence duration (ms) to consider end of utterance
            model: Deepgram model (nova-2, nova-3)
        """
        self.api_key = api_key
        self.language = language
        self.on_transcript = on_transcript
        self.endpointing_ms = endpointing_ms
        self.model = model
        self._connection = None
        self._listen_thread = None
        self._running = False

    def start(self):
        """Open WebSocket connection to Deepgram and start listening."""
        client = DeepgramClient(api_key=self.api_key)

        self._ctx = client.listen.v1.connect(
            model=self.model,
            language=self.language,
            encoding="linear16",
            sample_rate=SAMPLE_RATE,
            channels=1,
            interim_results=True,
            punctuate=True,
            smart_format=True,
            endpointing=str(self.endpointing_ms),
        )
        self._connection = self._ctx.__enter__()

        # Register event handlers
        self._connection.on(EventType.MESSAGE, self._on_message)
        self._connection.on(EventType.ERROR, self._on_error)
        self._connection.on(EventType.OPEN, lambda _: print("[Transcriber] WebSocket open"))
        self._connection.on(EventType.CLOSE, lambda _: print("[Transcriber] WebSocket closed"))

        # start_listening() is blocking — run in a thread
        self._running = True
        self._listen_thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._listen_thread.start()

        print(f"[Transcriber] Connected (lang={self.language}, model=nova-2)")

    def _listen_loop(self):
        """Run the blocking start_listening in a thread."""
        try:
            self._connection.start_listening()
        except Exception as e:
            if self._running:
                print(f"[Transcriber] Listen error: {e}")

    def _on_message(self, message):
        """Handle incoming messages from Deepgram."""
        if isinstance(message, ListenV1Results):
            if not message.channel or not message.channel.alternatives:
                return
            transcript = message.channel.alternatives[0].transcript
            if not transcript:
                return
            is_final = message.is_final
            if self.on_transcript:
                self.on_transcript(transcript, is_final)

    def _on_error(self, error):
        print(f"[Transcriber] Error: {error}")

    def send(self, audio_bytes: bytes):
        """Send raw PCM audio bytes to Deepgram. Silently drops on error."""
        if not self._connection or not audio_bytes or not self._running:
            return
        try:
            self._connection.send_media(audio_bytes)
        except Exception:
            pass  # Don't spam errors — connection may be reconnecting

    def stop(self):
        """Close the connection."""
        self._running = False
        if self._connection:
            try:
                self._connection.send_finalize()
                self._connection.send_close_stream()
            except Exception:
                pass
        if self._ctx:
            try:
                self._ctx.__exit__(None, None, None)
            except Exception:
                pass
            self._ctx = None
        self._connection = None
        print("[Transcriber] Disconnected.")


if __name__ == "__main__":
    import sys

    use_mic = "--mic" in sys.argv
    lang = "pt-BR" if use_mic else "en-US"
    duration = 15

    print(f"\n{'='*50}")
    print(f"  TESTE DE TRANSCRICAO EM TEMPO REAL")
    print(f"  Idioma: {lang}")
    print(f"  Fonte: {'Microfone' if use_mic else 'Sistema (BlackHole)'}")
    print(f"  Duracao: {duration}s")
    print(f"{'='*50}\n")

    def on_transcript(text, is_final):
        marker = "FINAL" if is_final else "     "
        print(f"  [{marker}] {text}")

    transcriber = RealtimeTranscriber(language=lang, on_transcript=on_transcript)
    transcriber.start()

    # Small delay to let WebSocket connect before sending audio
    time.sleep(0.5)

    if use_mic:
        import sounddevice as sd
        default_in = sd.default.device[0]
        capture = AudioCapture.__new__(AudioCapture)
        capture.device_index = default_in
        capture.sample_rate = SAMPLE_RATE
        capture.channels = 1
        capture.block_size = 4096
        capture.stream = None
        capture._callback = None
        capture.start(transcriber.send)
    else:
        capture = AudioCapture(BLACKHOLE_2CH)
        capture.start(transcriber.send)

    try:
        time.sleep(duration)
    except KeyboardInterrupt:
        pass
    finally:
        capture.stop()
        transcriber.stop()
        print("\nDone.")
