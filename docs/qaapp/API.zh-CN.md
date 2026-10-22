# qaapp HTTP API 参考

## HTTP API 参考

| 方法 | 路径 | 鉴权 | 说明 |
| --- | --- | --- | --- |
| GET | `/healthz` | **否** | 健康检查 |
| GET | `/api/v1/bootstrap` | **否** | 浏览器启动引导：返回是否需要鉴权；启用 `browser_auth` 时签发 HttpOnly 会话 Cookie |
| GET | `/api/v1/agents` | **否** | Agent 列表 |
| POST | `/api/v1/conversations` | 是 | 创建对话 → `{"conversation_id": "..."}` |
| GET | `/api/v1/conversations` | 是 | 列出对话（`?limit=&offset=`） |
| GET | `/api/v1/conversations/{id}` | 是 | 对话详情（含 `messages`、`turns`、`pending_approvals`） |
| POST | `/api/v1/conversations/{id}/messages` | 是 | 发送消息（`stream:true` 走 SSE）；body 可带 `attachment_ids` |
| POST | `/api/v1/conversations/{id}/attachments` | 是 | 上传附件（multipart，字段名 `files`，1..2 个），服务端立即解析 |
| GET | `/api/v1/conversations/{id}/workspace` | 是 | 列出会话临时目录文件（uploads/extracted/scripts/tmp/outputs） |
| GET | `/api/v1/conversations/{id}/files/{相对路径}` | 是 | 下载会话目录内的文件（越界路径一律 404） |
| GET | `/api/v1/conversations/{id}/turns/{turn_id}` | 是 | 单轮完整轨迹（含中间执行过程） |
| POST | `/api/v1/conversations/{id}/approvals/{request_id}` | 是 | 响应审批 |
| POST | `/api/v1/conversations/{id}/interrupt` | 是 | 中断当前回答 |
| WS | `/api/v1/conversations/{id}/stream` | **否** | WebSocket 流式订阅 |

> 「鉴权」列 = 配置了 `api_tokens` 之后是否要求凭据。浏览器默认走 `browser_auth`
> 的 HttpOnly 会话 Cookie（用户无需手填 token）；API 客户端仍用
> `Authorization: Bearer <token>`。`/healthz`、`/api/v1/bootstrap`、`/api/v1/agents`
> 与 WebSocket 在开启鉴权后仍然开放，见 §9.1。

**发送消息**：

```bash
curl -N http://127.0.0.1:8787/api/v1/conversations/<id>/messages \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{"content": "分析福建售电量情况", "stream": true}'
```

`agent` 字段可省略（自动路由）；`stream:false` 同步返回 `{"conversation_id", "turn_id"}`。
`content` 可以为空——只要带了 `attachment_ids`（前端直接传文件不写文字是合法用法）。

**上传附件**（单次 1..2 个，字段名固定 `files`）：

```bash
curl -X POST http://127.0.0.1:8787/api/v1/conversations/<id>/attachments \
  -H "Authorization: Bearer <token>" \
  -F "files=@报告.docx" -F "files=@售电量.xlsx"
# 201 -> {"conversation_id":"...","attachments":[
#   {"attachment_id":"att_...","name":"报告.docx","kind":"word","size":12345,
#    "parse_status":"ok","text_chars":8420,"preview":"...",
#    "stored_path":"uploads/报告.docx","extracted_path":"extracted/报告.docx.txt",
#    "meta":{"paragraphs":76,"tables":3}}, ...]}
```

拿到 `attachment_id` 后随消息发送：

```bash
curl -N http://127.0.0.1:8787/api/v1/conversations/<id>/messages \
  -H "Content-Type: application/json" -H "Authorization: Bearer <token>" \
  -d '{"content":"帮我分析这份报表","stream":true,"attachment_ids":["att_xxx","att_yyy"]}'
```

失败码：`415 unsupported_file_type`（类型不支持）、`413 file_too_large` / `too_many_files`
（过大 / 过多）、`404 attachment_not_found`（id 不存在或不属于该会话）。

**会话目录**：

```bash
GET  /api/v1/conversations/{id}/workspace          # 列出 uploads/extracted/scripts/tmp/outputs
GET  /api/v1/conversations/{id}/files/scripts/analyze.py   # 下载（带 Bearer）
```

**SSE 事件类型**：`attachments_ready`（本轮附件，历史回放靠它还原芯片）/ `agent_routed` /
`status` / `tool_started` / `tool_completed` / `tool_progress` / `assistant_delta` /
`assistant_complete` / `approval_required` / `approval_resolved` / `turn_files` / `error` /
`turn_complete`。

`turn_files` 在 `turn_complete` 之前发出，是「中间过程文件 / 结果文件」卡片的权威来源：

```jsonc
{
  "type": "turn_files",
  "truncated": false,
  "changed": [
    { "path": "scripts/analyze.py", "dir": "scripts", "action": "created",
      "size": 3456, "mtime": 1790000000.1, "added": 120, "removed": 0, "kind": "script" }
  ],
  "results": [
    { "path": "outputs/reports/报告.docx", "name": "报告.docx", "title": "报告",
      "kind": "word", "size": 124928, "mtime": 1790000012.3,
      "download_path": "/api/v1/conversations/{id}/files/outputs/reports/报告.docx" }
  ]
}
```

- `changed`：服务端对会话目录做轮次前后 diff 的结果（排除 `uploads/`、`extracted/`、
  `meta.json`、`attachments.json` 等），最多 100 条；`added/removed` 对二进制文件为 `null`。
- `results`：`outputs/` 下本轮产出的「成果性文档」，只接受 Word / PDF / PPT / Excel /
  Markdown；csv、py、txt、json、图片等属于过程文件，只在过程区展示，不进入结果区。
  最多 8 个。前端点击卡片即下载（`download_path` 供外部客户端使用；前端实际用
  `path` + 鉴权 fetch，避免中文未编码）。
- 展示时机：前端在 `turn_complete`（任务全部结束）后才一次性展示过程文件与结果文件
  卡片；进行中不展示，避免文件列表随工具调用反复增减。中断 / 断流时也在流结束时
  补一次展示。
- 事件写入轨迹，刷新页面后的历史回放同样依赖它；旧轨迹没有该事件时，参考前端会从
  工具输出推导（近似结果）。

**审批 / 选项式确认**：收到 `approval_required`（带 `request_id`）后：

```jsonc
POST /api/v1/conversations/{id}/approvals/{request_id}
{ "decision": "approve", "reply": "按供电单位" }
// decision: approve | deny | always；always = 本对话内该工具不再询问
// reply: 问题（kind=question）的选项 label 或自定义回答；权限/编辑审批可省略
```

`kind=question` 的载荷可以带具体选项（2–6 个），模型在口径、范围、输出格式等不明确时使用：

```jsonc
{
  "type": "approval_required",
  "kind": "question",
  "request_id": "abc123",
  "payload": {
    "question": "报告按什么口径生成？",
    "options": [
      { "label": "按供电单位", "description": "适合管理汇报", "recommended": true },
      { "label": "按行业分类", "description": "适合结构分析" }
    ],
    "allow_other": true
  }
}
```

- `options[].label` 是回传值；`description` 只用于展示；`recommended` 只做视觉提示，不默认选中。
- 前端始终提供「其他」自由输入，用户也可以回传不在 `options` 里的文本。
- 审批/问题进入终态后追加 `approval_resolved`（写入轨迹，刷新后不会重新显示为待处理）：

```jsonc
{ "type": "approval_resolved", "request_id": "abc123", "kind": "question",
  "decision": "approve", "reply": "按供电单位" }
```

超时为 `decision: "timeout"`、`reply: ""`；问题会返回 `(no response)` 给模型。

---

