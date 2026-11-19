# Srm-Open 用户指南

面向**科研教学**与**非商业工业研发**。所有示例使用内置占位数据，不是经过
验证的发动机设计。

## 1. 安装

```bash
pip install -e .            # 核心引擎（numpy/scipy/pydantic/PyYAML/shapely）
pip install -e ".[all]"     # 含绘图、CAD 导出、外部热化学、优化器
```

核心引擎可离线运行；`cantera` / `rocketcea` 仅用于参考值比对，不是运行依赖。

## 2. 命令行（CLI）

```bash
srm templates                       # 列出内置药型模板
srm propellants                     # 列出推进剂条目
srm thermo --propellant ap_htpb_80  # 评估热化学（c*, γ, T_c, 分子量）

# 正向设计：由指标生成三维药型并评估
srm forward --template star_6_point --propellant ap_htpb_80 \
    --thrust 8000 --burn-time 4 --max-pressure 9e6 --report out/ --json out/r.json

# 逆向设计：由弹道要求反推药型参数
srm inverse --template star_6_point --thrust 6000 --burn-time 4 --max-pressure 9e6

# 逆向：由已有药型项目反推几何特征
srm reverse --project examples/projects/02_star_neutral/project.yaml

# 逆向路径 A：由二维端口轮廓反推药型参数
srm fit-outline --points outline.csv --family star --outer-diameter 0.16 --length 0.8

# 可辨识性诊断（条件数 / VIF / frozen / inactive）
srm sensitivity --family star --outer-diameter 0.16

# 批量研究：参数扫描与蒙特卡洛
srm sweep --template tube_simple --thrust 6000 --burn-time 4 \
    --parameter max_chamber_pressure --values 6e6,7e6,8e6
srm monte-carlo --template tube_simple --thrust 6000 --burn-time 4 \
    --vary density:15 --samples 100 --seed 42

# 导出几何（stl / step / 点云）
srm export-cad --template star_6_point --format stl --out grain.stl
srm export-cad --template star_6_point --format pointcloud --out grain.xyz
```

> 直接以源码运行：`PYTHONPATH=src python -m srm_open.api.cli ...`

## 3. Python API

```python
from srm_open.api import design_forward, design_from_template

# A) 指标 → 药型 → 性能（正向设计，自动选型 + 合成药柱 + 局部精修）
r = design_forward({"thrust": 8000, "burn_time": 4.0, "max_chamber_pressure": 9e6,
                    "outer_diameter": 0.16, "length_ratio": 5,
                    "thrust_profile": "neutral"}, "ap_htpb_80")
print(r.performance.total_impulse, r.performance.specific_impulse)
print(r.ballistics.peak_pressure, r.grain.segments[0].params)

# 密集参数扫描时可关闭精修以节省时间（此时推力可能略偏离指标）
r_fast = design_forward({...}, "ap_htpb_80", refine=False)

# B) 给定模板 → 性能
r = design_from_template("tube_simple", "ap_htpb_80",
                         {"thrust": 4000, "burn_time": 3.0})
print(r.verification.checks)
```

## 4. 示例工程

```bash
PYTHONPATH=src python examples/run_case.py examples/projects/06_synthesis_star
```

| 目录 | 类型 | 说明 |
|---|---|---|
| 01_tube_sounding | 固定几何 | 小型探空火箭管形药柱 |
| 02_star_neutral | 固定几何 | 星形药柱（近中性） |
| 03_wagon_wheel_regressive | 固定几何 | 车轮形（退减） |
| 04_multi_perf | 固定几何 | 7 孔多孔药柱 |
| 05_two_segment_booster | 固定几何 | 两段柱药助推器 |
| 06_synthesis_star | 指标驱动 | 由指标合成星形药柱 |
| 07_synthesis_tube | 指标驱动 | 由指标合成管形药柱（渐进） |
| 08_inverse_identifiability | 逆向研究 | 轮廓反演 + 可辨识性 + 扫描 + 蒙特卡洛 |

每个用例运行后会在 `output/` 生成 `report.md`、`report.html`、`report.pdf`、曲线 PNG 与 `result.json`。

## 5. 报告内容

报告包含：指标表、性能表、校核项及残差、假设与来源（provenance）、曲线图。

三种格式由**同一份 Markdown 渲染**，内容不会互相矛盾。PDF 由 matplotlib 生成，
是唯一的可选格式：未安装 matplotlib 时 `write_report()` 只返回 md/html，不会报错。

`provenance` 段落记录复现所需的全部信息（NFR-2）：

| 字段 | 含义 |
|---|---|
| software version | 本软件版本 |
| model choices | 内弹道/燃面/热化学/强度所用模型 |
| dependency versions | numpy / scipy / shapely / pydantic / PyYAML 的运行时版本 |
| input hash (sha256) | 对指标集+药型+推进剂+喷管做规范化 JSON 后的哈希，可用于比对两次设计是否同输入 |
| random seed | 逆向优化与 Monte-Carlo 使用的种子 |

同样的输入得到同样的 `input hash`，因此"两次报告是否为同一设计"可以直接比对哈希，
不必逐行 diff。
`result.json` 为完整 `DesignResult` 序列化，可用于二次分析。

## 6. 配置

`configs/default.yaml` 分层配置（内弹道模型、燃面通道、网格、优化器、报告等）。
可通过 `srm_open.config.get("burnback.n_steps")` 读取。

## 7. 教学建议

- 改动单一参数（如 `--max-pressure`）观察压强/推力曲线变化；
- 对比解析通道与 Level-Set 通道的燃面曲线，理解网格离散误差；
- 用 `srm thermo` 对比不同推进剂的热化学与比冲；
- 用逆向设计从目标推力/总冲反推药型参数，体会可辨识性与病态性；
- 用 `srm sensitivity` 对比管形（参数正交、可辨识）与星形（孔径/长度共线、
  病态）的灵敏度矩阵，理解"为什么同一份燃面数据对不同药型的信息量不同"；
- 用 `srm monte-carlo` 观察燃速系数与密度的分散如何传到总冲，建立"公差—性能"
  的定量直觉。

## 8. 边界与免责

仅供科研、教学与非商业研发；禁止武器化/批产/军贸用途。输出不保证工程正确性，
工程应用须经独立验证与专业评审。详见仓库 README 与
`docs/开源固体火箭发动机设计软件实现方案.md`。
