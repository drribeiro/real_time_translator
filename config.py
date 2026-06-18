import os
import sounddevice as sd
from dotenv import load_dotenv

load_dotenv()

# Audio settings
SAMPLE_RATE = int(os.getenv("SAMPLE_RATE", "16000"))
CHANNELS = 1
BLOCK_SIZE = int(os.getenv("BLOCK_SIZE", "2048"))

# STT settings
ENDPOINTING_MS = int(os.getenv("ENDPOINTING_MS", "150"))
INTERIM_SUBTITLES = os.getenv("INTERIM_SUBTITLES", "true").lower() == "true"

# Device names
# Device names (configurable — supports renamed/aggregate devices)
BLACKHOLE_2CH = os.getenv("DEVICE_CAPTURE", "BlackHole 2ch")
BLACKHOLE_16CH = os.getenv("DEVICE_VIRTUAL_MIC", "BlackHole 16ch")

# API Keys
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY", "")
DEEPL_API_KEY = os.getenv("DEEPL_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")


# Supported languages: display name -> {stt, deepl_src, deepl_tgt, tts}
LANGUAGES = {
    "English": {"stt": "en-US", "deepl_src": "EN", "deepl_tgt": "EN-US", "tts": "en"},
    "Portugues (BR)": {"stt": "pt-BR", "deepl_src": "PT", "deepl_tgt": "PT-BR", "tts": "pt"},
    "Espanol": {"stt": "es", "deepl_src": "ES", "deepl_tgt": "ES", "tts": "es"},
    "Francais": {"stt": "fr", "deepl_src": "FR", "deepl_tgt": "FR", "tts": "fr"},
    "Deutsch": {"stt": "de", "deepl_src": "DE", "deepl_tgt": "DE", "tts": "de"},
    "Italiano": {"stt": "it", "deepl_src": "IT", "deepl_tgt": "IT", "tts": "it"},
    "Japanese": {"stt": "ja", "deepl_src": "JA", "deepl_tgt": "JA", "tts": "ja"},
    "Korean": {"stt": "ko", "deepl_src": "KO", "deepl_tgt": "KO", "tts": "ko"},
    "Chinese": {"stt": "zh", "deepl_src": "ZH", "deepl_tgt": "ZH", "tts": "zh"},
    "Russian": {"stt": "ru", "deepl_src": "RU", "deepl_tgt": "RU", "tts": "ru"},
    "Dutch": {"stt": "nl", "deepl_src": "NL", "deepl_tgt": "NL", "tts": "nl"},
    "Polish": {"stt": "pl", "deepl_src": "PL", "deepl_tgt": "PL", "tts": "pl"},
}


def get_input_devices() -> list[tuple[int, str]]:
    """Return list of (index, name) for input devices."""
    devices = sd.query_devices()
    result = []
    for i, dev in enumerate(devices):
        if dev["max_input_channels"] > 0:
            result.append((i, dev["name"]))
    return result


def find_device(name_substring: str, kind: str = "input") -> int | None:
    """Find audio device index by name substring.
    kind: 'input' or 'output'
    """
    devices = sd.query_devices()
    for i, dev in enumerate(devices):
        if name_substring.lower() in dev["name"].lower():
            if kind == "input" and dev["max_input_channels"] > 0:
                return i
            elif kind == "output" and dev["max_output_channels"] > 0:
                return i
    return None


def list_audio_devices():
    """Print all available audio devices."""
    devices = sd.query_devices()
    print(f"\n{'='*60}")
    print(f"  DISPOSITIVOS DE AUDIO ({len(devices)} encontrados)")
    print(f"{'='*60}\n")

    for i, dev in enumerate(devices):
        in_ch = dev["max_input_channels"]
        out_ch = dev["max_output_channels"]

        if in_ch > 0 and out_ch > 0:
            tipo = "IN/OUT"
        elif in_ch > 0:
            tipo = "IN    "
        else:
            tipo = "   OUT"

        marker = ""
        if BLACKHOLE_2CH.lower() in dev["name"].lower():
            marker = " << BlackHole 2ch"
        elif BLACKHOLE_16CH.lower() in dev["name"].lower():
            marker = " << BlackHole 16ch"

        print(f"  [{i:2d}] {tipo}  {dev['name']}{marker}")

    # Show defaults
    default_in = sd.default.device[0]
    default_out = sd.default.device[1]
    print(f"\n  Default input:  [{default_in}] {devices[default_in]['name']}")
    print(f"  Default output: [{default_out}] {devices[default_out]['name']}")
    print()


if __name__ == "__main__":
    list_audio_devices()
