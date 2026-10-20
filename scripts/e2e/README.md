# 手工 E2E 套件

这些脚本需要**真实模型 API**（部分还需要 Docker 或可交互终端），因此不在 CI 中运行。
CI 的回归范围是 `tests/`；这里只用于发布前或改动底层运行时后的手工验证。

## 公共前置

- 已安装开发依赖：`pip install -e ".[dev]"`
- 模型凭据可用（按脚本使用的 provider 设置，例如 `ANTHROPIC_AUTH_TOKEN` /
  `ANTHROPIC_BASE_URL` / `ANTHROPIC_MODEL`）
- 在仓库根目录执行；脚本会自己把 `src/` 加进 `sys.path`

## 套件

| 脚本 | 验证内容 | 额外依赖 |
| --- | --- | --- |
| `e2e_smoke.py` | 运行时主链路（模型、工具、MCP、权限、会话） | 真实模型 |
| `local_system_scenarios.py` | MCP / 插件 / bridge / slash commands | 真实模型 |
| `test_cli_flags.py` | CLI 参数与启动行为 | 真实模型 |
| `test_harness_features.py` | 重试、skills、并行工具、路径权限 | 真实模型 |
| `test_headless_rendering.py` | headless REPL 渲染 | 真实模型 |
| `test_real_skills_plugins.py` | 真实 skills / plugins / hooks | 真实模型、外部仓库 |
| `react_tui_e2e.py` | React TUI 启动与交互 | `uv`、可交互终端 |
| `test_react_tui_redesign.py` | React TUI 布局/欢迎页 | `uv`、可交互终端 |
| `test_tui_interactions.py` | 命令选择器/权限流/快捷键 | `uv`、可交互终端 |
| `test_docker_sandbox_e2e.py` | Docker 沙箱生命周期 | Docker daemon |

## 约定

- 新脚本必须提供 `main()` 或 pytest 入口，失败时返回非 0。
- 必须使用临时目录，不得改写仓库内的 `.openharness/`、`.ohmo/` 或用户会话。
- 需要清理的外部资源（Docker 容器、临时 worktree、测试插件）必须在 `finally` 中回收。
- 如果多个脚本开始出现相同的路径/环境/子进程封装，再抽到 `_common.py`；不要为了“统一”
  提前制造一个没人用的工具模块。
