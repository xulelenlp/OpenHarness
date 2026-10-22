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
`~/.openharness/settings.json` (legacy) or the centralized `config/backend/base/settings.json`.
See [`docs/CONFIGURATION.zh-CN.md`](docs/CONFIGURATION.zh-CN.md) for the full
schema.

### Q&A service

```bash
python -m qaapp init          # seed config templates into config/backend/base
python -m qaapp config        # show the config files actually in effect
python -m qaapp webapp run    # 0.0.0.0:8787
```

For local development, `scripts/dev_services.sh` runs the backend as a daemon
and the web client in the background, with logs under `~/.ohmo/logs/` and
`.openharness/run/`:

```bash
./scripts/dev_services.sh start     # background: backend :8787 + frontend :5173
./scripts/dev_services.sh status    # pid / port / health check
./scripts/dev_services.sh restart   # stop then start
./scripts/dev_services.sh stop      # stop both
```

The reference web client lives in [`frontend/webapp/`](frontend/webapp/).
Agent definitions, external-agent endpoints, and routing hints all live in
[`config/backend/base/agents.json`](config/backend/base/agents.json); the UI no longer asks users to
type a Bearer token (the server reads `webapp.json:api_tokens` and issues a
browser session cookie).

## Repository layout

```text
src/openharness/     core runtime: API clients, engine, tools, permissions,
                     plugins, MCP, skills, sessions, TUI
ohmo/                personal-agent layer: workspace package, memory, gateway
qaapp/               Q&A web service, layered into config/data/agents/
                     workspace/tools/server
frontend/terminal/   React terminal UI
frontend/webapp/     React web client for qaapp
frontend/autopilot-dashboard/
                     React/Vite dashboard for GitHub Pages
config/              centralized frontend/backend config (base/dev/prod/local)
tests/               automated tests (pytest; this is the CI surface)
tests/e2e/           opt-in real-model scenarios (excluded from default pytest)
scripts/             operational scripts; manual E2E suites live in scripts/e2e/
docs/                core config/customization, plus docs/qaapp/ for the service
```

## Maintenance

Automated tests belong in `tests/` and are the only suite CI runs:

```bash
ruff check src tests scripts qaapp
pytest -q                       # excludes the opt-in real_api marker
```

Real-model scenarios under `tests/e2e/` run only when explicitly requested:

```bash
OPENHARNESS_E2E_WORKSPACE=/path/to/sample-repo \
ANTHROPIC_API_KEY=... pytest tests/e2e -m real_api -q
```

Manual E2E checks that need real model calls, Docker, or an interactive TUI
live in [`scripts/e2e/`](scripts/README.md#手工-e2e-套件). They are intentionally not
part of CI.

qaapp keeps a per-conversation workspace under `~/.ohmo/conversations/`.
Retention is configurable in `webapp.json` and is applied through one command:

```bash
python -m qaapp cleanup --help     # dry-run by default; --apply to delete
```

## Documentation

- [qaapp deployment and API](qaapp/README.md)
- [qaapp documentation index](docs/qaapp/README.zh-CN.md)
- [qaapp architecture and extension contracts](docs/qaapp/ARCHITECTURE.zh-CN.md)
- [Documentation index](docs/README.md)
- [Configuration](docs/CONFIGURATION.zh-CN.md)
- [Customization and plugins](docs/CUSTOMIZATION.zh-CN.md)
- [Contributing](CONTRIBUTING.md)
- [Changelog](CHANGELOG.md)

## License

MIT. See [`LICENSE`](LICENSE).
