# qaapp 增量镜像：以本地已导入的完整镜像为基础，只替换代码。
# 基础镜像已包含 Python/依赖/Node/字体/系统库；这里不下载任何东西。

ARG BASE_IMAGE=openharness-qaapp:0.1.9-offline
FROM ${BASE_IMAGE}

ARG VERSION=dev
ARG COMMIT=unknown

# 代码：覆盖 base 里的旧版本
COPY --chown=1000:1000 src /app/src
COPY --chown=1000:1000 ohmo /app/ohmo
COPY --chown=1000:1000 qaapp /app/qaapp
COPY --chown=1000:1000 skills /app/skills
COPY --chown=1000:1000 plugins /app/plugins
COPY --chown=1000:1000 scripts /app/scripts
COPY --chown=1000:1000 deploy /app/deploy

# 前端构建产物
COPY --chown=1000:1000 frontend/webapp/dist /app/frontend/webapp/dist

# 配置模板（不含 .env；部署时挂载项目配置）
COPY --chown=1000:1000 config/backend/agents.json /app/config/backend/agents.json
COPY --chown=1000:1000 config/backend/dangerous-actions.json /app/config/backend/dangerous-actions.json
COPY --chown=1000:1000 config/backend/datasources.json /app/config/backend/datasources.json
COPY --chown=1000:1000 config/backend/webapp.json /app/config/backend/webapp.json
COPY --chown=1000:1000 config/backend/.env_prod /app/config/backend/.env_prod

COPY --chown=1000:1000 README.md LICENSE pyproject.toml /app/

RUN printf 'version=%s\ncommit=%s\nbase=%s\n' \
        "${VERSION}" "${COMMIT}" "${BASE_IMAGE}" > /app/BUILD_INFO

USER 1000:1000

# 覆盖 base 的入口，保证新布局生效
ENTRYPOINT ["/app/deploy/entrypoint.sh"]
