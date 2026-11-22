#!/usr/bin/env python3
"""Generate every figure the handbook embeds.

Two kinds of figure live here:

* **Data plots** read their numbers from `scripts/llm_sizing.py`, which is the
  single source for the capacity/throughput tables.  Nothing is typed in twice,
  so a figure cannot drift from the table beside it.
* **Flow diagrams** are drawn from box-and-arrow primitives.  They are pictures
  of prose that already exists in the handbook; the point is to let a reader
  see the branch structure at a glance.

Run with no arguments to write into `docs/llm_agent/figures/`:

    python3 scripts/build_handbook_figures.py

The output is committed. `tests/unit/test_handbook_figures.py` re-derives the
data plots and fails if a committed PNG is stale.
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

import llm_sizing as ls  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT = REPO_ROOT / "docs" / "llm_agent" / "figures"

GIB = 1024**3

# Palette kept small and colour-blind safe; the handbook prints in greyscale
# too, so every series also differs by marker or hatch.
C_MAIN = "#1f4e79"
C_ALT = "#c00000"
C_GREY = "#8c8c8c"
C_FILL = "#dce6f1"
C_WARN = "#fff2cc"
C_STOP = "#f8cbad"

plt.rcParams.update(
    {
        "font.family": ["Noto Sans CJK SC", "DejaVu Sans"],
        "font.size": 9,
        "axes.titlesize": 10,
        "axes.labelsize": 9,
        "axes.unicode_minus": False,
        "figure.dpi": 160,
        "savefig.dpi": 160,
        "savefig.bbox": "tight",
        "savefig.facecolor": "white",
    }
)


# ---------------------------------------------------------------- data plots


def fig_decode_by_hardware() -> None:
    """Decode rate per hardware tier, one line per model.

    The point a reader should take away: a 30B MoE streams only its active
    experts, so it outruns a dense 14B on the same card.
    """
    t = ls.table_decode()
    rows = t["rows"]
    hw = [r["hardware"] for r in rows]
    models = [m[0] for m in ls.MODELS if m[0] in ("Qwen3-8B", "Qwen2.5-14B", "Qwen3-30B-A3B", "Qwen2.5-32B")]

    fig, ax = plt.subplots(figsize=(7.2, 3.4))
    x = range(len(hw))
    styles = {
        "Qwen3-8B": dict(marker="o", color=C_MAIN, ls="-"),
        "Qwen2.5-14B": dict(marker="s", color=C_GREY, ls="--"),
        "Qwen3-30B-A3B": dict(marker="^", color=C_ALT, ls="-"),
        "Qwen2.5-32B": dict(marker="D", color="#2e7d32", ls=":"),
    }
    for m in models:
        ax.plot(
            list(x),
            [r[m] for r in rows],
            label=m,
            markersize=4,
            linewidth=1.4,
            **styles[m],
        )
    ax.set_yscale("log")
    ax.set_xticks(list(x))
    ax.set_xticklabels(hw, rotation=30, ha="right", fontsize=7.5)
    ax.set_ylabel("decode 速率 (token/s，log)")
    ax.set_title("图 2-1  各硬件档位解码速率（8k 上下文，Q4_K_M，BW_EFF=0.65）")
    ax.grid(True, which="both", alpha=0.25, linewidth=0.6)
    ax.legend(fontsize=7.5, ncol=4, loc="upper left", frameon=False)
    ax.annotate(
        "30B MoE 只读激活专家\n故快于稠密 14B",
        xy=(4, rows[4]["Qwen3-30B-A3B"]),
        xytext=(5.1, 90),
        fontsize=7,
        arrowprops=dict(arrowstyle="->", color=C_ALT, lw=0.9),
        color=C_ALT,
    )
    fig.savefig(OUT / "fig-2-1-decode-by-hardware.png")
    plt.close(fig)


def fig_vram_vs_context() -> None:
    """VRAM needed at 8k / 32k for each model, with the 32 GB line drawn.

    Shows why 32 GB is the break: 32B at 32k does not fit, 30B-A3B at 32k does.
    """
    fig, ax = plt.subplots(figsize=(7.2, 3.2))
    names, w8, w32 = [], [], []
    for name, p, p_act, layers, kvh, hd in ls.MODELS:
        if name == "Llama-3.3-70B":
            continue
        kv = ls.kv_bytes_per_token(layers, kvh, hd)
        weight = (p_act if p_act < p * 0.5 else p) * ls.QUANT["Q4_K_M"]
        names.append(name)
        w8.append(weight + kv * 8192 / GIB + 2.5)
        w32.append(weight + kv * 32768 / GIB + 2.5)

    y = range(len(names))
    h = 0.36
    ax.barh([i + h / 2 for i in y], w8, height=h, color=C_MAIN, label="8k 上下文")
    ax.barh([i - h / 2 for i in y], w32, height=h, color=C_ALT, label="32k 上下文", hatch="//")
    ax.axvline(32, color="#000000", ls="--", lw=1.2)
    ax.text(32.4, len(names) - 0.6, "32 GB\n(T1 档)", fontsize=7.5, va="top")
    ax.axvline(48, color=C_GREY, ls=":", lw=1.2)
    ax.text(48.4, len(names) - 0.6, "48 GB\n(T2 档)", fontsize=7.5, va="top", color=C_GREY)
    ax.set_yticks(list(y))
    ax.set_yticklabels(names, fontsize=8)
    ax.set_xlabel("显存需求 (GiB) = 权重 + KV + 2.5 GiB 开销")
    ax.set_title("图 2-2  显存需求随上下文增长（Q4_K_M）")
    ax.set_xlim(0, 62)
    ax.grid(True, axis="x", alpha=0.25, linewidth=0.6)
    ax.legend(fontsize=7.5, frameon=False, loc="lower right")
    fig.savefig(OUT / "fig-2-2-vram-vs-context.png")
    plt.close(fig)


def fig_concurrency_curve() -> None:
    """p95 slot count against team size, one line per think time.

    This is the chart that kills the "one GPU per person" rule: the curve is
    concave, so ten designers need three slots, not ten.
    """
    fig, ax = plt.subplots(figsize=(6.4, 3.2))
    users = list(range(1, 21))
    for think, style in ((2, "-"), (3, "--"), (5, "-."), (10, ":")):
        ax.plot(
            users,
            [ls.peak_concurrency(n, 30.0, think) for n in users],
            style,
            color=C_MAIN,
            linewidth=1.4,
            label=f"思考 {think} 分钟",
        )
    ax.plot(users, users, color=C_GREY, lw=0.8, ls=":", label="按人头（错误做法）")
    ax.scatter([10], [ls.peak_concurrency(10, 30.0, 5)], color=C_ALT, zorder=5, s=45)
    ax.annotate(
        f"10 人 / 5 分钟 → {ls.peak_concurrency(10, 30.0, 5)} 槽",
        xy=(10, ls.peak_concurrency(10, 30.0, 5)),
        xytext=(11.2, 9),
        fontsize=7.5,
        color=C_ALT,
        arrowprops=dict(arrowstyle="->", color=C_ALT, lw=0.9),
    )
    ax.set_xlabel("设计团队人数")
    ax.set_ylabel("p95 并发槽数")
    ax.set_title("图 1-1  并发槽数 vs 团队人数（服务 30 s，阻塞概率 5%）")
    ax.grid(True, alpha=0.25, linewidth=0.6)
    ax.legend(fontsize=7.5, frameon=False, loc="upper left")
    fig.savefig(OUT / "fig-1-1-concurrency-curve.png")
    plt.close(fig)


def fig_latency_under_load() -> None:
    """300-token answer time, single request vs 3 concurrent."""
    rows = ls.table_latency_under_load()["rows"]
    labels = [f"{r['tier']}\n{r['model']} @{r['ctx'] // 1024}k" for r in rows]
    single = [r["s_300tok"] for r in rows if r["batch"] == 1]
    triple = [r["s_300tok"] for r in rows if r["batch"] == 3]
    keep = [i for i, r in enumerate(rows) if r["batch"] == 1]
    labels = [labels[i] for i in keep]

    fig, ax = plt.subplots(figsize=(7.2, 3.2))
    x = range(len(labels))
    ax.bar([i - 0.19 for i in x], single, width=0.36, color=C_MAIN, label="1 路")
    ax.bar([i + 0.19 for i in x], triple, width=0.36, color=C_ALT, label="3 路并发", hatch="//")
    for i, (a, b) in enumerate(zip(single, triple)):
        ax.text(i - 0.19, a + 0.4, f"{a:.1f}", ha="center", fontsize=7)
        ax.text(i + 0.19, b + 0.4, f"{b:.1f}", ha="center", fontsize=7, color=C_ALT)
    ax.axhline(10, color=C_GREY, ls=":", lw=1)
    ax.text(len(labels) - 0.4, 10.4, "10 s 可接受线", fontsize=7, color=C_GREY, ha="right")
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, fontsize=7)
    ax.set_ylabel("300 token 回答耗时 (s)")
    ax.set_title("图 3-1  负载下的回答时延（估算，BW_EFF=0.65）")
    ax.grid(True, axis="y", alpha=0.25, linewidth=0.6)
    ax.legend(fontsize=7.5, frameon=False)
    fig.savefig(OUT / "fig-3-1-latency-under-load.png")
    plt.close(fig)


def fig_kv_growth() -> None:
    """KV cache size against context, per model.

    The slope is what forces a decision at 32k: KV, not weights, is what stops
    fitting.
    """
    fig, ax = plt.subplots(figsize=(6.4, 3.0))
    ctx = [1024 * i for i in range(1, 65)]
    for name, _p, _p_act, layers, kvh, hd in ls.MODELS:
        if name == "Llama-3.3-70B":
            continue
        kv = ls.kv_bytes_per_token(layers, kvh, hd)
        ax.plot([c // 1024 for c in ctx], [kv * c / GIB for c in ctx], lw=1.4, label=f"{name}")
    ax.axvline(32, color=C_GREY, ls="--", lw=1)
    ax.set_xlabel("上下文长度 (k token)")
    ax.set_ylabel("KV cache (GiB，fp16)")
    ax.set_title("图 2-3  KV cache 随上下文线性增长")
    ax.grid(True, alpha=0.25, linewidth=0.6)
    ax.legend(fontsize=7.5, frameon=False)
    fig.savefig(OUT / "fig-2-3-kv-growth.png")
    plt.close(fig)


def fig_batched_decode() -> None:
    """Measured aggregate throughput against the bandwidth ceiling.

    The measurement is `llama-batched-bench` on this 2-core build host.  The
    ceiling is `llm_sizing.decode_tok_s_batched` at the measured STREAM
    bandwidth.  They agree to 0.1 % at batch 4 -- which is the claim that
    matters: the script is a *ceiling*, so it never flatters a deployment.
    At batch 1 the measurement beats the ceiling because one sequence's weights
    stay in cache, so the ceiling is conservative exactly where it is used for
    sizing.
    """
    # llama-batched-bench -t 2 -c 8192 -ntg 64 -npl 1,2,4  (Qwen2.5-0.5B Q4_K_M)
    measured = {1: 51.88, 2: 58.13, 4: 89.10}
    W = 462.96 / 1024
    kv = ls.kv_bytes_per_token(24, 2, 64)
    bw = 30.47  # STREAM scale, this host

    fig, ax = plt.subplots(figsize=(6.8, 3.4))
    bs = [1, 2, 4]
    agg = [measured[b] for b in bs]
    per = [measured[b] / b for b in bs]
    ceil = [ls.decode_tok_s_batched(bw, W, kv, 8192, b) * b for b in bs]

    ax.plot(bs, agg, "o-", color=C_MAIN, lw=1.6, markersize=6, label="实测聚合吞吐")
    ax.plot(bs, ceil, "s--", color=C_ALT, lw=1.3, markersize=5, label="带宽上界（bw=30.47 GB/s）")
    ax.plot(bs, per, "^:", color=C_GREY, lw=1.2, markersize=5, label="实测每路速率")

    for b, a, c in zip(bs, agg, ceil):
        ax.annotate(f"{a:.1f}", (b, a), textcoords="offset points", xytext=(0, 8),
                    ha="center", fontsize=7.5, color=C_MAIN)
        if b == 4:
            ax.annotate(f"{c:.1f}", (b, c), textcoords="offset points", xytext=(0, -14),
                        ha="center", fontsize=7.5, color=C_ALT)

    ax.set_xscale("log", base=2)
    ax.set_xticks(bs)
    ax.set_xticklabels([str(b) for b in bs])
    ax.set_xlabel("并发批大小 (batch)")
    ax.set_ylabel("吞吐 (token/s)")
    ax.set_title("图 5-1  批量解码：实测 vs 带宽上界（2 核 CPU，8k 上下文）")
    ax.grid(True, which="both", alpha=0.25, linewidth=0.6)
    ax.legend(fontsize=7.5, frameon=False, loc="upper left")
    ax.text(
        0.98, 0.06,
        "batch=4 时上界 89.2 / 实测 89.1（差 0.1%）\n"
        "→ 模型是上界，不会美化部署",
        transform=ax.transAxes, ha="right", fontsize=7,
        bbox=dict(boxstyle="round,pad=0.35", facecolor=C_WARN, edgecolor="#bf9000", lw=0.7),
    )
    fig.savefig(OUT / "fig-5-1-batched-decode.png")
    plt.close(fig)


def fig_thread_sweep() -> None:
    """Throughput against thread count: SMT hurts this workload.

    2 physical cores beat 4 threads by 17 % on decode.  Anyone who sets
    `-t $(nproc)` gets the slower number and no error, so the handbook has to
    show the shape, not just state the rule.
    """
    # llama-bench -t N -p 512 -n 128 -r 2
    data = {1: (120.06, 32.74), 2: (227.35, 52.48), 4: (219.88, 45.00)}

    fig, ax = plt.subplots(figsize=(6.2, 3.2))
    ts = [1, 2, 4]
    ax.plot(ts, [data[t][0] for t in ts], "o-", color=C_MAIN, lw=1.6, markersize=6, label="prompt (pp512)")
    ax.plot(ts, [data[t][1] for t in ts], "s-", color=C_ALT, lw=1.6, markersize=6, label="decode (tg128)")

    best = max(ts, key=lambda t: data[t][1])
    ax.axvline(best, color="#2e7d32", ls="--", lw=1.1)
    ax.annotate(
        f"最优 {best} 线程\n(物理核数)",
        xy=(best, data[best][1]), xytext=(best + 0.35, data[best][1] + 18),
        fontsize=7.5, color="#2e7d32",
        arrowprops=dict(arrowstyle="->", color="#2e7d32", lw=0.9),
    )
    ax.annotate(
        f"4 线程反而慢 {100 * (1 - data[4][1] / data[2][1]):.0f}%\n(SMT 争用)",
        xy=(4, data[4][1]), xytext=(3.05, data[4][1] - 22),
        fontsize=7.5, color=C_ALT,
        arrowprops=dict(arrowstyle="->", color=C_ALT, lw=0.9),
    )
    ax.set_xticks(ts)
    ax.set_xlabel("线程数 (-t)")
    ax.set_ylabel("吞吐 (token/s)")
    ax.set_title("图 5-2  线程数扫描：超线程在本机是负收益")
    ax.grid(True, alpha=0.25, linewidth=0.6)
    ax.legend(fontsize=7.5, frameon=False)
    fig.savefig(OUT / "fig-5-2-thread-sweep.png")
    plt.close(fig)


def fig_measured_vs_estimate() -> None:
    """The one place measured and estimated numbers meet.

    CPU measurement is real (this build host); GPU bars are the script's
    estimate and are marked as such, because quoting them as measured is the
    failure this figure exists to prevent.
    """
    measured = 52.48          # llama-bench tg128, -t 2
    bw_meas = 30.47           # STREAM scale, this host
    kv = ls.kv_bytes_per_token(24, 2, 64)
    eff = measured * (0.4521 * ls.GIB + kv * 8192) / (bw_meas * 1e9)
    labels = ["本机实测\n(2 物理核)", "带宽上界模型\n(同机 bw=30.47)"]
    vals = [measured, ls.decode_tok_s(bw_meas, 0.4521, kv, 8192)]
    colors = [C_MAIN, C_GREY]

    fig, ax = plt.subplots(figsize=(5.6, 3.0))
    bars = ax.bar(labels, vals, color=colors, width=0.5)
    bars[1].set_hatch("//")
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v + 40, f"{v:.0f}", ha="center", fontsize=9)
    ax.set_ylabel("decode 速率 (token/s)")
    ax.set_title("图 4-1  实测与估算必须分开标注（0.5B，Q4_K_M）")
    ax.text(
        0.5,
        0.72,
        f"实测反解效率 {eff:.2f}\n脚本假设 {ls.BW_EFF:.2f}\n(batch=1 上界偏保守)",
        transform=ax.transAxes,
        fontsize=8,
        ha="center",
        bbox=dict(boxstyle="round,pad=0.4", facecolor=C_WARN, edgecolor="#bf9000", lw=0.8),
    )
    ax.grid(True, axis="y", alpha=0.25, linewidth=0.6)
    fig.savefig(OUT / "fig-4-1-measured-vs-estimate.png")
    plt.close(fig)


def fig_tier_fit() -> None:
    """Required VRAM against each tier's usable capacity, at 8k and 32k.

    Bars past the dashed capacity line are the configurations that do not fit,
    which is the only thing a reader needs from a procurement chart.
    """
    rows = ls.table_tiers()["rows"]
    labels, need, cap, ctxs, fits = [], [], [], [], []
    for r in rows:
        labels.append(f"{r['tier']}\n{r['model']}\n{r['quant']}")
        need.append(r["total"])
        cap.append(r["usable"])
        ctxs.append(r["ctx"])
        fits.append(r["fits"])

    fig, ax = plt.subplots(figsize=(7.4, 3.4))
    x = range(len(labels))
    colors = [C_MAIN if f else C_ALT for f in fits]
    bars = ax.bar(list(x), need, color=colors, width=0.62)
    for b, f in zip(bars, fits):
        if not f:
            b.set_hatch("xx")
    ax.step([i - 0.42 for i in x] + [len(labels) - 0.58], cap + [cap[-1]],
            where="post", color="#000000", ls="--", lw=1.2, label="该档可用显存 (0.92×)")
    for i, (n, c, t, f) in enumerate(zip(need, cap, ctxs, fits)):
        ax.text(i, max(n, c) + 1.4, f"{n:.1f}/{c:.1f} GiB\n{t // 1024}k", ha="center", fontsize=6.6,
                color="#000000" if f else C_ALT)
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, fontsize=6.4)
    ax.set_ylabel("显存 (GiB)")
    ax.set_title("图 1-2  各档位装得下吗：需求 vs 可用（Q4_K_M）")
    ax.grid(True, axis="y", alpha=0.25, linewidth=0.6)
    ax.legend(fontsize=7.5, frameon=False, loc="upper left")
    ax.set_ylim(0, max(max(need), max(cap)) * 1.25)
    fig.savefig(OUT / "fig-1-2-tier-fit.png")
    plt.close(fig)


# ------------------------------------------------------------- flow diagrams


def _box(ax, x, y, w, h, text, fc=C_FILL, ec=C_MAIN, fs=8, bold=False, style="round"):
    ax.add_patch(
        FancyBboxPatch(
            (x, y),
            w,
            h,
            boxstyle=f"{style},pad=0.02,rounding_size=0.08",
            facecolor=fc,
            edgecolor=ec,
            linewidth=1.1,
        )
    )
    ax.text(
        x + w / 2,
        y + h / 2,
        text,
        ha="center",
        va="center",
        fontsize=fs,
        fontweight="bold" if bold else "normal",
        linespacing=1.35,
    )


def _arrow(ax, p1, p2, color=C_MAIN, style="->", lw=1.1, ls="-"):
    ax.add_patch(
        FancyArrowPatch(p1, p2, arrowstyle=style, color=color, lw=lw, linestyle=ls,
                        mutation_scale=11, shrinkA=2, shrinkB=2)
    )


def fig_flow_airgap() -> None:
    """Three-stage ferry: prepare zone -> one-way media -> isolated zone."""
    fig, ax = plt.subplots(figsize=(7.4, 3.5))
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 5.2)
    ax.axis("off")

    _box(ax, 0.15, 2.9, 2.9, 1.9, "准备区\n(可联网)\n\n下载权重 / 二进制\n计算 SHA256", fc="#e8f1e8", ec="#2e7d32")
    _box(ax, 3.55, 2.9, 2.6, 1.9, "单向介质\n\n只写一次的\n移动硬盘 / DVD-R\n记录哈希清单", fc=C_WARN, ec="#bf9000")
    _box(ax, 6.65, 2.9, 3.0, 1.9, "隔离区\n(永不联网)\n\n校验哈希 → 导入\n拒绝任何不匹配", fc="#fde9e9", ec=C_ALT)

    _arrow(ax, (3.1, 3.85), (3.5, 3.85))
    _arrow(ax, (6.2, 3.85), (6.6, 3.85))

    _box(ax, 0.15, 0.5, 9.5, 1.5,
         "禁止：双向 U 盘 / 直接拷贝到隔离区 / \"先装上再验证\" / 用文件名当版本标识\n"
         "允许的唯一路径：准备区 → 校验 → 单向介质 → 隔离区复校 → 导入",
         fc="#f2f2f2", ec=C_GREY, fs=8)

    _arrow(ax, (5.0, 2.85), (5.0, 2.05), color=C_STOP, style="-|>", ls="--")
    ax.text(5.15, 2.35, "反向禁止", fontsize=7.5, color=C_ALT)

    ax.set_title("图 2-4  三段式摆渡：模型文件进入隔离区的唯一路径", fontsize=10)
    fig.savefig(OUT / "fig-2-4-airgap-ferry.png")
    plt.close(fig)


def fig_flow_startup() -> None:
    """Startup gate chain: hash -> port -> launch -> health."""
    fig, ax = plt.subplots(figsize=(7.4, 4.2))
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 6.4)
    ax.axis("off")

    _box(ax, 3.3, 5.4, 3.4, 0.8, "run-server.sh 启动", fc=C_FILL, bold=True)
    _box(ax, 3.3, 4.2, 3.4, 0.85, "verify_models.py\n逐文件 SHA256", fc=C_FILL)
    _box(ax, 0.35, 4.2, 2.5, 0.85, "exit 65\n\"hash mismatch\"", fc=C_STOP, ec=C_ALT)
    _box(ax, 3.3, 3.0, 3.4, 0.85, "端口占用检查\n(默认 9931)", fc=C_FILL)
    _box(ax, 7.15, 3.0, 2.5, 0.85, "自动 +1 重试\n最多 20 次", fc=C_WARN, ec="#bf9000")
    _box(ax, 3.3, 1.85, 3.4, 0.85, "llama-server 加载权重\n(GGUF mmap)", fc=C_FILL)
    _box(ax, 3.3, 0.7, 3.4, 0.85, "/health 返回 200\n才算启动成功", fc="#e8f1e8", ec="#2e7d32", bold=True)

    _arrow(ax, (5.0, 5.4), (5.0, 5.05))
    _arrow(ax, (5.0, 4.2), (5.0, 3.85))
    _arrow(ax, (5.0, 3.0), (5.0, 2.7))
    _arrow(ax, (5.0, 1.85), (5.0, 1.55))
    _arrow(ax, (3.3, 4.6), (2.85, 4.6), color=C_ALT)
    ax.text(2.9, 4.72, "不匹配", fontsize=7, color=C_ALT, ha="right")
    _arrow(ax, (6.7, 3.42), (7.15, 3.42), color="#bf9000")
    ax.text(6.78, 3.52, "占用", fontsize=7, color="#bf9000")
    _arrow(ax, (7.15, 3.15), (6.7, 3.15), color="#bf9000", ls="--")

    ax.set_title("图 4-2  启动门禁链：任一环节失败必须停，不允许\"先起来再说\"", fontsize=10)
    fig.savefig(OUT / "fig-4-2-startup-gate.png")
    plt.close(fig)


def fig_flow_phases() -> None:
    """P0-P9 phase chain with the two review gates."""
    fig, ax = plt.subplots(figsize=(7.6, 3.6))
    ax.set_xlim(0, 12.4)
    ax.set_ylim(0, 4.6)
    ax.axis("off")

    phases = [
        ("P0", "边界\n角色"),
        ("P1", "环境\n固化"),
        ("P2", "模型\n导入"),
        ("P3", "数据\n清洗"),
        ("P4", "规模\n决策"),
        ("P5", "训练\n配比"),
        ("P6", "Agent\n层"),
        ("P7", "评分\n更新"),
        ("P8", "运维"),
        ("P9", "验收"),
    ]
    w, gap = 1.02, 0.2
    for i, (code, label) in enumerate(phases):
        x = 0.25 + i * (w + gap)
        fc = "#e8f1e8" if i < 3 else (C_FILL if i < 6 else C_WARN)
        _box(ax, x, 2.3, w, 1.0, f"{code}\n{label}", fc=fc, fs=7.5)
        if i:
            _arrow(ax, (x - gap, 2.8), (x, 2.8), lw=0.9)

    for pos, text in ((3, "G1 环境就绪"), (6, "G2 数据可用"), (10, "G3 验收")):
        x = 0.25 + pos * (w + gap) - gap / 2
        ax.plot([x, x], [1.95, 3.65], color=C_ALT, lw=1.0, ls="--")
        ax.text(x, 3.85, text, fontsize=7, color=C_ALT, ha="center", rotation=0)

    ax.text(
        6.2,
        1.2,
        "P1–P2 与 P3 可并行（数据清洗不依赖机器到货）；P4 必须在 P5 之前冻结\n"
        "任何阶段未过闸门不得进入下一阶段：回退比返工便宜",
        fontsize=7.5,
        ha="center",
        color="#333333",
    )
    ax.set_title("图 0-1  十个阶段与三道闸门（G1/G2/G3）", fontsize=10)
    fig.savefig(OUT / "fig-0-1-phase-gates.png")
    plt.close(fig)


def fig_flow_troubleshoot() -> None:
    """Layer-by-layer triage: process -> port -> model -> prompt -> data."""
    fig, ax = plt.subplots(figsize=(7.4, 4.4))
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 6.6)
    ax.axis("off")

    _box(ax, 3.2, 5.7, 3.6, 0.75, "服务不可用 / 答非所问", fc=C_FILL, bold=True)
    layers = [
        ("L1 进程\nps / systemctl", "没有 → 看 srm-error.log"),
        ("L2 端口\nss -lntp", "没有 → 端口被占或绑 127.0.0.1"),
        ("L3 模型\n/health + /props", "200 但空 → 权重截断"),
        ("L4 提示\ncurl 直连 /v1", "400 → 模板不匹配，查 --chat-template"),
        ("L5 数据\n检索命中率", "答非所问 → 索引缺失/切分错"),
    ]
    for i, (left, right) in enumerate(layers):
        y = 4.55 - i * 0.95
        _box(ax, 0.35, y, 3.3, 0.72, left, fc="#f2f2f2", ec=C_GREY, fs=7.5)
        _box(ax, 4.6, y, 5.0, 0.72, right, fc=C_WARN, ec="#bf9000", fs=7.5)
        _arrow(ax, (3.65, y + 0.36), (4.6, y + 0.36), lw=0.9, color=C_GREY)
        if i:
            _arrow(ax, (2.0, y + 0.95), (2.0, y + 0.72), lw=0.9)

    ax.text(5.0, 0.25, "按层排查：上一层没确认，不要动下一层", fontsize=8, ha="center", color=C_ALT)
    ax.set_title("图 7-1  五层定位法（自上而下，逐层确认）", fontsize=10)
    fig.savefig(OUT / "fig-7-1-troubleshoot-layers.png")
    plt.close(fig)


def fig_flow_acceptance() -> None:
    """Acceptance gates: air-gap proof first, then capability."""
    fig, ax = plt.subplots(figsize=(7.4, 3.8))
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 5.4)
    ax.axis("off")

    _box(ax, 3.4, 4.6, 3.2, 0.7, "验收开始", fc=C_FILL, bold=True)
    _box(ax, 0.3, 3.2, 4.3, 1.1, "闸门 A：证明无外连\n拔网线 24 h + 抓包零外连\n+ 依赖清单全本地", fc="#e8f1e8", ec="#2e7d32")
    _box(ax, 5.4, 3.2, 4.3, 1.1, "闸门 B：能力达标\n四维评分 ≥ 阈值\n+ 题库样本量足够", fc=C_WARN, ec="#bf9000")
    _box(ax, 0.3, 1.7, 4.3, 0.95, "A 不过 → 停\n不要谈能力", fc=C_STOP, ec=C_ALT, fs=7.5)
    _box(ax, 5.4, 1.7, 4.3, 0.95, "B 不过 → 分维度定位\n补数据 / 换模型 / 加 RAG", fc="#f2f2f2", ec=C_GREY, fs=7.5)
    _box(ax, 3.4, 0.35, 3.2, 0.8, "签收 + 进入维护节奏", fc=C_FILL, bold=True)

    _arrow(ax, (5.0, 4.6), (2.45, 4.3))
    _arrow(ax, (5.0, 4.6), (7.55, 4.3))
    _arrow(ax, (2.45, 3.2), (2.45, 2.65), color=C_ALT)
    _arrow(ax, (7.55, 3.2), (7.55, 2.65), color="#bf9000")
    _arrow(ax, (7.55, 1.7), (6.6, 1.15), color="#bf9000", ls="--")
    ax.text(7.05, 1.28, "重测", fontsize=7, color="#bf9000")

    ax.set_title("图 9-1  验收顺序：先证明离线，再证明有用", fontsize=10)
    fig.savefig(OUT / "fig-9-1-acceptance-gates.png")
    plt.close(fig)


FIGURES = {
    "fig-0-1-phase-gates.png": fig_flow_phases,
    "fig-1-1-concurrency-curve.png": fig_concurrency_curve,
    "fig-1-2-tier-fit.png": fig_tier_fit,
    "fig-2-1-decode-by-hardware.png": fig_decode_by_hardware,
    "fig-2-2-vram-vs-context.png": fig_vram_vs_context,
    "fig-2-3-kv-growth.png": fig_kv_growth,
    "fig-2-4-airgap-ferry.png": fig_flow_airgap,
    "fig-3-1-latency-under-load.png": fig_latency_under_load,
    "fig-4-1-measured-vs-estimate.png": fig_measured_vs_estimate,
    "fig-4-2-startup-gate.png": fig_flow_startup,
    "fig-5-1-batched-decode.png": fig_batched_decode,
    "fig-5-2-thread-sweep.png": fig_thread_sweep,
    "fig-7-1-troubleshoot-layers.png": fig_flow_troubleshoot,
    "fig-9-1-acceptance-gates.png": fig_flow_acceptance,
}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    made = 0
    for name, fn in FIGURES.items():
        fn()
        path = OUT / name
        if not path.exists():
            print(f"MISSING {name}", file=sys.stderr)
            return 1
        print(f"{name:42s} {path.stat().st_size:>7d} B")
        made += 1
    print(f"\n{made} figures -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
