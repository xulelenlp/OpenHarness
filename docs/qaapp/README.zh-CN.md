# qaapp 文档索引

qaapp 是面向客户 APP/Web 端的问答产品服务，基于 OpenHarness 与 ohmo 构建。

| 文档 | 内容 |
| --- | --- |
| [快速开始 / CLI](../../qaapp/README.md) | 5 步启动、命令速查、测试 |
| [部署与运维](DEPLOYMENT.zh-CN.md) | 环境、配置、前端部署、安全、排障 |
| [HTTP API](API.zh-CN.md) | REST / SSE / WebSocket 契约 |
| [架构与扩展](ARCHITECTURE.zh-CN.md) | 分层、生命周期、扩展契约、Checklist |
| [业务库参考](DATABASE.zh-CN.md) | 数据源、表路由、口径、实测结构 |

## 代码结构

```text
qaapp/
├── cli.py                 # abcqa 命令
├── config/                # 配置路径 / 模型 / 加载校验
├── data/                  # MySQL/Redis、目录、查询、质量、图表
├── agents/                # Agent 注册表、路由、能力清单、分析场景
├── workspace/             # 会话目录、附件、文档解析、轮次快照、轨迹、清理
├── tools/                 # db_query / report / script / consult / policy
├── server/                # QaWebService、会话运行时、HTTP API
└── examples/              # abcqa init 使用的配置模板（随包安装）
```

> 公开契约是 CLI、HTTP API、配置文件名/解析顺序与磁盘布局；内部 Python 模块路径
> 在 0.2 重构中已按职责分层，扩展时请优先依赖 `server/`、`tools/` 的公共入口。
