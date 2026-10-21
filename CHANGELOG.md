# Changelog

All notable changes to OpenHarness should be recorded in this file.

The format is based on Keep a Changelog, and this project currently tracks changes in a lightweight, repository-oriented way.

## [Unreleased]

### Added

- **业务库直连（qaapp）**：新增 `db_query` 工具与 `qaapp/data_client.py` / `data_catalog.py` / `data_query.py` / `data_quality.py`，按 `docs/database_query/` 的 A1/A2/B1/B2/T/四率逻辑查询客户 MySQL；以 `ai_data_struct` 实际可用性优先、层级表/`_ssx` 回退，处理月末日期、周期/机构/维度回退、无数据诊断、恒定序列/陈旧/异常/空值/单位等质量问题。新增 `docs/database_query/IMPLEMENTATION.zh-CN.md` 记录实测库结构与文档差异。
- **数据源配置集中管理**：新增唯一 `datasources.json`（`${DB_HOST}` 等占位符 + 环境变量/就近 `.env`）与唯一加载器 `qaapp/datasources.py`；MySQL/Redis 连接信息不再散落在 `webapp.json` 或代码里。新增 `abcqa datasource show/check`，MySQL 只读/连接池/超时/行数上限，Redis 只读 db0 + 前缀白名单。
- **多智能体灵活编排**：新增 `consult_agents` 工具，可一次并行/顺序调用 1–4 个外部智能体，支持单次超时、502/会话失败重试、部分失败、原文落 `outputs/agents/` 与截断预览；能力清单改为“可组合、不要只选一个”，并按 `AgentEntry.role` 动态生成取数/分析/知识组合矩阵。
- **业务分析场景与 Word 报告**：新增 10 类业务场景模板（`qaapp/analysis_scenarios.py`）、`business-analysis` / `database-query` / `multi-agent-orchestration` 技能（统一放仓库根 `skills/`，随 git 维护，`.openharness/settings.json` 的 `project_skill_dirs` 已包含 `skills`），以及 `write_report_docx`（封面/目录域/标题层级/表格/图片/页眉页脚页码/来源与数据质量附录）。通用助手仍是默认 Agent、工具仍为 `*`；四阶段分析只在业务分析任务中按需加载，不改变开放问答/写作/代码等原有能力。
- `pyproject.toml` 新增 `pymysql` / `redis` / `matplotlib` 依赖。

- qaapp 新增**浏览器免手工 token**：前端启动时调用 `GET /api/v1/bootstrap`，服务端读取 `webapp.json` 的 `api_tokens` 并签发 HttpOnly 会话 Cookie（值是进程内随机串，不是 token 本身），后续请求凭 Cookie 鉴权。页面移除 Bearer Token 输入框，token 不再进入 localStorage / JS；`webapp.json:browser_auth` 可关闭并回退到严格 Bearer（`VITE_API_TOKEN` 或反向代理注入）。API 客户端仍支持 `Authorization: Bearer`。
- qaapp 新增**动态能力清单**（`qaapp/capabilities.py`）：每轮根据 `agents.json` 生成「可调度能力 + 决策顺序」注入 harness 系统提示，新增 Agent 不需要再改通用助手的 `system_prompt`；清单只列本轮实际可用的工具，`external_agent` / `run_script` 被裁掉时对应能力不会出现。通用助手提示词同步改为「直接回答 / 专业智能体 / `run_script` 执行 / 查证 / 澄清」的自主决策原则。
- qaapp 的 `agents.json` **`tools` / `disallowed_tools` 现在真正生效**（`qaapp/tool_policy.py`）：支持 `*` 与 fnmatch 通配，`disallowed_tools` 优先；被裁掉的工具不会出现在模型工具表。`QueryEngine` 新增 `set_tool_registry()` 供宿主按 Agent 切换能力边界。
- 新增架构与扩展规范文档 [`docs/QAAPP_ARCHITECTURE.zh-CN.md`](docs/QAAPP_ARCHITECTURE.zh-CN.md)：分层架构、请求生命周期、能力矩阵、外部智能体/harness/工具/传输/路由/配置/API 的扩展契约、自主编排决策链、安全边界与 DoD 清单。
- 外部 Agent 工具描述改为**由配置驱动**：移除写死的 `power-grid-analysis` / `smart-data-query` 判例，路由规则只按 `description` 的领域关键词匹配；新增智能体无需改插件代码。
- qaapp 新增统一的**工作区清理模块** `qaapp/maintenance.py` 与 `abcqa cleanup`：一次扫描会话目录、对应的 `session-*.json` / `latest-<hash>.json` / `traces/*.jsonl` 以及会话目录已删除的孤儿 trace；**默认 dry-run**，`--apply` 才删除，`--older-than-days` / `--keep-latest` 可覆盖配置默认值。删除前做路径白名单与符号链接校验；只处理 qaapp 的 12 位会话 ID，不误删 ohmo 个人助手会话。`conversation.max_active_bundles` / `idle_timeout_seconds` 从“预留字段”变为实际保留策略。
- Hooks now support a `priority` field (default `0`). Within an event, hooks run highest-priority first, and hooks sharing a priority keep their registration order. This lets users order, for example, a security-check hook ahead of a logging hook regardless of where each is declared in settings or contributed by plugins.
- `edit_file` and `write_file` in the React TUI now preview a unified diff before applying file changes, let users approve once or for the rest of the session, and skip the extra prompt automatically in `full_auto` mode.
- qaapp 支持**对话框上传附件**：`POST /api/v1/conversations/{id}/attachments`（multipart，单次最多 2 个），服务端立即解析 `.docx` / `.xlsx` / `.xls` / `.txt` / `.md` / `.csv` / `.json` / `.log` / `.yaml` / `.xml` 并把内容注入模型上下文；`POST /messages` 通过 `attachment_ids` 引用附件，新增 SSE 事件 `attachments_ready`（写入轨迹，刷新页面后仍能还原附件芯片）。依赖新增 `python-docx` / `openpyxl` / `xlrd` / `pandas` / `python-multipart`。
- qaapp 支持**每会话独立临时目录**：`~/.ohmo/conversations/<conversation_id>/{uploads,extracted,scripts,tmp,outputs}`（`conversation_workspace.root` 可覆盖），配套 `GET /workspace`（列出文件）与 `GET /files/{相对路径}`（下载，越界 404）；目录内 `write_file` / `edit_file` / `notebook_edit` 免审批（敏感路径与硬拒绝仍优先，可用 `conversation_workspace.auto_allow_write_tools` 关闭）。
- qaapp 新增内置工具 **`run_script`**：把模型生成的脚本按会话写入 `scripts/`，用共享 conda 环境（自带 pandas/openpyxl/python-docx）的 `python` 或 `node` 执行，工作目录为会话目录根，产物约定写到 `outputs/`；默认走危险操作门禁审批，可用 `{"tool": "run_script", "action": "allow"}` 放开。参考前端同步补齐上传按钮 / 拖拽 / 附件芯片 / 会话文件抽屉与 `npm run check:attachments` 自检。
- 所有带 `{conversation_id}` 的 HTTP/WebSocket 路由新增 ID 格式白名单校验（`^[0-9a-f]{12}$`），修掉「用 `../` 构造会话 ID 造成快照 / 会话目录路径穿越」的隐患。
- qaapp 配置改为**可见路径优先**：按 `--config-dir` / `QAAPP_CONFIG_DIR` → **`<cwd>/config/<name>`（项目内、可提交 git）** → `<ohmo workspace>/<name>` 解析三份配置，并在启动日志与 `abcqa config`（新增 `source` 字段）中显示实际生效文件。修掉「配置放在隐藏目录 `~/.ohmo/`、服务器 `git pull` 更新不到、导致 external Agent 报 `Unknown external agent ... (none configured)`」的部署缺陷；仓库新增可见的 `config/agents.json`，`abcqa init --config-dir ./config` 可生成示例。另新增启动自检：`kind=external` 只写 `external_ref` 且对应配置缺失时直接打 ERROR 日志。
- qaapp 统一外部 Agent 的会话 ID 映射为「一个 qaapp 会话 = 一个对端会话」：`external_agent` 无论是由外部 Agent 直接派发，还是被 harness 总控当作工具调用，都会固定注入 `conversation_id`（配置为固定值时用该值）。修掉插件在调用方未传 ID 时**每次生成随机 uuid、导致对端每次新建会话**的问题——这会把对端会话存储的抖动放大成「会话创建失败」。
- qaapp 的 Agent 配置**收敛到唯一维护文件 `agents.json`**：`kind=external` 可用新增的 `external` 字段内联 `type` / `conversation_id` / `timeout_seconds` / `response` / `config`（endpoint、method、headers、request_body），qaapp 在会话启动时把这些配置注入 `external_agent` 工具（内存注入，不生成第二份文件）；同名以 `agents.json` 为准，插件原有的 `external-agents.json` 文件发现继续作为兜底。`external.conversation_id` 支持配固定值以复用对端已有会话（对端报「会话创建失败」时可用）。`qaapp/examples/external-agents.json`（过时占位副本）删除，`plugins/external-agents/external-agents.json` 清空为占位并注明已迁移。

### Changed

- **web_search 默认后端由 DuckDuckGo 改为 Bing**：DDG 的 HTML 端点在受限网络中不可达且域名常被 DNS 污染（污染答案形如 `31.13.94.49` / `2001::6ca0:accc`），已移除其解析与 `/l/` 跳转归一化逻辑。新增 `web.search_url` 设置与 `OPENHARNESS_WEB_SEARCH_URL` 覆盖（默认 `https://cn.bing.com/search`），解析 Bing 的 `b_algo` 结果块并解开 `/ck/a` 跳转链接。实测同一查询 1.9s 返回 5 条结果。
- qaapp 参考前端移除侧边栏「Bearer Token（可选）」输入框：改为启动时自动 bootstrap 浏览器会话，收到 401 时自动重建一次并重试；新增可选的 `VITE_API_TOKEN` 构建变量作为「关闭 browser_auth」时的兜底。
- qaapp 的 LLM 分类路由标注为**预留能力**：`resolve_agent` 仍接受 `llm_classifier`，但服务端未注入；文档不再声称 `routing.mode=llm` 会生效，生产默认推荐 harness 总控 + 动态能力清单。
- 仓库根目录重新整理：修正了误提交的「English Case Converter」README，改为 OpenHarness 的实际入口/目录/验证说明；删除与项目无关的一次性脚本 `date_converter.py`。手工 E2E 脚本统一收进 `scripts/e2e/`，并新增 `scripts/README.md` / `scripts/e2e/README.md` 明确“自动化测试放 `tests/`、脚本放 `scripts/e2e/`”的约定。
- 复用而非复制：会话 ID 白名单收敛到 `qaapp.conversation_files.CONVERSATION_ID_PATTERN`；ohmo 的 `latest-<hash>.json` 路径改为公开的 `get_session_key_latest_path()`，清理逻辑不再重复哈希算法。
- 合并 project memory 与 ohmo personal memory 的**软删除清理逻辑**：`remove_memory_entry` 的扫描、禁用标记、索引摘除统一走 `openharness.memory.manager.soft_delete_memory_entry()`；`next_memory_path` / `effective_memory_signature` 也收敛到同一模块，`ohmo/memory.py` 只保留存储位置与默认类型/分类的差异，去掉了两份几乎相同的实现。
- **Breaking**: the launch commands are unified and renamed — `oh` → `abc` (coding assistant) and `ohmo` → `abcag` (personal agent). The legacy `oh`, `ohmo`, `openh`, and `openharness` console scripts are removed; `python -m openharness` / `python -m ohmo` module entry points remain available for internal process spawning.

### Fixed

- 修掉 `web_search failed: ` / `web_fetch failed: ` **错误报文为空**的问题：`httpcore` 把 `TimeoutError` 包成 `ConnectTimeout(TimeoutError())`、`httpx` 再用 `str(exc)` 转发，导致 `str(exc)` 为空串。新增 `utils/error_report.py`，从异常类型、原因链、目标 host:port、耗时、代理状态重建可读报文（`ConnectTimeout｜原始错误信息为空｜目标 …:443｜耗时 20.3s｜代理 未配置｜原因 ConnectTimeout <- TimeoutError()`），并附可执行提示；`web_fetch` 同步接入。
- 新增 `utils/netlog.py`：每次出网向 `<logs>/web.log` 追加一行（`event`、`endpoint`、`q`、`elapsed`、`status`、`type`、`message`、`args`、`cause`、`dns`、`detail`），失败为 WARNING，服务器上可直接下载定位；`dns=` 字段可辅助识别 DNS 污染。
- `web_search` 在解析不到结果时不再标记为错误，改为返回 `No search results found …`（HTTP 状态与端点一并给出）。
- Codex subscription requests now pass reasoning effort separately, enabling `gpt-5.5` with `xhigh` effort instead of treating `gpt-5.5 xhigh` as an unsupported model name.
- Telegram channel now delivers replies again under `abcag init --no-interactive` and other configs that do not write a `reply_to_message` field. `TelegramConfig` declares `reply_to_message: bool = True` so the attribute access in `TelegramChannel.send` no longer raises `AttributeError` and outbound progress/tool-hint/final messages are sent as expected. See issue #243.

## [0.1.9] - 2026-05-07

### Added

- Added a bundled `skill-creator` skill for creating, improving, and verifying OpenHarness/ohmo skills.
- User-invocable skills can now be triggered directly as slash commands, with support for skill-specific arguments and model override metadata.

### Fixed

- `oh setup` can now update the API key for an already-configured API-key provider profile instead of only changing the model.
- `oh provider edit <profile> --api-key <key>` can now replace a saved profile API key, and `oh provider add ... --api-key <key>` can store one during profile creation.

## [0.1.8] - 2026-05-06

### Added

- Built-in `nvidia` provider profile so `oh setup` offers NVIDIA NIM as a first-class OpenAI-compatible provider choice, with `NVIDIA_API_KEY` auth source, `openai/gpt-oss-120b` as the default model, and the NVIDIA NIM endpoint.
- Built-in `qwen` provider profile so `oh setup` offers Qwen (DashScope) as a first-class provider choice, with `dashscope_api_key` auth source, `qwen-plus` as the default model, and the DashScope OpenAI-compatible endpoint.
- Plugin tool discovery: plugins can now provide `BaseTool` subclasses in a `<plugin>/tools/` directory and they are auto-discovered, instantiated, and registered in the tool registry at runtime. Add `tools_dir` to `plugin.json` (defaults to `"tools"`).
- `oh --dry-run` safe preview mode for inspecting resolved runtime settings, auth state, prompt assembly, commands, skills, tools, and configured MCP servers without executing the model or tools.
- Built-in `minimax` provider profile so `oh setup` offers MiniMax as a first-class provider choice, with `MINIMAX_API_KEY` auth source, `MiniMax-M2.7` as the default model, and `MiniMax-M2.7-highspeed` in the model picker.
- Docker as an alternative sandbox backend (`sandbox.backend = "docker"`) for stronger execution isolation with configurable resource limits, network isolation, and automatic image management.
- Built-in `gemini` provider profile so `oh setup` offers Google Gemini as a first-class provider choice, with `gemini_api_key` auth source and `gemini-2.5-flash` as the default model.
- `diagnose` skill: trace agent run failures and regressions using structured evidence from run artifacts.
- OpenAI-compatible API client (`--api-format openai`) supporting any provider that implements the OpenAI `/v1/chat/completions` format, including Alibaba DashScope, DeepSeek, GitHub Models, Groq, Together AI, Ollama, and more.
- `OPENHARNESS_API_FORMAT` environment variable for selecting the API format.
- `OPENAI_API_KEY` fallback when using OpenAI-format providers.
- GitHub Actions CI workflow for Python linting, tests, and frontend TypeScript checks.
- `CONTRIBUTING.md` with local setup, validation commands, and PR expectations.
- `docs/SHOWCASE.md` with concrete OpenHarness usage patterns and demo commands.
- GitHub issue templates and a pull request template.
- React TUI assistant messages now render structured Markdown blocks, including headings, lists, code fences, blockquotes, links, and tables.
- Built-in `codex` output style for compact, low-noise transcript rendering in React TUI.

### Fixed

- Subprocess teammate spawn (`agent` tool, `task_create`) now works on Windows under Git Bash. `subprocess_backend.spawn` builds a direct-exec `argv` list and passes it through new `argv=` and `env=` kwargs on `BackgroundTaskManager.create_agent_task` / `create_shell_task`; `_start_process` then runs the executable via `asyncio.create_subprocess_exec(*argv)` with no shell in between. Previously the spawn command was a single string interpreted by `bash -lc`, which on Windows could not reliably exec a Windows-pathed Python interpreter (e.g. `C:\Users\...\python.exe`) — Git Bash's escape parser consumed the backslashes from the embedded env-prefix and, even with proper quoting, bash launched via `asyncio.create_subprocess_exec` returned `command not found` for Windows-pathed binaries that worked perfectly when invoked interactively. Bypassing the shell sidesteps the entire class of cross-platform quoting and path-translation hazard. The legacy shell-evaluated `command=` path is preserved for callers (e.g. `BashTool`) that legitimately want shell semantics. See issue #230.
- Bundled skill loader now uses `yaml.safe_load` for SKILL.md frontmatter, matching the user-skill loader. The shared parser is extracted to `openharness.skills._frontmatter` so bundled and user skills handle YAML block scalars (`>`, `|`), quoted values, and other standard YAML constructs the same way.
- Compaction now detects llama.cpp/OpenAI-compatible context overflow errors, accounts for image blocks in auto-compact token estimates, and strips image payloads from summarizer-only compaction requests.
- Large tool results are now bounded in conversation history: oversized outputs are saved under `tool_artifacts`, old MCP results become microcompactable, and context collapse trims stale tool-result payloads.
- ohmo now keeps personal memory isolated from OpenHarness project memory: `/memory` in ohmo sessions targets the ohmo workspace memory store, and ohmo runtime prompt refreshes no longer inject project memory unless explicitly requested.
- Fixed `glob` and `grep` tools hanging indefinitely when the `rg` subprocess produced enough stderr output to fill the OS pipe buffer. `stderr` is now redirected to `DEVNULL` so it is discarded rather than blocking the child process.
- Fixed `bash_tool` hanging after a timed-out command when the subprocess stdout stream stayed open. `_read_remaining_output` now applies a 2-second `asyncio.wait_for` timeout so the tool always returns promptly.
- Fixed `session_runner` background task deadlock caused by an unread `stderr=PIPE` stream. The subprocess now uses `stderr=STDOUT` so all output merges into the single readable stdout pipe.
- React TUI prompt input now treats the raw DEL byte (`0x7f`) as backward delete while preserving true forward-delete escape sequences, fixing backspace failures seen in some macOS terminal environments.
- `todo_write` tool now updates an existing unchecked item in-place when `checked=True` instead of appending a duplicate `[x]` line.

- Built-in `Explore` and `claude-code-guide` agents no longer hard-code `model="haiku"`, which caused them to fail for users on non-Anthropic providers (OpenAI, Bedrock, custom base URLs, etc.). Both agents now use `model="inherit"` so they run with whatever model the parent session is using. `build_inherited_cli_flags` is also fixed to skip the `--model` flag entirely when the value is `"inherit"`, letting the subprocess correctly inherit the parent model via the `OPENHARNESS_MODEL` environment variable instead of receiving the literal string `"inherit"` as a model name.

- React TUI spinner now stays visible throughout the entire agent turn: `assistant_complete` no longer resets `busy` state prematurely, and `tool_started` explicitly sets `busy=true` so the status bar remains active even when tool calls follow an assistant message. `line_complete` is the sole signal that ends the turn and clears the spinner.
- Skill loader now uses `yaml.safe_load` to parse SKILL.md frontmatter, correctly handling YAML block scalars (`>`, `|`), quoted values, and other standard YAML constructs instead of naive line-by-line splitting.
- `BackendHostConfig` was missing the `cwd` field, causing `AttributeError: 'BackendHostConfig' object has no attribute 'cwd'` on startup when `oh` was run after the runtime refactor that added `cwd` support to `build_runtime`.
- Shell-escape `$ARGUMENTS` substitution in command hooks to prevent shell injection from payload values containing metacharacters like `$(...)` or backticks.
- Swarm `_READ_ONLY_TOOLS` now uses actual registered tool names (snake_case) instead of PascalCase, fixing read-only auto-approval in `handle_permission_request`.
- Memory scanner now parses YAML frontmatter (`name`, `description`, `type`) instead of returning raw `---` as description.
- Memory search matches against body content in addition to metadata, with metadata weighted higher for relevance.
- Memory search tokenizer handles Han characters for multilingual queries.
- Fixed duplicate response in React TUI caused by double Enter key submission in the input handler.
- Fixed concurrent permission modals overwriting each other in TUI default mode when the LLM returns multiple tool calls in one response; `_ask_permission` now serialises callers via an `asyncio.Lock` so each modal is shown and resolved before the next one is emitted.
- Fixed React TUI Markdown tables to size columns from rendered cell text so inline formatting like code spans and bold text no longer breaks alignment.
- Fixed grep tool crashing with `ValueError` / `LimitOverrunError` when ripgrep outputs a line longer than 64 KB (e.g. minified assets or lock files). The asyncio subprocess stream limit is now 8 MB and oversized lines are skipped rather than terminating the session.
- Fixed React TUI exit leaving the shell prompt concatenated with the last TUI line. The terminal cleanup handler now writes a trailing newline (`\n`) alongside the cursor-show escape sequence so the shell prompt always starts on a fresh line.
- Reduced React TUI redraw pressure when `output_style=codex` by avoiding token-level assistant buffer flushes during streaming.

### Changed

- ohmo Feishu group routing now supports managed group creation, gateway-scoped provider/model commands, and stricter group mention handling so group conversations only wake ohmo when explicitly addressed.
- Dry-run output now reports a `ready` / `warning` / `blocked` readiness verdict, concrete `next_actions`, likely matching skills/tools for normal prompts, and richer slash-command previews for read-only vs stateful command paths.
- React TUI now groups consecutive `tool` + `tool_result` transcript rows into a single compound row: success shows the result line count inline (e.g. `→ 24L`), errors show a red icon and up to 5 lines of error detail beneath the tool row. Standalone successful tool results are suppressed to reduce transcript noise; standalone errors are still surfaced.
- README now links to contribution docs, changelog, showcase material, and provider compatibility guidance.
- README quick start now includes a one-command demo and clearer provider compatibility notes.
- README provider compatibility section updated to include OpenAI-format providers.

## [0.1.7] - 2026-04-18

### Fixed

- Install script now links `oh`, `ohmo`, and `openharness` into `~/.local/bin` instead of prepending the virtualenv `bin` directory to `PATH`, which avoids overriding Conda-managed shells while preserving global command discovery.
- React TUI prompt now supports `Shift+Enter` for inserting a newline without submitting the current prompt.
- React TUI busy-state animation is less error-prone on Windows terminals: the extra pseudo-animation line was removed, Windows now uses conservative ASCII spinner frames, and the spinner interval was slightly slowed to reduce flashing.

## [0.1.0] - 2026-04-01

### Added

- Initial public release of OpenHarness.
- Core agent loop, tool registry, permission system, hooks, skills, plugins, MCP support, and terminal UI.
