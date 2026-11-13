"""How large is the smallest real change? Each sampled chunk text gets one minimal edit, and the
‖Δv‖ between the edited and the original text (both encoded alone, same process) is recorded.
Compared with the batch-noise bound in noise.json, this tells how much room there is between the two.

Sample: 100 random distinct chunk texts of the eight indexes plus the 20 longest (a one-character
edit weighs least in a long text). Edits, one at a time, at a seeded random position:
  delete_char     delete one non-space character
  replace_letter  replace one letter with another letter
  replace_digit   replace one digit with another digit (texts without digits are skipped)
  replace_word    replace one word (≥ 3 letters) with a word of the same length from another text
  space_trailing  add one space at the end
  space_double    turn one space into two (texts without spaces are skipped)

Usage: python reports/experiments/v4.1.1-vectors/perturb.py   (after run.py; writes perturb.json)
"""

from __future__ import annotations

import json
import random
import re
import sys

import numpy as np

from run import CONFIGS, HERE, ROOT, load

sys.path.insert(0, str(ROOT))


def edits(text: str, rng: random.Random, words: list[str]) -> dict[str, str]:
    out = {}
    chars = [i for i, c in enumerate(text) if not c.isspace()]
    i = rng.choice(chars)
    out["delete_char"] = text[:i] + text[i + 1:]
    letters = [i for i, c in enumerate(text) if c.isalpha() and c.isascii()]
    if letters:
        i = rng.choice(letters)
        c = text[i]
        pool = "abcdefghijklmnopqrstuvwxyz" if c.islower() else "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        out["replace_letter"] = text[:i] + rng.choice(pool.replace(c, "")) + text[i + 1:]
    digits = [i for i, c in enumerate(text) if c.isdigit()]
    if digits:
        i = rng.choice(digits)
        out["replace_digit"] = text[:i] + rng.choice("0123456789".replace(text[i], "")) + text[i + 1:]
    found = [m for m in re.finditer(r"[A-Za-zÀ-ÿ]{3,}", text)]
    if found:
        m = rng.choice(found)
        same = [w for w in words if len(w) == len(m.group()) and w.lower() != m.group().lower()]
        if same:
            out["replace_word"] = text[:m.start()] + rng.choice(same) + text[m.end():]
    out["space_trailing"] = text + " "
    spaces = [i for i, c in enumerate(text) if c == " "]
    if spaces:
        i = rng.choice(spaces)
        out["space_double"] = text[:i] + " " + text[i:]
    return out


def main() -> None:
    from rag.index.embedder import bge_m3
    texts = sorted({t for c in CONFIGS for t, _ in load(ROOT / ".rag" / "indexes" / c).values()})
    rng = random.Random(4)
    longest = sorted(texts, key=len, reverse=True)[:20]
    sample = longest + rng.sample([t for t in texts if t not in longest], 100)
    words = sorted({w for t in texts for w in re.findall(r"[A-Za-zÀ-ÿ]{3,}", t)})
    embedder = bge_m3()
    encode = lambda t: np.asarray(embedder.encode_passages([t])[0], dtype=np.float64)  # noqa: E731
    norms: dict[str, list[float]] = {}
    for k, text in enumerate(sample):
        base = encode(text)
        for kind, edited in edits(text, random.Random(k), words).items():
            norms.setdefault(kind, []).append(float(np.linalg.norm(encode(edited) - base)))
    bound = json.loads((HERE / "noise.json").read_text(encoding="utf-8"))["bound"]
    result = {"sample": len(sample), "longest_included": len(longest), "bound": bound, "kinds": {
        kind: {"n": len(v), "min": min(v), "p05": float(np.percentile(v, 5)), "median": float(np.median(v)),
               "zero": sum(x == 0 for x in v), "within_bound": sum(x <= bound for x in v),
               "within_10x_bound": sum(x <= 10 * bound for x in v)}
        for kind, v in norms.items()}}
    (HERE / "perturb.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    for kind, r in result["kinds"].items():
        print(kind, r)


if __name__ == "__main__":
    main()
