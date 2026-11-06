#
# qaapp 运行镜像：Python 3.11 + Node 20 + 中文字体 + 常用命令行工具。
# 前端在 webapp 阶段构建，运行期由 qaapp 后端托管 dist（同源）。
#
# 构建入口是仓库根目录的 deploy/build.sh；不直接 docker build 也可以：
#   docker build --platform linux/amd64 -f deploy/docker/Dockerfile -t qaapp:dev .

ARG PYTHON_IMAGE=python:3.11-slim-bookworm
ARG NODE_IMAGE=node:20-bookworm-slim

# ---------------------------------------------------------------------------
# 前端构建
# ---------------------------------------------------------------------------
FROM ${NODE_IMAGE} AS webapp

ARG NPM_CONFIG_REGISTRY=

WORKDIR /build

# 先装依赖，利用 Docker 层缓存。
COPY frontend/webapp/package.json frontend/webapp/package-lock.json ./frontend/webapp/
RUN if [ -n "${NPM_CONFIG_REGISTRY}" ]; then npm config set registry "${NPM_CONFIG_REGISTRY}"; fi \
    && cd frontend/webapp \
    && npm ci --no-audit --no-fund

COPY frontend/webapp ./frontend/webapp
# vite.config.ts 的 envDir 是 <repo>/config/frontend，必须保持同样的相对结构。
COPY config/frontend ./config/frontend
# 生产构建固定使用 .env_prod，避免把开发机上的 .env（可能指向 127.0.0.1）打进产物。
RUN cp config/frontend/.env_prod config/frontend/.env \
    && cd frontend/webapp \
    && npm run build

# ---------------------------------------------------------------------------
# 运行镜像
# ---------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime

ARG PIP_INDEX_URL=
ARG CONSTRAINTS=1
ARG APP_VERSION=dev
ARG GIT_COMMIT=unknown
ARG BUILD_DATE=unknown

LABEL org.opencontainers.image.title="openharness-qaapp" \
      org.opencontainers.image.description="OpenHarness qaapp web service (backend + web frontend)" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="${GIT_COMMIT}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.source="https://github.com/xulelenlp/OpenHarness"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src:/app \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    TZ=Asia/Shanghai \
    HOME=/data/home \
    OHMO_WORKSPACE=/data \
    OPENHARNESS_DATA_DIR=/data/data \
    OPENHARNESS_LOGS_DIR=/data/logs \
    QAAPP_CONFIG_DIR=/app/config/backend \
    QAAPP_STATIC_DIR=/app/frontend/webapp/dist \
    MPLCONFIGDIR=/data/home/.cache/matplotlib

# 运行期系统依赖：
# - fonts-noto-cjk：matplotlib 图表中文；tzdata：ZoneInfo("Asia/Shanghai")
# - ripgrep/git/bash/jq：agent 的文件检索、git 能力与脚本排障
# - libgomp1/libfreetype6/libatomic1/libstdc++6：numpy/matplotlib/node 动态库
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        fontconfig \
        fonts-noto-cjk \
        fonts-wqy-zenhei \
        git \
        jq \
        less \
        libatomic1 \
        libfreetype6 \
        libgomp1 \
        libstdc++6 \
        passwd \
        procps \
        ripgrep \
        tzdata \
        unzip \
        zip \
    && rm -rf /var/lib/apt/lists/*

# run_script 的 node 分支需要 node 可执行文件（不需要 npm）。
COPY --from=webapp /usr/local/bin/node /usr/local/bin/node

WORKDIR /app

# 先复制包源码再安装，依赖层可复用；editable 安装保留仓库相对路径，
# 否则 qaapp/paths.py 的 skills/plugins 目录会指向 site-packages 而失效。
COPY pyproject.toml README.md LICENSE ./
COPY deploy/docker/constraints.txt /tmp/constraints.txt
COPY src ./src
COPY ohmo ./ohmo
COPY qaapp ./qaapp
# hatch 的 force-include 需要在构建时看到这些路径；.env_dev 只放占位符，构建后删除。
COPY config ./config
COPY frontend/terminal ./frontend/terminal
RUN printf '# build-time placeholder for hatch force-include; real config is mounted at runtime\n' \
        > config/backend/.env_dev \
    && if [ "${CONSTRAINTS}" = "1" ]; then \
        python -m pip install --no-cache-dir -c /tmp/constraints.txt -e .; \
    else \
        python -m pip install --no-cache-dir -e .; \
    fi \
    && rm -f /tmp/constraints.txt config/backend/.env_dev

COPY skills ./skills
COPY plugins ./plugins
COPY scripts ./scripts
COPY deploy ./deploy
# 保留前端源码（便于容器内排查），运行期实际使用的是下面构建好的 dist。
COPY frontend/webapp ./frontend/webapp
COPY --from=webapp /build/frontend/webapp/dist ./frontend/webapp/dist

# 非 root 运行（UID/GID 1000）；安装脚本会把宿主 data/config 目录 chown 到 1000。
RUN mkdir -p /data/home/.cache/matplotlib /data/logs /data/sessions /data/conversations \
    && if ! getent group 1000 >/dev/null; then groupadd --gid 1000 app; fi \
    && if ! id -u 1000 >/dev/null 2>&1; then \
        useradd --uid 1000 --gid 1000 --home-dir /data/home --shell /bin/bash --no-create-home app; \
    fi \
    && chown -R 1000:1000 /app /data \
    && chmod +x /app/deploy/docker/entrypoint.sh \
    && mkdir -p /tmp/qaapp-selfcheck/mpl /tmp/qaapp-selfcheck/cache \
    && HOME=/tmp/qaapp-selfcheck MPLCONFIGDIR=/tmp/qaapp-selfcheck/mpl \
        XDG_CACHE_HOME=/tmp/qaapp-selfcheck/cache \
        python /app/deploy/selfcheck.py --runtime --strict

USER 1000:1000

EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8787/healthz >/dev/null || exit 1

ENTRYPOINT ["/app/deploy/docker/entrypoint.sh"]
