"""Local speech-to-text backend for JARVIS.

The module deliberately imports faster-whisper lazily so macOS and other
non-local-STT installs do not acquire a hard runtime dependency merely by
importing the server.
"""

from __future__ import annotations

import io
import threading


class LocalSTT:
    """One serialized faster-whisper model shared by the voice server."""

    def __init__(
        self,
        model_name: str = "small.en",
        device: str = "cuda",
        compute_type: str = "float16",
    ) -> None:
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self._model = None
        self._lock = threading.Lock()

    def _get_model(self):
        if self._model is None:
            from faster_whisper import WhisperModel

            self._model = WhisperModel(
                self.model_name,
                device=self.device,
                compute_type=self.compute_type,
            )
        return self._model

    def warm(self) -> None:
        """Load the model without performing recognition."""
        with self._lock:
            self._get_model()

    def transcribe(self, audio: bytes) -> str:
        """Return one final English transcript from encoded audio bytes."""
        if not audio:
            return ""

        with self._lock:
            model = self._get_model()

            segments, _ = model.transcribe(
                io.BytesIO(audio),
                language="en",
                beam_size=1,
                vad_filter=False,
                condition_on_previous_text=False,
            )

            return " ".join(
                segment.text.strip()
                for segment in segments
                if segment.text.strip()
            ).strip()
