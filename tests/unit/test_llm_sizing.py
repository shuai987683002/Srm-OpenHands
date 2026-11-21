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


def _doc_table(section: str, ncols: int) -> list[list[str]]:
    """Rows of the first pipe table under `section`, cells stripped of bold marks."""
    text = (ROOT / "docs" / "llm_agent" / "离线SRM领域AI智能体部署方案.md").read_text(
        encoding="utf-8"
    )
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
    for section in ("**T1 档（POC", "**T2 档（推荐落地"):
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

    rows = _doc_table("### 8.4 决策点 D4-1", 4)
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
