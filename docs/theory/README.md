# 理论文档索引（Theory）

本目录给出各模型的方程、假设与代码对应关系。所有模型均为**公开教科书级**，
不含受控细节；具体数值一律由数据字典/用户提供。

| 文件 | 内容 |
|---|---|
| [internal_ballistics.md](internal_ballistics.md) | 燃速定律、平衡压强、瞬态充填、c*/推力、建模假设 |
| [burnback_models.md](burnback_models.md) | 几何解析通道、Level-Set 通道、双通道互校、药型族 |
| [grain_cavity_families.md](grain_cavity_families.md) | 各类内腔（回转/等截面/星形/翼型/楔形/嵌金属丝）的几何参数、构型特征、适用工况与燃面/性能对比 |
| [assumptions.md](assumptions.md) | 全项目假设与适用域（含"资料不足"处明确声明） |

## 代码对照

| 模型 | 代码 |
|---|---|
| 燃速定律 | `srm_open.propellant.burn_rate` |
| 热化学（Gibbs） | `srm_open.thermo.gibbs` / `srm_open.thermo.backend` |
| 喷管等熵 | `srm_open.nozzle.model` |
| 燃面（解析） | `srm_open.burnback.geometric` |
| 燃面（Level-Set） | `srm_open.burnback.levelset` |
| 内弹道 | `srm_open.ballistics.solver` |
| 性能 | `srm_open.performance.analysis` |
| 柱药主体 | `srm_open.grain_body.main_body` |
| 结构（Lamé / FEM） | `srm_open.integrity.lame` / `srm_open.integrity.fem` |

## 参考来源（公开）

- G.P. Sutton, O. Biblarz, *Rocket Propulsion Elements*（固体推进基础、喷管）。
- NASA CEA / NASA-7 多项式（热化学，`data/thermo` 来源见 `data_registry/`）。
- Saint-Robert（Vieille）燃速定律、Minkowski 膨胀/距离函数（燃面演化）。
- 厚壁圆筒 Lamé 解（结构校核参考解）。

> 引用具体文献编号前，请通过官方渠道核对原文；本仓库不复制受控内容。
