# Real-API end-to-end checks

这些测试会调用真实模型，默认**不参与** `pytest -q`（`real_api` marker 被排除）。
它们需要：

- `ANTHROPIC_API_KEY`（必填；缺失时自动 skip）
- `ANTHROPIC_BASE_URL`（可选）
- `OPENHARNESS_E2E_WORKSPACE`（必填；指向一个可供 Agent 读写的不熟悉代码库）

运行：

```bash
OPENHARNESS_E2E_API=1 \
OPENHARNESS_E2E_WORKSPACE=/path/to/sample-repo \
ANTHROPIC_API_KEY=... \
pytest tests/e2e -m real_api -q
```

这些测试会修改工作区中的临时文件；请始终指向一次性/可丢弃的代码库。
