# qaapp —— 问答产品服务

基于 **OpenHarness** 与 **ohmo** 构建的问答产品后端。面向客户 APP / Web 前端，
提供「配置化多 Agent 接入 + 文件可配置的高危操作门禁 + 完整对话 API + 带对话 ID
的历史（含中间执行过程）」。

本文档面向**启动、配置与部署落地**，逐项讲清需要留意的坑。

---


> **文档导航**
> - 部署、配置、运维与排障：[`docs/qaapp/DEPLOYMENT.zh-CN.md`](../docs/qaapp/DEPLOYMENT.zh-CN.md)
> - HTTP / SSE / WebSocket API：[`docs/qaapp/API.zh-CN.md`](../docs/qaapp/API.zh-CN.md)
> - 架构、分层与扩展契约：[`docs/qaapp/ARCHITECTURE.zh-CN.md`](../docs/qaapp/ARCHITECTURE.zh-CN.md)
> - 业务库口径与实测结构：[`docs/qaapp/DATABASE.zh-CN.md`](../docs/qaapp/DATABASE.zh-CN.md)

---

## 快速开始（5 步跑起来）

> 依赖与安装细节见[部署文档](../docs/qaapp/DEPLOYMENT.zh-CN.md)；先确认 `python -c "import qaapp"` 能通过。

```bash
cd <repo-root>   # 进入仓库根

# ① 验证依赖可用
python -c "import qaapp, openharness, ohmo; print('依赖 OK')"

# ② 写入配置模板到 config/backend（随 git 管理）
python -m qaapp init
#    若已生成 abcqa 脚本，也可写：abcqa init
cp config/backend/.env_dev config/backend/.env   # 开发；生产用 .env_prod
cp config/frontend/.env_dev config/frontend/.env
#    已存在时跳过；想覆盖用 --force

# ③ 配置模型与 API Key（关键！不配无法调用模型）
#    编辑 config/backend/.env 的 OPENHARNESS_*（模型/密钥），详见[部署文档](../docs/qaapp/DEPLOYMENT.zh-CN.md)

# ④ 校验配置
python -m qaapp config              # 配置文件的实际路径与校验结果
python -m qaapp agents list         # 列出注册的 Agent
python -m qaapp dangerous validate  # 校验高危操作策略

# ⑤ 启动服务
python -m qaapp webapp run          # 前台运行（Ctrl+C 退出），日志输出到 stdout
# 或后台守护：
python -m qaapp webapp start        # 后台启动，日志写入 <workspace>/logs/webapp.log
python -m qaapp webapp status       # 查看运行状态
python -m qaapp webapp stop         # 停止
```

验证：

```bash
curl http://127.0.0.1:8787/healthz
# → {"status":"ok","active_sessions":0}

curl http://127.0.0.1:8787/api/v1/bootstrap
# 配了 api_tokens 时 → {"auth_required":true,"browser_session":true,...}
# 前端据此自动建立 HttpOnly 会话，用户不需要在页面填 token（见[部署文档](../docs/qaapp/DEPLOYMENT.zh-CN.md)）
```

> 服务**默认监听 `0.0.0.0:8787`（所有网卡）**，局域网内其他设备可用
> `http://<你的本机IP>:8787` 访问。查本机 IP：macOS `ipconfig getifaddr en0`、
> Linux `hostname -I`。对外监听时请务必配置 `api_tokens`（见[部署文档](../docs/qaapp/DEPLOYMENT.zh-CN.md)）。

---

## 命令行速查

```bash
abcqa init [--workspace DIR] [--force]     # 写入 config/backend 配置模板
abcqa config [--workspace DIR]             # 配置文件实际路径与校验结果
abcqa cleanup [--older-than-days N] [--keep-latest N] [--apply] [--json]
                                           # 清理闲置会话目录/快照/轨迹（默认 dry-run）
abcqa agents list                          # 列出注册的 Agent
abcqa agents validate                      # 校验 agents.json（exit 1 表示有错）
abcqa dangerous validate                   # 校验 dangerous-actions.json
abcqa dangerous show                       # 打印当前高危策略
abcqa webapp run [--workspace DIR] [--cwd DIR] [--host H] [--port P]
abcqa webapp start|stop|status [--workspace DIR]
```

---

## 项目简介

| 能力 | 说明 |
| --- | --- |
| 多 Agent 接入 | 通过 `agents.json` 声明 Agent：默认由 **harness 通用助手总控**（开放问答直接回答，需要专业能力时自动调用 `external_agent` 工具），也支持关键词路由 / 显式指定 |
| 动态能力清单 | 每轮由 `agents.json` 生成「可调度能力 + 决策顺序」注入系统提示：新增 Agent 不用改通用提示词 |
| 选项式澄清 | 分析报告/上传文档分析总结/指标口径查询开工前对齐关键参数；1 个点缺失给 2–4 个选项，2 个以上用「任务简报」卡 +「其他」，确认后再继续；不新增计划模式 |
| Agent 工具边界 | `tools` / `disallowed_tools` 在运行时真正生效（支持 `*` 与通配），不再只是声明 |
| 浏览器免手工 token | 前端启动时 `GET /api/v1/bootstrap`，服务端读取 `webapp.json:api_tokens` 并签发 HttpOnly 会话 Cookie；token 不进入页面/JS |
| 高危操作门禁 | `dangerous-actions.json` 对删除/写文件/执行命令等做 allow / confirm / deny 三级控制 |
| 对话 API | REST + SSE + WebSocket，保留 Agent 调用与操作系统文件能力 |
| 对话历史 | 每个对话有独立 `conversation_id`，历史（含中间工具执行过程）可查询 |
| Word 报告 | `write_report_docx` 固定按国网公文格式输出（A4、35/28mm 页边距、29 磅行距、宋体二号标题、仿宋三号正文） |
| 附件上传解析 | 对话框上传 Word / Excel / TXT（单条消息最多 2 个），服务端主动解析成文本并注入模型上下文 |
| 上传文档分析 | 按需加载 `document-analysis` 技能：盘点 Excel/Word/TXT → 抽取核验 → 对比/趋势/异常/原因分析 → 结构化总结；不把 1500 字预览当全文 |
| 会话临时目录 | 每个 `conversation_id` 一个独立目录 `~/.ohmo/conversations/<id>/`（uploads / extracted / scripts / tmp / outputs） |
| 会话脚本执行 | 内置 `run_script` 工具：脚本一律写入本会话 `scripts/`，用共享 conda 环境执行，产物落 `outputs/` |

**核心设计原则：能力保留、风险受控** —— 工具不会因为“危险”被删除，而是在运行时
被门禁拦一道，规则全部来自可编辑的 JSON 文件。

---

## 测试与校验

```bash
# 后端：配置 / 路由 / 能力清单 / 工具白名单 / 门禁 / 轨迹 / 附件 / 会话目录 / run_script
pytest tests/test_qaapp -q
ruff check qaapp tests/test_qaapp

# 前端：类型与构建 + 自检（Markdown 渲染、附件/文件卡、选项式澄清）
cd frontend/webapp && npm run build
npm run check:render
npm run check:attachments
npm run check:turn-files
npm run check:questions
```

> 本机若设置了 `NO_PROXY` 且包含 `[::1]` 这种带方括号的条目，`httpx2.Client()` 会因
> 解析端口失败而报 `Invalid port`——跑涉及模型客户端的用例前先把它去掉：
> `NO_PROXY="localhost,127.0.0.1,::1" pytest tests/test_qaapp -q`。

