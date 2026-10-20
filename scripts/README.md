# scripts/ 目录约定

这里只放**运维/一次性工具**，不放业务代码，也不放单元测试。

| 路径 | 类型 | 说明 |
| --- | --- | --- |
| `install.sh` / `install_dev.sh` / `install.ps1` | 运维 | 安装/开发环境引导 |
| `sync_nanobot_channels.sh` | 运维 | 从上游同步 nanobot 渠道实现 |
| `migrate_memory.py` | 迁移 | 调用 `openharness.memory.migrate`，不要在这里复制迁移逻辑 |
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
