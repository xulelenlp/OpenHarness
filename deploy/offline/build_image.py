#!/usr/bin/env python3
"""在没有 Docker 的构建机上生成可直接 ``docker load`` 的 linux/amd64 镜像。

背景：目标服务器在内网、不能访问外网。因此镜像必须在这里一次性备齐：
基础 rootfs、全部 Python 依赖、Node、ripgrep、中文字体、前端产物、应用代码。

做法（不执行任何 Linux 二进制）：

1. 从镜像代理拉取 ``python:3.11-bookworm``（amd64）的 layers 并展开成 rootfs；
2. ``pip download`` 拉取 linux cp311 wheels，``pip install --target`` 装进
   ``/app/vendor``（只写文件，不执行）；
3. 放入 Node linux-x64、ripgrep 静态二进制、Noto CJK 字体；
4. 把 rootfs 打成单层 tar，并写 docker-save 兼容的 ``manifest.json`` /
   ``config.json``，输出 ``qaapp-image-<tag>-amd64.tar.gz``。

目标机只需要 ``docker load``，不需要 Docker build，也不需要外网。

依赖：构建机需要 Python 3.9+、pip、外网（PyPI/镜像代理）；不需要 Docker。
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import NoReturn

REPO_ROOT = Path(__file__).resolve().parents[2]
CONSTRAINTS = REPO_ROOT / "deploy" / "docker" / "constraints.txt"

DEFAULT_REGISTRY = "https://docker.m.daocloud.io"
DEFAULT_AUTH_URL = "https://m.daocloud.io/auth/token"
DEFAULT_BASE_IMAGE = "python:3.11-bookworm"
DEFAULT_PIP_INDEX = "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple"
DEFAULT_NODE_VERSION = "v20.19.0"
DEFAULT_NODE_URL = (
    "https://mirrors.tuna.tsinghua.edu.cn/nodejs-release/{version}/"
    "node-{version}-linux-x64.tar.xz"
)
DEFAULT_FONT_URL = (
    "https://mirrors.tuna.tsinghua.edu.cn/debian/pool/main/f/fonts-noto-cjk/"
    "fonts-noto-cjk_20240730+repack1-1_all.deb"
)
RG_PACKAGE = "ripgrep-bin==15.2.0"

WHEEL_PLATFORMS = ("manylinux_2_28_x86_64", "manylinux2014_x86_64")
PY_VERSION = "3.11"
PY_ABI = "cp311"

FONT_FILES = ("NotoSansCJK-Regular.ttc", "NotoSansCJK-Bold.ttc")


def info(message: str) -> None:
    print(f"[INFO] {message}", flush=True)


def warn(message: str) -> None:
    print(f"[WARN] {message}", file=sys.stderr, flush=True)


def die(message: str) -> "NoReturn":
    print(f"错误：{message}", file=sys.stderr, flush=True)
    raise SystemExit(1)


def read_version() -> str:
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return match.group(1) if match else "0.0.0"


def read_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return "unknown"
    return result.stdout.strip() or "unknown"


# ---------------------------------------------------------------------------
# HTTP / registry
# ---------------------------------------------------------------------------


def _http_json(url: str, headers: dict[str, str], timeout: float = 60.0) -> dict:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _http_download(url: str, dest: Path, headers: dict[str, str], *, retries: int = 3) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    for attempt in range(1, retries + 1):
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=120) as response:
                total = int(response.headers.get("Content-Length") or 0)
                done = 0
                last_report = 0.0
                with tmp.open("wb") as handle:
                    while True:
                        chunk = response.read(1024 * 256)
                        if not chunk:
                            break
                        handle.write(chunk)
                        done += len(chunk)
                        now = time.monotonic()
                        if now - last_report > 5:
                            last_report = now
                            if total:
                                info(f"  {dest.name}: {done * 100 // total}% ({done // 1048576} MB)")
                            else:
                                info(f"  {dest.name}: {done // 1048576} MB")
            os.replace(tmp, dest)
            return
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            tmp.unlink(missing_ok=True)
            if attempt == retries:
                die(f"下载失败 {url}：{exc}")
            warn(f"下载重试 {attempt}/{retries}：{url}（{exc}）")
            time.sleep(2 * attempt)


class Registry:
    """极简 Docker Registry v2 客户端（只读，匿名 token）。"""

    def __init__(self, base: str, auth_url: str) -> None:
        self.base = base.rstrip("/")
        self.auth_url = auth_url
        self._token = ""
        self._scope = ""

    def _auth(self, repository: str) -> str:
        scope = f"repository:{repository}:pull"
        if self._token and self._scope == scope:
            return self._token
        query = urllib.parse.urlencode({"service": self.base.split("://", 1)[-1], "scope": scope})
        payload = _http_json(f"{self.auth_url}?{query}", {"Accept": "application/json"})
        token = str(payload.get("token") or payload.get("access_token") or "")
        if not token:
            die(f"镜像仓库认证失败：{self.auth_url}")
        self._token = token
        self._scope = scope
        return token

    def _headers(self, repository: str, accept: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._auth(repository)}", "Accept": accept}

    def _manifest(self, repository: str, reference: str, accept: str) -> dict:
        return _http_json(
            f"{self.base}/v2/{repository}/manifests/{reference}",
            self._headers(repository, accept),
        )

    def _blob(self, repository: str, digest: str, dest: Path) -> None:
        _http_download(
            f"{self.base}/v2/{repository}/blobs/{digest}",
            dest,
            {"Authorization": f"Bearer {self._auth(repository)}"},
        )

    def pull_amd64(self, image: str, layers_dir: Path, meta_dir: Path) -> list[Path]:
        repository, tag = _split_image(image)
        index = self._manifest(
            repository,
            tag,
            "application/vnd.docker.distribution.manifest.list.v2+json,"
            "application/vnd.oci.image.index.v1+json,"
            "application/vnd.docker.distribution.manifest.v2+json",
        )
        if "manifests" in index:
            digest = ""
            for item in index["manifests"]:
                platform = item.get("platform", {})
                if platform.get("os") == "linux" and platform.get("architecture") == "amd64":
                    digest = item["digest"]
                    break
            if not digest:
                die(f"{image} 没有 linux/amd64 manifest")
            manifest = self._manifest(
                repository,
                digest,
                "application/vnd.docker.distribution.manifest.v2+json,"
                "application/vnd.oci.image.manifest.v1+json",
            )
        else:
            manifest = index

        meta_dir.mkdir(parents=True, exist_ok=True)
        (meta_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        config_digest = manifest["config"]["digest"]
        config_path = meta_dir / "config.json"
        self._blob(repository, config_digest, config_path)

        layer_paths: list[Path] = []
        for index, layer in enumerate(manifest["layers"]):
            digest = layer["digest"]
            name = f"{index:02d}-{digest.split(':', 1)[1][:12]}.tar.gz"
            path = layers_dir / name
            if path.is_file() and path.stat().st_size == layer["size"]:
                info(f"复用基础层 {name}")
            else:
                info(f"下载基础层 {name} ({layer['size'] // 1048576} MB)")
                self._blob(repository, digest, path)
            layer_paths.append(path)
        return layer_paths


def _split_image(image: str) -> tuple[str, str]:
    if "@" in image:
        name, digest = image.split("@", 1)
        return _normalize_repo(name), digest
    last = image.rsplit("/", 1)[-1]
    if ":" in last:
        name, tag = image.rsplit(":", 1)
    else:
        name, tag = image, "latest"
    return _normalize_repo(name), tag


def _normalize_repo(name: str) -> str:
    """Docker Hub 官方镜像补 ``library/``；已是 ``ns/repo`` 形式则原样返回。"""
    if "/" not in name:
        return f"library/{name}"
    return name


# ---------------------------------------------------------------------------
# 基础 rootfs
# ---------------------------------------------------------------------------


def _remove(path: Path) -> None:
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    except FileNotFoundError:
        pass


def flatten_layers(layers: list[Path], rootfs: Path) -> None:
    if rootfs.exists():
        shutil.rmtree(rootfs)
    rootfs.mkdir(parents=True)
    for layer in layers:
        info(f"展开 {layer.name}")
        with tarfile.open(layer, "r:gz") as tar:
            members = tar.getmembers()
            for member in members:
                base = os.path.basename(member.name.rstrip("/"))
                if base == ".wh..wh..opq":
                    directory = rootfs / os.path.dirname(member.name)
                    if directory.is_dir():
                        for child in directory.iterdir():
                            _remove(child)
                elif base.startswith(".wh."):
                    _remove(rootfs / os.path.dirname(member.name) / base[4:])

            def _filter(member: tarfile.TarInfo, _path: str):
                member = member.replace(
                    uid=os.getuid(), gid=os.getgid(), uname="", gname=""
                )
                return member

            for member in members:
                base = os.path.basename(member.name.rstrip("/"))
                if base.startswith(".wh.") or member.isdev():
                    continue
                try:
                    tar.extract(member, path=rootfs, filter=_filter)
                except TypeError:
                    try:
                        tar.extract(member, path=rootfs)
                    except (OSError, PermissionError) as exc:
                        warn(f"跳过 {member.name}：{exc}")
                except (OSError, PermissionError) as exc:
                    warn(f"跳过 {member.name}：{exc}")


def ensure_base(args: argparse.Namespace, work: Path) -> Path:
    rootfs = work / "base" / "rootfs"
    if (rootfs / "etc" / "os-release").is_file() and not args.refresh:
        info(f"复用基础 rootfs：{rootfs}")
        return rootfs
    layers_dir = work / "base" / "layers"
    meta_dir = work / "base" / "meta"
    layers_dir.mkdir(parents=True, exist_ok=True)
    registry = Registry(args.registry, args.auth_url)
    layers = registry.pull_amd64(args.base_image, layers_dir, meta_dir)
    flatten_layers(layers, rootfs)
    return rootfs


# ---------------------------------------------------------------------------
# wheels / node / fonts / rg
# ---------------------------------------------------------------------------


def _run(cmd: list[str], *, timeout: float | None = None) -> None:
    info("执行：" + " ".join(cmd))
    result = subprocess.run(cmd, check=False, timeout=timeout)
    if result.returncode != 0:
        die(f"命令失败（exit {result.returncode}）：{' '.join(cmd)}")


def ensure_wheels(args: argparse.Namespace, work: Path) -> Path:
    wheels = work / "wheels"
    wheels.mkdir(parents=True, exist_ok=True)
    marker = wheels / ".complete"
    if marker.is_file() and not args.refresh:
        info(f"复用 wheels：{len(list(wheels.glob('*.whl')))} 个")
        return wheels
    cmd = [
        sys.executable, "-m", "pip", "download",
        "--disable-pip-version-check",
        "--only-binary=:all:",
        "--python-version", PY_VERSION,
        "--implementation", "cp",
        "--abi", PY_ABI,
        "--index-url", args.pip_index_url,
        "-c", str(CONSTRAINTS),
        "-r", str(CONSTRAINTS),
        "-d", str(wheels),
    ]
    for platform in WHEEL_PLATFORMS:
        cmd.extend(["--platform", platform])
    _run(cmd)
    marker.write_text(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + "\n", encoding="utf-8")
    return wheels


def ensure_node(args: argparse.Namespace, work: Path) -> Path:
    node = work / "node" / "node"
    if node.is_file() and not args.refresh:
        info(f"复用 node：{node}")
        return node
    archive = work / "node" / f"node-{args.node_version}-linux-x64.tar.xz"
    url = args.node_url.format(version=args.node_version)
    _http_download(url, archive, {})
    node.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:xz") as tar:
        member = next(
            (m for m in tar.getmembers() if m.name.endswith("/bin/node")), None
        )
        if member is None:
            die(f"{archive} 中没有 bin/node")
        extracted = tar.extractfile(member)
        if extracted is None:
            die("无法读取 node 二进制")
        with node.open("wb") as handle:
            shutil.copyfileobj(extracted, handle)
    os.chmod(node, 0o755)
    return node


def _read_ar_member(deb: Path, wanted: str) -> bytes:
    data = deb.read_bytes()
    if not data.startswith(b"!<arch>\n"):
        die(f"不是 ar/deb 文件：{deb}")
    offset = 8
    while offset + 60 <= len(data):
        header = data[offset: offset + 60]
        name = header[:16].decode("ascii", errors="replace").strip().rstrip("/")
        size_field = header[48:58].decode("ascii", errors="replace").strip()
        try:
            size = int(size_field)
        except ValueError:
            break
        body_start = offset + 60
        body_end = body_start + size
        if name == wanted:
            return data[body_start:body_end]
        offset = body_end + (1 if body_end % 2 else 0)
    die(f"{deb} 中缺少 {wanted}")


def ensure_fonts(args: argparse.Namespace, work: Path) -> Path:
    fonts = work / "fonts"
    fonts.mkdir(parents=True, exist_ok=True)
    if all((fonts / name).is_file() for name in FONT_FILES) and not args.refresh:
        info("复用字体")
        return fonts
    deb = fonts / "fonts-noto-cjk.deb"
    _http_download(args.font_url, deb, {})
    payload = _read_ar_member(deb, "data.tar.xz")
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:xz") as tar:
        for member in tar.getmembers():
            name = os.path.basename(member.name)
            if name in FONT_FILES:
                extracted = tar.extractfile(member)
                if extracted is None:
                    continue
                with (fonts / name).open("wb") as handle:
                    shutil.copyfileobj(extracted, handle)
    missing = [name for name in FONT_FILES if not (fonts / name).is_file()]
    if missing:
        die(f"字体解包失败，缺少：{missing}")
    return fonts


def ensure_rg(args: argparse.Namespace, work: Path) -> Path:
    rg = work / "rg" / "rg"
    if rg.is_file() and not args.refresh:
        info(f"复用 rg：{rg}")
        return rg
    rg_dir = work / "rg"
    rg_dir.mkdir(parents=True, exist_ok=True)
    _run(
        [
            sys.executable, "-m", "pip", "download",
            "--disable-pip-version-check",
            "--no-deps",
            "--only-binary=:all:",
            "--python-version", PY_VERSION,
            "--implementation", "cp",
            "--abi", PY_ABI,
            "--platform", "manylinux_2_28_x86_64",
            "--index-url", args.pip_index_url,
            "-d", str(rg_dir),
            RG_PACKAGE,
        ]
    )
    wheel = next(rg_dir.glob("ripgrep_bin-*.whl"), None)
    if wheel is None:
        die("没有下载到 ripgrep-bin wheel")
    import zipfile

    with zipfile.ZipFile(wheel) as archive:
        candidate = next(
            (name for name in archive.namelist() if name.endswith("/scripts/rg")), None
        )
        if candidate is None:
            die("ripgrep wheel 中没有 scripts/rg")
        with archive.open(candidate) as source, rg.open("wb") as target:
            shutil.copyfileobj(source, target)
    os.chmod(rg, 0o755)
    return rg


# ---------------------------------------------------------------------------
# 应用 rootfs
# ---------------------------------------------------------------------------


def copy_tree(src: Path, dst: Path, ignore=None) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst, symlinks=True, ignore=ignore)


def install_file(src: Path, dst: Path, mode: int = 0o755) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    os.chmod(dst, mode)


def write_text(path: Path, text: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.chmod(path, mode)


def install_vendor(rootfs: Path, wheels: Path, args: argparse.Namespace) -> None:
    vendor = rootfs / "app" / "vendor"
    if vendor.exists():
        shutil.rmtree(vendor)
    cmd = [
        sys.executable, "-m", "pip", "install",
        "--disable-pip-version-check",
        "--no-index",
        "--find-links", str(wheels),
        "--only-binary=:all:",
        "--no-compile",
        "--python-version", PY_VERSION,
        "--implementation", "cp",
        "--abi", PY_ABI,
        "--target", str(vendor),
        "-c", str(CONSTRAINTS),
        "-r", str(CONSTRAINTS),
    ]
    for platform in WHEEL_PLATFORMS:
        cmd.extend(["--platform", platform])
    _run(cmd)
    bin_dir = vendor / "bin"
    if bin_dir.is_dir():
        for script in bin_dir.iterdir():
            if not script.is_file():
                continue
            try:
                text = script.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if text.startswith("#!"):
                first, _, rest = text.partition("\n")
                if "python" in first:
                    script.write_text("#!/usr/local/bin/python\n" + rest, encoding="utf-8")
            os.chmod(script, 0o755)
    for cache in vendor.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)


def install_app(rootfs: Path, args: argparse.Namespace, wheels: Path) -> None:
    app = rootfs / "app"
    if app.exists():
        shutil.rmtree(app)
    app.mkdir(parents=True)
    ignore = shutil.ignore_patterns(
        "__pycache__", "*.pyc", "*.pyo", ".DS_Store", ".git", "node_modules",
        ".venv", ".conda", ".conda-pkgs", "dist",
    )
    for name in ("src", "ohmo", "qaapp", "skills", "plugins", "scripts", "deploy"):
        copy_tree(REPO_ROOT / name, app / name, ignore=ignore)
    copy_tree(REPO_ROOT / "frontend" / "webapp", app / "frontend" / "webapp", ignore=ignore)
    dist_src = REPO_ROOT / "frontend" / "webapp" / "dist"
    dist_dst = app / "frontend" / "webapp" / "dist"
    if not (dist_src / "index.html").is_file():
        die("frontend/webapp/dist 不存在；请先构建前端")
    shutil.copytree(dist_src, dist_dst)
    config_src = REPO_ROOT / "config" / "backend"
    config_dst = app / "config" / "backend"
    config_dst.mkdir(parents=True)
    for name in (
        "agents.json",
        "dangerous-actions.json",
        "datasources.json",
        "webapp.json",
        ".env_prod",
    ):
        shutil.copy2(config_src / name, config_dst / name)
    for name in ("README.md", "LICENSE", "pyproject.toml"):
        shutil.copy2(REPO_ROOT / name, app / name)
    install_vendor(rootfs, wheels, args)
    for name, module in (("abcqa", "qaapp"), ("abc", "openharness"), ("abcag", "ohmo")):
        write_text(
            rootfs / "usr/local/bin" / name,
            f'#!/bin/sh\nexec python -m {module} "$@"\n',
            0o755,
        )
    write_text(
        app / "BUILD_INFO",
        "\n".join(
            [
                f"version={args.version}",
                f"commit={args.commit}",
                "platform=linux/amd64",
                "base=python:3.11-bookworm",
                f"created={args.created}",
                "mode=offline-single-layer",
                "",
            ]
        ),
    )


def install_system(
    rootfs: Path, *, node: Path, rg: Path, fonts: Path, args: argparse.Namespace
) -> None:
    install_file(node, rootfs / "usr/local/bin/node")
    install_file(rg, rootfs / "usr/local/bin/rg")
    font_dir = rootfs / "usr/share/fonts/opentype/noto"
    for name in FONT_FILES:
        install_file(fonts / name, font_dir / name, 0o644)

    passwd = rootfs / "etc/passwd"
    group = rootfs / "etc/group"
    shadow = rootfs / "etc/shadow"
    gshadow = rootfs / "etc/gshadow"

    def append(path: Path, line: str) -> None:
        text = path.read_text(encoding="utf-8")
        if f"{line.split(':', 1)[0]}:" not in text:
            path.write_text(text.rstrip("\n") + "\n" + line + "\n", encoding="utf-8")

    append(group, "app:x:1000:")
    append(passwd, "app:x:1000:1000:qaapp:/data/home:/bin/bash")
    append(shadow, "app:!:20300:0:99999:7:::")
    if gshadow.exists():
        append(gshadow, "app:!::")
    os.chmod(passwd, 0o644)
    os.chmod(group, 0o644)
    os.chmod(shadow, 0o600)
    if gshadow.exists():
        os.chmod(gshadow, 0o640)

    for rel in (
        "data",
        "data/home",
        "data/home/.cache",
        "data/home/.cache/matplotlib",
        "data/data",
        "data/logs",
        "data/sessions",
        "data/conversations",
    ):
        directory = rootfs / rel
        directory.mkdir(parents=True, exist_ok=True)
        os.chmod(directory, 0o750 if rel == "data" else 0o755)


# ---------------------------------------------------------------------------
# 组装镜像
# ---------------------------------------------------------------------------


def build_layer(rootfs: Path, layer: Path) -> None:
    def _filter(member: tarfile.TarInfo):
        name = member.name
        if name == "app" or name.startswith("app/") or name == "data" or name.startswith("data/"):
            member.uid = member.gid = 1000
            member.uname = member.gname = "app"
        else:
            member.uid = member.gid = 0
            member.uname = member.gname = "root"
        if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
            return None
        return member

    info("打包 rootfs layer ...")
    with tarfile.open(layer, "w", format=tarfile.PAX_FORMAT) as tar:
        for entry in sorted(rootfs.iterdir(), key=lambda item: item.name):
            tar.add(str(entry), arcname=entry.name, recursive=True, filter=_filter)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_image(layer: Path, output: Path, args: argparse.Namespace) -> None:
    diff_id = sha256_file(layer)
    config = {
        "architecture": "amd64",
        "os": "linux",
        "created": args.created,
        "config": {
            "Env": [
                "PATH=/app/vendor/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                "PYTHONPATH=/app/vendor:/app/src:/app",
                "PYTHONUNBUFFERED=1",
                "PYTHONDONTWRITEBYTECODE=1",
                "PIP_DISABLE_PIP_VERSION_CHECK=1",
                "LANG=C.UTF-8",
                "LC_ALL=C.UTF-8",
                "TZ=Asia/Shanghai",
                "HOME=/data/home",
                "OHMO_WORKSPACE=/data",
                "OPENHARNESS_DATA_DIR=/data/data",
                "OPENHARNESS_LOGS_DIR=/data/logs",
                "QAAPP_CONFIG_DIR=/app/config/backend",
                "QAAPP_STATIC_DIR=/app/frontend/webapp/dist",
                "MPLCONFIGDIR=/data/home/.cache/matplotlib",
            ],
            "Entrypoint": ["/app/deploy/docker/entrypoint.sh"],
            "Cmd": [],
            "User": "1000:1000",
            "WorkingDir": "/app",
            "ExposedPorts": {"8787/tcp": {}},
            "StopSignal": "SIGTERM",
            "Healthcheck": {
                "Test": [
                    "CMD-SHELL",
                    "curl -fsS http://127.0.0.1:8787/healthz >/dev/null || exit 1",
                ],
                "Interval": 30_000_000_000,
                "Timeout": 5_000_000_000,
                "StartPeriod": 90_000_000_000,
                "Retries": 3,
            },
            "Labels": {
                "org.opencontainers.image.title": "openharness-qaapp",
                "org.opencontainers.image.description": "OpenHarness qaapp offline image",
                "org.opencontainers.image.version": args.version,
                "org.opencontainers.image.revision": args.commit,
                "org.opencontainers.image.created": args.created,
            },
        },
        "rootfs": {"type": "layers", "diff_ids": [f"sha256:{diff_id}"]},
        "history": [{"created": args.created, "created_by": "openharness offline rootfs"}],
    }
    manifest = [
        {"Config": "config.json", "RepoTags": [args.tag], "Layers": ["layer.tar"]}
    ]
    output.parent.mkdir(parents=True, exist_ok=True)
    info(f"写入镜像 {output} ...")
    with tarfile.open(output, "w:gz") as tar:
        for name, payload in (
            ("manifest.json", json.dumps(manifest, indent=2)),
            ("config.json", json.dumps(config, indent=2)),
        ):
            data = payload.encode("utf-8")
            member = tarfile.TarInfo(name)
            member.size = len(data)
            member.mode = 0o644
            tar.addfile(member, io.BytesIO(data))
        tar.add(layer, arcname="layer.tar")


# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成 qaapp 离线 Docker 镜像 tar")
    parser.add_argument("--tag", default="")
    parser.add_argument("--output", default="")
    parser.add_argument("--work-dir", default=str(Path(tempfile.gettempdir()) / "openharness-offline"))
    parser.add_argument("--base-image", default=DEFAULT_BASE_IMAGE)
    parser.add_argument("--registry", default=DEFAULT_REGISTRY)
    parser.add_argument("--auth-url", default=DEFAULT_AUTH_URL)
    parser.add_argument("--pip-index-url", default=DEFAULT_PIP_INDEX)
    parser.add_argument("--node-version", default=DEFAULT_NODE_VERSION)
    parser.add_argument("--node-url", default=DEFAULT_NODE_URL)
    parser.add_argument("--font-url", default=DEFAULT_FONT_URL)
    parser.add_argument("--refresh", action="store_true", help="重新下载/展开基础镜像与依赖")
    parser.add_argument("--clean", action="store_true", help="构建前清空 work-dir")
    args = parser.parse_args(argv)

    args.version = read_version()
    args.commit = read_commit()
    args.created = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if not args.tag:
        args.tag = f"openharness-qaapp:{args.version}-offline"
    if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9._/-]*(?::[A-Za-z0-9_][A-Za-z0-9_.-]*)?$", args.tag):
        die(f"非法镜像 tag：{args.tag}")
    work = Path(args.work_dir).expanduser().resolve()
    if args.clean and work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)
    if not args.output:
        args.output = str(REPO_ROOT / "dist" / f"qaapp-image-{args.version}-offline-amd64.tar.gz")
    output = Path(args.output).expanduser().resolve()

    info(f"版本 {args.version} / commit {args.commit} / tag {args.tag}")
    base_rootfs = ensure_base(args, work)
    wheels = ensure_wheels(args, work)
    node = ensure_node(args, work)
    fonts = ensure_fonts(args, work)
    rg = ensure_rg(args, work)

    rootfs = work / "rootfs"
    if rootfs.exists():
        shutil.rmtree(rootfs)
    info("复制基础 rootfs（约 1 GB，请耐心等待）...")
    shutil.copytree(base_rootfs, rootfs, symlinks=True)
    install_app(rootfs, args, wheels)
    install_system(rootfs, node=node, rg=rg, fonts=fonts, args=args)

    layer = work / "layer.tar"
    build_layer(rootfs, layer)
    info(f"layer 大小：{layer.stat().st_size / 1048576:.1f} MB")
    build_image(layer, output, args)
    info(f"完成：{output}（{output.stat().st_size / 1048576:.1f} MB）")
    info(f"docker load -i {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
