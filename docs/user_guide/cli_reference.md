# CLI 参考（Command-line reference）

安装后（`pip install -e .`）可直接调用 `srm`；未安装时用
`PYTHONPATH=src python -m srm_open.api.cli` 等效调用。

所有命令共用选项：

| 选项 | 说明 |
|---|---|
| `--propellant` | 推进剂 id（默认 `ap_htpb_80`） |
| `--thrust` | 目标推力 [N] |
| `--burn-time` | 目标工作时间 [s] |
| `--max-pressure` | 压强上限 [Pa] |
| `--report DIR` | 写出 Markdown/HTML 报告与曲线到 DIR |
| `--json PATH` | 写出 `DesignResult` JSON |

## `srm templates`

列出内置药型模板 id 与名称。输出为 JSON。

```bash
srm templates
```

## `srm propellants`

列出推进剂条目 id 与名称。

## `srm thermo --propellant ID`

评估推进剂热化学，输出 `c*`、`γ`、分子量、`T_c`、求解模式与是否收敛。

```bash
srm thermo --propellant ap_htpb_80
# { "c_star": 1550.0, "gamma": 1.19, "molecular_weight": 0.0245,
#   "chamber_temperature": 3100.0, "mode": "direct", "converged": true }
```

## `srm forward`

由指标正向设计：自动选型 + 合成药柱 + 评估性能。

```bash
srm forward --template star_6_point --propellant ap_htpb_80 \
    --thrust 8000 --burn-time 4 --max-pressure 9e6 --report out/ --json out/r.json
```

输出：喉径、峰值压强、总冲、比冲、平均推力、工作时间、装药量，以及校核项
通过数（并列出未通过的检查项）。

> 提示：固定模板的几何是给定的，其性能未必等于填写的指标，因此
> `*_vs_requirement` 检查可能不通过——这正是校核链路的提示。若希望由指标驱动
> 几何，请用 `--template` 配合指标并参考 `examples/projects/06..07`（`synthesis.yaml`）。

## `srm inverse`

由弹道要求反推药型参数（共享同一内核）。

```bash
srm inverse --template star_6_point --thrust 6000 --burn-time 4 --max-pressure 9e6
```

输出恢复的药型参数、喉径、性能，以及 `inverse_convergence` 检查（以**达成的
物理残差**判定，而非优化器内部 `success` 标志）。

## `srm reverse --project PATH`

由已有药型项目反推几何特征与燃面数据：总长、外径、长径比、端口面积、初始/
最大燃面、肉厚、装药体积/质量、装载分数。

```bash
srm reverse --project examples/projects/02_star_neutral/project.yaml
```

## `srm export-cad --template ID --format {stl,step} --out PATH`

导出药柱几何（`stl` 纯 Python；`step` 需可选依赖 `cadquery`）。

## `srm save-project --template ID --out PATH`

把模板 + 指标写成可复现的 `project.yaml`。

## 退出码

- `0` 成功；`2` 参数/文件错误。校核项未通过**不**改变退出码（结果为信息性）。
