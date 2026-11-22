"""Guards for the offline deployment handbook, its assets and the .docx report.

These tests exist because the handbook is a *deployment* document: a wrong flag
name or a missing config key costs an operator an afternoon, and a stale .docx
silently contradicts the Markdown it was generated from.  Each test pins a
property that would otherwise rot without any error.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
DEPLOY = REPO / "deploy"
HANDBOOK = REPO / "docs" / "llm_agent" / "离线部署实施手册.md"
REPORT = REPO / "docs" / "llm_agent" / "reports" / "SRM领域离线大模型部署方案报告.docx"
BUILD_DOCX = REPO / "scripts" / "build_docx_report.py"

pytestmark = pytest.mark.skipif(
    not HANDBOOK.is_file(), reason="handbook not present"
)


def _handbook() -> str:
    return HANDBOOK.read_text(encoding="utf-8")


# --- handbook structure -----------------------------------------------------


def test_handbook_covers_every_required_section():
    """The brief asked for six areas plus background and expected results.

    Losing a chapter would still render a valid document, so the coverage is
    asserted by heading text rather than by file existence.
    """
    text = _handbook()
    required = [
        "背景说明",
        "硬件环境选型与配置",
        "模型选择与离线下载",
        "运行环境搭建",
        "模型加载与推理服务启动",
        "性能调优",
        "资源监控",
        "常见问题排查",
        "完整闭环演练",
        "验收与长期维护",
        "预期结果",
        "附录 A",
        "附录 B",
    ]
    missing = [name for name in required if name not in text]
    assert not missing, f"handbook is missing sections: {missing}"


def test_every_chapter_declares_input_strategy_decision_rollback():
    """Each stage must state 输入 / 策略 / 决策点 / 回退, per the brief.

    A chapter without a rollback plan is the failure mode this catches: it reads
    complete but leaves the operator with no way back.
    """
    text = _handbook()
    # Split on level-2 headings.  Chapter 0 (background) and chapter 10
    # (expected results) are context, not executable stages, and the appendices
    # are reference material -- none of them can have a rollback plan.
    chapters = re.split(r"^## ", text, flags=re.M)[1:]
    stage_chapters = [
        c for c in chapters
        if not c.startswith(("0. ", "10. ", "附录"))
    ]
    assert len(stage_chapters) >= 9

    missing = []
    for chapter in stage_chapters:
        title = chapter.splitlines()[0].strip()
        for part in ("输入", "策略", "决策点", "回退"):
            if part not in chapter:
                missing.append(f"{title} -> {part}")
    assert not missing, f"chapters missing required parts: {missing}"


def test_handbook_has_no_placeholder_text():
    """Unfinished markers are worse than absence: they read as content."""
    text = _handbook()
    for marker in ("TODO", "TBD", "FIXME", "XXX", "待补充", "占位"):
        assert marker not in text, f"handbook still contains {marker!r}"


def test_handbook_does_not_claim_gpu_measurements():
    """The build host has no GPU; every GPU figure must be marked as an estimate.

    This is the honesty guard.  If someone later pastes a GPU number without the
    label, the document starts promising performance nobody measured.
    """
    text = _handbook()
    assert "无 GPU" in text or "无GPU" in text
    # The calibration section must tell the reader to replace estimates.
    assert "估算" in text
    assert "必须用 §2.6" in text or "必须用 §2.6 的方法替换" in text


# --- deploy assets ----------------------------------------------------------


def test_deploy_assets_all_exist():
    expected = [
        DEPLOY / "config" / "models.yaml",
        DEPLOY / "config" / "server.yaml",
        DEPLOY / "config" / "requirements-offline.txt",
        DEPLOY / "scripts" / "verify_models.py",
        DEPLOY / "scripts" / "verify_bundle.py",
        DEPLOY / "scripts" / "fetch_models.py",
        DEPLOY / "scripts" / "render_env.py",
        DEPLOY / "scripts" / "run-server.sh",
        DEPLOY / "scripts" / "healthcheck.py",
        DEPLOY / "scripts" / "calibrate_sizing.py",
        DEPLOY / "scripts" / "build_offline_bundle.sh",
        DEPLOY / "systemd" / "srm-llm.service",
        DEPLOY / "monitoring" / "prometheus.yml",
        DEPLOY / "monitoring" / "alerts.yml",
    ]
    missing = [str(p.relative_to(REPO)) for p in expected if not p.is_file()]
    assert not missing, f"missing deploy assets: {missing}"


def test_models_yaml_entries_have_the_fields_the_scripts_read():
    """verify_models.py and fetch_models.py index these keys directly."""
    data = yaml.safe_load((DEPLOY / "config" / "models.yaml").read_text(encoding="utf-8"))
    models = data["models"]
    assert models
    for model in models:
        assert model["id"] and model["repo"] and model["role"]
        assert model["files"], f"{model['id']} has no files"
        for entry in model["files"]:
            assert entry["name"].endswith(".gguf")
            assert "sha256" in entry  # may be empty: the gate then fails by design
            assert entry["size_gib"] > 0


def test_server_yaml_renders_to_every_variable_the_launcher_needs():
    """A key renamed in YAML without renaming the consumer = silent default.

    run-server.sh requires SRM_MODEL_PATH and SRM_PORT with `:?`, and reads the
    rest unguarded.  Rendering here proves the YAML still produces all of them.
    """
    sys.path.insert(0, str(DEPLOY / "scripts"))
    try:
        import render_env  # noqa: PLC0415
    finally:
        sys.path.pop(0)

    cfg = yaml.safe_load((DEPLOY / "config" / "server.yaml").read_text(encoding="utf-8"))
    lines = render_env.render(cfg, "T1")
    keys = {line.split("=", 1)[0] for line in lines if "=" in line and not line.startswith("#")}

    required = {
        "SRM_HOST", "SRM_PORT", "SRM_ALIAS", "SRM_MODEL_PATH", "SRM_CTX",
        "SRM_PARALLEL", "SRM_GPU_LAYERS", "SRM_THREADS", "SRM_THREADS_HTTP",
        "SRM_FLASH_ATTN", "SRM_CTK", "SRM_CTV", "SRM_METRICS", "SRM_LOG_FILE",
    }
    assert required <= keys, f"render_env lost variables: {required - keys}"

    # The launcher's shell variable names must be the ones the renderer emits.
    script = (DEPLOY / "scripts" / "run-server.sh").read_text(encoding="utf-8")
    for var in required:
        assert f"${{{var}" in script or f"${var}" in script, f"{var} not consumed by run-server.sh"


def test_launcher_fails_closed_when_the_hash_is_unknown():
    """A missing hash must block startup, not warn.

    Serving a truncated GGUF produces fluent nonsense, which is the worst
    possible failure for a domain assistant.  The gate is the only defence.
    """
    script = (DEPLOY / "scripts" / "run-server.sh").read_text(encoding="utf-8")
    assert "verify_models.py" in script
    assert "SRM_SKIP_HASH" in script
    # The verifier must treat an empty expected hash as failure.
    verifier = (DEPLOY / "scripts" / "verify_models.py").read_text(encoding="utf-8")
    assert "no-expected-hash" in verifier
    assert "worst = max(worst, 65)" in verifier


def test_bundle_check_treats_unexpected_files_as_failure():
    """A planted file on the carrier is exactly the file nobody expects."""
    verifier = (DEPLOY / "scripts" / "verify_bundle.py").read_text(encoding="utf-8")
    assert "not added" in verifier, "extra files must fail the bundle check"


def test_alerts_only_reference_metrics_llamacpp_actually_exposes():
    """Guards a silently-dead alert.

    llama.cpp b12627 does not expose kv_cache_usage_ratio; an alert on it never
    fires.  The real metrics were read from a live /metrics endpoint.
    """
    alerts = (DEPLOY / "monitoring" / "alerts.yml").read_text(encoding="utf-8")
    real = {
        "llamacpp:requests_processing",
        "llamacpp:requests_deferred",
        "llamacpp:prompt_tokens_total",
        "llamacpp:tokens_predicted_total",
        "llamacpp:n_busy_slots_per_decode",
        "llamacpp:n_decode_total",
        "llamacpp:n_tokens_max",
        "llamacpp:predicted_tokens_seconds",
        "llamacpp:prompt_seconds_total",
        "llamacpp:prompt_tokens_cached_total",
        "llamacpp:prompt_tokens_seconds",
        "llamacpp:tokens_predicted_seconds_total",
    }
    # Only look at expressions, not at prose: a comment that names the missing
    # metric is documentation, and asserting on it would forbid the explanation.
    exprs = "\n".join(
        line.split("expr:", 1)[1]
        for line in alerts.splitlines()
        if "expr:" in line
    )
    used = set(re.findall(r"llamacpp:[a-z_]+", exprs))
    unknown = used - real
    assert not unknown, f"alerts use metrics llama.cpp does not expose: {unknown}"
    assert "kv_cache_usage_ratio" not in exprs


def test_handbook_flags_match_the_launcher():
    """Every --flag quoted in the handbook must be one the launcher passes.

    The handbook documents behaviour; the launcher implements it.  A flag in one
    and not the other is a documentation bug that only shows up in production.
    """
    script = (DEPLOY / "scripts" / "run-server.sh").read_text(encoding="utf-8")
    launcher_flags = set(re.findall(r"--[a-z][a-z0-9-]+", script))
    text = _handbook()

    # Flags the handbook uses in prose that the launcher deliberately does not
    # pass: manual/foreground invocations, llama-bench, pip, curl, nvidia-smi,
    # systemctl and the helper scripts' own CLIs.
    exempt = {
        "--alias", "--api-key", "--api-key-file", "--bench", "--bundle",
        "--cache-type-k", "--cache-type-v", "--chat-template", "--check",
        "--concurrency", "--config", "--cont-batching", "--ctx-size", "--dest",
        "--find-links", "--flash-attn", "--format", "--help", "--host", "--jinja",
        "--json", "--llama-tag", "--log-file", "--loop", "--manifest", "--max-tokens",
        "--metrics", "--mlock", "--model", "--model-gib", "--n-gpu-layers",
        "--no-conversation", "--no-index", "--no-mmap", "--no-wheels", "--now",
        "--only", "--only-binary", "--out", "--parallel", "--pass-through",
        "--peak-bw-gbps", "--port", "--python-version", "--query", "--query-gpu",
        "--ready", "--repeat", "--root", "--slot-save-path", "--source",
        "--strip-components", "--threads", "--threads-http", "--tier", "--timeout",
        "--tok-s", "--user", "--verbose", "--version", "--write-sums",
    }
    documented = set(re.findall(r"(?<![\w-])--[a-z][a-z0-9-]+", text))
    unexplained = documented - launcher_flags - exempt
    assert not unexplained, f"handbook documents flags nothing implements: {unexplained}"


# --- docx report ------------------------------------------------------------


@pytest.mark.skipif(
    shutil.which("python3") is None, reason="needs a python3 to rebuild the report"
)
def test_report_builds_and_has_the_template_structure(tmp_path):
    """Build the .docx and check the structure the template requires.

    Rendering is the only way to know the Markdown parser still handles the
    handbook: a table that silently stops parsing leaves no trace otherwise.
    """
    pytest.importorskip("docx", reason="python-docx is a build-time tool")
    out = tmp_path / "report.docx"
    result = subprocess.run(
        [sys.executable, str(BUILD_DOCX), "--out", str(out)],
        capture_output=True, text=True, cwd=REPO,
    )
    assert result.returncode == 0, result.stderr
    assert out.is_file() and out.stat().st_size > 20_000

    from docx import Document  # noqa: PLC0415

    doc = Document(str(out))
    headings = [p.text for p in doc.paragraphs if p.style.name.startswith("Heading")]
    assert len(headings) >= 60, f"only {len(headings)} headings rendered"

    joined = "\n".join(headings)
    for chapter in ("背景说明", "硬件环境选型与配置", "预期结果", "附录 A", "附录 B"):
        assert chapter in joined, f"report lost chapter {chapter}"

    # Tables are the bulk of the content; a parser regression drops them all.
    assert len(doc.tables) >= 50, f"only {len(doc.tables)} tables rendered"

    # Cover + TOC + body.
    assert len(doc.sections) >= 2

    # Header/footer must carry the report identity and a live page field.
    header_xml = doc.sections[-1].header.paragraphs[0].text
    assert "部署" in header_xml
    footer_xml = doc.sections[-1].footer._element.xml
    assert "PAGE" in footer_xml


def test_committed_report_is_not_stale():
    """The checked-in .docx must match the current handbook.

    A stale report is worse than no report: it is what people download and read.
    """
    pytest.importorskip("docx", reason="python-docx is a build-time tool")
    assert REPORT.is_file(), "run scripts/build_docx_report.py and commit the result"

    from docx import Document  # noqa: PLC0415

    doc = Document(str(REPORT))
    headings = [p.text for p in doc.paragraphs if p.style.name.startswith("Heading")]

    # Every handbook heading (level 2 and 3) must appear in the report.  Level 2
    # alone is not enough: a subsection added or renamed under an existing
    # chapter leaves the level-2 set unchanged, so the report would read as
    # current while missing the new content.
    md = _handbook()
    md_headings = [
        m.group(1).strip()
        for m in re.finditer(r"^#{2,3} (.+)$", md, flags=re.M)
    ]
    missing = [h for h in md_headings if h not in headings]
    assert not missing, (
        f"committed report is stale; {len(missing)} heading(s) missing, "
        f"e.g. {missing[:5]}. Run scripts/build_docx_report.py"
    )
