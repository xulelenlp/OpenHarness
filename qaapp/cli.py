"""``abcqa`` —— qaapp 问答产品控制台入口。"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import typer

from ohmo.workspace import get_logs_dir, get_workspace_root, initialize_workspace
from qaapp.config import (
    apply_env_layers,
    config_status,
    get_agents_config_path,
    get_backend_config_dir,
    get_datasources_config_path,
    load_agent_registry,
    load_dangerous_actions,
    load_webapp_config,
    validate_agent_registry,
    validate_dangerous_actions,
)
from qaapp.data.datasources import (
    DataSourceConfigError,
    datasources_status,
    load_datasources_config,
)
from qaapp.server import QaWebService, configure_logging
from qaapp.workspace.maintenance import (
    apply_cleanup_plan,
    build_cleanup_plan,
    format_cleanup_plan,
    format_cleanup_result,
)

app = typer.Typer(
    name="abcqa",
    help="abcqa: 面向客户 APP/Web 端的问答产品服务（基于 OpenHarness）。",
    invoke_without_command=True,
    add_completion=False,
)
webapp_app = typer.Typer(name="webapp", help="运行/管理 Web 服务")
agents_app = typer.Typer(name="agents", help="查看/校验 Agent 注册表")
dangerous_app = typer.Typer(name="dangerous", help="校验高危操作配置")
datasource_app = typer.Typer(name="datasource", help="查看/检查 MySQL/Redis 数据源")

app.add_typer(webapp_app)
app.add_typer(agents_app)
app.add_typer(dangerous_app)
app.add_typer(datasource_app)

_WORKSPACE_HELP = "ohmo workspace 路径（默认 OHMO_WORKSPACE / ~/.ohmo）"
_CONFIG_DIR_HELP = (
    "项目配置目录（默认自动识别 <cwd>/config；用它可显式指定，"
    "等价于环境变量 QAAPP_CONFIG_DIR）。这是**随 git 管理**的可见配置位置"
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_REPO_TEMPLATES_DIR = _REPO_ROOT / "config" / "backend"
_PACKAGED_TEMPLATES_DIR = Path(__file__).resolve().parent / "_config" / "backend"


def _templates_dir() -> Path:
    """Return the canonical config templates (repo checkout or wheel)."""
    if _REPO_TEMPLATES_DIR.is_dir():
        return _REPO_TEMPLATES_DIR
    return _PACKAGED_TEMPLATES_DIR


def _pid_file(workspace: str | Path | None) -> Path:
    return get_workspace_root(workspace) / "webapp.pid"


def _log_file(workspace: str | Path | None) -> Path:
    return get_logs_dir(workspace) / "webapp.log"


def _pid_is_running(pid: int) -> bool:
    if sys.platform == "win32":
        return True  # Windows 上不做深度探测（简化处理）
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


@app.callback()
def _main(ctx: typer.Context) -> None:
    # Load centralized environment layers before any config is resolved.
    # Process environment variables still win (setdefault semantics).
    apply_env_layers(None, cwd=str(Path.cwd()))
    if ctx.invoked_subcommand is None:
        app.get_help(ctx)


# ---------------------------------------------------------------------------
# 顶层：config
# ---------------------------------------------------------------------------


@app.command("config")
def config_cmd(
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
    config_dir: str | None = typer.Option(None, "--config-dir", help=_CONFIG_DIR_HELP),
    cwd: str = typer.Option(str(Path.cwd()), "--cwd", help="项目工作目录（决定 <cwd>/config）"),
) -> None:
    """查看配置文件的**实际解析路径**、存在性与校验结果（含数据源）。"""
    apply_env_layers(workspace, cwd=cwd, config_dir=config_dir)
    print(
        json.dumps(
            config_status(workspace, cwd=cwd, config_dir=config_dir),
            ensure_ascii=False,
            indent=2,
        )
    )


@app.command("cleanup")
def cleanup_cmd(
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
    config_dir: str | None = typer.Option(None, "--config-dir", help=_CONFIG_DIR_HELP),
    cwd: str = typer.Option(str(Path.cwd()), "--cwd", help="项目工作目录（决定 <cwd>/config）"),
    older_than_days: float | None = typer.Option(
        None,
        "--older-than-days",
        help="清理闲置超过多少天的会话（默认取 webapp.json 的 idle_timeout_seconds）",
    ),
    keep_latest: int | None = typer.Option(
        None,
        "--keep-latest",
        help="始终保留最近 N 个会话（默认取 webapp.json 的 max_active_bundles）",
    ),
    apply: bool = typer.Option(False, "--apply", help="真正执行删除（默认只 dry-run 展示计划）"),
    as_json: bool = typer.Option(False, "--json", help="以 JSON 输出，便于脚本消费"),
) -> None:
    """清理闲置会话目录、会话快照与执行轨迹。

    默认只展示计划（dry-run）。确认后加 ``--apply``。只处理 12 位十六进制会话 ID
    对应的 qaapp 数据，不会碰 ohmo 个人助手的其它会话快照。
    """
    if older_than_days is not None and older_than_days < 0:
        raise typer.BadParameter("--older-than-days 不能为负数")
    if keep_latest is not None and keep_latest < 0:
        raise typer.BadParameter("--keep-latest 不能为负数")

    webapp = load_webapp_config(workspace, cwd=cwd, config_dir=config_dir)
    older_than_seconds = (
        None if older_than_days is None else float(older_than_days) * 86400
    )
    plan = build_cleanup_plan(
        workspace,
        webapp,
        older_than_seconds=older_than_seconds,
        keep_latest=keep_latest,
    )

    if not apply:
        if as_json:
            print(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2))
        else:
            print(format_cleanup_plan(plan))
        return

    result = apply_cleanup_plan(plan)
    if as_json:
        print(
            json.dumps(
                {"plan": plan.to_dict(), "result": result.to_dict()},
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(format_cleanup_result(result))
    if result.failed:
        raise typer.Exit(code=1)


@app.command("init")
def init_cmd(
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
    config_dir: str | None = typer.Option(None, "--config-dir", help=_CONFIG_DIR_HELP),
    cwd: str = typer.Option(str(Path.cwd()), "--cwd", help="项目工作目录（决定 <cwd>/config）"),
    force: bool = typer.Option(False, "--force", help="覆盖已存在的配置文件"),
) -> None:
    """将示例配置安装到项目 ``config/backend``（默认）或指定目录。"""
    root = initialize_workspace(workspace)
    if config_dir:
        explicit = Path(config_dir).expanduser().resolve()
        target_dir = explicit / "backend" if explicit.name == "config" else explicit
    elif workspace is not None:
        # Explicit workspace keeps the legacy flat layout for old deployments.
        target_dir = root
    else:
        target_dir = Path(cwd).expanduser().resolve() / "config" / "backend"
    target_dir.mkdir(parents=True, exist_ok=True)

    templates = _templates_dir()
    targets = {
        "settings.json": target_dir / "settings.json",
        ".env_dev": target_dir / ".env_dev",
        ".env_prod": target_dir / ".env_prod",
        "webapp.json": target_dir / "webapp.json",
        "agents.json": target_dir / "agents.json",
        "dangerous-actions.json": target_dir / "dangerous-actions.json",
        "datasources.json": target_dir / "datasources.json",
    }
    for name, target in targets.items():
        src = templates / name
        if not src.is_file():
            print(f"跳过 {name}（模板缺失：{src}）")
            continue
        if target.exists() and not force:
            print(f"跳过 {target}（已存在，使用 --force 覆盖）")
            continue
        target.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"写入 {target}")
    print(f"\n配置已就绪，workspace={root}，config_dir={target_dir}")
    print("提示：所有 Agent（含外部 Agent 的 endpoint / 请求体 / 响应解析）统一在 agents.json 里维护。")
    print("      开发/生产切换：cp config/backend/.env_dev config/backend/.env（或 .env_prod）。")
    print("      数据源（MySQL/Redis）在 datasources.json 维护；用 ${DB_HOST} 等占位符 + .env。")
    print("      检查：abcqa config；连通性：abcqa datasource check")


# ---------------------------------------------------------------------------
# webapp
# ---------------------------------------------------------------------------


@webapp_app.command("run")
def webapp_run_cmd(
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
    cwd: str = typer.Option(str(Path.cwd()), "--cwd", help="项目工作目录（决定 <cwd>/config）"),
    config_dir: str | None = typer.Option(None, "--config-dir", help=_CONFIG_DIR_HELP),
    host: str | None = typer.Option(None, "--host", help="覆盖 webapp.json 的 host"),
    port: int | None = typer.Option(None, "--port", help="覆盖 webapp.json 的 port"),
) -> None:
    """前台运行 Web 服务（Ctrl+C 退出）。"""
    # 先配日志：服务构造时就会打印「实际生效的配置文件」，不能被 WARNING 级别吞掉。
    configure_logging()
    initialize_workspace(workspace)
    service = QaWebService(workspace=workspace, cwd=cwd, config_dir=config_dir)
    service.run(host=host, port=port)


@webapp_app.command("start")
def webapp_start_cmd(
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
    cwd: str = typer.Option(str(Path.cwd()), "--cwd", help="项目工作目录（决定 <cwd>/config）"),
    config_dir: str | None = typer.Option(None, "--config-dir", help=_CONFIG_DIR_HELP),
) -> None:
    """后台启动 Web 服务（守护进程）。"""
    root = initialize_workspace(workspace)
    pid_file = _pid_file(root)
    if pid_file.exists():
        with contextlib.suppress(ValueError):
            if _pid_is_running(int(pid_file.read_text().strip())):
                print(f"webapp 已在运行 (pid={pid_file.read_text().strip()})")
                return

    _log_file(root).parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    pythonpath_entries = [str(_REPO_ROOT)]
    if env.get("PYTHONPATH"):
        pythonpath_entries.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_entries)

    popen_kwargs: dict = {
        "cwd": cwd,
        "env": env,
    }
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
        popen_kwargs["stdin"] = subprocess.DEVNULL
    else:
        popen_kwargs["start_new_session"] = True

    with _log_file(root).open("a", encoding="utf-8") as log_file:
        popen_kwargs["stdout"] = log_file
        popen_kwargs["stderr"] = log_file
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "qaapp",
                "webapp",
                "run",
                "--cwd",
                cwd,
                "--workspace",
                str(root),
                *(["--config-dir", str(config_dir)] if config_dir else []),
            ],
            **popen_kwargs,
        )
    pid_file.write_text(str(process.pid), encoding="utf-8")
    print(f"webapp started (pid={process.pid}, workspace={root})")


@webapp_app.command("stop")
def webapp_stop_cmd(
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
) -> None:
    """停止后台 Web 服务。"""
    pid_file = _pid_file(workspace)
    if not pid_file.exists():
        print("webapp 未在运行。")
        return
    try:
        pid = int(pid_file.read_text().strip())
    except ValueError:
        pid_file.unlink(missing_ok=True)
        print("pid 文件损坏，已清理。")
        return
    if _pid_is_running(pid):
        if sys.platform == "win32":
            with contextlib.suppress(Exception):
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, check=False)
        else:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGTERM)
        print(f"webapp stopped (pid={pid})")
    else:
        print("webapp 未在运行。")
    pid_file.unlink(missing_ok=True)


@webapp_app.command("status")
def webapp_status_cmd(
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
    config_dir: str | None = typer.Option(None, "--config-dir", help=_CONFIG_DIR_HELP),
    cwd: str = typer.Option(str(Path.cwd()), "--cwd", help="项目工作目录（决定 <cwd>/config）"),
) -> None:
    """查看 Web 服务运行状态。"""
    pid_file = _pid_file(workspace)
    running = False
    pid: int | None = None
    if pid_file.exists():
        with contextlib.suppress(ValueError):
            pid = int(pid_file.read_text().strip())
        if pid is not None and _pid_is_running(pid):
            running = True
    webapp = load_webapp_config(workspace, cwd=cwd, config_dir=config_dir)
    backend_dir = get_backend_config_dir(cwd, config_dir)
    print(
        json.dumps(
            {
                "running": running,
                "pid": pid,
                "host": webapp.host,
                "port": webapp.port,
                "provider_profile": webapp.provider_profile or "(inherit settings)",
                "workspace": str(get_workspace_root(workspace)),
                "backend_config_dir": str(backend_dir) if backend_dir else "",
                "agents_config": str(
                    get_agents_config_path(workspace, cwd=cwd, config_dir=config_dir)
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


# ---------------------------------------------------------------------------
# datasource
# ---------------------------------------------------------------------------


@datasource_app.command("show")
def datasource_show_cmd(
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
    config_dir: str | None = typer.Option(None, "--config-dir", help=_CONFIG_DIR_HELP),
    cwd: str = typer.Option(str(Path.cwd()), "--cwd", help="项目工作目录（决定 <cwd>/config）"),
) -> None:
    """打印数据源配置（密码脱敏）与实际解析路径。"""
    print(
        json.dumps(
            datasources_status(workspace, cwd=cwd, config_dir=config_dir),
            ensure_ascii=False,
            indent=2,
        )
    )


@datasource_app.command("check")
def datasource_check_cmd(
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
    config_dir: str | None = typer.Option(None, "--config-dir", help=_CONFIG_DIR_HELP),
    cwd: str = typer.Option(str(Path.cwd()), "--cwd", help="项目工作目录（决定 <cwd>/config）"),
) -> None:
    """只读检查 MySQL/Redis 连通性与关键表/键。"""
    path = get_datasources_config_path(workspace, cwd=cwd, config_dir=config_dir)
    print(f"# datasources.json: {path}")
    status = datasources_status(workspace, cwd=cwd, config_dir=config_dir)
    if status.get("errors"):
        for error in status["errors"]:
            print(f"✗ {error}")
        raise typer.Exit(code=1)
    try:
        config = load_datasources_config(workspace, cwd=cwd, config_dir=config_dir)
    except DataSourceConfigError as exc:
        print(f"✗ {exc}")
        raise typer.Exit(code=1)
    if not config.enabled:
        print("数据源未启用（datasources.json 中 mysql.enabled / redis.enabled 均为 false 或文件缺失）")
        return

    failed = False
    if config.mysql.enabled:
        from qaapp.data.mysql import MySqlClient

        client = MySqlClient(config.mysql)
        try:
            version = client.fetch_one("SELECT VERSION() AS version") or {}
            tables = client.fetch_one(
                "SELECT COUNT(*) AS count FROM information_schema.tables WHERE table_schema = DATABASE()"
            ) or {}
            print(
                f"MySQL OK：{config.mysql.host}:{config.mysql.port}/{config.mysql.database}，"
                f"version={version.get('version')}，tables={tables.get('count')}"
            )
            for table in (
                "zhss_index_basic_info",
                "zhss_result_data_qs",
                "zhss_result_data_ds",
                "qlyy_dimension",
                "zhss_org_new",
            ):
                try:
                    row = client.fetch_one(f"SELECT COUNT(*) AS count FROM {table}") or {}
                    print(f"  - {table}: {row.get('count')}")
                except Exception as exc:  # noqa: BLE001
                    print(f"  - {table}: 读取失败（{exc}）")
        except Exception as exc:  # noqa: BLE001
            failed = True
            print(f"✗ MySQL 检查失败：{exc}")
        finally:
            client.close()

    if config.redis.enabled:
        from qaapp.data.redis import RedisClient

        redis = RedisClient(config.redis)
        try:
            print(
                f"Redis OK：{config.redis.host}:{config.redis.port}/db0，"
                f"dbsize={redis.dbsize()}"
            )
            print(f"  白名单前缀：{'、'.join(config.redis.key_prefixes)}")
        except Exception as exc:  # noqa: BLE001
            failed = True
            print(f"✗ Redis 检查失败：{exc}")
        finally:
            redis.close()

    if failed:
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# agents
# ---------------------------------------------------------------------------


@agents_app.command("list")
def agents_list_cmd(
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
    config_dir: str | None = typer.Option(None, "--config-dir", help=_CONFIG_DIR_HELP),
    cwd: str = typer.Option(str(Path.cwd()), "--cwd", help="项目工作目录（决定 <cwd>/config）"),
) -> None:
    """列出注册表中所有 Agent（含 external 内联配置的来源文件）。"""
    print(f"# agents.json: {get_agents_config_path(workspace, cwd=cwd, config_dir=config_dir)}")
    registry = load_agent_registry(workspace, cwd=cwd, config_dir=config_dir)
    rows = []
    for agent in registry.agents:
        rows.append(
            {
                "id": agent.id,
                "kind": agent.kind,
                "name": agent.name,
                "enabled": agent.enabled,
                "description": agent.description[:80],
            }
        )
    print(json.dumps({"default_agent": registry.default_agent, "agents": rows}, ensure_ascii=False, indent=2))


@agents_app.command("validate")
def agents_validate_cmd(
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
    config_dir: str | None = typer.Option(None, "--config-dir", help=_CONFIG_DIR_HELP),
    cwd: str = typer.Option(str(Path.cwd()), "--cwd", help="项目工作目录（决定 <cwd>/config）"),
) -> None:
    """校验 agents.json（含 external 内联配置）。"""
    path = get_agents_config_path(workspace, cwd=cwd, config_dir=config_dir)
    print(f"# agents.json: {path}")
    errors = validate_agent_registry(workspace, cwd=cwd, config_dir=config_dir)
    if errors:
        for error in errors:
            print(f"✗ {error}")
        raise typer.Exit(code=1)
    print("agents.json 校验通过")


# ---------------------------------------------------------------------------
# dangerous
# ---------------------------------------------------------------------------


@dangerous_app.command("validate")
def dangerous_validate_cmd(
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
    config_dir: str | None = typer.Option(None, "--config-dir", help=_CONFIG_DIR_HELP),
    cwd: str = typer.Option(str(Path.cwd()), "--cwd", help="项目工作目录（决定 <cwd>/config）"),
) -> None:
    """校验 dangerous-actions.json。"""
    errors = validate_dangerous_actions(workspace, cwd=cwd, config_dir=config_dir)
    if errors:
        for error in errors:
            print(f"✗ {error}")
        raise typer.Exit(code=1)
    config = load_dangerous_actions(workspace, cwd=cwd, config_dir=config_dir)
    print(f"dangerous-actions.json 校验通过（{len(config.rules)} 条规则）")


@dangerous_app.command("show")
def dangerous_show_cmd(
    workspace: str | None = typer.Option(None, "--workspace", help=_WORKSPACE_HELP),
    config_dir: str | None = typer.Option(None, "--config-dir", help=_CONFIG_DIR_HELP),
    cwd: str = typer.Option(str(Path.cwd()), "--cwd", help="项目工作目录（决定 <cwd>/config）"),
) -> None:
    """打印当前高危操作策略。"""
    config = load_dangerous_actions(workspace, cwd=cwd, config_dir=config_dir)
    print(config.model_dump_json(indent=2))


# ---------------------------------------------------------------------------
# 模块入口
# ---------------------------------------------------------------------------


def main() -> None:
    app()


if __name__ == "__main__":
    main()
