"""V3.8 diagnostic: run the normal evaluation with questions replaced by translations.

usage: python run_translated.py <config> <translations.json>
"""
import json
import shutil
import sys
from dataclasses import replace
from pathlib import Path

import rag.eval.runner as runner
from rag.config import resolve_config
from rag.registry import assemble
from rag.paths import Workspace

config_name, translations_path = sys.argv[1], Path(sys.argv[2])
translated = json.loads(translations_path.read_text(encoding="utf-8"))["questions"]

original_load = runner.load_dataset
original_name = runner.run_name


def load_translated(folder):
    dataset = original_load(folder)
    missing = [c.case_id for c in dataset.cases if c.case_id not in translated]
    if missing:
        raise SystemExit(f"缺少译文：{missing}")
    return replace(dataset, cases=tuple(replace(c, question=translated[c.case_id]) for c in dataset.cases))


runner.load_dataset = load_translated
runner.run_name = lambda *a: original_name(*a) + "_translated"

workspace = Workspace(Path.cwd())
assembly = assemble(resolve_config(workspace.root, config_name))
folder = runner.run_evaluation(workspace, assembly, assembly.config.eval_top_k)
shutil.copy(translations_path, folder / "translated-questions.json")
# keep the diagnostic out of later runs' "change since last run" comparison
result_path = folder / "result.json"
result = json.loads(result_path.read_text(encoding="utf-8"))
result["dataset"]["version"] += "+translated"
result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(folder)
