"""验证四工具统一提取与下游依赖补齐。"""

from __future__ import annotations

from argparse import Namespace
import json
from pathlib import Path

import pytest

from experiments.table_extraction.bootstrap import cli
from experiments.table_extraction.presentation import workflow


def _write_manifest(root: Path, tool: str, stem: str, payload: dict[str, object]) -> None:
    """在临时工具目录写入一个 manifest。"""
    path = root / tool / stem / "manifest.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_run_all_extractors_continues_after_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    """一种工具失败后仍执行剩余工具，并汇总返回失败项。"""
    calls: list[str] = []

    def result(tool: str, succeeded: bool = True, error: bool = False):
        def execute(_: Path) -> bool:
            calls.append(tool)
            if error:
                raise RuntimeError("broken")
            return succeeded
        return execute

    extractors = (
        ("pymupdf", result("pymupdf")),
        ("camelot", result("camelot", succeeded=False)),
        ("unstructured", result("unstructured", error=True)),
        ("docling", result("docling")),
    )

    failures = workflow.run_all_extractors(Path("sample.pdf"), extractors)

    assert calls == ["pymupdf", "camelot", "unstructured", "docling"]
    assert failures == [
        "camelot: one or more strategies failed",
        "unstructured: RuntimeError: broken",
    ]


def test_extraction_readiness_checks_all_tool_manifests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """四工具 manifest 的来源、策略和成功状态共同决定是否完整。"""
    pdf_path = (tmp_path / "sample.pdf").resolve()
    pdf_path.touch()
    output_root = tmp_path / "output"
    source = str(pdf_path)
    _write_manifest(output_root, "pymupdf", pdf_path.stem, {
        "tool": "pymupdf",
        "source_file": source,
        "strategies": {name: 0 for name in workflow.STRATEGIES},
    })
    _write_manifest(output_root, "camelot", pdf_path.stem, {
        "tool": "camelot",
        "source_pdf": source,
        "runs": [
            {"flavor": flavor, "status": "success"}
            for flavor in workflow.SUPPORTED_FLAVORS
        ],
    })
    _write_manifest(output_root, "unstructured", pdf_path.stem, {
        "tool": "unstructured",
        "source_pdf": source,
        "status": "success",
    })
    _write_manifest(output_root, "docling", pdf_path.stem, {
        "tool": "docling",
        "source_pdf": source,
        "status": "success",
    })

    assert workflow.incomplete_extractions(output_root, pdf_path) == []

    camelot_path = output_root / "camelot" / pdf_path.stem / "manifest.json"
    camelot_path.write_text(json.dumps({
        "tool": "camelot",
        "source_pdf": source,
        "runs": [{"flavor": "lattice", "status": "failed"}],
    }), encoding="utf-8")
    assert workflow.extraction_issue(output_root, "camelot", pdf_path) == (
        "未成功的 parser: lattice, stream, network, hybrid"
    )


def test_confirm_retries_invalid_answer_and_defaults_to_no(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Y/N 交互会重试无效输入，空输入按拒绝处理。"""
    answers = iter(["maybe", "Y"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    assert workflow.confirm("continue") is True

    monkeypatch.setattr("builtins.input", lambda _: "")
    assert workflow.confirm("continue") is False


def test_grouping_dependency_decline_stops_before_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """用户拒绝时不得执行自动提取。"""
    monkeypatch.setattr(workflow, "incomplete_extractions", lambda *_: ["docling: missing"])
    monkeypatch.setattr(workflow, "extraction_issue", lambda *args: "missing")
    monkeypatch.setattr(workflow, "confirm", lambda _: False)

    with pytest.raises(SystemExit, match="操作已取消"):
        workflow.ensure_extractions(
            Path("sample.pdf"), Path("output"),
            lambda _: pytest.fail("extractors must not run after N"),
        )


def test_grouping_can_decline_optional_extraction_and_continue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """只有可选工具缺失时，用户拒绝补齐仍可继续分组。"""
    monkeypatch.setattr(workflow, "incomplete_extractions", lambda *_: ["camelot: missing"])
    monkeypatch.setattr(
        workflow,
        "extraction_issue",
        lambda _, tool, __: None if tool == "docling" else "missing",
    )
    monkeypatch.setattr(workflow, "confirm", lambda _: False)

    workflow.ensure_extractions(
        Path("sample.pdf"), Path("output"),
        lambda _: pytest.fail("extractors must not run after N"),
    )


def test_grouping_dependency_acceptance_extracts_and_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """用户同意后补齐提取，并在复检通过时返回下游流程。"""
    inspections = iter([["docling: missing"], []])
    monkeypatch.setattr(workflow, "incomplete_extractions", lambda *_: next(inspections))
    monkeypatch.setattr(workflow, "extraction_issue", lambda *_: None)
    monkeypatch.setattr(workflow, "confirm", lambda _: True)
    calls: list[Path] = []

    pdf_path = Path("sample.pdf")
    workflow.ensure_extractions(
        pdf_path, Path("output"), lambda path: calls.append(path) or [],
    )

    assert calls == [pdf_path]


def test_scoring_acceptance_completes_extraction_and_grouping_chain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """评分缺少分组时，一次确认可按顺序补齐依赖并继续评分。"""
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.touch()
    monkeypatch.setattr(cli, "parse_scoring_args", lambda: Namespace(pdf=pdf_path, output_dir=None))
    monkeypatch.setattr(cli, "_grouping_issue", lambda _: "groups.json 缺失")
    monkeypatch.setattr(cli, "_incomplete_extractions", lambda _: ["docling: missing"])
    monkeypatch.setattr(cli, "_confirm", lambda _: True)
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(
        cli,
        "_ensure_extractions",
        lambda path, already_confirmed=False: calls.append(("extract", already_confirmed)),
    )
    monkeypatch.setattr(
        cli,
        "_execute_grouping",
        lambda path, root: calls.append(("grouping", root)) or root / path.stem / "groups.json",
    )
    report_path = cli.PROJECT_ROOT / "tmp" / "scoring.json"
    monkeypatch.setattr(
        cli,
        "run_scoring",
        lambda path, tools_root, output_dir: calls.append(("scoring", output_dir)) or report_path,
    )

    cli.run_table_scoring()

    assert calls[0] == ("extract", True)
    assert calls[1] == ("grouping", cli.DEFAULT_GROUPING_OUTPUT_ROOT)
    assert calls[2][0] == "scoring"


def test_scoring_rebuilds_grouping_after_extraction_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """分组虽存在但提取不完整时，补齐提取后必须重建分组。"""
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.touch()
    monkeypatch.setattr(cli, "parse_scoring_args", lambda: Namespace(pdf=pdf_path, output_dir=None))
    monkeypatch.setattr(cli, "_grouping_issue", lambda _: None)
    monkeypatch.setattr(cli, "_incomplete_extractions", lambda _: ["camelot: missing"])
    monkeypatch.setattr(cli, "_confirm", lambda _: True)
    calls: list[str] = []
    monkeypatch.setattr(
        cli,
        "_ensure_extractions",
        lambda path, already_confirmed=False: calls.append("extract"),
    )
    monkeypatch.setattr(
        cli,
        "_execute_grouping",
        lambda path, root: calls.append("grouping") or root / path.stem / "groups.json",
    )
    report_path = cli.PROJECT_ROOT / "tmp" / "scoring.json"
    monkeypatch.setattr(
        cli,
        "run_scoring",
        lambda path, tools_root, output_dir: calls.append("scoring") or report_path,
    )

    cli.run_table_scoring()

    assert calls == ["extract", "grouping", "scoring"]
