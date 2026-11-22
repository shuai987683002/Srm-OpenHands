# 需求合规矩阵（FR / NFR 审计）

本文件对照 [`docs/开源固体火箭发动机设计软件实现方案.md`](开源固体火箭发动机设计软件实现方案.md)
第 2 章的 13 条功能需求与 8 条非功能需求，逐条给出**实现位置**与**可执行的验证证据**。

审计原则：每一条都必须指向一个能在本仓库里跑出来的证据——测试、脚本或示例输出，
而不是"已经实现了"的口头结论。无法给出证据的条目记为**部分满足**并写明缺口。

复现全部证据：

```bash
PYTHONPATH=src pytest -q                      # 功能/回归测试（140 项）
PYTHONPATH=src pytest -m verification -q -s   # 9 个校核算例 V1–V9
PYTHONPATH=src pytest -m performance -q -s    # NFR-3 性能预算
```

---

## 1. 功能性需求（FR）

| 编号 | 需求摘要 | 实现位置 | 验证证据 | 状态 |
|---|---|---|---|---|
| FR-1 | 输入总体指标并校验一致性 | `models/requirements.py`、`orchestration/requirements.py` | 5 项 `test_requirements_*`：补全 I_t / m_p、冲突不覆盖、自由度提示、F vs F_avg 提示 | ✅ |
| FR-2 | 由指标生成参数化药型（选型+初值+几何） | `orchestration/synthesis.py`、`api/facade.py:design_forward` | `examples/projects/06_synthesis_star`、`07_synthesis_tube`（9/9 校核通过） | ✅ |
| FR-3 | 由几何/燃面/弹道要求反推参数 | `inverse/design.py`（`fit_outline` / `fit_burn_area` / `inverse_design` / `sensitivity_matrix`）、`inverse/optimize.py` | 路径 A/A'/B/C 四组用例；V7（轮廓往返）、V8（可辨识性）；`examples/projects/08_inverse_identifiability` | ✅ |
| FR-4 | 推进剂与喷管参数管理（数据字典驱动） | `propellant/database.py`、`data/propellants/*.yaml`、`nozzle/model.py` | 4 项 `test_propellant_db_*`（YAML 往返、用户增删、未知 id 报错、来源必填）；喷管用例 `test_exit_mach_*`/`test_thrust_coefficient_*` | ✅ |
| FR-5 | 燃面演化：几何解析 + Level-Set 双通道互校 | `burnback/geometric.py`、`burnback/levelset.py`、`burnback/engine.py:crosscheck` | V4（`test_v4_burnback_crosscheck`，多药型参数化，最大偏差 < 8%） | ✅ |
| FR-6 | 零维平衡压强内弹道（含余药阶段） | `ballistics/solver.py` | V6（平衡压强闭式解 + 质量守恒残差 ~1e-15）；`test_core.py` 求解器用例 | ✅ |
| FR-7 | 性能分析：总冲/比冲/C_F/A_b/A_t/余药/装载分数/峰值压强 | `performance/analysis.py`、`models/results.py:PerformanceResult` | 报告 §5 Performance 表；`test_core.py` 性能用例 | ✅ |
| FR-8 | 柱药主体计算（几何/肉厚/燃面/余药/体积/强度） | `grain_body/main_body.py`、`integrity/lame.py`、`integrity/fem.py` | V5（轴对称 FEM vs Lamé 厚壁解，< 0.4%）；报告 §6 Grain body | ✅ |
| FR-9 | 校核链路（推力↔压强↔燃面↔几何一致性） | `verification/checks.py` | 每次设计自动执行 7–9 项检查，结果写入 `verification.checks` | ✅ |
| FR-10 | 三维几何导出（STEP/STL/点云）+ 三维预览 | `geometry/sketch.py:grain_to_step/grain_to_stl/grain_to_pointcloud`、CLI `export-cad`、`viz/` | `test_stl_export_is_watertight_ascii`、`test_step_export_writes_iso10303`、`test_cli_pointcloud_export`；`test_viz.py` 6 项（12 个模板全部可渲染且非空白） | ✅ |
| FR-11 | 报告导出 PDF/HTML/Markdown | `report/__init__.py:write_report` | `test_report_pdf_is_a_valid_pdf` 等 5 项；示例 `output/report.pdf` | ✅ |
| FR-12 | 脚本 API / CLI：批量扫描、敏感性、Monte-Carlo；可选 REST | `api/cli.py`、`api/facade.py`、`api/rest.py`、`orchestration/study.py` | `srm sensitivity / sweep / monte-carlo` 三组 CLI 用例；V9（同种子逐位一致）；`test_rest_api.py` 13 项（真实引擎经 HTTP 调用，404/422 语义、provenance 哈希透传、逆向闭环命中目标、STL/草图契约） | ✅ |
| FR-13 | 逆向优化器（匹配 A_b(t)/F(t)/I_t，输出可行设计集） | `inverse/design.py:inverse_design`、`inverse/optimize.py` | `test_end_to_end.py` 逆向收敛用例 + 收敛残差写入报告 | ✅ |

## 2. 非功能性需求（NFR）

| 编号 | 需求 | 实现位置 | 验证证据 | 状态 |
|---|---|---|---|---|
| NFR-1 | 跨平台，Python ≥ 3.10 | `pyproject.toml:requires-python` | 纯 Python + numpy/scipy，无平台专有调用 | ✅ |
| NFR-2 | 数值可复现：固定种子、记录版本与依赖哈希、依赖锁定 | `provenance.py`、`orchestration/forward.py`、`uv.lock` | `test_provenance_records_dependencies_and_input_hash`：同输入哈希相同、异输入不同；`uv sync --frozen` 实测可复现安装（130 包，含哈希） | ✅ |
| NFR-3 | 单次零维内弹道 < 1 s；Level-Set 单方案 < 30 s | 数值实现 | `pytest -m performance -s`：实测 90 ms / 0.06 s（60×60×20） | ✅ |
| NFR-4 | 核心模块测试覆盖率 ≥ 80% | `tests/` | `pytest --cov=srm_open`：**86%**（3462 语句，476 未覆盖） | ✅ |
| NFR-5 | 插件式注册（推进剂/燃速/喷管/燃面算法）+ 数据集 schema | `geometry/families.py:register_family`、`propellant/burn_rate.py:register_burn_rate_law`、`data/schema/`、`scripts/build_schema.py` | `test_burn_rate_law_is_pluggable`（运行时替换燃速律）；`test_every_grain_family_has_a_builder_and_a_design_space`；`test_schema.py` 11 项（schema 与模型一致、5 个示例工程全部通过校验、坏数据被拒） | ✅ |
| NFR-6 | 可解释：结果可回溯到公式与假设 | `docs/theory/*.md`、`Provenance`、`GrainBodyResult.assumptions` | 报告 §11 列出模型选择、假设与数据来源 | ✅ |
| NFR-7 | 国际化：中文优先，预留英文 | 文档中文优先；标识符/字段名为英文 | 报告与文档中文；代码与数据键英文，具备接入 gettext 的结构 | ⚠️ 部分 |
| NFR-8 | 安全：不联网、不内置敏感数据、SBOM 可审计 | `data_registry/sbom.md`、`data_registry/data_sources.md` | 核心求解不发起网络请求；SBOM 登记全部运行时/可选依赖与许可证 | ✅ |

### 本轮补充的能力（计划 §6/§7 中先前只写在文档里的部分）

| 计划条目 | 先前状态 | 现在 |
|---|---|---|
| §6 后端 FastAPI（REST） | 仅有 CLI/脚本 API | `api/rest.py`：`/health`、`/templates`、`/propellants`、`/propellants/{id}/thermo`、`/families`、`/design`（可选 `summary/curves/sketch`）、`/inverse`、`/sketch`、`/grain/stl`；`api` 为可选 extra，未装 FastAPI 时核心包不受影响 |
| §7 `data/schema/` 数据集 JSON Schema | 目录不存在 | 5 个 schema 由 `scripts/build_schema.py` 从 pydantic 模型生成；测试保证"模型改了但没重新生成"会失败 |
| §M5 三维可视化 | 只有 2D 曲线 | `viz/`：药柱三维图与燃面回归示意图，已接入报告（`grain_3d.png`、`burnback_3d.png`） |
| §7 `uv.lock` 依赖锁定 | 缺失 | 已生成并提交；`uv sync --frozen` 实测可用 |
| §M5 Web 前端 | 计划中列出 | 已实现：`src/srm_open/web/`（原生 HTML/CSS/JS，无构建链）+ `srm serve` 启动；正向/逆向表单、SVG 药型截面、推力-压强与燃面积曲线、校核清单、STL 下载 |

三维预览选 matplotlib 而非计划中的 PyVista：教学用图的诉求是"看得懂药型与退移方向"，
不值得为此让核心包依赖 VTK。真正的几何交付走 STEP/STL 导出。

### NFR-7 的缺口说明

当前没有 gettext/`.po` 目录，界面语言固定为中文（文档）与英文（代码标识符）。
这是**有意的最小实现**：项目尚无第二种自然语言的翻译需求，过早引入 i18n 框架
会增加字符串间接层而无实际收益。要接入时，改动集中在 `report/__init__.py` 的
标题常量与 CLI 帮助文本，不涉及数值核心。此处如实记为部分满足，不做"已完成"的表述。

## 3. 范围边界（与方案 §1.3 一致）

以下内容**不在**本软件范围内，报告中也不声称具备：

- 三维 CFD 内流场、喷管两相流损失、侵蚀燃烧的定量预测；
- 药柱结构有限元（`integrity/fem.py` 仅为轴对称工程估算，非认证级 FEA）；
- 点火瞬态、壳体与绝热层详细设计、外弹道与制导；
- 任何"可用于实际发动机定型"的结论。所有输出均为**教学与初步设计级估算**。
