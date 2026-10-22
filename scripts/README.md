# scripts/ 目录约定

这里只放**运维/一次性工具**，不放业务代码，也不放单元测试。

| 路径 | 类型 | 说明 |
| --- | --- | --- |
| `install.sh` / `install_dev.sh` / `install.ps1` | 运维 | 安装/开发环境引导 |
| `dev_services.sh` | 运维 | 前后端开发服务后台启动/停止/重启/状态/日志（`start` / `stop` / `restart` / `status` / `logs`） |
| `sync_nanobot_channels.sh` | 运维 | 从上游同步 nanobot 渠道实现 |
| `migrate_memory.py` | 迁移 | 调用 `openharness.memory.migrate`，不要在这里复制迁移逻辑 |
| `migrate_settings_to_env.py` | 迁移 | 把旧 `settings.json` 的模型/品牌/运行参数迁移到 `config/backend/.env`（幂等、自动备份） |
| `e2e/` | 手工 E2E | 需要真实模型 / Docker / TUI 的手工验证脚本，**不在 CI 中运行** |

## 新增脚本的规则

1. **自动化测试放 `tests/`**，不要新增 `scripts/test_*.py`。CI 只跑 `tests/`，放在
   `scripts/` 的测试不会被回归覆盖，时间一长就会腐烂。
2. 只在“必须人工执行、依赖真实外部服务”时才新增 `scripts/e2e/` 脚本，并在文件头写清：
   前置条件、环境变量、是否会改写用户数据、如何清理。
3. 一次性调试脚本（日期转换、临时数据生成等）不要提交到仓库根目录；需要保留时放进
   `scripts/e2e/` 或 `examples/`，并说明用途。
4. 多个脚本共用的路径/环境/子进程逻辑，优先抽到 `scripts/e2e/_common.py`，不要再复制
   一遍颜色常量、`PROJECT_ROOT` 和环境变量拼装。

## 手工 E2E 套件
| 脚本 | 验证内容 | 额外依赖 |
| --- | --- | --- |
| `e2e_smoke.py` | 运行时主链路（模型、工具、MCP、权限、会话） | 真实模型 |
| `react_tui_e2e.py` | React TUI 启动与交互 | `uv`、可交互终端 |
| `test_docker_sandbox_e2e.py` | Docker 沙箱生命周期 | Docker daemon |

被删除的旧脚本（`local_system_scenarios.py`、`test_cli_flags.py`、`test_harness_features.py`、
`test_headless_rendering.py`、`test_real_skills_plugins.py`、`test_react_tui_redesign.py`、
`test_tui_interactions.py`）对应场景已由 `tests/` 与 `tests/e2e/` 覆盖。

## 约定

- 新脚本必须提供 `main()` 或 pytest 入口，失败时返回非 0。
- 必须使用临时目录，不得改写仓库内的 `.openharness/`、`.ohmo/` 或用户会话。
- 需要清理的外部资源（Docker 容器、临时 worktree、测试插件）必须在 `finally` 中回收。
- 如果多个脚本开始出现相同的路径/环境/子进程封装，再抽到 `_common.py`；不要为了“统一”
  提前制造一个没人用的工具模块。
