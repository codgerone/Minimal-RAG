"""Adds the perturbation layer (independent generations 1–3) to the comparison page data already
collected for generation 0 (reports/query/v3.14/data.json), without collecting generation 0 again: the
same as the second half of `python -m rag query-compare --name v3.14`. The plans of generations 1–3 are
cached (reports/experiments/v3.14-phrasings-v8/run_plans.py), so no model is called.

Run: python reports/experiments/v3.14-perturbation/run_perturb.py
"""

from __future__ import annotations

from pathlib import Path

from rag.eval.query_compare import collect_perturbation
from rag.jsonio import read_json
from rag.paths import Workspace
from rag.reports.query_compare import write_query_page

ROOT = Path(__file__).resolve().parents[3]


def main() -> None:
    workspace = Workspace(ROOT)
    perturbation = collect_perturbation(workspace, progress=lambda label: print(label, flush=True))
    data = read_json(ROOT / "reports/query/v3.14/data.json")   # everything but the process detail
    data["perturbation"] = perturbation
    write_query_page(workspace, data)
    print("done")


if __name__ == "__main__":
    main()
