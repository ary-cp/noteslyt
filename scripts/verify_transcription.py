"""Offline provider compatibility checks; no model or audio upload required."""
import sys
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock
import httpx
from openai import APIStatusError
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.transcription import Transcriber

def failure(code, message):
    response = httpx.Response(code, request=httpx.Request("POST", "http://test/audio/transcriptions"))
    return APIStatusError(message, response=response, body=None)

def client(model):
    t = object.__new__(Transcriber)
    t.model, t.language = model, None
    t._to_segments = lambda result, path: result
    return t

def main():
    path = Path("test.wav")
    moss = client("audio-cpp/MOSS-Transcribe-Diarize-GGUF")
    moss._create = Mock(return_value=NS(text="Hindi transcript"))
    moss.transcribe(path)
    moss._create.assert_called_once_with(path, "json", None)
    normal = client("whisper")
    normal._create = Mock(side_effect=[failure(501, "model did not report language; use response_format json"), NS(text="ok"), NS(text="ok")])
    normal.transcribe(path); normal.transcribe(path)
    assert [call.args[1:] for call in normal._create.call_args_list] == [("verbose_json", None), ("json", None), ("json", None)]
    for status in (401, 429, 500):
        t = client("whisper")
        t._create = Mock(side_effect=failure(status, "service failed"))
        try: t.transcribe(path)
        except APIStatusError: pass
        else: raise AssertionError("provider failure was hidden")
        assert t._create.call_count == 1
    print("TRANSCRIPTION COMPATIBILITY VERIFIED")

if __name__ == "__main__": main()
