"""Drift guard for the offline-agent sizing tables.

The numbers in `docs/llm_agent/离线SRM领域AI智能体部署方案.md` are quoted in
procurement decisions, so the script that produces them must keep producing the
same ones.  If a formula or a constant changes, this fails and the document has
to be re-issued instead of silently disagreeing with its own script.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "llm_sizing.py"


def _load():
    spec = importlib.util.spec_from_file_location("llm_sizing", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_kv_cache_matches_the_documented_per_token_growth():
    m = _load()
    # 32B: 64 layers, 8 KV heads, head_dim 128, fp16 -> 256 KiB/token = 2.0 GiB @8k
    kv = m.kv_bytes_per_token(64, 8, 128)
    assert kv == 262144
    assert round(kv * 8192 / m.GIB, 1) == 2.0
    assert round(kv * 32768 / m.GIB, 1) == 8.0
    # MoE with 4 KV heads pays a quarter of that: 96 KiB/token
    assert m.kv_bytes_per_token(48, 4, 128) == 98304


def test_decode_throughput_scales_with_bandwidth_and_falls_with_context():
    m = _load()
    kv = m.kv_bytes_per_token(64, 8, 128)
    short = m.decode_tok_s(960.0, 19.9, kv, 8192)
    long = m.decode_tok_s(960.0, 19.9, kv, 32768)
    assert short > long
    # documented figure for a 48 GB card on 32B-Q4 @8k is ~27 tok/s
    assert 25 < short < 29
    assert abs(m.decode_tok_s(1920.0, 19.9, kv, 8192) - 2 * short) < 0.2


def test_24gb_cannot_hold_a_32b_q4_at_8k():
    m = _load()
    rows = [r for r in m.table_fit()["rows"] if r["gpu_gib"] == 24]
    by_model = {r["model"]: r["best"] for r in rows}
    assert by_model["Qwen2.5-32B"] is None
    assert by_model["Llama-3.3-70B"] is None
    # the 8B stays feasible at 32k, which is why T1 is built around it
    assert by_model["Qwen3-8B"]["ctx"] == 32768


def test_32gb_holds_32b_q4_only_at_16k():
    m = _load()
    rows = [r for r in m.table_fit()["rows"] if r["gpu_gib"] == 32]
    by_model = {r["model"]: r["best"] for r in rows}
    # documented T1 line: 32B-Q4 @16k needs 26.4 GiB of 29.4 usable
    assert by_model["Qwen2.5-32B"]["ctx"] == 16384
    assert by_model["Qwen2.5-32B"]["quant"] == "Q4_K_M"
    assert by_model["Llama-3.3-70B"] is None
    # MoE fits 32k on the same card, which is the argument for T1 + 30B-A3B
    assert by_model["Qwen3-30B-A3B"]["ctx"] == 32768


def test_training_hours_are_six_pt_over_achievable_flops():
    m = _load()
    hours = m.train_hours(8.2, 30e6, 165.0)
    assert 6.5 < hours < 7.5  # documented: ~7 h for 8B / 30M tokens on a 4090


def test_concurrency_covers_the_8x48g_70b_tier():
    m = _load()
    rows = m.table_concurrency()["rows"]
    single = {(r["gpu_gib"], r["ctx"], r["kv"]): r["concurrency"] for r in rows if r["n_gpu"] == 1}
    # documented: 32B-Q4 on one 48 GB card is ~10 seqs at 8k FP16 KV, ~21 at FP8 KV
    assert single[(48, 8192, "fp16")] == 10
    assert single[(48, 8192, "fp8")] == 21
    # 70B-FP8 tensor-parallel over 8x48G: documented ~103 seqs at 8k FP16 KV
    tp = [r for r in rows if r["n_gpu"] == 8 and r["ctx"] == 8192 and r["kv"] == "fp16"]
    assert len(tp) == 1
    assert 95 <= tp[0]["concurrency"] <= 110


def test_json_payload_is_serialisable_and_complete():
    m = _load()
    payload = {
        "model_sizes": m.table_model_sizes(),
        "decode": m.table_decode(),
        "fit": m.table_fit(),
        "concurrency": m.table_concurrency(),
        "training": m.table_training(),
    }
    import json

    round_trip = json.loads(json.dumps(payload, ensure_ascii=False))
    assert round_trip["concurrency"]["rows"]
    assert len(round_trip["model_sizes"]["rows"]) == len(m.MODELS)


MAIN_DOC = ROOT / "docs" / "llm_agent" / "离线SRM领域AI智能体部署方案.md"
REVIEW_DOC = ROOT / "docs" / "llm_agent" / "附件方案评审与优化建议.md"


def _doc_table(section: str, ncols: int, doc: Path | None = None) -> list[list[str]]:
    """Rows of the first pipe table under `section`, cells stripped of bold marks."""
    text = (doc or MAIN_DOC).read_text(encoding="utf-8")
    start = text.index(section)
    rows = []
    for line in text[start:].splitlines():
        if not line.startswith("|"):
            if rows:
                break
            continue
        cells = [c.strip().replace("*", "") for c in line.strip("|").split("|")]
        if len(cells) != ncols or set(cells[0]) <= set("-: "):
            continue
        rows.append(cells)
    return rows[1:]  # drop the header


def test_documented_model_size_table_matches_the_script():
    m = _load()
    by_name = {r["model"]: r for r in m.table_model_sizes()["rows"]}
    # 模型 | 总参 | 激活 | KV/token | KV@8k | KV@32k | Q4 | Q8 | FP16
    for cells in _doc_table("### 3.2 ", 9):
        row = by_name[cells[0]]
        for cell, key in zip(cells[4:], ("kv_8k_gib", "kv_32k_gib", "Q4_K_M", "Q8_0", "FP16")):
            assert abs(float(cell) - row[key]) <= 0.06, (cells[0], key, cell, row[key])


def test_documented_fit_table_sums_are_internally_consistent():
    """§8.2's combined table must equal weight + KV@32k + overhead, cell by cell."""
    m = _load()
    by_name = {r["model"]: r for r in m.table_model_sizes()["rows"]}
    label = {
        "8B": "Qwen3-8B",
        "30B-A3B": "Qwen3-30B-A3B",
        "32B": "Qwen2.5-32B",
        "70B": "Llama-3.3-70B",
    }
    quant_key = {"FP16": "FP16", "FP8": "FP8", "Q8": "Q8_0", "Q4": "Q4_K_M"}
    # 组合 | 权重 | +KV@32k | +开销 | 合计 | ...
    for cells in _doc_table("**第 2 步：在 32k 下反算显存", 9):
        size, quant = cells[0].split()
        row = by_name[label[size]]
        weight, kv, overhead, total = (float(c) for c in cells[1:5])
        assert abs(weight - row[quant_key[quant]]) <= 0.06, (
            cells[0],
            weight,
            row[quant_key[quant]],
        )
        assert abs(kv - row["kv_32k_gib"]) <= 0.06, (cells[0], kv, row["kv_32k_gib"])
        assert overhead == 2.5
        assert abs(total - (weight + kv + overhead)) <= 0.06, (cells[0], total)


def test_storage_footprint_quoted_in_the_document_is_reproducible():
    """§3.4 quotes 409 GiB for Q4+Q8 on six models plus FP16 on the three main ones."""
    m = _load()
    rows = m.table_model_sizes()["rows"]
    total = (
        sum(r["Q4_K_M"] for r in rows)
        + sum(r["Q8_0"] for r in rows)
        + sum(r["FP16"] for r in rows if r["model"] in ("Qwen3-8B", "Qwen3-30B-A3B", "Qwen2.5-32B"))
    )
    assert abs(total - 409) <= 1.0, total


def test_documented_training_table_matches_the_script():
    m = _load()
    rows = {(r["hardware"], r["model"]): r for r in m.table_training()["rows"]}
    # 项 | 8B LoRA（4090） | 32B QLoRA（4090） | 32B 全参（H100×8）
    doc = _doc_table("### 9.4 训练超参与资源", 4)
    hours = {cells[0]: cells[1:] for cells in doc if "token" in cells[0]}
    expected = {
        "30M": [
            rows[("RTX 4090", "Qwen3-8B")]["hours_30m"],
            rows[("RTX 4090", "Qwen2.5-32B")]["hours_30m"],
            rows[("H100 x8", "Qwen2.5-32B")]["hours_30m"],
        ],
        "100M": [
            rows[("RTX 4090", "Qwen3-8B")]["hours_100m"],
            rows[("RTX 4090", "Qwen2.5-32B")]["hours_100m"],
            rows[("H100 x8", "Qwen2.5-32B")]["hours_100m"],
        ],
    }
    for prefix, want in expected.items():
        cells = hours[next(k for k in hours if k.startswith(prefix))]
        for cell, value in zip(cells, want):
            assert abs(float(cell.split()[0].lstrip("~")) - value) <= 0.6, (prefix, cell, value)


def _wan_range(cell: str) -> tuple[float, float]:
    """'¥1.6–2.2 万' / '¥1.2 万' -> (1.6, 2.2)."""
    nums = re.findall(r"\d+(?:\.\d+)?", cell.split("万")[0])
    assert nums, cell
    lo = float(nums[0])
    return lo, float(nums[1]) if len(nums) > 1 else lo


def test_bom_line_items_add_up_to_the_quoted_totals():
    """§3.6's 小计 column must sum to the 合计 row, in both bounds."""
    for section in ("**T1 档（POC", "**T1.5 档（团队档", "**T2 档（精度/审计档"):
        rows = _doc_table(section, 5)
        total_row = next(c for c in rows if "合计" in c[0])
        lo = hi = 0.0
        for cells in rows:
            if "合计" in cells[0] or not cells[4].strip():
                continue
            a, b = _wan_range(cells[4])
            lo, hi = lo + a, hi + b
        want_lo, want_hi = _wan_range(total_row[4])
        # the 合计 row is rounded to whole 万元, so allow 0.5
        assert abs(lo - want_lo) <= 0.5, (section, lo, want_lo)
        assert abs(hi - want_hi) <= 0.5, (section, hi, want_hi)


def test_documented_t3_bom_bounds_add_up():
    """§3.6's T3 table mixes an L40S and an H100 GPU line; each bound sums one of them."""
    rows = _doc_table("**T3 档 BOM", 5)
    items = [c for c in rows if c[0].startswith(("GPU", "服务器", "内存", "存储", "机房"))]
    gpu = {c[0]: _wan_range(c[4]) for c in items if c[0].startswith("GPU")}
    rest = [_wan_range(c[4]) for c in items if not c[0].startswith("GPU")]
    low_lo = gpu["GPU（低档）"][0] + sum(a for a, _ in rest)
    low_hi = gpu["GPU（低档）"][1] + sum(b for _, b in rest)
    high_lo = gpu["GPU（高档）"][0] + sum(a for a, _ in rest)
    high_hi = gpu["GPU（高档）"][1] + sum(b for _, b in rest)
    total = next(c[4] for c in rows if "合计" in c[0])
    # the 合计 cell quotes both the rounded range and the exact per-item sum
    assert (round(low_lo, 1), round(low_hi, 1)) == (60.5, 94.0), (low_lo, low_hi)
    assert (round(high_lo, 1), round(high_hi, 1)) == (148.5, 206.0), (high_lo, high_hi)
    for token in ("60–95", "60.5–94", "150–205", "148.5–206"):
        assert token in total, (token, total)


def test_documented_check_count_table_matches_the_engine():
    """§2.3's 6/8/9/10/11/12/13 must be what `run_checks` actually emits."""
    from srm_open.api import facade
    from srm_open.integrity.lame import ElasticMaterial
    from srm_open.models.requirements import RequirementSet

    base = {"thrust": 8000.0}
    steps = [
        (base, 6),
        ({**base, "burn_time": 4.0}, 8),
        ({**base, "burn_time": 4.0, "max_chamber_pressure": 9e6}, 9),
        (
            {
                **base,
                "burn_time": 4.0,
                "max_chamber_pressure": 9e6,
                "outer_diameter": 0.16,
                "length_ratio": 5.0,
            },
            9,
        ),  # example 06/07's requirement
        ({**base, "burn_time": 4.0, "max_chamber_pressure": 9e6, "specific_impulse": 230.0}, 10),
        (
            {
                **base,
                "burn_time": 4.0,
                "max_chamber_pressure": 9e6,
                "specific_impulse": 230.0,
                "min_load_fraction": 0.6,
            },
            11,
        ),
        (
            {
                **base,
                "burn_time": 4.0,
                "max_chamber_pressure": 9e6,
                "specific_impulse": 230.0,
                "min_load_fraction": 0.6,
                "max_sliver_fraction": 0.05,
            },
            12,
        ),
    ]
    for kwargs, want in steps:
        result = facade.design_forward(RequirementSet(**kwargs), refine=False)
        assert len(result.verification.checks) == want, (kwargs, want)

    material = ElasticMaterial(
        youngs_modulus=2.0e11, poisson_ratio=0.29, allowable_stress=5.0e8, name="steel"
    )
    full = dict(steps[-1][0], min_safety_factor=1.5)
    result = facade.design_forward(RequirementSet(**full), refine=False, material=material)
    assert len(result.verification.checks) == 13


def test_documented_family_counts_match_the_engine():
    """§11.3 claims 14 families, 13 with a design space, only custom_polygon raising."""
    from srm_open.inverse.design import default_design_space
    from srm_open.models.grain import GrainFamily

    assert len(list(GrainFamily)) == 14
    built, rejected = [], []
    for family in GrainFamily:
        try:
            default_design_space(family, 0.16, (0.4, 1.2))
        except ValueError:
            rejected.append(family.value)
        else:
            built.append(family.value)
    assert len(built) == 13
    assert rejected == ["custom_polygon"]


def test_documented_decode_table_matches_the_script():
    m = _load()
    by_hw = {r["hardware"]: r for r in m.table_decode()["rows"]}
    # hardware label in the document -> key in HARDWARE
    hw = {
        "2ch DDR5-5600": "2ch DDR5-5600 desktop",
        "8ch DDR5-4800": "8ch DDR5-4800 server",
        "RTX 3090": "RTX 3090 24G",
        "RTX 4090": "RTX 4090 24G",
        "RTX 5090": "RTX 5090 32G",
        "RTX 6000 Ada": "RTX 6000 Ada 48G",
        "L40S": "L40S 48G",
        "A100": "A100-80G",
        "H100-SXM": "H100-SXM 80G",
        "Apple M3 Ultra": "Apple M3 Ultra",
    }
    models = [
        "Qwen3-4B",
        "Qwen3-8B",
        "Qwen2.5-14B",
        "Qwen3-30B-A3B",
        "Qwen2.5-32B",
        "Llama-3.3-70B",
    ]
    for cells in _doc_table("### 3.3 ", 7):
        key = next((v for k, v in hw.items() if cells[0].startswith(k)), None)
        assert key, cells[0]
        for cell, model in zip(cells[1:], models):
            expected = by_hw[key][model]
            assert abs(float(cell) - expected) / max(1.0, expected) <= 0.03, (
                key,
                model,
                cell,
                expected,
            )


PHASE_CHAPTERS = {
    4: "P0",
    5: "P1",
    6: "P2",
    7: "P3",
    8: "P4",
    9: "P5",
    10: "P6",
    11: "P7",
    12: "P8",
    13: "P9",
}


def test_every_phase_chapter_states_input_strategy_decision_and_fallback():
    """The brief requires every phase to name input / strategy / decision point / fallback.

    Checked on the *subsection headings*, not on prose: a chapter that merely
    mentions "回退" in a sentence does not give the reader a contract to execute.
    """
    text = (ROOT / "docs" / "llm_agent" / "离线SRM领域AI智能体部署方案.md").read_text(
        encoding="utf-8"
    )
    chunks = re.split(r"^## ", text, flags=re.M)
    seen = {}
    for chunk in chunks:
        m = re.match(r"(\d+)\. ", chunk)
        if not m:
            continue
        seen[int(m.group(1))] = chunk
    assert set(PHASE_CHAPTERS) <= set(seen), sorted(set(PHASE_CHAPTERS) - set(seen))

    for chapter, phase in PHASE_CHAPTERS.items():
        heads = re.findall(r"^### \d+\.\d+ (.+)$", seen[chapter], flags=re.M)
        for keyword in ("输入", "策略", "决策点", "回退"):
            assert any(keyword in h for h in heads), (phase, chapter, keyword, heads)


def test_phase_quick_reference_table_lists_every_phase_with_four_columns():
    """§1.5 must stay a usable 10-row contract table, one row per phase."""
    rows = _doc_table("### 1.5 阶段速查表", 5)
    assert len(rows) == 10, rows
    assert [r[0].split()[0] for r in rows] == [f"P{i}" for i in range(10)]
    for cells in rows:
        assert all(c.strip() for c in cells), cells


def test_d4_1_inequalities_in_the_tier_table_actually_hold():
    """Every `a < b` / `a > b` in the D4-1 table is evaluated, not trusted.

    The tier table is the procurement decision point, so a flipped comparison
    ("32B full-param fits in 8x48G") would send a reader to the wrong budget.
    The training-memory figures use the §9.4 accounting (weights + adapter
    state), not a weight multiplier -- that is what makes 32B QLoRA fit in
    24 GiB while 32B full-param needs 525 GiB.
    """
    usable = {
        "32G": 32 * 0.92,
        "48G": 48 * 0.92,
        "8x48G": 8 * 48 * 0.92,
        "8x80G": 8 * 80 * 0.92,
    }
    assert round(usable["32G"], 1) == 29.4
    assert round(usable["48G"], 1) == 44.2
    assert round(usable["8x48G"], 1) == 353.3
    assert round(usable["8x80G"], 1) == 588.8

    lora8 = 16 + 4  # §9.4: FP16 weights + adapter/optimiser state
    qlora32 = 18 + 6  # §9.4: NF4 weights + adapter/optimiser state
    full32 = 32.8 * 16  # AdamW, 16 bytes per parameter
    assert round(full32, 1) == 524.8

    rows = _doc_table("### 8.4 决策点 D4-1", 5)
    text = " ".join(c for cells in rows for c in cells)
    pairs = re.findall(r"(\d+(?:\.\d+)?)\s*([<>])\s*(\d+(?:\.\d+)?)", text)
    assert len(pairs) >= 6, pairs
    for lhs, op, rhs in pairs:
        a, b = float(lhs), float(rhs)
        assert (a < b) if op == "<" else (a > b), (lhs, op, rhs)

    # the claims that decide each tier
    assert lora8 < usable["32G"]
    # 32B QLoRA's 24 GiB excludes activations, so the 5.4 GiB left on a 32 GB
    # card is not a fit -- the document deliberately does not promise it.
    assert qlora32 < usable["32G"]
    assert usable["32G"] - qlora32 < 6
    assert qlora32 < usable["48G"]
    assert full32 > usable["48G"] and full32 > usable["8x48G"]
    assert full32 < usable["8x80G"]


def test_d4_1_concurrency_cells_match_the_script():
    """11/22, 10/21, 103/206 in the tier table come from `table_concurrency`."""
    m = _load()
    got = {
        (r["model"], r["gpu_gib"], r["kv"]): r["concurrency"]
        for r in m.table_concurrency()["rows"]
        if r["ctx"] == 8192
    }
    assert got[("32B Q4_K_M x1", 48, "fp16")] == 10
    assert got[("32B Q4_K_M x1", 48, "fp8")] == 21
    assert got[("70B FP8 x8 (TP)", 48, "fp16")] == 103
    assert got[("70B FP8 x8 (TP)", 48, "fp8")] == 206
    # 30B-A3B on a 32 GB card is not a script case, so recompute with its formula
    base = 30.5 * m.QUANT["Q4_K_M"] + 2.5
    for kv_bytes, want in ((2, 11), (1, 22)):
        per = m.kv_bytes_per_token(48, 4, 128, kv_bytes) * 8192 / m.GIB
        assert int((32 * 0.92 - base) / per) == want, kv_bytes


# --- review document (附件方案评审与优化建议.md) ------------------------------


def test_eval_power_numbers_quoted_in_the_review_are_reproducible():
    """The review's central claim is that 30 questions are too few.

    That claim only stands if the intervals are computed, not asserted, so the
    table is re-derived here cell by cell.
    """
    m = _load()
    power = m.table_eval_power()
    rows = {r["n"]: r for r in power["rows"]}
    assert rows[30]["half_width"] == 0.1108
    assert rows[30]["lo"] == 0.7438 and rows[30]["hi"] == 0.9654
    assert rows[150]["half_width"] == 0.0484
    assert power["n_for_10pct"] == 34
    assert power["n_for_5pct"] == 141
    assert power["n_for_3pct"] == 384
    assert power["n_14b_vs_32b_paired"] == 116
    assert power["n_5pt_gap_paired"] == 469
    # a paired design must never need more questions than an unpaired one
    assert power["n_14b_vs_32b_paired"] < power["n_14b_vs_32b"]
    assert power["n_5pt_gap_paired"] < power["n_5pt_gap"]

    doc = _doc_table("### 6.2 策略：先算样本量", 6, REVIEW_DOC)
    quoted = {int(c[0]): c for c in doc if c[0].isdigit()}
    for n, cells in quoted.items():
        assert (
            abs(
                float(cells[3].replace("%", "").replace("[", "").split(",")[0])
                - rows[n]["lo"] * 100
            )
            <= 0.15
        ), n
        assert (
            abs(float(cells[4].lstrip("±").replace(" pp", "")) - rows[n]["half_width"] * 100)
            <= 0.15
        ), n


def test_review_rule_of_three_matches_its_safety_argument():
    """20 injection cases with zero failures only bound the rate at 15%."""
    m = _load()
    assert round(m.rule_of_three(20) * 100) == 15
    assert round(m.rule_of_three(30) * 100) == 10


def test_review_storage_and_power_figures_come_from_the_script():
    m = _load()
    budget = m.storage_budget_gib()
    assert budget["weights"] == 173.7
    assert budget["retrievers_fp32"] == 4.5
    assert budget["total"] == 178.2
    # peak = 575 W GPU + 170 W CPU + 100 W platform, quoted in §3.4
    assert round(m.psu_headroom(845, 1200), 2) == 1.42
    assert round(m.psu_headroom(845, 1600), 2) == 1.89
    assert round(m.ups_energy_wh(845, 10)) == 141


def test_review_bandwidth_per_gib_ranking_matches_its_argument():
    """The review argues the 5090 wins on bandwidth per GiB, not on capacity."""
    m = _load()
    rows = {r["hardware"]: r["bw_per_gib"] for r in m.bandwidth_per_gib()}
    assert rows["RTX 5090 32G"] == 56.0
    assert rows["RTX 6000 Ada 48G"] == 20.0
    assert rows["L40S 48G"] == 18.0
    assert rows["A100-80G"] == 25.5
    assert max(rows.values()) == rows["RTX 5090 32G"]


def test_review_serving_table_matches_the_script_formulas():
    """§3.2's eight rows are recomputed from the same closed forms."""
    m = _load()
    by_name = {r["model"]: r for r in m.table_model_sizes()["rows"]}
    cfg = {n: (p, layers, kvh, hd) for n, p, _pa, layers, kvh, hd in m.MODELS}

    def need(model, ctx, gpu, quant="Q4_K_M", overhead=2.5):
        _p, layers, kvh, hd = cfg[model]
        kv = m.kv_bytes_per_token(layers, kvh, hd) * ctx / m.GIB
        weight = by_name[model][quant]
        # the script rounds the total, not the weight: 30.5 x 0.606 = 18.483,
        # so the row reads 21.7, not 18.5 + 0.75 + 2.5 = 21.8
        total = round(cfg[model][0] * m.QUANT[quant] + kv + overhead, 1)
        return weight, round(kv, 2), total, int((gpu * 0.92 - weight - overhead) / kv)

    w, kv, tot, seqs = need("Qwen3-30B-A3B", 8192, 32)
    assert (w, kv, tot, seqs) == (18.5, 0.75, 21.7, 11)
    w, kv, tot, seqs = need("Qwen2.5-32B", 16384, 32)
    assert (w, kv, tot, seqs) == (19.9, 4.0, 26.4, 1)
    w, kv, tot, seqs = need("Qwen2.5-32B", 32768, 32)
    assert tot > 32 * 0.92  # does not fit at all -- the "concurrency 0" row
    w, kv, tot, seqs = need("Qwen2.5-14B", 32768, 32)
    assert (w, kv, tot, seqs) == (8.9, 6.0, 17.4, 3)
    w, kv, tot, seqs = need("Qwen2.5-32B", 32768, 48)
    assert (w, kv, tot, seqs) == (19.9, 8.0, 30.4, 2)
    w, kv, tot, seqs = need("Llama-3.3-70B", 32768, 96)
    assert (w, kv, tot, seqs) == (42.8, 10.0, 55.3, 4)


def test_review_candidate_table_matches_the_serving_table():
    """The 14B/30B-A3B/32B comparison must agree with §3.2's own numbers."""
    doc = _doc_table("| 模型 | 权重 GiB (Q4) | 32k 并发", 6, REVIEW_DOC)
    cells = {c[0]: c for c in doc}
    assert cells["Qwen2.5-14B"][1:5] == ["8.9", "3", "12", "104.2"]
    assert cells["Qwen3-30B-A3B"][1:5] == ["18.5", "2", "11", "56.4"]
    assert cells["Qwen3-32B"][1:5] == ["19.9", "0（16k 仅 1）", "3", "49.6"]


def test_review_phase_table_has_the_four_elements_per_phase():
    """Every phase row states input, strategy, decision point and fallback."""
    text = REVIEW_DOC.read_text(encoding="utf-8")
    start = text.index('### 7.2 策略：把"验收"改成"判据 + 测试用例"')
    block = text[start:].splitlines()
    rows = [ln for ln in block if ln.startswith("| A")]
    assert len(rows) == 6, rows
    for ln in rows:
        cells = [c.strip() for c in ln.strip("|").split("|")]
        assert len(cells) == 6, cells
        assert cells[3] and cells[4] and cells[5], cells


def test_review_acceptance_criteria_are_numbered_and_complete():
    text = REVIEW_DOC.read_text(encoding="utf-8")
    ids = re.findall(r"^\| V-(\d+) \|", text, flags=re.M)
    assert [int(i) for i in ids] == list(range(1, 13))


def test_review_risk_table_covers_every_risk_it_names():
    text = REVIEW_DOC.read_text(encoding="utf-8")
    ids = re.findall(r"^\| R-(\d+) \|", text, flags=re.M)
    assert [int(i) for i in ids] == list(range(1, 11))


def test_review_cross_references_resolve_in_the_document_they_name():
    """`§x.y` means this file; `主方案 §x.y` means the main plan.

    A review that cites a section that does not exist is worse than no citation,
    so both namespaces are checked against their own heading sets.
    """
    review = REVIEW_DOC.read_text(encoding="utf-8")
    main = MAIN_DOC.read_text(encoding="utf-8")

    def headings(text):
        return set(re.findall(r"^#{2,4} (\d+(?:\.\d+)?)", text, flags=re.M))

    local_refs = set(re.findall(r"(?<!主方案 )§(\d+(?:\.\d+)?)", review))
    assert local_refs <= headings(review), sorted(local_refs - headings(review))

    external = set(re.findall(r"主方案 §(\d+(?:\.\d+)?)", review))
    assert external, "the review should cite the main plan at least once"
    assert external <= headings(main), sorted(external - headings(main))


def test_review_and_main_plan_share_one_number_source():
    """Both documents must quote the same script; neither may hard-code its own."""
    for doc in (MAIN_DOC, REVIEW_DOC):
        text = doc.read_text(encoding="utf-8")
        assert "scripts/llm_sizing.py" in text, doc.name


# --- team sizing (5-10 designers) -------------------------------------------


def test_concurrency_is_far_below_headcount_for_a_design_team():
    """The tier recommendation turns on this: 10 users need 2-3 slots, not 10.

    A design team reads each answer for minutes.  Concurrency is the binomial
    tail, so a 10-person team at a 5-minute think time needs 3 slots -- which is
    why T1 (11 slots at 8k) is the right tier and T2 is not.
    """
    m = _load()
    rows = {(r["users"], r["think_min"]): r["slots_p95"] for r in m.table_team_sizing()["rows"]}
    assert rows[(5, 10)] == 1
    assert rows[(5, 5)] == 2
    assert rows[(10, 10)] == 2
    assert rows[(10, 5)] == 3
    assert rows[(10, 3)] == 4
    assert rows[(10, 2)] == 5
    # monotone in both directions, and never above headcount
    for (n, t), slots in rows.items():
        assert 0 < slots <= n, (n, t, slots)
    assert m.active_fraction(30.0, 5.0) == 0.1


def test_ten_users_fit_in_t1_but_t2_is_slower():
    """The review's claim: for 5-10 users T2 is 4x the price and 4x slower."""
    m = _load()
    rows = {
        (r["tier"], r["model"], r["ctx"], r["batch"]): r
        for r in m.table_latency_under_load()["rows"]
    }
    t1 = rows[("T1 32 GB", "Qwen3-30B-A3B", 8192, 3)]
    t2 = rows[("T2 48 GB", "Qwen2.5-32B", 32768, 3)]
    assert t1["s_300tok"] == 5.7
    assert t2["s_300tok"] == 22.7
    # the slower machine is the expensive one -- that is the whole finding
    assert t2["s_300tok"] / t1["s_300tok"] > 3.5

    # T1 at 8k carries the 3 slots a 10-person team needs
    tiers = {(r["tier"], r["model"], r["ctx"]): r for r in m.table_tiers()["rows"]}
    assert tiers[("T1 最小可用", "Qwen3-30B-A3B", 8192)]["slots"] >= 3
    # ...but at 32k it does not, which is what T1.5 exists for
    assert tiers[("T1 最小可用", "Qwen3-30B-A3B", 32768)]["slots"] < 3
    assert tiers[("T1.5 团队档", "Qwen3-30B-A3B", 32768)]["slots"] >= 3


def test_tier_table_covers_every_tier_and_keeps_both_names():
    """T0/T1/T1.5/T2/T3 and the deck's 6/32/64/48/96 GB ladder are one table."""
    m = _load()
    rows = m.table_tiers()["rows"]
    tiers = {r["tier"] for r in rows}
    assert {"T0 实验课", "T1 最小可用", "T1.5 团队档", "T2 推荐落地", "T3 生产"} == tiers
    labels = {r["deck_label"] for r in rows}
    assert {"6 GB", "32 GB", "64 GB", "48 GB", "96 GB"} == labels
    # every row must be internally consistent: fits <=> slots > 0
    for r in rows:
        assert r["fits"] == (r["total"] <= r["usable"]), r
        if r["fits"]:
            assert r["slots"] >= 0
    # the 6 GB row needs llama.cpp's smaller overhead or it would not fit
    t0 = next(r for r in rows if r["tier"] == "T0 实验课")
    assert t0["runtime"] == "llama.cpp" and t0["overhead"] == 1.0
    assert t0["fits"] is True


def test_two_card_tier_accounts_for_pcie_all_reduce():
    """No NVLink on 5090: two cards do not double decode speed."""
    m = _load()
    assert m.TP_EFF < 1.0
    tiers = {(r["tier"], r["model"], r["ctx"]): r for r in m.table_tiers()["rows"]}
    one = tiers[("T1 最小可用", "Qwen3-30B-A3B", 32768)]
    two = tiers[("T1.5 团队档", "Qwen3-30B-A3B", 32768)]
    assert two["n_gpu"] == 2 and one["n_gpu"] == 1
    # capacity doubles -> more slots; bandwidth does not -> less than 2x speed
    assert two["slots"] > one["slots"]
    assert two["tok_s"] < 2 * one["tok_s"]
    assert 1.4 < two["tok_s"] / one["tok_s"] < 1.8


def test_main_plan_recommends_by_concurrency_not_headcount():
    """Guard the corrected D4-1 rule: T1 is the 5-10 person default, not T2."""
    text = MAIN_DOC.read_text(encoding="utf-8")
    assert "5–10 人团队的主力档" in text
    assert "先按并发人数定档，不按人数头数定档" in text
    # the superseded recommendation must be marked as such, not silently dropped
    assert "上一版的错误" in text
    # and the T1.5 tier must exist in both the tier table and the BOM
    assert "T1.5 团队档" in text
    assert "**T1.5 档（团队档" in text


def test_documented_concurrency_table_matches_the_binomial_tail():
    """§1.1.1's table is the算据 for choosing T1 over T2; it must be computed."""
    m = _load()
    rows = {(r["users"], r["think_min"]): r["slots_p95"] for r in m.table_team_sizing()["rows"]}
    doc = _doc_table("| 人数 | 思考 10 min", 5)
    assert len(doc) == 4, doc
    for cells in doc:
        n = int(cells[0].replace("*", ""))
        for think, cell in zip((10, 5, 3, 2), cells[1:]):
            want = rows[(n, think)]
            got = int(cell.replace("*", ""))
            assert got == want, (n, think, got, want)
