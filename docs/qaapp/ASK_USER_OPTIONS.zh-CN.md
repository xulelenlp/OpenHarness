# 选项式澄清验收案例

本文是「任务执行中遇到不明确点时，先给选项让用户确认，再继续执行」的验收清单。
它验证的是现有 `ask_user_question` 工具 + 审批事件通道的扩展，不是一套新的计划模式。

## 1. 前置条件

```bash
# 后端与前端（默认后端 :8787，前端 :5173）
./scripts/dev_services.sh restart

# 如果 ~/.ohmo 不在可写目录，可指定仓库内测试工作区：
# OHMO_WORKSPACE="$PWD/.openharness/run/ohmo-test" ./scripts/dev_services.sh restart
```

- `config/backend/settings.json` 里已配置可用模型；`config/backend/agents.json` 的通用助手
  `tools` 为 `["*"]`（包含 `ask_user_question`）。
- 浏览器打开 `http://localhost:5173`。

## 2. 自动化回归

```bash
# 后端（清理 NO_PROXY，规避当前 shell 中 [::1] 与 httpx 0.28 的已知冲突）
NO_PROXY=localhost,127.0.0.1,::1 no_proxy=localhost,127.0.0.1,::1 \
  .venv/bin/python -m pytest \
  tests/test_tools/test_ask_user_question.py \
  tests/test_tools/test_integration_flows.py \
  tests/test_engine/test_query_engine.py \
  tests/test_ui/test_react_backend.py \
  tests/test_qaapp/test_capabilities.py \
  tests/test_qaapp/test_service.py -q

# 前端
cd frontend/webapp
npm run typecheck
npm run check:render
npm run check:attachments
npm run check:turn-files
npm run check:questions
npm run build
```

期望：全部退出码 0；`check:questions` 覆盖「等待态 → 已确认 → 历史回放」、超时、权限审批
标题、选项去重/上限/字符串兼容等。

## 3. 手动验收案例

### 案例 A：确定性的单问题闭环（推荐先跑）

输入：

```text
我在做前端验收：请先用 ask_user_question 就“报告聚焦哪个分析维度”给我 3 个选项
（含一个推荐项）；等我确认后，只回复一句“已收到：<我的选择>”，
不要调用其他工具、不要生成文件。
```

预期：

1. 执行轨迹出现 `ask_user_question`，状态为「等待回答」；下方出现「需要你确认」问题卡。
2. 问题卡展示 3 个选项 +「其他（自行输入）」，至少一个带「推荐」；「确认并继续」在未选择前禁用。
3. 顶栏与输入区出现「等待确认」，输入框占位为「请先回答上方的问题…」。
4. 选择第一项 →「确认并继续」→ 卡片变为「已确认：<选项>」，工具步骤完成。
5. 模型只回复「已收到：<选项>」，不再调用工具；任务结束。
6. 刷新页面后，问题仍是「已确认」完成态，不会重新出现按钮。

### 案例 B：隐式触发（验证模型行为）

输入：

```text
帮我做一份售电量分析报告，信息不足的地方先问我。
```

预期：模型在统计范围 / 分析维度 / 输出格式等关键点上调用 `ask_user_question`，给出
2–4 个具体选项；用户确认后继续取数与生成报告。模型如果直接给合理默认值并说明，也属于
可接受行为；本案例用于观察模型倾向，不作为硬性断言。

### 案例 C：「其他」自由输入

在问题卡选择「其他（自行输入）」，输入：

```text
按日粒度，最近 7 天，只要 Markdown，不要 Word
```

确认后预期：

- 卡片显示「已确认：按日粒度，最近 7 天，只要 Markdown，不要 Word」；
- 工具结果原样带回该文本；模型按该口径继续；
- 「其他」为空时按钮保持禁用。

### 案例 D：连续多个问题

使用真实报告类 prompt（例如案例 B）。模型可能在「统计范围 → 分析维度 → 输出格式」
上连续提问。每回答一个，下一张问题卡继续出现；全部确认后任务才继续执行。
输入区在任一问题等待期间都保持禁用发送。

### 案例 E：待确认时刷新

问题卡出现后直接刷新页面。预期：

- 会话恢复后问题卡与选项重新出现，仍可作答；
- 作答后任务继续（原 SSE 连接由后端保持；若后端已结束进程则按超时/中断处理）。

### 案例 F：回答后刷新

回答问题并等任务结束后刷新。预期：

- 原位置显示「已确认：<答案>」，不再有 radio/按钮；
- 执行轨迹、附件、文件卡、最终回答完整。

### 案例 G：权限 / 编辑审批回归

触发一次 `run_script` 或需要确认的工具：

- 「允许 / 总是允许 / 拒绝」三态正常；
- 权限卡可展开工具参数；编辑审批可看到 diff；
- 处理结果写入轨迹，刷新后不重复弹出；
- 问题卡的 `approval_resolved` 不影响权限审批原有语义。

### 案例 H：纯文本问题

如果模型调用 `ask_user_question` 但不带 `options`：

- 卡片显示问题正文 + 单行输入 +「确认并继续」；
- 提交后答案回传，任务继续；
- 不出现空白选项区或错误。

## 4. 视觉验收

在 1440、768、390 三档检查：

- 空状态：品牌标识、标题、示例问题卡、输入区不溢出；
- 对话中：用户气泡右对齐、助手正文可读、执行轨迹紧凑；
- 待确认：问题卡完整可见，选项可点击，底部「确认并继续」不被输入区遮挡；
- 移动端：侧边栏默认隐藏，汉堡按钮可打开；文件抽屉为覆盖层；选项文字换行不破版；
- 刷新/切换会话：无横向滚动、无按钮重复、无「思考中…」卡在等待确认状态。

## 5. 排障

| 现象 | 检查 |
| --- | --- |
| 模型一直文字追问，不出选项卡 | 看该 Agent 的 `tools` 是否包含 `ask_user_question`；看系统提示/能力清单是否加载；用案例 A 的显式 prompt 区分「模型没调工具」与「链路问题」 |
| 有 `tool_started` 但没有问题卡 | 浏览器控制台看 `[sse]` 是否收到 `approval_required`；后端日志看 `sse emit`；检查 SSE 分帧 |
| 点击确认后卡片不消失 | 看 `POST /approvals/{request_id}` 是否 200；看是否收到 `approval_resolved`；检查是否重复提交 |
| 刷新后问题又变成待处理 | 确认轨迹里有 `approval_resolved`；旧会话没有该事件时属于预期（只对新版本产生的轨迹生效） |
| 选项显示不全 | 服务端最多接受 6 个；前端会过滤空 label / 重复 label；检查 `payload.options` |
| 审批超时 | 默认 300s；超时后发 `approval_resolved(timeout)`，问题返回空串 |

## 6. 收尾

```bash
./scripts/dev_services.sh stop
```
