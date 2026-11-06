# deploy/

qaapp 的打包/部署目录。只保留 5 个文件：

```text
deploy/
├── build.sh          # 唯一构建入口：选 env + docker build + docker save
├── Dockerfile        # FROM 本地基础镜像，COPY 当前代码
├── entrypoint.sh     # 容器入口
├── run.sh            # 目标机启动（使用包内 config/backend）
└── README.md
```

详细步骤见
[`docs/qaapp/BUILD_AND_DEPLOY.zh-CN.md`](../docs/qaapp/BUILD_AND_DEPLOY.zh-CN.md)。

## 前置：导入基础镜像（只一次）

```bash
cd /root/qaapp-simple
docker load -i qaapp-base-image.tar.gz
docker images | grep openharness-qaapp
```

默认基础镜像 tag：`openharness-qaapp:0.1.9-offline`。

## 环境文件

```text
config/backend/env_files/
├── env_dev    # 开发环境
└── env_pro    # 生产环境
```

这两个文件含密码/Key，已被 `.gitignore` 忽略，不提交。
构建时 `--env dev|pro` 会把选中的文件复制为 `config/backend/.env`，
并放入部署包 `dist/qaapp-simple/config/backend/.env`。

## 构建

```bash
cd /root/workspace/workspace/OpenHarness
git pull
./deploy/build.sh --env pro
```

- 使用本地基础镜像做增量构建，`--network none`，不下载基础镜像/依赖；
- 输出 `dist/qaapp-image-<version>-<timestamp>-amd64.tar.gz`；
- 同时整理 `dist/qaapp-simple/`（已包含选中的 `config/backend/.env`）。

自定义：

```bash
./deploy/build.sh \
  --env pro \
  --base openharness-qaapp:0.1.9-offline \
  --tag openharness-qaapp:0.1.9-20261106 \
  --output dist/qaapp-image-0.1.9-20261106-amd64.tar.gz
```

> 输出 tag 不能等于基础镜像 tag，避免覆盖基础镜像。

## 部署（不需要 vim）

```bash
cd /root/qaapp-simple
docker load -i qaapp-image-<version>-<timestamp>-amd64.tar.gz
./run.sh
```

`run.sh` 默认挂载同目录的 `config/backend`；构建时已经选好 dev/pro，
所以目标机不需要再改 `.env`。只有后续要换库/改密码时才编辑包内
`config/backend/.env` 后 `docker restart qaapp`。

默认数据目录 `/root/qaapp-data`，端口 `8787`，容器名 `qaapp`。

## 说明

- 基础镜像 = 环境（Python/依赖/Node/字体），只需导入一次；请保留 tar。
- 增量构建只更新代码；依赖/Node/字体变更需要新的基础镜像。
- 环境配置在打包时确定；部署包自带配置，减少目标机手工操作。
