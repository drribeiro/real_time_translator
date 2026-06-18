"""Audio output: playback to headphones and virtual mic (BlackHole 16ch)."""

import time
import numpy as np
import sounddevice as sd
from config import SAMPLE_RATE, BLACKHOLE_16CH, find_device


class AudioOutput:
    """Plays audio to a specified output device."""

    def __init__(self, device_name: str | None = None, sample_rate: int = SAMPLE_RATE):
        if device_name:
            self.device_index = find_device(device_name, kind="output")
            if self.device_index is None:
                raise RuntimeError(f"Output device '{device_name}' not found.")
        else:
            self.device_index = sd.default.device[1]  # default output
        self.sample_rate = sample_rate

    def play_bytes(self, audio_bytes: bytes, sample_rate: int | None = None):
        """Play raw int16 PCM bytes."""
        sr = sample_rate or self.sample_rate
        data = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32767.0
        sd.play(data, samplerate=sr, device=self.device_index)
        sd.wait()

    def play_array(self, data: np.ndarray, sample_rate: int | None = None):
        """Play a numpy float32 array."""
        sr = sample_rate or self.sample_rate
        sd.play(data, samplerate=sr, device=self.device_index)
        sd.wait()


def generate_tone(freq: float = 440.0, duration: float = 2.0,
                  sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Generate a sine wave tone."""
    t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)
    tone = (0.5 * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    return tone


def test_headphone_output():
    """Play a test tone to default output (headphones/speakers)."""
    output = AudioOutput()  # default output
    dev_name = sd.query_devices(output.device_index)["name"]
    print(f"\nTeste 1: Tocando tom de 440Hz em '{dev_name}'...")
    print("(Voce deve ouvir um bip nos fones/caixas)\n")

    tone = generate_tone(440.0, 2.0)
    output.play_array(tone)
    print("  >> Done.\n")


def test_virtual_mic():
    """Play a test tone to BlackHole 16ch (virtual mic)."""
    try:
        output = AudioOutput(device_name=BLACKHOLE_16CH)
    except RuntimeError:
        print(f"\n  >> BlackHole 16ch nao encontrado.")
        print(f"     Instale com: brew install blackhole-16ch")
        return

    dev_name = sd.query_devices(output.device_index)["name"]
    print(f"\nTeste 2: Enviando tom de 440Hz para '{dev_name}' (mic virtual)...")
    print("(Abra um app de gravacao com input = BlackHole 16ch para verificar)\n")

    tone = generate_tone(440.0, 2.0)
    output.play_array(tone)
    print("  >> Done.\n")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--virtual-mic":
        test_virtual_mic()
    elif len(sys.argv) > 1 and sys.argv[1] == "--both":
        test_headphone_output()
        test_virtual_mic()
    else:
        test_headphone_output()
