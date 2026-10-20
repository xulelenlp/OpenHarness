# OpenHarness

OpenHarness is an open-source Python agent harness for building Claude-style
coding assistants, a personal agent, and a configurable multi-agent Q&A
service. It ships a tool loop, permissions, plugins, MCP, skills, session
storage, and a React terminal UI.

## Entry points

| Command | What it runs | Start here |
| --- | --- | --- |
| `abc` | terminal coding assistant | `abc --help` |
| `abcag` | ohmo personal agent (channels, memory, workspace) | `abcag --help` |
| `abcqa` | Q&A web service (multi-agent routing, SSE, attachments, session workspace) | [`qaapp/README.md`](qaapp/README.md) |

## Quick start

```bash
git clone https://github.com/HKUDS/OpenHarness.git
cd OpenHarness
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

abc --help
abcag --help
abcqa --help
```

The first real model call needs a provider profile and credentials in
`~/.openharness/settings.json` (or the project-level `.openharness/settings.json`).
See [`docs/CONFIGURATION.zh-CN.md`](docs/CONFIGURATION.zh-CN.md) for the full
schema.

### Q&A service

```bash
python -m qaapp init          # seed example configs into the ohmo workspace
python -m qaapp config        # show the config files actually in effect
python -m qaapp webapp run    # 0.0.0.0:8787
```

The reference web client lives in [`frontend/webapp/`](frontend/webapp/).
Agent definitions, external-agent endpoints, and routing hints all live in
[`config/agents.json`](config/agents.json); the UI no longer asks users to
type a Bearer token (the server reads `webapp.json:api_tokens` and issues a
browser session cookie).

## Repository layout

```text
src/openharness/     core runtime: API clients, engine, tools, permissions,
                     plugins, MCP, skills, sessions, TUI
ohmo/                personal-agent layer: workspace, memory, gateway, storage
qaapp/               Q&A web service: REST/SSE/WS, routing, attachments,
                     run_script, maintenance/cleanup
frontend/terminal/   React terminal UI
frontend/webapp/     React web client for qaapp
config/              project-visible qaapp config (agents, etc.)
tests/               automated tests (pytest; this is the CI surface)
scripts/             operational scripts; manual E2E suites live in scripts/e2e/
docs/                configuration, customization, showcase, architecture
```

## Maintenance

Automated tests belong in `tests/` and are the only suite CI runs:

```bash
ruff check src tests scripts qaapp
pytest -q
```

Manual E2E checks that need real model calls, Docker, or an interactive TUI
live in [`scripts/e2e/`](scripts/e2e/README.md). They are intentionally not
part of CI.

qaapp keeps a per-conversation workspace under `~/.ohmo/conversations/`.
Retention is configurable in `webapp.json` and is applied through one command:

```bash
python -m qaapp cleanup --help     # dry-run by default; --apply to delete
```

## Documentation

- [qaapp deployment and API](qaapp/README.md)
- [qaapp architecture and extension contracts](docs/QAAPP_ARCHITECTURE.zh-CN.md)
- [Configuration](docs/CONFIGURATION.zh-CN.md)
- [Customization and plugins](docs/CUSTOMIZATION.zh-CN.md)
- [Contributing](CONTRIBUTING.md)
- [Changelog](CHANGELOG.md)

## License

MIT. See [`LICENSE`](LICENSE).
