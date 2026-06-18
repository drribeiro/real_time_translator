"""Real-time speech-to-text via Deepgram WebSocket streaming."""

import json
import threading
import time
from deepgram import DeepgramClient
from deepgram.core.events import EventType
from deepgram.listen.v1.types.listen_v1results import ListenV1Results
from config import DEEPGRAM_API_KEY, SAMPLE_RATE, ENDPOINTING_MS, BLACKHOLE_2CH
from audio_capture import AudioCapture


class TranscriptSegment:
    """A segment of transcribed text with metadata."""
    def __init__(self, text: str, is_final: bool, speaker: int = -1,
                 speech_final: bool = False):
        self.text = text
        self.is_final = is_final
        self.speaker = speaker  # -1 = unknown, 0+ = speaker index
        self.speech_final = speech_final  # True when speaker pauses


class SmartAccumulator:
    """Accumulates transcript segments for better translation quality."""

    def __init__(self, on_ready=None, max_segments=3, pause_timeout=1.5):
        """
        Args:
            on_ready: callback(text: str, speaker: int) — called when accumulated text is ready
            max_segments: max final segments before flushing
            pause_timeout: seconds of silence to trigger flush
        """
        self.on_ready = on_ready
        self.max_segments = max_segments
        self.pause_timeout = pause_timeout
        self._segments = []  # list of (text, speaker)
        self._current_speaker = -1
        self._timer = None
        self._lock = threading.Lock()

    def add(self, segment: TranscriptSegment):
        """Add a transcript segment. Flushes when appropriate."""
        if not segment.text.strip():
            return

        with self._lock:
            # Cancel pending timer
            if self._timer:
                self._timer.cancel()
                self._timer = None

            if not segment.is_final:
                return

            # Speaker changed — flush previous speaker's text first
            if (segment.speaker != self._current_speaker
                    and self._current_speaker >= 0
                    and segment.speaker >= 0
                    and self._segments):
                self._flush()

            self._current_speaker = segment.speaker
            self._segments.append((segment.text, segment.speaker))

            # Flush if enough segments or speech_final (long pause)
            if len(self._segments) >= self.max_segments or segment.speech_final:
                self._flush()
            else:
                # Start timer — flush after pause
                self._timer = threading.Timer(self.pause_timeout, self._timer_flush)
                self._timer.daemon = True
                self._timer.start()

    def _timer_flush(self):
        with self._lock:
            self._flush()

    def _flush(self):
        if not self._segments:
            return
        text = " ".join(t for t, _ in self._segments)
        speaker = self._segments[0][1]
        self._segments.clear()
        if self.on_ready:
            self.on_ready(text, speaker)

    def flush_remaining(self):
        """Force flush any remaining text."""
        with self._lock:
            if self._timer:
                self._timer.cancel()
                self._timer = None
            self._flush()


class RealtimeTranscriber:
    """Connects to Deepgram WebSocket and streams audio for real-time transcription."""

    def __init__(self, api_key: str = DEEPGRAM_API_KEY, language: str = "en-US",
                 on_transcript=None, on_interim=None, endpointing_ms: int = ENDPOINTING_MS,
                 model: str = "nova-2", diarize: bool = True):
        """
        Args:
            on_transcript: callback(text: str, is_final: bool, speaker: int)
            on_interim: callback(text: str) — for partial/interim display
            endpointing_ms: silence duration (ms) to consider end of utterance
            model: Deepgram model (nova-2, nova-3)
            diarize: enable speaker detection
        """
        self.api_key = api_key
        self.language = language
        self.on_transcript = on_transcript
        self.on_interim = on_interim
        self.endpointing_ms = endpointing_ms
        self.model = model
        self.diarize = diarize
        self._connection = None
        self._listen_thread = None
        self._running = False

    def start(self):
        """Open WebSocket connection to Deepgram and start listening."""
        client = DeepgramClient(api_key=self.api_key)

        connect_params = dict(
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
        if self.diarize:
            connect_params["diarize"] = "true"

        self._ctx = client.listen.v1.connect(**connect_params)
        self._connection = self._ctx.__enter__()

        self._connection.on(EventType.MESSAGE, self._on_message)
        self._connection.on(EventType.ERROR, self._on_error)
        self._connection.on(EventType.OPEN, lambda _: None)
        self._connection.on(EventType.CLOSE, lambda _: None)

        self._running = True
        self._listen_thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._listen_thread.start()

        print(f"[Transcriber] Connected (lang={self.language}, model={self.model}, diarize={self.diarize})")

    def _listen_loop(self):
        try:
            self._connection.start_listening()
        except Exception as e:
            if self._running:
                print(f"[Transcriber] Listen error: {e}")

    def _on_message(self, message):
        """Handle incoming messages from Deepgram."""
        if isinstance(message, ListenV1Results):
            self._handle_result(message)
        elif isinstance(message, bytes):
            pass  # audio data, ignore
        else:
            # Try to parse as raw JSON for diarization data
            try:
                self._handle_raw(message)
            except Exception:
                pass

    def _handle_result(self, result):
        if not result.channel or not result.channel.alternatives:
            return
        alt = result.channel.alternatives[0]
        transcript = alt.transcript
        if not transcript:
            return

        is_final = result.is_final
        speech_final = getattr(result, 'speech_final', False)

        # Try to get speaker from words
        speaker = -1
        try:
            words = alt.words if hasattr(alt, 'words') else []
            if words:
                # Get speaker from first word (may have speaker field)
                first_word = words[0]
                speaker = getattr(first_word, 'speaker', -1)
                if speaker is None:
                    speaker = -1
        except Exception:
            pass

        if not is_final and self.on_interim:
            self.on_interim(transcript)

        if self.on_transcript:
            self.on_transcript(transcript, is_final, speaker)

    def _handle_raw(self, message):
        """Try to extract speaker info from raw message dict."""
        data = None
        if hasattr(message, 'model_dump'):
            data = message.model_dump()
        elif isinstance(message, dict):
            data = message

        if not data:
            return

        channel = data.get('channel', {})
        alts = channel.get('alternatives', [])
        if not alts:
            return

        alt = alts[0]
        transcript = alt.get('transcript', '')
        if not transcript:
            return

        words = alt.get('words', [])
        speaker = -1
        if words:
            speaker = words[0].get('speaker', -1)

        is_final = data.get('is_final', False)
        speech_final = data.get('speech_final', False)

        if not is_final and self.on_interim:
            self.on_interim(transcript)

        if self.on_transcript:
            self.on_transcript(transcript, is_final, speaker)

    def _on_error(self, error):
        print(f"[Transcriber] Error: {error}")

    def send(self, audio_bytes: bytes):
        """Send raw PCM audio bytes to Deepgram. Silently drops on error."""
        if not self._connection or not audio_bytes or not self._running:
            return
        try:
            self._connection.send_media(audio_bytes)
        except Exception:
            pass

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


if __name__ == "__main__":
    import sys

    use_mic = "--mic" in sys.argv
    lang = "pt-BR" if use_mic else "en-US"
    duration = 20

    print(f"\n{'='*50}")
    print(f"  TESTE DE TRANSCRICAO + DIARIZACAO")
    print(f"  Idioma: {lang}")
    print(f"  Fonte: {'Microfone' if use_mic else 'Sistema (BlackHole)'}")
    print(f"  Duracao: {duration}s")
    print(f"{'='*50}\n")

    # Test with SmartAccumulator
    def on_ready(text, speaker):
        tag = f"Pessoa {speaker + 1}" if speaker >= 0 else "?"
        print(f"  [{tag}] {text}")
        print()

    def on_interim(text):
        print(f"  [...] {text}", end="\r")

    acc = SmartAccumulator(on_ready=on_ready, max_segments=3, pause_timeout=1.5)

    def on_transcript(text, is_final, speaker):
        if is_final:
            seg = TranscriptSegment(text, is_final, speaker)
            acc.add(seg)

    transcriber = RealtimeTranscriber(
        language=lang, on_transcript=on_transcript,
        on_interim=on_interim, diarize=True,
    )
    transcriber.start()
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
        acc.flush_remaining()
        capture.stop()
        transcriber.stop()
        print("\nDone.")
