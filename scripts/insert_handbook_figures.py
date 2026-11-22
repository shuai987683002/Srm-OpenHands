#!/usr/bin/env python3
"""Insert the generated figures into the two Markdown documents, idempotently.

The figures live in `docs/llm_agent/figures/` and are referenced by relative
path so the Markdown renders on GitHub and the .docx builder can resolve them
from the same source.  Running this script twice must not duplicate a figure:
each insertion is anchored on a unique heading or sentence and skipped when the
image reference is already present.

    python3 scripts/insert_handbook_figures.py            # both documents
    python3 scripts/insert_handbook_figures.py --check    # report only, exit 1 if stale

Exit codes: 0 all references present, 1 stale (with --check), 66 missing source.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOCS = REPO / "docs" / "llm_agent"
PLAN = DOCS / "离线SRM领域AI智能体部署方案.md"
HANDBOOK = DOCS / "离线部署实施手册.md"

# (file, anchor text, image file, caption).  The image block is appended right
# after the anchor paragraph, which keeps it inside the section it illustrates.
INSERTS: list[tuple[Path, str, str, str]] = [
    # ---- handbook ---------------------------------------------------------
    (
        HANDBOOK,
        "标注「估算」= 闭式模型推算，**GPU 到货后必须用 §2.6 的方法替换**。",
        "fig-0-1-phase-gates.png",
        "实施阶段与门禁：每阶段必须先过门禁才进入下一阶段，任一红灯即回退",
    ),
    (
        HANDBOOK,
        "**结论：10 人只需 3 个并发槽**，不是 10 个。这条结论直接决定了档位（主方案 §1.1.1 有完整推导）。",
        "fig-1-1-concurrency-curve.png",
        "并发槽位需求随人数与活跃率的增长：10 人在 5% 尾部只要 3 槽",
    ),
    (
        HANDBOOK,
        "**决策规则（按顺序判，命中即停）**：\n\n1. 要 ECC、要厂商 SLA、要第三方审计报告？→ **T2**。\n2. 单次分析需要 >8k 上下文（整份标准/试验报告入 prompt）？→ **T1.5**。\n3. 人数 ≤10 且以上皆否？→ **T1**。\n4. 只做教学演示、不碰真实资料？→ **T0**。",
        "fig-1-2-tier-fit.png",
        "四档硬件对「模型规模 × 上下文」的容纳能力，格子内为可运行配置",
    ),
    (
        HANDBOOK,
        "> **不要买 24 GB 卡做主力**：32B-Q4@8k 需 19.9（权重）+ 2.0（KV）+ 2.5（框架开销）\n> = 24.4 GiB > 22.1 GiB 可用，**单请求都放不下**（主方案 §3.2）。",
        "fig-2-2-vram-vs-context.png",
        "显存占用随上下文增长：24 GB 卡在 32B-Q4 上越过分界线",
    ),
    (
        HANDBOOK,
        "标注「实测」= 本机真实执行结果（4 核 x86-64、15 GB、**无 GPU**）；",
        "fig-2-1-decode-by-hardware.png",
        "解码速率的硬件决定关系：带宽是唯一主导项，算力只影响 prefill",
    ),
    (
        HANDBOOK,
        "校验器把三类问题分开报告，因为处理方式不同：",
        "fig-2-4-airgap-ferry.png",
        "三段式摆渡：准备区联网取件 → 介质校验 → 隔离区只读挂载",
    ),
    (
        HANDBOOK,
        "权重每步只读一次，KV 每个序列各读一次。所以 3 个并发时每个用户的速度\n**不是** 1/3，而是 `(W+KV)/(W+3KV)` 的比例。",
        "fig-5-1-batched-decode.png",
        "批量解码实测：聚合吞吐上升、每路速率下降，batch=4 时与带宽上界差 0.1%",
    ),
    (
        HANDBOOK,
        "> 上式为**估算**。实测环境无 GPU，因此这条曲线必须在到货后用\n> `healthcheck.py --concurrency N` 逐点标定（§2.6）。",
        "fig-5-2-thread-sweep.png",
        "线程数扫描：本机 2 物理核最优，4 线程（含 SMT）解码反而慢 14%",
    ),
    (
        HANDBOOK,
        "### 5.5 决策点 D-P1：上下文换并发",
        "fig-2-3-kv-growth.png",
        "KV 缓存随上下文线性增长：这是「32k 只剩 2 槽」的量化原因",
    ),
    (
        HANDBOOK,
        "| 场景 | ctx | 槽位 | 单用户 tok/s（估算） | 选它 |",
        "fig-3-1-latency-under-load.png",
        "并发升高时时延与吞吐的取舍：吞吐饱和后继续加并发只增加排队",
    ),
    (
        HANDBOOK,
        "参数写在 `deploy/config/server.yaml`，由 `render_env.py` 渲染成 systemd 的\n`EnvironmentFile`。好处是：改参数不动 unit 文件、diff 能看清改了什么、\n文档与运行配置读同一组数字。",
        "fig-4-2-startup-gate.png",
        "启动门禁：哈希 → 端口 → 探活 → 真实生成，任一失败即不对外服务",
    ),
    (
        HANDBOOK,
        "### 7.3 故障表（按现象索引）",
        "fig-7-1-troubleshoot-layers.png",
        "分层排查顺序：从进程到模型逐层排除，避免在错误的层上反复重启",
    ),
    (
        HANDBOOK,
        "### 9.3 验收门禁（逐条可判）",
        "fig-9-1-acceptance-gates.png",
        "验收门禁的判定链：无外连与能力达标是两条独立证据链，缺一不可",
    ),
    (
        HANDBOOK,
        "本机（4 核 x86-64，无 GPU）用 llama.cpp b12627 实测：",
        "fig-4-1-measured-vs-estimate.png",
        "实测与上界模型的对照：batch=1 时上界偏保守，饱和后精确",
    ),
    (
        HANDBOOK,
        "| 模型门禁有效 | 空哈希/错哈希/缺文件/非 GGUF 全部拦截 | `verify_models.py` |",
        "fig-4-3-ui-chat.png",
        "实测 Web UI 界面：模型名与 token 统计由服务端提供；两条真实回答暴露了 0.5B 的领域幻觉",
    ),
    # ---- main plan --------------------------------------------------------
    (
        PLAN,
        "- 以上全部由 `scripts/llm_sizing.py` 复算，改参数即可重出表。",
        "fig-0-1-phase-gates.png",
        "P0–P9 阶段与门禁：任一阶段门禁不过，回退到上一阶段而不是带着缺陷前进",
    ),
    (
        PLAN,
        "### 2.2 系统架构（单机/小集群，全离线）",
        "fig-2-4-airgap-ferry.png",
        "离线三段式摆渡：唯一联网的准备区 → 介质与哈希校验 → 隔离区只读运行",
    ),
    (
        PLAN,
        "3. **训练可行性**：`LoRA 需 ≈ W×1.2`；`全参 AdamW 需 ≈ 16 B/参数`（fp16 权重+梯度+fp32 主权重+优化器状态）。",
        "fig-2-1-decode-by-hardware.png",
        "解码速率由带宽决定：同一模型换硬件，速度随有效带宽近似线性变化",
    ),
    (
        PLAN,
        "4. 30+ 人或多项目并行 → T3；若还需要 32B 全参训练，T3 必须选 **8×80G**（8×48G 不够）。",
        "fig-1-2-tier-fit.png",
        "四档硬件对「模型规模 × 上下文」的容纳能力，格子内为可运行配置",
    ),
]


def image_block(name: str, caption: str) -> str:
    return f"\n![{caption}](figures/{name})\n\n*{caption}*\n"


def apply(path: Path, anchor: str, name: str, caption: str, check: bool) -> bool:
    """Return True when the file needed (or would need) a change."""
    text = path.read_text(encoding="utf-8")
    ref = f"(figures/{name})"
    if ref in text:
        return False
    if anchor not in text:
        raise SystemExit(f"FATAL: anchor not found in {path.name}: {anchor[:60]!r}")
    block = image_block(name, caption)
    # Insert after the whole anchor paragraph.
    idx = text.index(anchor) + len(anchor)
    new = text[:idx] + "\n" + block.strip("\n") + "\n" + text[idx:]
    if not check:
        path.write_text(new, encoding="utf-8")
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="report stale docs, do not write")
    args = ap.parse_args(argv)

    for p in (PLAN, HANDBOOK):
        if not p.is_file():
            print(f"FATAL: missing {p}", file=sys.stderr)
            return 66

    changed = 0
    seen: set[tuple[Path, str]] = set()
    for path, anchor, name, caption in INSERTS:
        key = (path, name)
        if key in seen:          # two anchors, one figure: keep the first
            continue
        seen.add(key)
        if apply(path, anchor, name, caption, args.check):
            changed += 1
            print(f"{'would insert' if args.check else 'inserted'}: {name} -> {path.name}")

    if args.check and changed:
        print(f"{changed} figure(s) missing", file=sys.stderr)
        return 1
    print(f"{changed} change(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
