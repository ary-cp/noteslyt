"""Render eval/results/latest.json as a README-ready markdown table."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def write_table(result: dict, out: Path) -> None:
    a = result["aggregate"]
    m = result["models"]
    lines = [
        f"<!-- generated from latest.json ({result['date']}) — do not hand-edit -->",
        "",
        f"| tier | faithfulness | relevancy | context P / R | refusal | skipped |",
        f"| --- | --- | --- | --- | --- | --- |",
        f"| {result['tier']} ({result['n_items']} items) "
        f"| {a['faithfulness']:.3f} | {a['relevancy']:.3f} "
        f"| {a['precision']:.2f} / {a['recall']:.2f} "
        f"| {a['refusal']:.0%} | {result['skipped']} |",
        "",
        f"Model `{m['llm']}` + embedding `{m['embedding']}` "
        f"({m['embedding_mode']}, dim {m['embedding_dim']}), "
        f"transcription `{m['transcription']}`, seed {result['seed']}, "
        f"git `{result['git']}`, corpus `{result['corpus_sha']}`.",
        "",
    ]
    out.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    root = Path(__file__).parent / "results" / "latest.json"
    data = json.loads(root.read_text(encoding="utf-8"))
    write_table(data, Path(sys.argv[1]) if len(sys.argv) > 1 else root.with_suffix(".md"))
    print("wrote markdown")
