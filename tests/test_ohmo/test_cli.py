import json
import logging
from pathlib import Path

from typer.testing import CliRunner

from ohmo.cli import _build_gateway_logging_handlers, app


def test_ohmo_help():
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "personal-agent app" in result.output
    assert "config" in result.output


def test_ohmo_init_and_doctor(tmp_path: Path):
    runner = CliRunner()
    workspace = tmp_path / ".ohmo-home"
    result = runner.invoke(app, ["init", "--workspace", str(workspace)])
    assert result.exit_code == 0, result.output
    assert str(workspace) in result.output
    assert (workspace / "gateway.json").is_file()

    doctor = runner.invoke(app, ["doctor", "--cwd", str(tmp_path), "--workspace", str(workspace)])
    assert doctor.exit_code == 0, doctor.output
    assert "ohmo doctor:" in doctor.output
    assert "workspace: ok" in doctor.output


def test_ohmo_init_is_idempotent_and_points_to_config(tmp_path: Path):
    runner = CliRunner()
    workspace = tmp_path / ".ohmo-home"
    first = runner.invoke(app, ["init", "--workspace", str(workspace)])
    assert first.exit_code == 0

    second = runner.invoke(app, ["init", "--workspace", str(workspace)])
    assert second.exit_code == 0
    assert "ohmo workspace already exists." in second.output
    assert "`abcag config`" in second.output


def test_ohmo_init_defaults_to_deny_all_remote_access(tmp_path: Path):
    runner = CliRunner()
    workspace = tmp_path / ".ohmo-home"
    result = runner.invoke(app, ["init", "--workspace", str(workspace)])
    assert result.exit_code == 0
    config = json.loads((workspace / "gateway.json").read_text(encoding="utf-8"))
    assert config["channel_configs"] == {}
    assert config["enabled_channels"] == []


def test_ohmo_config_sets_provider_profile_noninteractively(tmp_path: Path):
    runner = CliRunner()
    workspace = tmp_path / ".ohmo-home"
    assert runner.invoke(app, ["init", "--workspace", str(workspace)]).exit_code == 0

    result = runner.invoke(
        app, ["config", "--workspace", str(workspace), "--profile", "codex"]
    )
    assert result.exit_code == 0, result.output
    config = json.loads((workspace / "gateway.json").read_text(encoding="utf-8"))
    assert config["provider_profile"] == "codex"
    assert "Saved provider_profile=codex." in result.output


def test_gateway_logging_handlers_write_gateway_log_file(tmp_path: Path):
    workspace = tmp_path / ".ohmo-home"
    runner = CliRunner()
    result = runner.invoke(app, ["init", "--workspace", str(workspace)])
    assert result.exit_code == 0

    handlers = _build_gateway_logging_handlers(workspace, console=True, log_file=True)
    try:
        file_handlers = [handler for handler in handlers if isinstance(handler, logging.FileHandler)]
        console_handlers = [
            handler
            for handler in handlers
            if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler)
        ]
        assert len(file_handlers) == 1
        assert len(console_handlers) == 1

        record = logging.LogRecord(
            name="ohmo.gateway.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="GATEWAY_LOG_OK",
            args=(),
            exc_info=None,
        )
        file_handlers[0].emit(record)
        file_handlers[0].flush()

        log_path = workspace / "logs" / "gateway.log"
        assert "GATEWAY_LOG_OK" in log_path.read_text(encoding="utf-8")
    finally:
        for handler in handlers:
            handler.close()
