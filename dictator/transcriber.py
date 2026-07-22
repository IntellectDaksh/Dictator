"""Local speech-to-text via faster-whisper, GPU if available, CPU fallback."""
import threading

import numpy as np
from faster_whisper import WhisperModel

from .paths import WHISPER_CACHE

SAMPLE_RATE = 16000


class Transcriber:
    def __init__(self, size):
        self._lock = threading.Lock()
        self.size = size
        self.device = "?"
        self.model = None

    def load(self):
        with self._lock:
            try:
                model = WhisperModel(self.size, device="cuda", compute_type="float16",
                                     download_root=WHISPER_CACHE)
                # warmup forces CUDA init so a broken CUDA falls back at load
                # time, not mid-dictation
                list(model.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32),
                                      language="en")[0])
                self.device = "CUDA"
            except Exception as e:
                print(f"CUDA unavailable ({type(e).__name__}), using CPU")
                model = WhisperModel(self.size, device="cpu", compute_type="int8",
                                     download_root=WHISPER_CACHE)
                self.device = "CPU"
            self.model = model
        print(f"STT ready: {self.size} on {self.device}")

    def transcribe(self, audio, vocab=()):
        with self._lock:
            if self.model is None:
                return ""
            # vocab is NOT fed to Whisper as initial_prompt: on quiet/unclear
            # audio the model would latch onto the prompt and echo the
            # vocabulary word back verbatim instead of transcribing the real
            # speech (reproduced: every dictation came back as just the one
            # configured vocab word, regardless of what was said). Rewording
            # the prompt didn't help — dropping it did. Vocabulary is still
            # used for spelling in the cleanup step (ollama_cleanup).
            prompt = None
            # vad_filter drops trailing silence/breath noise and
            # no_repeat_ngram_size + condition_on_previous_text=False stop the
            # stuck-repeating-letter/gibberish hallucination short clips trigger
            segments, _ = self.model.transcribe(
                audio, language="en", beam_size=5, initial_prompt=prompt,
                vad_filter=True, vad_parameters=dict(min_silence_duration_ms=300),
                no_repeat_ngram_size=3, condition_on_previous_text=False)
            return " ".join(s.text.strip() for s in segments).strip()

    def reload(self, size):
        self.size = size
        self.load()
