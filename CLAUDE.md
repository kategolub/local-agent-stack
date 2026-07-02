# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A self-hosted agent stack: an Ollama container serving a local LLM, plus a Python agent container that calls it on a configurable schedule. Zero API cost — everything runs locally.

## Stack layout

- `docker-compose.yml` — two services: `ollama` (model server on port 11434) and `agent` (scheduled task runner)
- `agent.py` — all agent logic: calls Ollama's `/api/generate`, writes per-run output to `/app/output/<timestamp>.txt`, appends to `/app/state/history.json`
- `Dockerfile` + `requirements.txt` — build the agent container (Python 3.11-slim, deps: `requests`, `schedule`)
- `.env` — sets `OLLAMA_HOST`, `MODEL_NAME`, `RUN_INTERVAL_MINUTES`; consumed by docker-compose

> **Note:** `docker-compose.yml` has `build: ./agent`, but `Dockerfile` and `agent.py` currently live at the repo root. Either move the files into an `agent/` subdirectory or change `build: .` to fix this before running.

## Running the stack

```bash
# Start both services
docker compose up -d

# Pull a model (one-time, pick one)
docker exec -it ollama ollama pull mistral     # good balance on 16GB+ RAM
docker exec -it ollama ollama pull phi3        # lighter, faster on modest hardware

# Restart agent after model is available
docker compose restart agent

# Tail agent logs
docker compose logs -f agent
```

Output files land in `agent/output/` on the host; full run history at `agent/state/history.json`.

## Configuration

All tunable values are environment variables (set in `.env`, passed through docker-compose):

| Variable | Default | Purpose |
|---|---|---|
| `OLLAMA_HOST` | `http://ollama:11434` | Ollama API endpoint |
| `MODEL_NAME` | `mistral` | Must match a pulled model |
| `RUN_INTERVAL_MINUTES` | `60` | How often the agent fires |

To change the agent's task, edit `TASK_PROMPT` directly in `agent.py`.

## Architecture notes

`agent.py` uses the `schedule` library (not system cron): it runs once on startup, then fires every `RUN_INTERVAL_MINUTES`. The main loop polls `schedule.run_pending()` every 15 seconds.

The Ollama call uses `stream: false` with a 120-second timeout. Errors are caught, logged, and written to the output file as `ERROR: <message>` so the run still gets recorded in history.

### Planned next stages (not yet implemented)

1. **Tool-calling** — parse structured model responses and execute functions (web search, file read, API calls), feeding results back into the conversation
2. **MCP** — add MCP servers as containers; agent becomes an MCP client so tools are protocol-standardized and swappable
3. **Multi-agent** — split into planner + worker agents communicating via shared JSON state or a message queue (e.g. Redis)
