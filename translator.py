"""Text translation via DeepL API."""

import time
import deepl
from config import DEEPL_API_KEY


class TextTranslator:
    """Translates text using DeepL API."""

    def __init__(self, api_key: str = DEEPL_API_KEY,
                 source_lang: str = "EN", target_lang: str = "PT-BR"):
        self.translator = deepl.Translator(api_key)
        self.source_lang = source_lang
        self.target_lang = target_lang

    def translate(self, text: str) -> str:
        """Translate text. Returns translated string."""
        if not text.strip():
            return ""
        result = self.translator.translate_text(
            text,
            source_lang=self.source_lang,
            target_lang=self.target_lang,
        )
        return result.text

    def swap_languages(self):
        """Swap source and target languages."""
        # DeepL uses "EN" for source but target can be "EN-US" or "EN-GB"
        if self.target_lang.startswith("EN"):
            self.source_lang = "EN"
            self.target_lang = "PT-BR"
        else:
            self.source_lang = "PT"
            self.target_lang = "EN-US"


if __name__ == "__main__":
    import sys
    from config import SAMPLE_RATE, BLACKHOLE_2CH
    from audio_capture import AudioCapture
    from transcriber import RealtimeTranscriber

    use_mic = "--mic" in sys.argv
    if use_mic:
        stt_lang = "pt-BR"
        src_lang, tgt_lang = "PT", "EN-US"
        source_label, target_label = "PT", "EN"
    else:
        stt_lang = "en-US"
        src_lang, tgt_lang = "EN", "PT-BR"
        source_label, target_label = "EN", "PT"

    duration = 20

    print(f"\n{'='*50}")
    print(f"  TESTE DE TRANSCRICAO + TRADUCAO")
    print(f"  Direcao: {source_label} -> {target_label}")
    print(f"  Fonte: {'Microfone' if use_mic else 'Sistema (BlackHole)'}")
    print(f"  Duracao: {duration}s")
    print(f"{'='*50}\n")

    translator = TextTranslator(source_lang=src_lang, target_lang=tgt_lang)

    def on_transcript(text, is_final):
        if is_final and text.strip():
            translated = translator.translate(text)
            print(f"  [{source_label}] {text}")
            print(f"  [{target_label}] {translated}")
            print()

    transcriber = RealtimeTranscriber(language=stt_lang, on_transcript=on_transcript)
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
        capture.stop()
        transcriber.stop()
        print("Done.")
