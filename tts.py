"""Text-to-Speech: macOS native 'say' + OpenAI TTS."""

import subprocess
import tempfile
import os
import wave
import numpy as np
import sounddevice as sd
from config import SAMPLE_RATE

# macOS voices per language
MACOS_VOICES = {
    "en": "Samantha",
    "pt": "Luciana",
    "es": "Paulina",
    "fr": "Thomas",
    "de": "Anna",
    "it": "Alice",
    "ja": "Kyoko",
    "ko": "Yuna",
    "zh": "Tingting",
}

# OpenAI TTS voices
OPENAI_VOICES = ["alloy", "echo", "fable", "onyx", "nova", "shimmer"]


class TextToSpeech:
    """TTS with macOS say or OpenAI API."""

    def __init__(self, language: str = "pt", output_device: int | None = None,
                 rate: int = 220, engine: str = "macos", openai_api_key: str = "",
                 openai_voice: str = "nova"):
        """
        Args:
            language: "en", "pt", etc.
            output_device: sounddevice output device index
            rate: speech rate (macOS: words/min, OpenAI: 0.5-2.0 mapped from 100-400)
            engine: "macos" or "openai"
            openai_api_key: OpenAI API key (required if engine="openai")
            openai_voice: OpenAI voice name
        """
        self.language = language
        self.voice = MACOS_VOICES.get(language, "Samantha")
        self.output_device = output_device
        self.rate = rate
        self.engine = engine
        self.openai_api_key = openai_api_key
        self.openai_voice = openai_voice
        self._openai_client = None

        if engine == "openai" and openai_api_key:
            import openai
            self._openai_client = openai.OpenAI(api_key=openai_api_key)

    def speak(self, text: str):
        """Speak text through system audio (blocking)."""
        if not text.strip():
            return
        if self.engine == "openai" and self._openai_client:
            self._speak_openai(text)
        else:
            subprocess.run(
                ["say", "-v", self.voice, "-r", str(self.rate), text],
                capture_output=True,
            )

    def speak_to_device(self, text: str, device_index: int | None = None, gain: float = 1.0):
        """Speak text through a specific audio device via sounddevice."""
        if not text.strip():
            return

        device = device_index or self.output_device or sd.default.device[1]

        if self.engine == "openai" and self._openai_client:
            data, sr = self._synthesize_openai(text)
        else:
            data, sr = self._synthesize_macos(text)

        if data is None or len(data) == 0:
            return

        if gain != 1.0:
            data = np.clip(data * gain, -1.0, 1.0)

        sd.play(data, samplerate=sr, device=device)
        sd.wait()

    def _synthesize_macos(self, text: str) -> tuple[np.ndarray | None, int]:
        """Generate audio via macOS say. Returns (float32_array, sample_rate)."""
        with tempfile.NamedTemporaryFile(suffix=".aiff", delete=False) as f:
            tmp_path = f.name

        try:
            subprocess.run(
                ["say", "-v", self.voice, "-r", str(self.rate), "-o", tmp_path, text],
                capture_output=True,
            )

            wav_path = tmp_path + ".wav"
            subprocess.run(
                ["afconvert", "-f", "WAVE", "-d", "LEI16", tmp_path, wav_path],
                capture_output=True,
            )

            with wave.open(wav_path, "rb") as wf:
                frames = wf.readframes(wf.getnframes())
                sr = wf.getframerate()
                channels = wf.getnchannels()

            data = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32767.0
            if channels > 1:
                data = data.reshape(-1, channels)[:, 0]  # mono

            os.unlink(wav_path)
            return data, sr
        except Exception:
            return None, SAMPLE_RATE
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def _synthesize_openai(self, text: str) -> tuple[np.ndarray | None, int]:
        """Generate audio via OpenAI TTS. Returns (float32_array, sample_rate)."""
        try:
            # Map rate 100-400 to OpenAI speed 0.5-2.0
            speed = 0.5 + (self.rate - 100) / (400 - 100) * 1.5
            speed = max(0.25, min(4.0, speed))

            response = self._openai_client.audio.speech.create(
                model="tts-1",
                voice=self.openai_voice,
                input=text,
                response_format="pcm",  # raw 24kHz 16-bit mono PCM
                speed=speed,
            )

            audio_bytes = response.content
            data = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32767.0
            return data, 24000  # OpenAI PCM is 24kHz
        except Exception as e:
            print(f"[TTS OpenAI Error] {e}, falling back to macOS say")
            return self._synthesize_macos(text)

    def _speak_openai(self, text: str):
        """Speak via OpenAI TTS to system output."""
        data, sr = self._synthesize_openai(text)
        if data is not None and len(data) > 0:
            sd.play(data, samplerate=sr)
            sd.wait()


if __name__ == "__main__":
    print(f"\n{'='*50}")
    print(f"  TESTE DE TEXT-TO-SPEECH")
    print(f"{'='*50}\n")

    print("  Teste 1: macOS say (Luciana PT-BR)...")
    tts = TextToSpeech(language="pt", engine="macos")
    tts.speak("Olá, isso é um teste com a voz nativa do Mac.")
    print("  >> Done.\n")

    print("  Teste 2: macOS say (Samantha EN)...")
    tts = TextToSpeech(language="en", engine="macos")
    tts.speak("Hello, this is a test with the native Mac voice.")
    print("  >> Done.\n")

    # OpenAI test (only if key is set)
    from config import OPENAI_API_KEY
    if OPENAI_API_KEY:
        print("  Teste 3: OpenAI TTS (nova)...")
        tts = TextToSpeech(language="pt", engine="openai",
                           openai_api_key=OPENAI_API_KEY, openai_voice="nova")
        tts.speak("Olá, isso é um teste com a voz da OpenAI.")
        print("  >> Done.\n")
    else:
        print("  Teste 3: OpenAI TTS — OPENAI_API_KEY nao configurada, pulando.\n")

    print("  Testes concluidos!")
