# Local Agentic Job Monitor (Ollama + MCP + Docker)

A self-hosted, zero-API-cost agentic stack that scrapes tech job listings from [DOU](https://jobs.dou.ua) and emails you a digest of new postings on a schedule. Runs entirely locally via Docker — no external AI API keys required.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     Docker Compose                       │
│                                                          │
│  ┌──────────┐      ┌──────────┐      ┌───────────────┐  │
│  │  ollama  │      │  agent   │      │  mcp-server   │  │
│  │          │      │          │─────►│               │  │
│  │ Mistral  │◄─────│ MCP      │ MCP  │ scrape_jobs() │  │
│  │ 7B (LLM) │      │ Client + │ SSE  │ send_email()  │  │
│  │          │      │ Scheduler│      │               │  │
│  └──────────┘      └──────────┘      └───────────────┘  │
│                                              │           │
│                                       ┌──────▼──────┐   │
│                                       │  state/     │   │
│                                       │  output/    │   │
│                                       └─────────────┘   │
└─────────────────────────────────────────────────────────┘
                                               │
                                          Gmail SMTP
                                               │
                                          Your inbox
```

## Agentic loop

Each scheduled run, the agent:

1. Connects to the MCP server and lists available tools
2. Sends the goal + tools to Ollama (local LLM)
3. The LLM decides which tool to call next
4. The agent executes the tool call via MCP and feeds the result back to the LLM
5. Repeats until the LLM calls `send_email` and stops

```
agent ──► Ollama: "find and email job listings"
            │
            ▼
         tool_call: scrape_jobs("JavaScript")
            │
agent ──► MCP server: scrape_jobs("JavaScript")
            │ returns: "Found 15 jobs, 8 new..."
            │
         tool_call: scrape_jobs("AI/ML")
            │   ...
            ▼
         tool_call: send_email(subject, intro)
            │
agent ──► MCP server: formats HTML, sends via Gmail, saves seen URLs
```

The LLM is in control — it decides what to call and when to stop, not hardcoded logic.

## Stack

| Container | Role |
|---|---|
| `ollama` | Serves the local LLM (Mistral 7B) on port 11434 |
| `agent` | Async MCP client + scheduler; drives the agentic loop |
| `mcp-server` | Exposes `scrape_jobs` and `send_email` tools over MCP/SSE |

## Prerequisites

- Docker + Docker Compose
- ~8GB free RAM
- A Gmail account with [2-Step Verification](https://myaccount.google.com/security) enabled
- A [Gmail App Password](https://myaccount.google.com/apppasswords)

## Setup

1. Clone the repo and copy the env file:

   ```bash
   cp .env.example .env  # then fill in your values
   ```

2. Start the stack:

   ```bash
   docker compose up -d
   ```

3. Pull the model (one-time, ~4GB):

   ```bash
   docker exec -it ollama ollama pull mistral
   ```

4. Trigger the first run and watch the agent work:

   ```bash
   docker compose restart agent
   docker compose logs -f agent
   ```

You'll see the LLM's tool calls in the logs. The first successful run sends an email and writes seen job URLs to `state/seen_urls.json` — subsequent runs only email new listings.

## Configuration

All config lives in `.env`:

| Variable | Purpose |
|---|---|
| `MODEL_NAME` | Ollama model to use (`mistral`, `llama3.1:8b`, etc.) |
| `JOB_CATEGORIES` | Comma-separated DOU categories to scrape |
| `RUN_INTERVAL_MINUTES` | How often the agent fires |
| `EMAIL_FROM` / `EMAIL_TO` | Gmail addresses |
| `EMAIL_PASSWORD` | Gmail App Password (16 chars) |

## Output

- `output/<timestamp>.txt` — LLM summary from each run
- `state/history.json` — full run history
- `state/seen_urls.json` — deduplication index (jobs already emailed)

## Extending

The natural next steps:
- **Multi-agent** — split into planner + worker agents communicating via shared state or a message queue
- **More MCP tools** — add tools for filtering by salary, keyword search, Telegram notifications
- **Stronger model** — swap Mistral for `llama3.1:8b` (better tool-calling) once GPU/RAM allows
