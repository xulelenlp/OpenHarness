# qaapp 参考前端

基于 Vite + React + TypeScript 的参考 Web 前端，展示与 qaapp 对话 API 的完整交互：

- 对话列表 / 新建对话
- SSE 流式问答
- 多 Agent 路由结果展示（消息头上的 Agent 徽标）
- 工具执行过程（中间执行轨迹）折叠展示
- 高危操作审批（允许 / 总是允许 / 拒绝）
- 中断当前回答

## 开发

```bash
cd frontend/webapp
npm install
npm run dev      # http://localhost:5173，/api 与 /healthz 代理到 127.0.0.1:8787
```

启动后端：

```bash
abcqa init          # 首次写入示例配置
abcqa webapp run    # 127.0.0.1:8787
```

## 生产

```bash
npm run build       # 产物输出到 dist/
```

将 `dist/` 目录路径填入 `~/.ohmo/webapp.json` 的 `static_dir`，
重启 `abcqa webapp run`，即可由同一端口托管前端与 API。
