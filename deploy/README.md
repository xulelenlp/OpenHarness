# deploy/

qaapp 的**唯一打包/部署目录**。只保留 5 个脚本/配置（另加本 README）：

```text
deploy/
├── build.sh          # 构建镜像 + 整理 dist/qaapp-simple/（不需要 Docker）
├── build_image.py    # 下载基础镜像/依赖、组装单层镜像
├── constraints.txt   # Python 依赖 pin
├── entrypoint.sh     # 容器入口
└── run.sh            # 目标机启动：docker load 后直接运行
```

详细步骤见
[`docs/qaapp/BUILD_AND_DEPLOY.zh-CN.md`](../docs/qaapp/BUILD_AND_DEPLOY.zh-CN.md)。

## 构建

```bash
# 首次 / 依赖变更（构建机需要外网）
./deploy/build.sh

# 日常改代码：从已有镜像离线重建（不需要外网，2–4 分钟）
./deploy/build.sh --from-image dist/qaapp-image-0.1.9-amd64.tar.gz

# 只重新整理部署目录，不重建镜像
./deploy/build.sh --reuse-image
```

产物：

```text
dist/qaapp-image-<version>-amd64.tar.gz
dist/qaapp-simple/
├── qaapp-image-<version>-amd64.tar.gz
├── run.sh
├── README.md
├── QUICK_DEPLOY.zh-CN.md
├── BUILD_AND_DEPLOY.zh-CN.md
└── SHA256SUMS
```

## 部署

```bash
# 目标机
cd /root/qaapp-simple
docker load -i qaapp-image-0.1.9-amd64.tar.gz

# 只改项目配置里的 DB/Redis
vi /root/workspace/workspace/OpenHarness/config/backend/.env

# 启动
./run.sh
```

默认：配置 `/root/workspace/workspace/OpenHarness/config/backend`，数据
`/root/qaapp-data`，端口 `8787`，容器名 `qaapp`。

## 文件说明

| 文件 | 作用 |
| --- | --- |
| `build.sh` | 构建入口；支持完整构建、`--from-image` 离线重建、`--reuse-image` |
| `build_image.py` | 拉取基础镜像/wheels/Node/字体，组装单层 docker-save 镜像 |
| `constraints.txt` | 运行期依赖锁定 |
| `entrypoint.sh` | 容器启动脚本（`python -m qaapp webapp run`） |
| `run.sh` | 目标机最简启动；挂载项目配置 + 数据目录 |

## 注意

- 构建机不需要 Docker；目标机只需要 Docker。
- 目标机不需要外网、不需要 `docker build`、不部署 MySQL/Redis。
- 配置（尤其 DB/Redis）永远只改项目 `config/backend/.env`，不需要重建镜像。
- 详细构建/部署/排障见
  [`BUILD_AND_DEPLOY.zh-CN.md`](../docs/qaapp/BUILD_AND_DEPLOY.zh-CN.md)。
