# 热化学后端（Thermochemistry Backends）

本文件说明 `srm_open.thermo.backend` 的**可插拔热化学后端**：各自做什么、
什么时候用、数值从哪里来，以及如何交叉核对。

所有后端都返回同一个 `ThermoResult`：

| 字段 | 含义 |
|---|---|
| `c_star` | 特征速度 c*（m/s），`c* = √(R·T_c/M) / Γ` |
| `gamma` | 比热比 γ |
| `molecular_weight` | 燃烧产物平均摩尔质量 M（kg/mol） |
| `chamber_temperature` | 燃烧室温度 T_c（K） |
| `chamber_pressure` | 求值压强（Pa） |
| `mode` | 实际使用的后端名，用于可追溯 |
| `converged` | 迭代后端是否收敛 |
| `mole_fractions` | 平衡组分摩尔分数（若后端提供） |

`evaluate(propellant, pressure, backend)` 的分派规则：

1. `backend="direct"`：直接读用户填写的 c*、γ、T_c、M（`propellant.thermo.mode == "direct"`）。
2. `backend="auto"`：
   - 若推进剂 `thermo.mode == "direct"` → 用 `direct`；
   - 否则若提供了 `composition` 且 `heat_of_formation` → 用 `gibbs`；
   - 否则回退到已有的 `direct` 记录；再不行报错。
3. `backend="gibbs"`：强制自带 Gibbs 求解器（需 composition + 生成焓）。
4. `backend="rocketcea"` / `backend="cantera"`：外部后端，见下文。

## 1. `gibbs`（自带，默认可用）

- 实现：`srm_open.thermo.gibbs.GibbsEquilibrium`，NASA-7 多项式 + 元素守恒约束的
  平衡组分求解；先解绝热火焰温度（HP 平衡），再在该温度/压强下求平衡组分。
- 数据：`data/thermo/thermo.yaml`，74 个组分（含 AP 体系所需 Cl 组分），
  由 `scripts/build_thermo_db.py` 生成，来源为 NASA TM-4513 / Cantera 公开数据。
- 适用：任意由 `composition`（元素质量或摩尔比）+ `heat_of_formation` 描述的配方。
- 局限：AP 体系的**含氯凝聚相**（如 HCl 冷凝、Al₂O₃ 凝聚）建模简化，
  计算 T_ad 低于 Cantera 同库结果。**因此 AP 类配方默认建议用文献 `direct` 值**，
  Gibbs 结果用于趋势分析与教学。

## 2. `rocketcea`（可选）

- 依赖 NASA CEA 的 Fortran 内核（`rocketcea` Python 包装），**非运行必需**。
- 现状：`_evaluate_rocketcea` 需要把推进剂配方转换为 CEA 的**自定义配方卡片**
  （custom formulation deck）。本仓库未内置该卡片生成器，因此该后端当前会抛出
  `NotImplementedError`，并提示改用 `gibbs`。
- 若需启用：按 CEA 输入格式（`problem`/`reactant`/`oxid`/`fuel` 卡）生成 deck，
  调用 `rocketcea.CEA_Obj` 获得 c*、γ、T_c，再映射回 `ThermoResult`。
  实现后请补充对应的交叉核对用例（与 `gibbs` 或文献值比对）。

## 3. `cantera`（可选）

- 依赖 `cantera` 与用户提供的**机理文件**（如 `gri30.yaml`）。
- 现状：`_evaluate_cantera` 需要用户机理；本仓库不内置机理，故该后端当前抛出
  `NotImplementedError`，并提示改用 `gibbs`。
- 若需启用：以 `FreeFlame`/`Equilibrate` 求定压绝热平衡，取 T_ad 与组分，
  再算 γ、M、c*，映射回 `ThermoResult`。
- 用途：在校核测试（V1）中作为**参考值来源**与自带 Gibbs 求解器比对。

## 4. `direct`（用户给定）

- 完全由用户/数据字典提供 c*、γ、T_c、M，不做任何化学计算。
- 适合已有试验/文献数据、或需要与既定设计对齐的场景。
- 数据条目中应写明 `source`，便于追溯。

## 5. 交叉核对建议

| 场景 | 做法 |
|---|---|
| 自带 Gibbs 是否可信 | 与 Cantera gri30 平衡值比对（见 V1 用例） |
| 配方改动的影响 | 同配方分别跑 `gibbs` 与外部后端，比较 c*、T_c |
| 后端不一致 | 记录两者差值；以文献/试验值裁决，并在结果 `mode` 中注明 |

> 任何后端的选择都会写入 `DesignResult.provenance.model_choices["thermo"]`，
> 保证结果可回溯到所用模型与数据来源。
