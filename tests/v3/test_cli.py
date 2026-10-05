import pytest

from rag.v3.adapters.config_store import ConfigurationStore
from rag.v3.adapters.e5 import E5Error
from rag.v3.adapters.openrouter import LanguageModelError
from rag.v3.cli import _ensure_llm_key, _require_healthy, build_parser, main, resolve_context


def test_v3_parser_rejects_legacy_selector_and_conflicting_choices(tmp_path):
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["search", "question", "--pipeline", "v2"])
    with pytest.raises(SystemExit):
        parser.parse_args(["search", "question", "--config", "plain_text",
                           "--configure"])
    with pytest.raises(SystemExit):
        parser.parse_args(["ingest", "--all-configs", "--config", "plain_text"])
    with pytest.raises(SystemExit):
        parser.parse_args(["browse", "--offset", "-1"])
    with pytest.raises(SystemExit):
        parser.parse_args(["chunks", "--document", "x.pdf", "--page", "0"])


def test_v3_default_and_named_config_resolve_before_runtime(tmp_path):
    store = ConfigurationStore(tmp_path / ".rag/system-v3")
    store.install_builtins()
    parser = build_parser()
    default = resolve_context(parser.parse_args(["search", "question"]), store,
                              interactive=False, default_k=3)
    assert default.configuration_name == "structured"
    assert default.selection_mode == "default"
    assert default.query_k == 3
    named = resolve_context(parser.parse_args(["ingest", "--config", "plain_text",
                                               "--force"]), store,
                            interactive=False, default_k=3)
    assert named.configuration_name == "plain_text"
    assert named.force
    assert named.index_identity != default.index_identity
    all_configs = resolve_context(parser.parse_args(["ingest", "--all-configs"]),
                                  store, interactive=False, default_k=3)
    assert all_configs.configuration is None
    assert all_configs.selection_mode == "all_configs"


def test_wizard_saves_complete_configuration_only_after_final_validation(tmp_path,
                                                                         monkeypatch, capsys):
    store = ConfigurationStore(tmp_path / ".rag/system-v3")
    store.install_builtins()
    answers = []

    def answer(prompt):
        answers.append(prompt)
        if "选择配置编号" in prompt:
            return "0"
        if "装配起点" in prompt:
            return "1"
        if "新配置 NAME" in prompt:
            return "review_variant"
        return ""

    monkeypatch.setattr("builtins.input", answer)
    context = resolve_context(build_parser().parse_args(["documents", "--configure"]),
                              store, interactive=True, default_k=3)
    assert context.configuration_name == "review_variant"
    assert context.selection_mode == "wizard"
    assert store.load("review_variant") == context.configuration
    assert "document_processor.main_parser" in capsys.readouterr().out


def test_wizard_retries_invalid_and_duplicate_names_without_saving_draft(tmp_path,
                                                                          monkeypatch, capsys):
    store = ConfigurationStore(tmp_path / ".rag/system-v3")
    store.install_builtins()
    names = iter(("bad name", "PLAIN_TEXT", "review_variant"))

    def answer(prompt):
        if "选择配置编号" in prompt:
            return "0"
        if "装配起点" in prompt:
            return "1"
        if "新配置 NAME" in prompt:
            return next(names)
        return ""

    monkeypatch.setattr("builtins.input", answer)
    context = resolve_context(build_parser().parse_args(["documents", "--configure"]),
                              store, interactive=True, default_k=3)
    assert context.configuration_name == "review_variant"
    assert store.list_names() == ("plain_text", "review_variant", "structured")
    output = capsys.readouterr().out
    assert output.count("名称不可用") == 2
    assert "索引状态" in output
    assert "插件：" in output


def test_unknown_configuration_lists_available_names(tmp_path):
    store = ConfigurationStore(tmp_path / ".rag/system-v3")
    store.install_builtins()
    with pytest.raises(ValueError, match="plain_text.*structured"):
        resolve_context(build_parser().parse_args(["search", "q", "--config", "missing"]),
                        store, interactive=False, default_k=3)


def test_fresh_workspace_requires_explicit_choice_before_installing_configs(tmp_path,
                                                                              monkeypatch):
    store = ConfigurationStore(tmp_path / ".rag/system-v3")
    args = build_parser().parse_args(["documents"])
    with pytest.raises(ValueError, match="尚无已保存配置"):
        resolve_context(args, store, interactive=False, default_k=3)
    assert store.list_names() == ()
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")
    context = resolve_context(args, store, interactive=True, default_k=3)
    assert context.configuration_name == "structured"
    assert context.selection_mode == "default"
    assert store.list_names() == ("plain_text", "structured")


def test_all_configs_reports_every_target_after_one_fails(tmp_path, monkeypatch, capsys):
    ConfigurationStore(tmp_path / ".rag/system-v3").install_builtins()
    visited = []

    def run_one(context, _args, _root, summary):
        visited.append(context.configuration_name)
        if context.configuration_name == "plain_text":
            raise ValueError("injected publication failure")
        summary.append("跳过")
        return 0

    monkeypatch.setattr("rag.v3.cli._one", run_one)
    assert main(["ingest", "--all-configs"], workspace_root=tmp_path,
                interactive=False) == 3
    assert visited == ["plain_text", "structured"]
    output = capsys.readouterr().out
    assert "plain_text | 失败 | injected publication failure" in output
    assert "structured | 跳过" in output


def test_cli_resolves_builtin_environment_override_to_new_index(tmp_path, monkeypatch):
    store = ConfigurationStore(tmp_path / ".rag/system-v3")
    store.install_builtins()
    args = build_parser().parse_args(["documents", "--config", "plain_text"])
    monkeypatch.delenv("CHUNK_SIZE", raising=False)
    monkeypatch.delenv("CHUNK_OVERLAP", raising=False)
    baseline = resolve_context(args, store, interactive=False, default_k=3)
    saved = store.load("plain_text")
    monkeypatch.setenv("CHUNK_SIZE", "401")
    monkeypatch.setenv("CHUNK_OVERLAP", "40")
    overridden = resolve_context(args, store, interactive=False, default_k=3)
    assert overridden.index_identity != baseline.index_identity
    assert overridden.configuration != saved
    assert store.load("plain_text") == saved


def test_noninteractive_missing_llm_key_is_service_error_without_prompt(tmp_path,
                                                                         monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(LanguageModelError) as raised:
        _ensure_llm_key(tmp_path, interactive=False)
    assert raised.value.code == "llm_missing_credentials"


def test_documents_reports_unbuilt_index_without_creating_vector_store(tmp_path, capsys):
    ConfigurationStore(tmp_path / ".rag/system-v3").install_builtins()
    (tmp_path / "documents").mkdir()
    assert main(["documents", "--config", "plain_text"], workspace_root=tmp_path,
                interactive=False) == 2
    assert "索引状态：不可用" in capsys.readouterr().out
    assert not (tmp_path / ".rag/system-v3/chroma").exists()


def test_invalid_default_top_k_is_configuration_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TOP_K", "0")
    assert main(["documents"], workspace_root=tmp_path, interactive=False) == 2
    assert "TOP_K 必须是正整数" in capsys.readouterr().err


def test_noninteractive_readiness_reports_issue_without_writer(monkeypatch):
    class Factory:
        index = object()
        def one(self, slot):
            assert slot == "system.index_health"
            return self
        def check(self, index, sources):
            issue = type("Issue", (), {"code": "source_new"})()
            return type("Report", (), {"usable": False, "issues": (issue,)})()

    with pytest.raises(ValueError, match="索引不可用"):
        _require_healthy(Factory(), (), interactive=False)


@pytest.mark.parametrize("error", [
    E5Error("encoding_failed", "query encoding failed"),
    LanguageModelError("llm_upstream_failed", "request failed"),
])
def test_service_errors_have_exit_code_four(tmp_path, monkeypatch, capsys, error):
    ConfigurationStore(tmp_path / ".rag/system-v3").install_builtins()

    def fail(*_args):
        raise error

    monkeypatch.setattr("rag.v3.cli._one", fail)
    assert main(["documents"], workspace_root=tmp_path, interactive=False) == 4
    assert error.code in capsys.readouterr().err
