"""Multimodal embedding probe: does the configured EMBEDDING_* endpoint really
encode bytes, or does it silently embed the data-URI text?

Run:  uv run python scripts/probe_multimodal.py [--save PATH]

Method (the 2026-10-09 manual probe, automated): embed a text query plus two
*maximally different* blobs per modality, then compare cosines. A joint
model puts different blobs far apart (audio sine vs noise ~= 0.3-0.7, two
different images clearly below 0.9). An engine that tokenizes the data URI
as text returns near-identical vectors for any two blobs (~0.95+).

Exit 0 + PASS when every modality separates; exit 1 + FAIL_SAFE_TO_TRANSCRIPT
otherwise. Never touches the database.
"""

from __future__ import annotations

import argparse
import io
import math
import random
import struct
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.llm import build_embedder


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)


def sine_wav(freq: float, seconds: float = 3.0, rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        n = int(rate * seconds)
        frames = b"".join(
            struct.pack("<h", int(10000 * math.sin(2 * math.pi * freq * i / rate)))
            for i in range(n)
        )
        w.writeframes(frames)
    return buf.getvalue()


def noise_wav(seconds: float = 3.0, rate: int = 16000) -> bytes:
    rng = random.Random(7)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        n = int(rate * seconds)
        w.writeframes(b"".join(struct.pack("<h", rng.randint(-10000, 10000)) for _ in range(n)))
    return buf.getvalue()


def solid_png(color: tuple[int, int, int], size: tuple[int, int] = (64, 64)) -> bytes:
    from PIL import Image

    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def checker_png(size: tuple[int, int] = (64, 64)) -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", size, (10, 10, 10))
    d = ImageDraw.Draw(img)
    step = 8
    for y in range(0, size[1], step):
        for x in range(0, size[0], step):
            if (x + y) // step % 2 == 0:
                d.rectangle([x, y, x + step - 1, y + step - 1], fill=(240, 240, 240))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", default=None, help="append one line of results to PATH")
    args = ap.parse_args()

    settings = get_settings()
    ep = settings.endpoint("embedding")  # type: ignore[arg-type]
    print(f"endpoint: {ep['model']} @ {ep['base_url']}")
    if settings.embedding_mode == "local":
        # The managed server lives behind the app lifespan; init files here so
        # the probe works standalone (first run downloads engine + model).
        from app.llama_embed import init_embedding_server

        init_embedding_server(settings)
    embedder = build_embedder(settings)
    if not hasattr(embedder, "embed_image") or not hasattr(embedder, "embed_audio"):
        print("FAIL: embedder has no native byte path; transcript modes only.")
        return 1

    text = embedder.embed(["thermodynamics entropy heat transfer"])[0]
    print(f"text dim: {len(text)}")
    errors: dict[str, str] = {}

    def guarded(label: str, fn):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — an engine error is a verdict, not a crash
            errors[label] = f"{type(exc).__name__}: {str(exc)[:160]}"
            print(f"  [ERROR] {label}: {errors[label]}")
            return None

    audio_a = guarded("audio embed", lambda: embedder.embed_audio(sine_wav(440.0), "audio/wav"))
    audio_b = embedder.embed_audio(noise_wav(), "audio/wav") if audio_a is not None else None
    image_a = guarded("image embed", lambda: embedder.embed_image(solid_png((200, 30, 30)), "image/png")) if audio_a is not None else None
    image_b = None
    if image_a is not None:
        image_b = guarded("image embed", lambda: embedder.embed_image(checker_png(), "image/png"))

    results: dict[str, float] = {}
    verdicts: dict[str, bool] = {}
    if audio_a is not None and audio_b is not None:
        results["audio_diff_blobs"] = cosine(audio_a, audio_b)
        verdicts["audio_diff_blobs"] = results["audio_diff_blobs"] < 0.90
    if image_a is not None and image_b is not None:
        results["image_diff_blobs"] = cosine(image_a, image_b)
        verdicts["image_diff_blobs"] = results["image_diff_blobs"] < 0.90
    if audio_a is not None:
        results["text_vs_audio"] = cosine(text, audio_a)
        verdicts["text_vs_audio"] = results["text_vs_audio"] < 0.95
    ok = bool(verdicts) and all(verdicts.values())
    for key, value in results.items():
        print(f"  [{'PASS' if verdicts[key] else 'FAIL'}] cosine {key} = {value:.4f}")
    if args.save:
        with open(args.save, "a") as fh:
            fh.write(f"{ep['model']} @ {ep['base_url']} " + " ".join(f"{k}={v:.4f}" for k, v in results.items()) + f" {'PASS' if ok else 'FAIL'}\n")
    if ok:
        print("NATIVE OK — the endpoint encodes bytes; native audio/video modes are safe.")
        return 0
    print("FAIL_SAFE_TO_TRANSCRIPT — the endpoint embeds URI text, not bytes.")
    print("Keep AUDIO_EMBED_MODE=transcript (and video captions). To go native, serve")
    print("a joint model (e.g. EmbeddingGemma-2-GGUF + mmproj) from llama.cpp and re-run this probe.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
