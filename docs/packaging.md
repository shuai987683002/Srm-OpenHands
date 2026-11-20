# 打包与分发

目标：产出**免 Python 环境**的单文件可执行程序，内置 `data/` 与 `configs/`，
启动后行为与源码运行一致。

## 产物

| 平台 | 产物 | 大小 | 构建方式 |
| --- | --- | --- | --- |
| Linux x86_64 | `dist/srm` | ~166 MB | 本机 PyInstaller |
| Windows x86_64 | `release/srm.exe` | ~83 MB | Wine 容器内 Windows CPython + PyInstaller |

两者均为真正的本机可执行格式（ELF / PE32+），只依赖系统基础库（glibc 等）。

## 构建

### Linux

```bash
.venv/bin/pyinstaller packaging/srm_open.spec --noconfirm \
    --distpath dist --workpath build/pyi
```

### Windows（在 Linux 上交叉构建）

```bash
./packaging/build_windows_exe.sh
```

脚本做三件事：在宿主机下载 `win_amd64` wheel（宿主机 TLS 正常，容器内 pip 无法完成
握手）；构建镜像并在 Wine 中安装依赖、运行 PyInstaller；把 `srm.exe` 取到 `release/`。

## 实现要点与踩坑

- **入口脚本**：`packaging/srm_entry.py` 用绝对导入调用 `srm_open.api.cli:main`。
  直接以 `srm_open/api/cli.py` 为入口会因相对导入失败
  （`attempted relative import with no known parent package`）。
- **双击启动**：exe 保留控制台（`console=True`），因为主用法仍是命令行。为让
  双击可用，`main()` 在**无参数**时直接启动 Web UI 并打开浏览器，而不是抛
  `error: the following arguments are required: command` 后退出 2——在资源管理器里
  那看起来就是窗口一闪而过。`serve` 遇到端口占用会自动向后试 20 个端口，避免
  第二个实例静默失败。
- **崩溃可见性**：`main()` 在 frozen 模式下捕获未处理异常，打印 traceback、写入
  `srm-error.log`（exe 目录 → 当前目录 → `%LOCALAPPDATA%\srm-open` → 临时目录，
  逐级回退以兼容 Program Files 只读场景），并等待回车再退出。
- **数据路径**：`config.repo_root()` 优先识别 `sys.frozen` / `sys._MEIPASS`，
  再回退到 `pyproject.toml` 搜索与当前目录，保证打包后仍能找到 `data/`、`configs/`。
- **体积**：spec 中排除 tkinter/PyQt 与可选重型依赖；去掉 `skimage` 后由 395 MB 降至 166 MB。
- **Windows Python 安装**：GUI 安装器在无桌面的 Wine 中不可靠，改用
  embeddable zip（解压即用），并写 `python311._pth` 打开 `site`。
- **离线依赖**：容器内 `pip` 直连 PyPI 会报 `OSError(22, 'Invalid parameter.')`，
  故改为 `--no-index --find-links Z:\wheelhouse`。注意 Wine 把 `/` 映射为 `Z:`，
  `C:\wheelhouse` 不存在。
- **`pefile` / `pywin32-ctypes`**：`pip download --platform win_amd64` 会漏掉这两个
  纯 Python wheel，需用 `--no-deps` 单独补下。
- **`KeyError: 'PATH'`**：PyInstaller 的 shapely 钩子读取 `os.environ['PATH']`，
  而 Wine 的 Windows 环境块中没有该变量。通过 `.bat` 里 `set PATH=...` 后再启动
  PyInstaller 解决（在 shell 里前置 `PATH=` 会被 Wine 覆盖）。

## 验证清单

```bash
dist/srm --help                      # 子命令齐全
dist/srm templates                   # 内置模板可读
dist/srm propellants                 # 内置推进剂可读
dist/srm design --thrust 5000 --burn-time 3 --outer-diameter 0.15 --family star
dist/srm inverse --family star --outer-diameter 0.16 --thrust 6000 --burn-time 4
```

期望：`verification : 8/8 passed`（给定压强约束时为 9/9）。
打包产物与源码运行输出逐字节一致，可据此判定打包是否忠实。

## 注意

可执行文件是构建产物，**不纳入版本库**（见 `.gitignore`：`dist/`、
`packaging/wheelhouse/`、`build/pyi/`、`release/*.exe`、`release/SHA256SUMS`）。
发布通过 GitHub Releases 完成：`.github/workflows/release.yml` 在打 tag
（如 `v0.1.0`）时构建 exe、wheel 与 sdist，上传为 release 资产。
分发时请连同 `LICENSE` 与免责声明一并提供。

> 为什么不把 exe 提交到仓库：它是 80 MB 的二进制，一旦进入 git 历史，
> 此后每次 clone 都要下载它，且无法真正删除。Releases 提供同样的下载便利，
> 却不污染历史。`pip install` 仍是 Linux/macOS 用户的首选。

## 发布流程

```bash
# 1. 确认版本号一致（工作流会校验）
grep -n '^version' pyproject.toml          # → 0.1.0
grep -n '__version__' src/srm_open/__init__.py

# 2. 打 tag 并推送，触发构建与发布
git tag v0.1.0
git push origin v0.1.0
```

工作流分四个 job：`version-check`（tag 与版本号必须一致）→
`windows-exe`（Wine 构建 exe，实测 `design` 与 `serve`）→
`python-dist`（wheel + sdist，并在干净 venv 中安装验证）→
`publish`（汇总为 Release 资产）。任一步失败都不会发布。
