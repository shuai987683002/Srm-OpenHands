# Srm-Open —— 开源固体火箭发动机设计软件

> 面向**科研教学**与**非商业工业研发**的开源固体火箭发动机（SRM）装药与内弹道设计平台。
> 目标：共享科学技术、促进行业对固体火箭发动机的研究。

## 定位（一句话）
从「指标 ↔ 药型 ↔ 燃面 ↔ 内弹道 ↔ 性能」双向贯通、并内置校核链路的固体发动机设计工具。

- **正向设计**：初始总体指标（推力/工作时间/装药量/总冲/比冲…）→ 三维药型方案 → 性能。
- **逆向设计**：由现有药型几何 / 燃面数据 / 推力-总冲-工作时间-比冲要求 → 反推关键设计参数。
- **核心分析**：燃面演化、零维内弹道、性能核心分析、柱药主体计算。

## 适用边界（摘要）
- 单室、单级、固体推进剂；常见药型族（圆柱/管形/星形/车轮/开槽/分段柱药）。
- 零维平衡压强内弹道 + 工程级喷管/结构简化校核。
- **不覆盖**：液体/混合/电推进；高保真 CFD；详细有限元；点火/工艺/试验/安全性。

## 开源使用边界（摘要）
- 仅供**科研、教学与非商业研发**；禁止武器化/批产/军贸用途。
- 使用者须自行遵守所在国家/地区法律、出口管制与知识产权规定。
- 输出结果不保证工程正确性，工程应用须经独立验证与专业评审。
- 建议许可证：Apache-2.0（最终以立项确认为准）。

## 快速开始

```bash
pip install -e ".[all]"                     # 安装（含绘图/CAD/外部热化学/优化器）
srm templates                               # 列出内置药型模板
srm propellants                             # 列出推进剂条目
srm thermo --propellant ap_htpb_80          # 评估热化学（c*, γ, T_c, 分子量）

# 正向设计：总体指标 → 药型方案 → 性能（自动选型 + 合成 + 精修）
srm design --thrust 8000 --burn-time 4 --max-pressure 9e6 \
    --outer-diameter 0.16 --length-ratio 5 --thrust-profile neutral \
    --report out/ --json out/r.json

# 给定模板 → 性能（固定几何的裕度评估）
srm forward --template star_6_point --propellant ap_htpb_80 \
    --thrust 8000 --burn-time 4 --max-pressure 9e6 --report out/ --json out/r.json

# 逆向设计：弹道要求 → 反推药型参数
# 既可从模板出发（用模板的族与外径作为设计空间），也可直接给族与外径
srm inverse --template star_6_point --thrust 6000 --burn-time 4 --max-pressure 9e6
srm inverse --family star --outer-diameter 0.16 --length 0.6 \
    --thrust 6000 --burn-time 4 --max-pressure 9e6

# 逆向路径 A：二维端口轮廓 → 参数化药型参数
srm fit-outline --points outline.csv --family slotted --outer-diameter 0.16 --length 0.6

# 可辨识性/病态性：条件数 + VIF + frozen/inactive
srm sensitivity --family star --outer-diameter 0.16

# 参数扫描 / 蒙特卡洛不确定性（固定种子、可复现）
srm sweep --template tube_simple --thrust 6000 --burn-time 4 \
    --parameter max_chamber_pressure --values 6e6,7e6,8e6
srm monte-carlo --template tube_simple --thrust 6000 --burn-time 4 \
    --vary density:15 --vary burn_rate.a:2e-7 --samples 200 --seed 42

# 示例工程（固定几何 / 指标驱动两类）
PYTHONPATH=src python examples/run_case.py examples/projects/06_synthesis_star
```

> 若安装后提示找不到 `srm`，说明用户级脚本目录不在 `PATH`（常见于容器）。
> 执行 `export PATH="$HOME/.local/bin:$PATH"`，或直接用等效方式
> `PYTHONPATH=src python -m srm_open.api.cli ...`。

### Windows 免安装版

不想装 Python 的话，从
[Releases](https://github.com/shuai987683002/Srm-OpenHands/releases) 下载
`srm.exe`（约 80 MB，无需运行时）：

```powershell
.\srm.exe                                # 双击等价：启动 Web UI 并自动打开浏览器
.\srm.exe design --thrust 4000 --burn-time 2.5
.\srm.exe serve --port 8080              # 指定端口；被占用时自动顺延
```

**双击 `srm.exe` 即可**：无参数启动会拉起本地 Web UI 并打开默认浏览器，不需要
命令行。若 8000 端口被占用，会自动向后寻找可用端口并在窗口里打印实际地址。

程序崩溃时窗口不会立刻消失：traceback 会打印出来并写入 `srm-error.log`
（优先 exe 所在目录，只读时回退到 `%LOCALAPPDATA%\srm-open\`），按回车才退出。

该二进制已内置数据库与前端资源，可脱离源码树运行。每个 Release 附
`SHA256SUMS`，校验：`certutil -hashfile srm.exe SHA256`。

exe 不打进 git 仓库（80 MB 的二进制会永久留在历史里），由
`.github/workflows/release.yml` 在打 tag 时构建并发布；自行构建见
[docs/packaging.md](docs/packaging.md)。

也可以直接在浏览器里设计（正向 / 逆向）：

```bash
pip install -e ".[api]"     # FastAPI + uvicorn
srm serve                   # 打开 http://127.0.0.1:8000/ui/
```

界面无构建链、不联网：填指标 → 得到药型截面、推力/压强与燃面积曲线、校核清单，
并可下载 STL。全部数值由同一后端引擎产生，与 CLI/API 结果一致。

也可用 Python API：

```python
from srm_open.api import design_forward

# 指标 → 自动选型 + 合成药柱 → 性能
r = design_forward({"thrust": 8000, "burn_time": 4.0, "max_chamber_pressure": 9e6,
                    "outer_diameter": 0.16, "length_ratio": 5,
                    "thrust_profile": "neutral"}, "ap_htpb_80")
print(r.performance.total_impulse, r.ballistics.peak_pressure, r.grain.segments[0].params)
```

## 功能模块

| 模块 | 职责 |
|---|---|
| 指标解析与校验 | 指标补全、冗余关系自洽性、欠定/过定识别 |
| 推进剂模型 | 燃速定律（Saint-Robert 等）、数据字典 |
| 热化学 | 自带 Gibbs 平衡求解器（NASA-7，74 组分）+ Cantera/RocketCEA 适配 |
| 喷管模型 | 等熵面积比、推力系数、质量流量、c* |
| 药型几何 | 11 类参数化药型族、2D 截面、3D 导出 |
| 燃面演化 | 几何解析通道 + Level-Set 网格通道 + 双通道互校 |
| 内弹道 | 零维平衡压强闭式解 + 瞬态充填 ODE |
| 性能分析 | 推力、总冲、比冲、推力系数、装载分数、余药 |
| 柱药主体 | 主体燃面/肉厚/余药/装载、长径比、工程强度校核 |
| 逆向设计 | 几何逆向（轮廓/点云拟合）、燃面逆向、弹道逆向 + 优化器 + 可辨识性（条件数/VIF） |
| 批量研究 | 参数扫描、蒙特卡洛不确定性（固定种子可复现） |
| 校核链路 | 弹道/性能/装药/结构/数值一致性检查 |
| 报告/API | Markdown/HTML/PDF 报告 + 曲线、脚本 API、CLI、可选 REST（FastAPI） |
| 3D 预览 | 药柱与燃面回归的三维示意图（matplotlib，教学用） |
| Web 前端 | 浏览器内正向/逆向设计、SVG 药型截面与曲线、STL 下载（`srm serve`） |

## 目录结构

```
Srm-OpenHands/
├── src/srm_open/        # 计算内核与 API（models, thermo, geometry, burnback,
│                        #   ballistics, performance, integrity, inverse,
│                        #   orchestration, io, report, api, web）
│   └── data/            # data/ 的镜像副本，供 wheel 安装后定位数据库
├── data/                # 模板库、推进剂库、热化学库（源副本，脚本在此生成）
├── configs/             # 分层配置
├── tests/               # unit / integration / verification
├── examples/            # 示例工程 + 教学 notebook
├── scripts/             # 数据库构建、敏感性扫描
├── data/schema/         # 数据集 JSON Schema（由脚本生成，防漂移）
├── docs/                # 方案、理论、用户指南、校核报告
└── data_registry/       # 数据来源登记 + SBOM
```

## 校核与验证

```bash
PYTHONPATH=src python -m pytest tests -q                                  # 全部测试
PYTHONPATH=src python -m pytest tests/verification -m verification -q -s  # 打印校核数字
PYTHONPATH=src python -m pytest tests/performance -m performance -q -s    # 性能预算（NFR-3）
PYTHONPATH=src python -m pytest --cov=srm_open                            # 覆盖率（NFR-4）
```

实测结论（详见 [docs/verification/verification_report.md](docs/verification/verification_report.md)）：
热化学对比 Cantera 偏差 < 0.1%；喷管等熵恒等式精确；管形几何闭式解 < 0.005%；
燃面双通道互校最大 < 8%；FEM 对比 Lamé < 0.4%（无体积锁死）；平衡压强闭式解一致、
质量守恒残差 ~1e-15。

## 独立可执行文件（免 Python 环境）

无需安装 Python 与依赖，直接运行单个可执行文件（内置 `data/`、`configs/`）：

```bash
# Windows（在 Linux 上用 Wine 容器交叉构建，产物为真正的 PE32+ 可执行文件）
./packaging/build_windows_exe.sh          # → release/srm.exe
release/srm.exe design --thrust 5000 --burn-time 3 --outer-diameter 0.15 --family star

# Linux（本机 PyInstaller）
.venv/bin/pyinstaller packaging/srm_open.spec --noconfirm \
    --distpath dist --workpath build/pyi  # → dist/srm
dist/srm inverse --family star --outer-diameter 0.15 --thrust 5000 --burn-time 3
```

两个产物均通过内置 8 项校核（推力、工作时间、比冲、峰值压强、喉径、装药量等），
且输出与源码运行逐字节一致。可执行文件为构建产物，不纳入版本库。

## 文档
- 完整实现方案：[docs/开源固体火箭发动机设计软件实现方案.md](docs/开源固体火箭发动机设计软件实现方案.md)
- 用户指南：[docs/user_guide/user_guide.md](docs/user_guide/user_guide.md)
- 理论模型：[docs/theory/](docs/theory/)（内弹道 / 燃面 / 假设）
- 校核报告：[docs/verification/verification_report.md](docs/verification/verification_report.md)
- 需求合规矩阵（FR/NFR 逐条证据）：[docs/requirements_compliance.md](docs/requirements_compliance.md)
- 打包与分发：[docs/packaging.md](docs/packaging.md)
- 离线领域 AI 智能体（硬件选型 + 分阶段部署 + 训练/评测方案）：
  [docs/llm_agent/离线SRM领域AI智能体部署方案.md](docs/llm_agent/离线SRM领域AI智能体部署方案.md)
  （数值由 `scripts/llm_sizing.py` 生成，`tests/unit/test_llm_sizing.py` 防漂移）
- 贡献规范：[CONTRIBUTING.md](CONTRIBUTING.md)

> 免责声明：本仓库及文档由 AI 辅助整理（OpenHands），仅用于学习与交流。
> 内置推进剂/材料数值均为占位示例，非实测或认证数据。
