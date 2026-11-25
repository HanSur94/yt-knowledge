"""Build-time check that faster-whisper can decode audio with the installed PyAV.

Run by the Dockerfile. A decoder that breaks on a dependency bump (av 19
dropped an argument faster-whisper passes) must fail the build, not fail
every video at runtime. No model download: decoding is the part that broke.
"""
import math
import struct
import tempfile
import wave

from faster_whisper import decode_audio

with tempfile.NamedTemporaryFile(suffix=".wav") as fh:
    with wave.open(fh.name, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(i / 10)))
                               for i in range(16000)))
    samples = decode_audio(fh.name)
    assert len(samples) == 16000, len(samples)
print("asr smoke: decode ok")
