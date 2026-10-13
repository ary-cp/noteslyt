"""Offline regression for model paths when llama-server uses a different cwd."""
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.llama_embed import LocalEmbeddingServer


def main():
    project = Path.cwd().resolve()
    data = project / "data"
    data.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="embedding-path-test-", dir=data) as tmp:
        root = Path(tmp).resolve()
        settings = SimpleNamespace(
            embedding_local_dir=root.relative_to(project),
            embedding_local_model_path=None,
            embedding_local_mmproj_path=None,
            embedding_local_model_file="weights.gguf",
            embedding_local_mmproj_file="projector.gguf",
            embedding_local_ctx=4096,
            embedding_local_ubatch_size=512,
            embedding_local_batch_size=512,
            embedding_local_video_fps=1,
            embedding_local_host="127.0.0.1",
            embedding_local_port=8899,
        )
        server = LocalEmbeddingServer(settings)
        assert server.root == root and server.root.is_absolute()
        expected = (root / "models/weights.gguf", root / "models/projector.gguf")
        for path in expected:
            path.touch()
        for mode in ("managed", "relative overrides", "absolute overrides"):
            if mode != "managed":
                paths = tuple(p.relative_to(project) for p in expected) if mode == "relative overrides" else expected
                settings.embedding_local_model_path, settings.embedding_local_mmproj_path = paths
            actual = server.ensure_model_files()
            assert actual == expected
            with patch.object(server, "binary", return_value=root / "engine/llama-server.exe"):
                cmd = server._server_command(*actual)
            for flag, path in zip(("--model", "--mmproj"), expected):
                argument = Path(cmd[cmd.index(flag) + 1])
                assert argument.is_absolute() and (server.root / argument) == path
                assert argument.is_file()
            print(f"PASS: {mode} resolve correctly from child working directory")
        settings.embedding_local_model_path = root / "missing.gguf"
        try:
            server.model_files()
            raise AssertionError("Missing explicit model path was accepted")
        except RuntimeError as exc:
            assert "override does not exist" in str(exc)
        print("PASS: missing override fails before launch/download")
    print("EMBEDDING PATHS VERIFIED")


if __name__ == "__main__":
    main()
