"""Desktop startup remains available when optional AI providers are offline."""
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
import httpx
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import Settings
from app.main import app
from app.llm import NativeUnsupportedError


def main():
    request = httpx.Request("POST", "http://127.0.0.1:1/embeddings")
    failures = [
        httpx.ConnectError("offline"),
        httpx.ReadTimeout("provider stalled"),
        httpx.HTTPStatusError("unavailable", request=request, response=httpx.Response(503, request=request)),
        TimeoutError("startup budget exhausted"),
        NativeUnsupportedError("text-only endpoint"),
    ]
    for failure in failures:
        with tempfile.TemporaryDirectory(prefix="noteslyt-offline-", ignore_cleanup_errors=True) as tmp:
            settings = Settings(_env_file=None, database_url="", data_dir=Path(tmp),
                auth_secret_key="offline-startup-test-secret-123456789", embedding_dim=768,
                embedding_mode="cloud", transcription_mode="cloud",
                audio_embed_mode="native", video_embed_mode="native",
                llm_base_url="http://127.0.0.1:1/v1", llm_api_key="test-only")
            with patch("app.main.get_settings", return_value=settings), patch("app.main.verify_native_support", side_effect=failure), TestClient(app) as client:
                assert client.get("/health").status_code == 200
                result = client.post("/auth/signup", json={"email":"offline@example.com", "password":"Offline-test-password-123", "display_name":"Test"})
                assert result.status_code in (200,201), result.text
                expected = "transcript" if isinstance(failure, NativeUnsupportedError) else "native"
                assert settings.audio_embed_mode == settings.video_embed_mode == expected
                print(f"PASS: {type(failure).__name__}: API/signup available; modes={expected}")
            app.state.db.close()
            app.state.store._client._system.stop()
    print("OFFLINE STARTUP VERIFIED")


if __name__ == "__main__":
    main()
