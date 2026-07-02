"""
Agentic job assistant:
- Connects to the MCP server (tool provider)
- Sends a goal to Ollama (LLM)
- The LLM decides which tools to call (scrape_jobs, send_email) and when to stop
- Results are logged to /app/output and /app/state
"""

import os
import json
import time
import datetime
import asyncio
import traceback

import requests
import schedule
from mcp import ClientSession
from mcp.client.sse import sse_client

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
MODEL_NAME = os.environ.get("MODEL_NAME", "llama3.1:8b")
MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "http://mcp-server:8000/sse")
RUN_INTERVAL_MINUTES = int(os.environ.get("RUN_INTERVAL_MINUTES", "60"))
JOB_CATEGORIES = [
    c.strip()
    for c in os.environ.get("JOB_CATEGORIES", "JavaScript").split(",")
    if c.strip()
]

OUTPUT_DIR = "/app/output"
STATE_DIR = "/app/state"
STATE_FILE = os.path.join(STATE_DIR, "history.json")

MAX_ITERATIONS = 12  # guard against infinite tool-call loops

_categories_str = ", ".join(f'"{c}"' for c in JOB_CATEGORIES)
SYSTEM_PROMPT = f"""You are a job-search assistant for DOU, a Ukrainian tech job board.

Follow these steps in order:
1. Call scrape_jobs for each of these categories: {_categories_str}
2. Call send_email with a short subject line and a 2-3 sentence intro summarizing what you found.

Rules:
- Always call scrape_jobs for ALL categories before calling send_email
- Always call send_email as your last action — never respond with plain text
"""


def _mcp_tools_to_ollama(tools) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description or "",
                "parameters": t.inputSchema,
            },
        }
        for t in tools
    ]


def _call_ollama(messages: list[dict], tools: list[dict]) -> dict:
    resp = requests.post(
        f"{OLLAMA_HOST}/api/chat",
        json={
            "model": MODEL_NAME,
            "messages": messages,
            "tools": tools,
            "stream": False,
        },
        timeout=600,
    )
    resp.raise_for_status()
    return resp.json()["message"]


async def _run_agentic_loop() -> str:
    async with sse_client(MCP_SERVER_URL) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools_result = await session.list_tools()
            ollama_tools = _mcp_tools_to_ollama(tools_result.tools)

            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": "Find and email the latest job listings now."},
            ]

            for iteration in range(MAX_ITERATIONS):
                print(f"  [iteration {iteration + 1}] Calling Ollama...")
                try:
                    msg = await asyncio.to_thread(_call_ollama, messages, ollama_tools)
                except Exception as e:
                    traceback.print_exc()
                    return f"ERROR calling Ollama: {e}"
                messages.append(msg)

                tool_calls = msg.get("tool_calls") or []
                if not tool_calls:
                    # LLM finished — return its final text
                    return msg.get("content", "Agent finished with no summary.")

                for tc in tool_calls:
                    fn = tc["function"]
                    name = fn["name"]
                    args = fn.get("arguments", {})
                    if isinstance(args, str):
                        args = json.loads(args)

                    print(f"  → tool call: {name}({list(args.keys())})")
                    result = await session.call_tool(name, args)
                    result_text = (
                        result.content[0].text
                        if result.content
                        else ""
                    )
                    print(f"  ← result: {result_text[:120]}{'...' if len(result_text) > 120 else ''}")

                    messages.append({
                        "role": "tool",
                        "name": name,
                        "content": result_text,
                    })

            return "Reached max iterations without finishing."


def ensure_dirs():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(STATE_DIR, exist_ok=True)


def load_history() -> list:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return []


def save_history(history: list):
    with open(STATE_FILE, "w") as f:
        json.dump(history, f, indent=2)


def run_agent_once():
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    print(f"[{timestamp}] Starting agentic run. Model={MODEL_NAME}")

    try:
        summary = asyncio.run(_run_agentic_loop())
        status = "ok"
    except Exception as e:
        traceback.print_exc()
        # Unpack ExceptionGroup sub-exceptions (anyio TaskGroup errors)
        if hasattr(e, "exceptions"):
            for sub in e.exceptions:
                print(f"[{timestamp}] Sub-exception: {type(sub).__name__}: {sub}")
                traceback.print_exception(type(sub), sub, sub.__traceback__)
        summary = f"ERROR: {e}"
        status = "error"
        print(f"[{timestamp}] {summary}")

    out_path = os.path.join(OUTPUT_DIR, f"{timestamp.replace(':', '-')}.txt")
    with open(out_path, "w") as f:
        f.write(summary)

    history = load_history()
    history.append({"timestamp": timestamp, "status": status, "summary": summary})
    save_history(history)

    print(f"[{timestamp}] Done. Summary: {summary[:120]}")


def main():
    ensure_dirs()
    print(f"Agent starting. Model={MODEL_NAME}, MCP={MCP_SERVER_URL}, interval={RUN_INTERVAL_MINUTES}m")

    run_agent_once()
    schedule.every(RUN_INTERVAL_MINUTES).minutes.do(run_agent_once)

    while True:
        schedule.run_pending()
        time.sleep(15)


if __name__ == "__main__":
    main()
