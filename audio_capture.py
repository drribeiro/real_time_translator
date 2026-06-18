"""Audio capture from system (BlackHole) and microphone."""

import time
import numpy as np
import sounddevice as sd
from config import SAMPLE_RATE, CHANNELS, BLOCK_SIZE, BLACKHOLE_2CH, find_device


class AudioCapture:
    """Captures audio from a specified device and forwards raw bytes via callback."""

    def __init__(self, device_name: str, sample_rate: int = SAMPLE_RATE,
                 channels: int = CHANNELS, block_size: int = BLOCK_SIZE):
        self.device_index = find_device(device_name, kind="input")
        if self.device_index is None:
            raise RuntimeError(
                f"Device '{device_name}' not found. "
                f"Run 'python config.py' to list available devices."
            )
        self.sample_rate = sample_rate
        self.channels = channels
        self.block_size = block_size
        self.stream = None
        self._callback = None

    def _audio_callback(self, indata, frames, time_info, status):
        if status:
            print(f"[AudioCapture] {status}")
        if self._callback:
            # Convert float32 to int16 bytes (what Deepgram expects)
            audio_int16 = (indata * 32767).astype(np.int16)
            self._callback(audio_int16.tobytes())

    def start(self, callback):
        """Start capturing. callback receives raw int16 PCM bytes."""
        self._callback = callback
        self.stream = sd.InputStream(
            device=self.device_index,
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype="float32",
            blocksize=self.block_size,
            callback=self._audio_callback,
        )
        self.stream.start()
        dev_name = sd.query_devices(self.device_index)["name"]
        print(f"[AudioCapture] Capturing from: {dev_name} (index {self.device_index})")

    def stop(self):
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None
            print("[AudioCapture] Stopped.")


def test_capture(device_name: str, duration: int = 5):
    """Capture audio for N seconds and print RMS levels."""
    print(f"\nTestando captura de '{device_name}' por {duration}s...")
    print("(Toque algum audio no sistema para ver niveis)\n")

    rms_values = []

    def on_audio(audio_bytes):
        data = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32)
        rms = np.sqrt(np.mean(data ** 2))
        rms_values.append(rms)
        # Visual bar
        bar_len = int(rms / 500)
        bar = "#" * min(bar_len, 50)
        print(f"  RMS: {rms:8.1f}  |{bar}")

    capture = AudioCapture(device_name)
    capture.start(on_audio)

    try:
        time.sleep(duration)
    except KeyboardInterrupt:
        pass
    finally:
        capture.stop()

    if rms_values:
        avg = np.mean(rms_values)
        peak = np.max(rms_values)
        print(f"\n  Media RMS: {avg:.1f}")
        print(f"  Pico RMS:  {peak:.1f}")
        if peak < 10:
            print("  >> ATENCAO: Niveis muito baixos. Verifique se o audio esta tocando")
            print("     e se o Multi-Output Device esta configurado corretamente.")
        else:
            print("  >> OK! Audio esta sendo capturado com sucesso.")
    print()


def test_microphone(duration: int = 5):
    """Capture audio from default microphone."""
    default_in = sd.default.device[0]
    dev_name = sd.query_devices(default_in)["name"]
    print(f"\nTestando microfone: {dev_name}")
    print(f"(Fale algo por {duration}s)\n")

    rms_values = []

    def on_audio(audio_bytes):
        data = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32)
        rms = np.sqrt(np.mean(data ** 2))
        rms_values.append(rms)
        bar_len = int(rms / 500)
        bar = "#" * min(bar_len, 50)
        print(f"  RMS: {rms:8.1f}  |{bar}")

    # Use default input device
    capture = AudioCapture.__new__(AudioCapture)
    capture.device_index = default_in
    capture.sample_rate = SAMPLE_RATE
    capture.channels = CHANNELS
    capture.block_size = BLOCK_SIZE
    capture.stream = None
    capture._callback = None

    capture.start(on_audio)

    try:
        time.sleep(duration)
    except KeyboardInterrupt:
        pass
    finally:
        capture.stop()

    if rms_values:
        avg = np.mean(rms_values)
        peak = np.max(rms_values)
        print(f"\n  Media RMS: {avg:.1f}")
        print(f"  Pico RMS:  {peak:.1f}")
        if peak < 10:
            print("  >> ATENCAO: Niveis muito baixos. Verifique o microfone.")
        else:
            print("  >> OK! Microfone funcionando.")
    print()


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--mic":
        test_microphone()
    elif len(sys.argv) > 1 and sys.argv[1] == "--both":
        test_capture(BLACKHOLE_2CH)
        test_microphone()
    else:
        # Default: test BlackHole capture
        test_capture(BLACKHOLE_2CH)
