# Self-IDE

A **local-first AI Agent IDE**: a single Python service exposes an OpenAI-compatible
proxy, a Goal engine, and parallel Subagent orchestration on your machine, with a
thin VS Code extension as the UI shell. Nothing leaves your box unless you explicitly
point the upstream gateway at a remote provider.

## What it is

| Component | Role |
|---|---|
| `server/self_ide_server.py` | HTTP service on **(port 19898)** — routes the dual-track model queue, quant-router, and OpenAI-compatible proxy to the SelfClaw Gateway |
| `server/goal_engine.py` | Goal engine — long-running goal loops that call the Gateway directly (no recursive self-calls) |
| `server/subagent_engine.py` | Subagent engine — parallel worker orchestration with persisted state |
| `server/verify_self_ide.py` | End-to-end regression verifier (16 checks, `--quick` for watchdogs) |
| `server/repair_desktop_plugin.py` | Idempotent config repair helper |
| `vscode-plugin/` | VS Code extension — status bar, health panel, Continue/Cline config writer. UI only; all logic stays server-side |

Cost metering (token accounting with atomic JSON persistence) lives inside the
server and is exposed via `/cost`, `/cost/reset`, `/cost/export`.

## Architecture

```
VS Code (self-ide extension)          Hermes Desktop plugin
   │ Node http → 127.0.0.1                │ gateway /api/plugins/self-ide
   ▼                                      ▼
┌─────────────────────────── :19898 ────────────────────────────┐
│ self_ide_server.py                                            │
│  ├─ dual-track model queue (read-only model-queue state)      │
│  ├─ quant-router      deterministic / fast / general tiers    │
│  ├─ goal_engine       goal loops ── direct Gateway call       │
│  ├─ subagent_engine   parallel subagents + state file         │
│  ├─ cost meter        token metering, atomic writes           │
│  └─ OpenAI-compatible proxy  /v1/chat/completions, /v1/models │
└───────────────┬───────────────────────────────────────────────┘
                ▼
   SelfClaw Gateway (default port 19899)   ← model pool / failover
```

## Configuration (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `HERMES_ROOT` | `~/hermes` | Agent workspace root. All Self-IDE artifacts (logs, cost meter, goal/subagent state) are written under this directory — never anywhere else |
| `GATEWAY_PORT` | `19899` | SelfClaw Gateway port used for the proxy / goal / subagent upstream |
| `SELF_IDE_PORT` | `19898` | Port this service listens on |
| `SELFCLAW_URL` | derived from `GATEWAY_PORT` | Full upstream base URL override |
| `ISOLATION_GUARD_DIR` | Isolation-guard directory scanned by verify_self_ide.py (default: `~/isolated-models`) |

The VS Code extension mirrors these: set `self-ide.hermesRoot` (empty ⇒
`HERMES_ROOT` env ⇒ `~/hermes`) and `self-ide.gatewayPort`.

## Quickstart

```bash
# 1. Point the agent at your workspace root
export HERMES_ROOT=~/your-agent-root
export GATEWAY_PORT=19899          # optional, default 19899

# 2. Start the service (idempotent --spawn mode also available)
python server/self_ide_server.py

#    Detached / self-heal friendly:
python server/self_ide_server.py --spawn

# 3. Verify
python server/verify_self_ide.py           # full  (exit 0 = all green)
python server/verify_self_ide.py --quick   # skipped real inference

# 4. Build & install the VS Code extension
cd vscode-plugin
npm install
npx tsc -p ./
npx @vscode/vsce package --no-dependencies
code --install-extension self-ide-1.0.0.vsix
```

Then in VS Code: `Ctrl+Shift+P` → **Self-IDE: Connect to Hermes**.

## Features

- **OpenAI-compatible proxy** — Any OpenAI client pointed at `http://127.0.0.1:19898/v1`
  rides your local gateway; with `routeViaGateway` the extension configures
  Continue/Cline the same way, so keys stay server-side.
- **Goal engine** — fire-and-iterate goals with persisted state; talks to the
  Gateway directly to avoid recursion deadlocks.
- **Subagents** — parallel fan-out workers with per-agent logs and a resumable
  state file.
- **Quant-router** — deterministic / fast / general model tiers picked by task
  suffix; CUDA / backtest-sensitive tasks stay on the deterministic tier.
- **Cost meter** — per-request token accounting, atomic JSON persistence,
  `/cost/reset` + CSV export.
- **Health you can trust** — health checks issue a real `chat/completions`
  probe, not just a port ping; local loopback requests bypass system proxies.

## Notes

- All runtime artifacts (`cost_meter.json`, `subagent_state.json`, `goal_state.json`,
  logs) are git-ignored at the repo root — this repo ships source only.
- Local loopback calls must bypass HTTP proxies (TUN/fake-ip setups can hijack
  `127.0.0.1`); the Node client does this natively, Python code uses an empty
  `ProxyHandler`.
- Default ports (19898 self-ide / 19899 gateway) are conventions, not constants —
  override via the env vars above.

## License

MIT — see [LICENSE](../LICENSE).
