# deploy/

qaapp 的 Docker 镜像打包与离线部署资产。

> **从源码构建镜像 + 内网部署？** 看
> [`docs/qaapp/BUILD_AND_DEPLOY.zh-CN.md`](../docs/qaapp/BUILD_AND_DEPLOY.zh-CN.md)
> 和 [`deploy/simple/`](simple/)。下面的 bundle/install 是高级一键安装方案，日常不需要。

完整说明见
[`docs/qaapp/OFFLINE_PACKAGING.zh-CN.md`](../docs/qaapp/OFFLINE_PACKAGING.zh-CN.md)（逐步操作手册）
和 [`docs/qaapp/IMAGE_DEPLOY.zh-CN.md`](../docs/qaapp/IMAGE_DEPLOY.zh-CN.md)（设计说明）。

```text
deploy/
├── simple/                  # 最简部署：镜像 tar + run.sh（推荐）
│   ├── run.sh               # docker load 后直接挂载项目配置运行
│   └── README.md
├── build.sh                 # 构建镜像 + 组装部署包（在 Docker 构建机执行）
├── build_offline.sh         # 无 Docker 构建机：生成单层离线镜像 + 部署包
├── offline/
│   └── build_image.py       # 下载/展开基础镜像、wheels、Node、字体并组装镜像
├── configure.py             # 容器内安全改写 config/backend/.env
├── selfcheck.py             # 运行期 / 配置 / 网络自检
├── docker/
│   ├── Dockerfile           # 多阶段：webapp 构建 + Python 运行镜像
│   ├── constraints.txt      # 运行期依赖 pin
│   └── entrypoint.sh        # 启动 qaapp webapp
└── bundle/                  # 部署包模板（会被复制进 dist/qaapp-deploy-*）
    ├── docker-compose.yaml
    ├── install.sh
    ├── upgrade.sh
    ├── backup.sh
    ├── cleanup.sh
    ├── uninstall.sh
    ├── smoke.sh
    └── README.zh-CN.md
```

## 构建

### 目标机无外网（推荐）

```bash
./deploy/build_offline.sh --tag openharness-qaapp:0.1.9-offline
```

构建机不需要 Docker，只需要 Python 3.9+（带 pip）和外网。脚本会下载基础镜像
（Docker 镜像代理）、linux cp311 wheels、Node 20、Noto CJK 字体、ripgrep，
组装成单层 `docker load` 镜像。目标机只需 `docker load` + `install.sh`，完全离线。

### 构建机有 Docker

```bash
./deploy/build.sh --platform linux/amd64
```

需要 Docker（含 buildx）与外网/内网 PyPI、npm、镜像仓库。构建期会执行
`python /app/deploy/selfcheck.py --runtime --strict`，依赖或中文字体缺失会直接失败。

## 安装

```bash
tar xzf dist/qaapp-deploy-*.tar.gz
cd qaapp-deploy-*
sudo ./install.sh --db-host <现有 MySQL> --db-user <只读账号> --db-password '***' \
  --db-name ai_search_fj --redis-host <现有 Redis> --port 8787
```

MySQL/Redis 不在本项目中部署；容器只通过配置访问现有实例。

## 测试

```bash
pytest tests/test_deploy_configure.py tests/test_deploy_bundle.py -q
ruff check deploy
```
