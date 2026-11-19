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

## `srm design`

**指标驱动正向设计**：给定总体指标，自动选型 → 合成药柱 → 迭代校正 → M9 局部精修
→ 评估性能。这是"从指标生成三维药型方案"的主入口（方案 §5.1）。

```bash
srm design --thrust 8000 --burn-time 4 --max-pressure 9e6 \
    --outer-diameter 0.16 --length-ratio 5 --thrust-profile neutral \
    --report out/ --json out/r.json
# throat diameter   : 0.03768 m
# average thrust    : 7999.8 N
# burn time         : 4.0000 s
# verification      : 9/9 passed
```

| 选项 | 说明 |
|---|---|
| `--family` | 指定药型（`tube`/`star`/`wagon_wheel`/…）；省略则按推力偏好自动选型 |
| `--outer-diameter` | 外径 [m] |
| `--length-ratio` | 长径比 |
| `--thrust-profile` | 推力偏好：`neutral`/`progressive`/`regressive` |
| `--no-refine` | 跳过 M9 局部精修（更快，但推力/工作时间可能偏离指标） |

> 关闭精修（`--no-refine`）时仅靠解析合成初值，指标通常只命中部分检查项；
> 精修后示例可达 9/9。精修为局部搜索，初值离可行域较远时请增大迭代或用 `srm inverse`。

## `srm forward`

给定**固定模板**几何，正向评估性能（裕度评估，非指标合成）。

```bash
srm forward --template star_6_point --propellant ap_htpb_80 \
    --thrust 8000 --burn-time 4 --max-pressure 9e6 --report out/ --json out/r.json
```

输出：喉径、峰值压强、总冲、比冲、平均推力、工作时间、装药量，以及校核项
通过数（并列出未通过的检查项）。

> 提示：固定模板的几何是给定的，其性能未必等于填写的指标，因此
> `*_vs_requirement` 检查可能不通过——这正是校核链路的提示。若希望由指标驱动
> 几何，请用 `srm design`（或 `examples/projects/06..07` 的 `synthesis.yaml`）。

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
