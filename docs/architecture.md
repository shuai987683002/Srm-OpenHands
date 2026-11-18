# 模块划分、职责与接口（Architecture）

本文件是 `docs/开源固体火箭发动机设计软件实现方案.md` 的实现侧对照：给出
**已落地的模块、职责、输入/输出与数据流**。所有模块位于 `src/srm_open/`。

## 1. 分层

```
            ┌──────────────────── api/ (facade + cli) ────────────────────┐
            │                                                              │
 orchestration/   io/            report/                         inverse/
 (正向流水线)  (项目读写/模板库)  (报告/曲线)                    (逆向流水线)
            │                                                              │
 ┌──────────┴───────── 计算内核 ──────────────────────────────────────────┐
 │ ballistics/   performance/   burnback/   geometry/   grain_body/        │
 │ nozzle/       propellant/    thermo/     integrity/                     │
 └────────────────────────────────────────────────────────────────────────┘
                              models/  (pydantic 数据模型)
                              config.py (分层配置)
```

依赖方向自上而下；内核模块之间不循环依赖。`models/` 被所有层引用。

## 2. 模块职责与输入/输出

| 模块 | 职责 | 主要输入 | 主要输出 |
|---|---|---|---|
| `models/` | 数据模型与校验 | 字典/JSON/YAML | `RequirementSet`、`GrainGeometry`、`PropellantModel`、`NozzleModel`、`DesignResult` 等 |
| `config.py` | 分层配置（YAML 1.1 数字串归一） | `configs/*.yaml` | `Config` 树 |
| `propellant/` | 燃速定律 + 推进剂库 | 推进剂 id / 自定义 | `PropellantModel`（含 `BurnRateLaw`） |
| `thermo/` | 热化学（Gibbs 平衡 + 适配器） | 元素配比、压强 | `ThermoResult`（c*, γ, T_c, MW, x_i） |
| `nozzle/` | 等熵喷管 | 喉径、膨胀比、γ | `Nozzle`（c*, ṁ, C_F, M_e） |
| `geometry/` | 参数化药型族 + 2D 分析 | 族 + 参数 | `GrainGeometry`、端口多边形、燃面/肉厚 |
| `burnback/` | 燃面演化（解析 + Level-Set） | `GrainGeometry` | `BurnbackCurve`（e, A_b, 体积） |
| `ballistics/` | 零维内弹道 | 推进剂 + 药柱 + 喷管 | `InternalBallisticsResult`（p_c(t), ṁ…） |
| `performance/` | 性能积分 | 内弹道 + 喷管 | `PerformanceResult`（F, I_t, I_sp…） |
| `grain_body/` | 柱药主体计算 | 药柱 + 推进剂 | `GrainBodyResult`（肉厚、余药、装载、长径比） |
| `integrity/` | 结构校核 | 几何 + 材料 + p | Lamé 解 / 轴对称 FEM 结果 |
| `inverse/` | 逆向设计 | 指标 / 燃面数据 / 现有药型 | `DesignResult` 或参数估计 |
| `orchestration/` | 指标补全 + 正向流水线 + 药柱合成 | 指标 + 推进剂 | `DesignResult` |
| `verification/` | 设计级校核链路 | `DesignResult` | 命名检查项（pass/fail + 残差） |
| `io/` | 项目读写 + 模板库 | `project.yaml` / 模板 | 项目对象 / `GrainGeometry` |
| `report/` | 报告与曲线 | `DesignResult` | Markdown / HTML / PNG |
| `api/` | 门面 + CLI | 用户输入 | `DesignResult` / 控制台输出 |

## 3. 核心数据模型

- `RequirementSet`：推力、工作时间、总冲、比冲、装药量、外径、长径比、
  压强上限、推力形状（neutral/progressive/regressive）、`thrust_profile` 曲线。
- `GrainGeometry`：`segments: list[GrainSegment]`；每段含
  `family`、`params`、`burn_mode`(internal/external/both/end)、`outer_diameter`、`length`。
- `PropellantModel`：密度、`BurnRateLaw`（a, n, σ_p, T_ref）、组成（元素配比）、
  热化学（direct 或 gibbs）。
- `NozzleModel`：喉径、膨胀比、γ。
- `DesignResult`：`requirement`、`grain`、`propellant`、`nozzle`、`burnback`、
  `ballistics`、`performance`、`grain_body`、`integrity`、`verification`、
  `provenance`、`mode`(forward/inverse)。

## 4. 数据流

### 正向

```
RequirementSet ─► requirements.complete() ─► (m_p, A_t, p_design, family)
      │                                              │
      │                        synthesize_grain() ◄──┘   (质量+均燃面+肉厚)
      ▼                                   │
 GrainGeometry ◄──────────────────────────┘
      │
      ├─► burnback ─► A_b(e)
      │        │
      │        └─► geometry  ─► A_b0, web, port area
      ▼
 ballistics (p_c(t)) ─► performance (F(t), I_t, I_sp) ─► grain_body (余药/装载)
      │                                                        │
      └────────────────► verification (校核链路) ◄─────────────┘
                                   │
                                   ▼
                          report (md/html/png) + result.json
```

### 逆向

```
目标（推力/总冲/工作时间/比冲） 或 现有药型几何 或 燃面数据 A_b(t)
        │
        ├─ 几何逆向：analyze_existing_grain() ─► 几何特征 + A_b(e) 拟合
        ├─ 燃面逆向：fit_burn_area(A_b(t)) ─► 燃速/肉厚/药型参数
        └─ 弹道逆向：inverse_design() ─► 设计空间优化 ─► GrainGeometry
                                   │
                                   ▼
                        与正向共享同一内核 ─► DesignResult
```

**关键一致性保证**：正/逆向的 `forward_design` 与 `inverse_design` 都调用同一
`ballistics/`、`burnback/`、`performance/`、`nozzle/` 代码；燃面只有两个通道
（解析 / Level-Set），两条流水线都经 `burn_area_at` 访问，不存在第三套实现。

## 5. 参数与校核指标

### 正向设计参数（输入 → 推导）

| 指标 | 符号 | 用途 |
|---|---|---|
| 推力 | F | 定喉径与工作点 |
| 工作时间 | t_b | 定肉厚（e = r·t_b） |
| 总冲 | I_t | 定装药量（m_p = I_t/(I_sp g0)） |
| 比冲 | I_sp | 热化学 + 喷管 |
| 压强上限 | p_max | 定设计压强与喉径 |
| 外径 / 长径比 | D / L-D | 定药柱包络 |

### 校核指标

| 指标 | 判据 |
|---|---|
| 平均推力偏差 | ≤ 5% |
| 总冲偏差 | ≤ 5% |
| 工作时间偏差 | ≤ 15% |
| 峰值压强 | ≤ 许用 |
| 质量守恒残差 | ≤ 1e-3 |
| 肉厚 / 尺寸 | > 0 |
| 燃面双通道偏差 | 报告（教学级网格） |
| 结构应力 vs Lamé | < 1% |

## 6. 可追溯与可复现

- `DesignResult.provenance`：软件版本、依赖版本、模型选择（内弹道模型、燃面
  通道、优化器）、随机种子、指标补全说明。
- 逆向优化固定 `seed=12345`，可复现。
- 报告把每个数字溯源到输入与模型假设。
