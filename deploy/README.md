# deploy/

qaapp 的打包/部署目录。只保留 5 个文件：

```text
deploy/
├── build.sh          # 唯一构建入口：docker build + docker save
├── Dockerfile        # FROM 本地基础镜像，COPY 当前代码
├── entrypoint.sh     # 容器入口
├── run.sh            # 目标机启动
└── README.md
```

详细步骤见
[`docs/qaapp/BUILD_AND_DEPLOY.zh-CN.md`](../docs/qaapp/BUILD_AND_DEPLOY.zh-CN.md)。

## 前置：导入基础镜像（只一次）

```bash
cd /root/qaapp-simple
docker load -i qaapp-image-0.1.9-amd64.tar.gz
docker images | grep openharness-qaapp
```

默认基础镜像 tag：`openharness-qaapp:0.1.9-offline`。

## 构建

```bash
cd /root/workspace/workspace/OpenHarness
git pull
./deploy/build.sh
```

- 使用本地基础镜像做增量构建，`--network none`，不下载基础镜像/依赖；
- 输出 `dist/qaapp-image-<version>-<timestamp>-amd64.tar.gz`；
- 同时整理 `dist/qaapp-simple/`。

自定义：

```bash
./deploy/build.sh \
  --base openharness-qaapp:0.1.9-offline \
  --tag openharness-qaapp:0.1.9-20261106 \
  --output dist/qaapp-image-0.1.9-20261106-amd64.tar.gz
```

> 输出 tag 不能等于基础镜像 tag，避免覆盖基础镜像。

## 部署

```bash
cd /root/qaapp-simple
docker load -i qaapp-image-<version>-<timestamp>-amd64.tar.gz

# 只改项目配置里的 DB/Redis
vi /root/workspace/workspace/OpenHarness/config/backend/.env

./run.sh
```

默认：配置 `/root/workspace/workspace/OpenHarness/config/backend`，数据
`/root/qaapp-data`，端口 `8787`，容器名 `qaapp`。

## 说明

- 基础镜像 = 环境（Python/依赖/Node/字体），只需导入一次；请保留 tar。
- 增量构建只更新代码；依赖/Node/字体变更需要新的基础镜像。
- DB/Redis 永远只改目标机 `config/backend/.env`，不需要重建镜像。
