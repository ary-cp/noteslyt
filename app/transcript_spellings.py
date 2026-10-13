"""Explicit, source-local spelling corrections; never phonetic guesses."""
import json
import re
from pathlib import Path


def validate_spellings(value: dict[str, str]) -> dict[str, str]:
    if not value or len(value) > 50:
        raise ValueError("Provide between 1 and 50 spelling corrections.")
    result = {}
    for old, new in value.items():
        old, new = old.strip(), new.strip()
        if not old or not new or max(len(old), len(new)) > 100 or any(c in old + new for c in "\n\r\t"):
            raise ValueError("Each spelling must be 1–100 characters on one line.")
        if old.casefold() in result:
            raise ValueError("Duplicate spelling correction.")
        result[old.casefold()] = new
    for old, new in result.items():
        if new.casefold() in result and new.casefold() != old:
            raise ValueError("Chained or circular spelling corrections are not supported.")
    return result


def correct_spellings(text: str, spellings: dict[str, str]) -> str:
    # One pass: do not cascade replacements or change substrings of other names.
    spellings = {**spellings, **{v.casefold(): v for v in spellings.values()}}
    pattern = r"(?<!\w)(?:" + "|".join(re.escape(k) for k in sorted(spellings, key=len, reverse=True)) + r")(?!\w)"
    return re.sub(pattern, lambda m: spellings.get(m.group().casefold(), m.group()), text, flags=re.IGNORECASE) if spellings else text


def spelling_path(original: Path) -> Path:
    return original.with_suffix(".spellings.json")


def load_spellings(original: Path) -> dict[str, str]:
    path = spelling_path(original)
    return validate_spellings(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else {}


def save_spellings(original: Path, spellings: dict[str, str]) -> None:
    path = spelling_path(original)
    temporary = path.with_suffix(".json.part")
    try:
        temporary.write_text(json.dumps(spellings, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
