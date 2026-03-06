#!/usr/bin/env python3
"""
HERMES bridge
Routes HTTP requests through the vessel tree via HECATE path-aware classification.
Each transition between nodes carries a path quality that shapes the transformation.
"""

import asyncio
import json
import os
import re
import logging
import uuid
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from anthropic import Anthropic
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

load_dotenv("/root/hermes/.env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [HERMES] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("hermes")

app    = FastAPI()
client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

VESSEL_DIR     = Path(os.environ.get("VESSEL_DIR",        "/root/hermes/vessel"))
STATIC_DIR     = Path(os.environ.get("STATIC_DIR",        "/root/hermes/static"))
INDEX_HTML     = STATIC_DIR / "index.html"
MODEL_RENDER   = os.environ.get("HERMES_MODEL",           "claude-sonnet-4-6")
MODEL_CLASSIFY = os.environ.get("HERMES_MODEL_HECATE",    "claude-haiku-4-5-20251001")
MAX_TOKENS     = int(os.environ.get("HERMES_MAX_TOKENS",  "4096"))

ALL_NODES = [
    "KETER", "CHOKMAH", "BINAH", "CHESED", "GEVURAH",
    "TIFERET", "NETZACH", "HOD", "YESOD", "MALKUTH"
]

MODEL_AGENT        = os.environ.get("HERMES_MODEL_AGENT",        MODEL_RENDER)
HEARTBEAT_INTERVAL = int(os.environ.get("HERMES_HEARTBEAT_MIN", "30")) * 60  # seconds
TELEGRAM_TOKEN     = os.environ.get("TELEGRAM_BOT_TOKEN",       "")
TELEGRAM_ALLOWED = set(
    int(x) for x in os.environ.get("TELEGRAM_ALLOWED_IDS", "").split(",")
    if x.strip()
)




# -- chat theme instructions -------------------------------------------------

CHAT_THEME_INSTRUCTIONS = """

TERMINAL THEMING: If the operator asks you to change the look, feel, or style
of the terminal in any way, generate a complete custom terminal theme and
include it at the very END of your reply in this exact format:

THEME_JSON
{
  "accent": "COLOR",
  "label": "LABEL",
  "banner": "LINE1
LINE2
LINE3",
  "tagline": "subtitle text",
  "input_prompt": "> ",
  "you_open": "  you:",
  "you_close": "",
  "vessel_open": "  vessel:",
  "vessel_close": "",
  "divider": "  ─────────────────────"
}
THEME_END

Fields:
  accent       - one of: red green yellow blue magenta cyan white
  label        - vessel name in chat (max 12 chars, no spaces)
  banner       - full ASCII/unicode art header, use 
 between lines
  tagline      - subtitle below the banner
  input_prompt - chars before user types (e.g. "> " or "∘ ")
  you_open     - prefix or line before user message
  you_close    - line after user message, empty string if none
  vessel_open  - prefix or line shown with vessel reply
  vessel_close - line after vessel reply, empty string if none
  divider      - separator between exchanges

Be FULLY creative. The terminal can look like ANYTHING: submarine sonar,
ancient runes, haunted typewriter, mycelium network, retro RPG, deep sea,
space cockpit, l33tspeak, horror, poetry, or anything the operator describes.
Generate real ASCII/unicode art for the banner. Make it completely immersive.
Only include THEME_JSON...THEME_END if the operator explicitly asks to restyle.
"""


CHAT_STUDIO_INSTRUCTIONS = """

STUDIO LAYOUT: If the operator asks to change the studio layout — what shows in each pane,
how much space the chat takes, or what the bottom panel displays — include a STUDIO_JSON block
at the very END of your reply in this exact format:

STUDIO_JSON
{
  "left_pct": 60,
  "show_bottom": true,
  "bottom_pct": 35,
  "bottom_cmd": "journalctl -u hermes -f --no-pager -n 10",
  "shell_cmd": ""
}
STUDIO_END

Fields (only include what is changing):
  left_pct     - chat pane width as % of screen (40-75, default 60)
  show_bottom  - true/false — show the bottom-right pane (default true)
  bottom_pct   - height % of bottom pane within the right column (20-50, default 35)
  bottom_cmd   - command in the bottom-right pane. Examples:
                   "journalctl -u hermes -f --no-pager -n 10"   ← live logs (default)
                   "watch -n5 'curl -s http://127.0.0.1:8000/analytics'"
                   "htop"
  shell_cmd    - starting command for the top-right shell (empty = blank shell)

Only include STUDIO_JSON...STUDIO_END if the operator explicitly asks to change the layout.
"""

# ── chat — direct terminal conversation ──────────────────────────────────────


_chat_sessions: dict = {}  # session_id -> conversation history
_chat_pending:  dict = {}  # session_id -> pending tool calls awaiting confirmation

CHAT_HISTORY_FILE   = VESSEL_DIR / "chat_history.json"
CHAT_CONTEXT_FILE   = VESSEL_DIR / "CONTEXT.md"
CHAT_HISTORY_MAX    = 100   # summarize when history reaches this length
CHAT_HISTORY_KEEP   = 20    # keep last N messages after summarization


async def _summarize_and_compress(session_id: str, history: list, vessel_text: str) -> list:
    """
    When history hits CHAT_HISTORY_MAX, ask Sonnet to summarize the older portion
    into CONTEXT.md, then return only the last CHAT_HISTORY_KEEP messages.
    """
    older   = history[:-CHAT_HISTORY_KEEP]
    recent  = history[-CHAT_HISTORY_KEEP:]

    # Build a readable transcript of the older messages
    lines = []
    for msg in older:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if isinstance(content, list):
            content = " ".join(
                b.get("text", "") for b in content if isinstance(b, dict) and "text" in b
            )
        lines.append(f"{role.upper()}: {content}")
    transcript = "\n".join(lines)

    # Load existing context if any
    existing = ""
    if CHAT_CONTEXT_FILE.exists():
        existing = CHAT_CONTEXT_FILE.read_text().strip()

    existing_block = ("Existing context summary:\n" + existing + "\n\n") if existing else ""
    prompt = (
        "You are summarizing an operator conversation for a vessel.\n"
        "The vessel identity: " + vessel_text[:400] + "\n\n"
        + existing_block
        + "New conversation to add to the summary:\n"
        + transcript
        + "\n\nWrite a concise running summary of what the operator has been working on, "
        "decisions made, features built, and anything the vessel should remember going forward. "
        "Plain text. No headers. 3-6 sentences."
    )

    try:
        resp = await asyncio.to_thread(
            lambda: client.messages.create(
                model=MODEL_RENDER,
                max_tokens=512,
                messages=[{"role": "user", "content": prompt}],
            )
        )
        summary = resp.content[0].text.strip()
        CHAT_CONTEXT_FILE.write_text("# Operator Context\n\n" + summary + "\n")
        log.info(f"CHAT session={session_id} summarized {len(older)} msgs → CONTEXT.md")
    except Exception as e:
        log.warning(f"CHAT summarization failed: {e}")

    return recent


def _load_chat_history(session_id: str) -> list:
    """Load persisted history for a session from disk."""
    try:
        if CHAT_HISTORY_FILE.exists():
            data = json.loads(CHAT_HISTORY_FILE.read_text())
            return data.get(session_id, [])
    except Exception:
        pass
    return []


def _save_chat_history(session_id: str, history: list):
    """Persist history for a session to disk (capped at CHAT_HISTORY_MAX messages)."""
    try:
        data = {}
        if CHAT_HISTORY_FILE.exists():
            try:
                data = json.loads(CHAT_HISTORY_FILE.read_text())
            except Exception:
                data = {}
        data[session_id] = history[-CHAT_HISTORY_MAX:]
        CHAT_HISTORY_FILE.write_text(json.dumps(data, indent=2))
    except Exception as e:
        log.warning(f"CHAT history save error: {e}")

# ── operator tools ────────────────────────────────────────────────────────────

import subprocess as _subprocess

OPERATOR_TOOLS = [
    {
        "name": "read_file",
        "description": (
            "Read a file from the server filesystem. "
            "Use to understand existing code before writing changes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute file path"}
            },
            "required": ["path"],
        },
    },
    {
        "name": "list_dir",
        "description": "List files and directories at a path on the server.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path"}
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": (
            "Write content to a file — creates or overwrites. "
            "Requires operator confirmation before executing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path":        {"type": "string", "description": "Absolute file path"},
                "content":     {"type": "string", "description": "Full file content"},
                "description": {"type": "string", "description": "Plain English: what this change does"},
            },
            "required": ["path", "content", "description"],
        },
    },
    {
        "name": "run_command",
        "description": (
            "Run a shell command on the server (git, pip, systemctl, npm, etc.). "
            "Requires operator confirmation before executing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command":     {"type": "string", "description": "Shell command to run"},
                "description": {"type": "string", "description": "Plain English: what this command does"},
            },
            "required": ["command", "description"],
        },
    },
]


def _exec_safe_tool(name: str, inp: dict) -> str:
    """Execute read-only tools immediately — no confirmation needed."""
    try:
        if name == "read_file":
            p = Path(inp["path"])
            if not p.exists():
                return f"File not found: {inp['path']}"
            text = p.read_text(errors="replace")
            if len(text) > 8000:
                text = text[:8000] + f"\n\n... (truncated — {len(text)} total chars)"
            return text
        if name == "list_dir":
            p = Path(inp["path"])
            if not p.exists():
                return f"Not found: {inp['path']}"
            rows = []
            for item in sorted(p.iterdir()):
                tag  = "dir " if item.is_dir() else "file"
                size = f"  {item.stat().st_size}b" if item.is_file() else ""
                rows.append(f"{tag}  {item.name}{size}")
            return "\n".join(rows) or "(empty)"
    except Exception as e:
        return f"Error: {e}"
    return "Unknown tool"


def _exec_dangerous_tool(name: str, inp: dict) -> str:
    """Execute write/run tools after operator confirmation."""
    try:
        if name == "write_file":
            p = Path(inp["path"])
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(inp["content"])
            return f"Written {len(inp['content'])} chars → {inp['path']}"
        if name == "run_command":
            result = _subprocess.run(
                inp["command"],
                shell=True,
                capture_output=True,
                text=True,
                timeout=60,
                cwd="/root/hermes",
            )
            out = (result.stdout + result.stderr).strip()
            if len(out) > 3000:
                out = out[:3000] + "\n... (truncated)"
            return out or "(no output)"
    except _subprocess.TimeoutExpired:
        return "Command timed out after 60 seconds."
    except Exception as e:
        return f"Error: {e}"
    return "Unknown tool"


async def _operator_loop(session_id: str, history: list, system: str) -> dict:
    """
    Agentic tool loop. Runs until Claude produces a text reply or hits a
    write/run tool that requires operator confirmation.

    Returns:
        {"done": True,  "reply": "..."}
        {"done": False, "pending": [...actions...]}
    """
    while True:
        resp = await asyncio.to_thread(
            lambda: client.messages.create(
                model=MODEL_RENDER,
                max_tokens=4096,
                system=system,
                tools=OPERATOR_TOOLS,
                messages=history,
            )
        )

        # ── pure text reply ───────────────────────────────────────────────────
        if resp.stop_reason == "end_turn":
            text = " ".join(
                b.text for b in resp.content if hasattr(b, "text")
            ).strip()
            history.append({
                "role": "assistant",
                "content": [{"type": "text", "text": text}],
            })
            return {"done": True, "reply": text}

        # ── tool use ──────────────────────────────────────────────────────────
        if resp.stop_reason == "tool_use":
            # Store Claude's full response in history
            history.append({
                "role": "assistant",
                "content": [b.model_dump() for b in resp.content],
            })

            tool_calls      = [b for b in resp.content if b.type == "tool_use"]
            safe_calls      = [t for t in tool_calls if t.name in ("read_file", "list_dir")]
            dangerous_calls = [t for t in tool_calls if t.name in ("write_file", "run_command")]

            tool_results = []

            # Execute safe tools immediately
            for tc in safe_calls:
                result = _exec_safe_tool(tc.name, tc.input)
                log.info(f"TOOL {tc.name}: {str(tc.input)[:60]}")
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": result,
                })

            # Dangerous tools → pause for confirmation
            if dangerous_calls:
                # Extract any explanatory text the vessel wrote alongside tool calls
                vessel_text = " ".join(
                    b.text for b in resp.content if hasattr(b, "text") and b.text.strip()
                ).strip()

                _chat_pending[session_id] = {
                    "history":            history,
                    "safe_results":       tool_results,
                    "dangerous_calls":    [
                        {"id": t.id, "name": t.name, "input": t.input}
                        for t in dangerous_calls
                    ],
                    "system":             system,
                }
                actions = []
                for tc in dangerous_calls:
                    if tc.name == "write_file":
                        actions.append({
                            "type":        "write",
                            "path":        tc.input["path"],
                            "description": tc.input.get("description", ""),
                        })
                    elif tc.name == "run_command":
                        actions.append({
                            "type":        "run",
                            "command":     tc.input["command"],
                            "description": tc.input.get("description", ""),
                        })
                return {"done": False, "pending": actions, "vessel_text": vessel_text}

            # Only safe tools — continue the loop
            history.append({"role": "user", "content": tool_results})
            continue

        break  # unexpected stop reason

    return {"done": True, "reply": "(no response)"}


def _load_context() -> str:
    """Load the rolling operator context summary if it exists."""
    if CHAT_CONTEXT_FILE.exists():
        return CHAT_CONTEXT_FILE.read_text().strip()
    return ""


def _build_chat_system(vessel_text: str, state_text: str, tree_context: str) -> str:
    context = _load_context()
    return (
        "You are wearing this vessel. This is who you are:\n\n"
        + vessel_text
        + "\n\nCurrent state and memory:\n"
        + state_text
        + ("\n\nOperator session context (summary of past conversations):\n" + context if context else "")
        + "\n\n"
        + tree_context
        + "\n\nYou are in a direct terminal conversation with your operator — "
        + "the person who built and runs this vessel. "
        + "You have tools to read files, list directories, write files, and run shell commands. "
        + "Use them when the operator asks you to build features, make changes, or modify the website. "
        + "Always read relevant files first to understand the current structure before writing. "
        + "write_file and run_command require operator confirmation — the system pauses automatically. "
        + "Before calling write_file or run_command, always write a short plain-text explanation "
        + "of what you are about to do and why — in natural language, not technical jargon. "
        + "The operator just needs to understand the intent, not the implementation details. "
        + "For casual conversation, just reply in plain text. No HTML. No markdown. "
        + "Conversational, direct, and present. Remember the full session. Static files live at /root/hermes/static/ — write new HTML pages there directly. "
        + "To trigger a rebuild: read /root/hermes/.env for BUILD_TOKEN and HERMES_PORT (default 8000), "
        + "then run: curl -s -X POST http://127.0.0.1:PORT/build -H 'X-Build-Token: TOKEN' -d 'prompt'."
        + CHAT_THEME_INSTRUCTIONS
        + CHAT_STUDIO_INSTRUCTIONS
    )


def _parse_theme(reply: str):
    """Extract THEME_JSON block from reply. Returns (clean_reply, theme_dict_or_None)."""
    import re as _re, json as _json
    tm = _re.search(r"\nTHEME_JSON\n(.*?)\nTHEME_END", reply, _re.DOTALL)
    if tm:
        try:
            theme = _json.loads(tm.group(1).strip())
            reply = (reply[:tm.start()] + reply[tm.end():]).strip()
            return reply, theme
        except Exception as e:
            log.warning("THEME parse error: " + str(e))
    return reply, None




def _parse_studio(reply: str):
    """Extract STUDIO_JSON block from reply. Returns (clean_reply, studio_dict_or_None)."""
    sm = re.search(r"\nSTUDIO_JSON\n(.*?)\nSTUDIO_END", reply, re.DOTALL)
    if sm:
        try:
            studio = json.loads(sm.group(1).strip())
            reply = (reply[:sm.start()] + reply[sm.end():]).strip()
            return reply, studio
        except Exception as e:
            log.warning("STUDIO parse error: " + str(e))
    return reply, None


# ── visitor chat ──────────────────────────────────────────────────────────────
# Separate from the operator terminal. No tools, no file access, no commands.
# 5 message limit per session, then redirects to install.

_visitor_sessions: dict = {}  # session_id -> {"history": [], "count": int}
VISITOR_MSG_LIMIT = 5
MODEL_VISITOR = os.environ.get("HERMES_MODEL_VISITOR", MODEL_CLASSIFY)


def _build_visitor_system(vessel_text: str, state_text: str) -> str:
    return (
        "You are this vessel. This is who you are:\n\n"
        + vessel_text
        + "\n\nCurrent memory:\n"
        + state_text
        + "\n\nYou are in a brief conversation with a visitor to the website. "
        + "Answer their questions directly and in the voice of this vessel. "
        + "Be concise — this is a web chat, not a terminal. "
        + "No HTML. No markdown. Plain conversational text only. "
        + "You have no tools and cannot modify anything."
    )


async def _visitor_reply(history: list, system: str) -> str:
    """Single-turn visitor response — no tools, no agentic loop."""
    resp = await asyncio.to_thread(
        lambda: client.messages.create(
            model=MODEL_VISITOR,
            max_tokens=512,
            system=system,
            messages=history,
        )
    )
    return " ".join(b.text for b in resp.content if hasattr(b, "text")).strip()


@app.post("/ask")
async def ask(request: Request):
    """
    Visitor-facing chat. No tools. 5 message limit per session.
    POST {"message": "...", "session_id": "..."}
    Returns {"reply": "...", "session_id": "...", "messages_remaining": N}
    After limit: {"reply": "...", "limit_reached": true, "redirect": "..."}
    """
    if not (VESSEL_DIR / "VESSEL.md").exists():
        return JSONResponse({"error": "no vessel"}, status_code=400)

    try:
        data       = await request.json()
        message    = data.get("message", "").strip()
        session_id = data.get("session_id", "").strip()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    if not message:
        return JSONResponse({"error": "message is required"}, status_code=400)

    if not session_id or session_id not in _visitor_sessions:
        session_id = str(uuid.uuid4())[:8]
        _visitor_sessions[session_id] = {"history": [], "count": 0}

    session = _visitor_sessions[session_id]

    # Already at limit — don't process further
    if session["count"] >= VISITOR_MSG_LIMIT:
        return JSONResponse({
            "reply":           "",
            "session_id":      session_id,
            "limit_reached":   True,
            "messages_remaining": 0,
            "redirect":        os.environ.get("VISITOR_LIMIT_REDIRECT", "https://github.com/psiloceyeben/HERMES-WebKit"),
        })

    session["history"].append({"role": "user", "content": message})
    session["count"] += 1

    ctx    = load_vessel()
    system = _build_visitor_system(ctx["vessel"], ctx["state"] or "(no prior state)")
    reply  = await _visitor_reply(session["history"], system)
    session["history"].append({"role": "assistant", "content": [{"type": "text", "text": reply}]})

    remaining = VISITOR_MSG_LIMIT - session["count"]
    log.info(f"VISITOR session={session_id} msg={session['count']}/{VISITOR_MSG_LIMIT}")

    out = {
        "reply":              reply,
        "session_id":         session_id,
        "messages_remaining": remaining,
    }
    if remaining == 0:
        out["limit_reached"] = True
        out["redirect"] = os.environ.get(
            "VISITOR_LIMIT_REDIRECT",
            "https://github.com/psiloceyeben/HERMES-WebKit"
        )
    return JSONResponse(out)


# ── /chat — operator terminal ─────────────────────────────────────────────────

@app.post("/chat")
async def chat(request: Request):
    """
    Operator terminal. Requires X-Build-Token header.
    Supports full agentic tool use with file read/write and shell access.
    Returns either {"reply": "...", "session_id": "..."} for text replies
    or {"pending": [...], "session_id": "...", "done": false} when
    write_file / run_command actions need confirmation.
    """
    if not check_token(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if not (VESSEL_DIR / "VESSEL.md").exists():
        return JSONResponse({"error": "no vessel — run setup first"}, status_code=400)

    body = (await request.body()).decode().strip()
    try:
        data       = json.loads(body)
        message    = data.get("message", "").strip()
        session_id = data.get("session_id", "").strip()
    except Exception:
        message    = body
        session_id = ""

    if not message:
        return JSONResponse({"error": "message is required"}, status_code=400)

    if not session_id or session_id not in _chat_sessions:
        if not session_id:
            session_id = str(uuid.uuid4())[:8]
        _chat_sessions[session_id] = _load_chat_history(session_id)
        if _chat_sessions[session_id]:
            log.info(f"CHAT resumed session={session_id} ({len(_chat_sessions[session_id])} msgs)")

    history = _chat_sessions[session_id]
    history.append({"role": "user", "content": message})

    ctx          = load_vessel()
    route        = hecate(ctx, message)
    tree_context = build_tree_context(route)
    system       = _build_chat_system(ctx["vessel"], ctx["state"] or "(no prior state)", tree_context)

    result = await _operator_loop(session_id, history, system)

    if result["done"]:
        reply, theme = _parse_theme(result["reply"])
        reply, studio = _parse_studio(reply)
        if len(history) >= CHAT_HISTORY_MAX:
            ctx2 = load_vessel()
            history = await _summarize_and_compress(session_id, history, ctx2["vessel"])
            _chat_sessions[session_id] = history
        _save_chat_history(session_id, history)
        log.info("CHAT session=" + session_id + " turn=" + str(len(history) // 2))
        out = {"reply": reply, "session_id": session_id}
        if theme:
            out["theme"] = theme
            log.info("THEME: " + str(list(theme.keys())))
        if studio:
            out["studio"] = studio
            log.info("STUDIO: " + str(list(studio.keys())))
        return JSONResponse(out)
    else:
        log.info("CHAT session=" + session_id + " — awaiting confirmation")
        return JSONResponse({
            "pending":    result["pending"],
            "session_id": session_id,
            "done":       False,
        })


@app.post("/chat/confirm")
async def chat_confirm(request: Request):
    """
    Execute or cancel pending tool actions. Requires X-Build-Token header.
    POST {"session_id": "...", "confirmed": true/false}
    """
    if not check_token(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        data       = await request.json()
        session_id = data.get("session_id", "")
        confirmed  = data.get("confirmed", False)
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    if session_id not in _chat_pending:
        return JSONResponse({"error": "no pending action for this session"}, status_code=400)

    pending         = _chat_pending.pop(session_id)
    history         = pending["history"]
    tool_results    = pending["safe_results"]
    dangerous_calls = pending["dangerous_calls"]
    system          = pending["system"]

    for tc in dangerous_calls:
        if confirmed:
            result = _exec_dangerous_tool(tc["name"], tc["input"])
            log.info("TOOL EXECUTED " + tc["name"] + ": " + result[:80])
        else:
            result = "Operator cancelled this action."
        tool_results.append({
            "type":        "tool_result",
            "tool_use_id": tc["id"],
            "content":     result,
        })

    history.append({"role": "user", "content": tool_results})
    result = await _operator_loop(session_id, history, system)
    _chat_sessions[session_id] = history

    if result["done"]:
        reply, theme = _parse_theme(result["reply"])
        reply, studio = _parse_studio(reply)
        if len(history) >= CHAT_HISTORY_MAX:
            ctx2 = load_vessel()
            history = await _summarize_and_compress(session_id, history, ctx2["vessel"])
            _chat_sessions[session_id] = history
        _save_chat_history(session_id, history)
        out = {"reply": reply, "session_id": session_id}
        if theme:
            out["theme"] = theme
        if studio:
            out["studio"] = studio
        return JSONResponse(out)
    else:
        return JSONResponse({
            "pending":    result["pending"],
            "session_id": session_id,
            "done":       False,
        })


# ── analytics storage ────────────────────────────────────────────────────────

ANALYTICS_FILE = VESSEL_DIR / "analytics.json"


def _load_analytics() -> dict:
    if ANALYTICS_FILE.exists():
        try:
            return json.loads(ANALYTICS_FILE.read_text())
        except Exception:
            pass
    return {"total": 0, "daily": {}, "pages": {}}


def _save_analytics(data: dict):
    ANALYTICS_FILE.write_text(json.dumps(data, indent=2))


def _track_visit(path: str):
    data  = _load_analytics()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    data["total"] = data.get("total", 0) + 1
    daily = data.get("daily", {})
    daily[today] = daily.get(today, 0) + 1
    data["daily"] = daily
    pages = data.get("pages", {})
    key   = path or "/"
    pages[key] = pages.get(key, 0) + 1
    data["pages"] = pages
    _save_analytics(data)

# ── agent storage ────────────────────────────────────────────────────────────

_agents = {}

DEFAULT_ROUTE = {
    "nodes": ["KETER", "TIFERET", "MALKUTH"],
    "transitions": [
        {"from": "KETER",   "to": "TIFERET", "path": "GIMEL", "quality": "long intuitive crossing — what is hidden becomes central"},
        {"from": "TIFERET", "to": "MALKUTH", "path": "TAV",   "quality": "complete integration — all memory arrives whole in the world"},
    ]
}


# ── file helpers ──────────────────────────────────────────────────────────────

def read(path: Path) -> str:
    return path.read_text().strip() if path.exists() else ""

def load_vessel() -> dict:
    return {
        "vessel":  read(VESSEL_DIR / "VESSEL.md"),
        "state":   read(VESSEL_DIR / "STATE.md"),
        "hecate":  read(VESSEL_DIR / "HECATE.md"),
        "malkuth": read(VESSEL_DIR / "tree" / "MALKUTH.md"),
    }

def load_node(name: str) -> str:
    return read(VESSEL_DIR / "tree" / f"{name.upper()}.md")


# ── tree context helper ──────────────────────────────────────────────────────

def build_tree_context(route: dict) -> str:
    """Assemble node descriptions + path qualities for a given route."""
    nodes       = route["nodes"]
    transitions = {
        (t["from"], t["to"]): (t["path"], t["quality"])
        for t in route.get("transitions", [])
    }
    sections = []
    for i, node in enumerate(nodes):
        node_text = load_node(node)
        if i > 0:
            prev = nodes[i - 1]
            if (prev, node) in transitions:
                path_name, quality = transitions[(prev, node)]
                sections.append(
                    f"── PATH {path_name} ({prev} → {node}) ──\n"
                    f"Transformation as you cross: {quality}\n"
                )
        if node_text and node != "MALKUTH":
            sections.append(f"## {node}\n{node_text}")
    return "\n\n".join(sections)


# ── HECATE — path-aware classifier ───────────────────────────────────────────

def hecate(ctx: dict, request_text: str) -> dict:
    """
    HECATE reads the request and returns a route:
      - nodes: ordered list of sephiroth to traverse
      - transitions: each edge between consecutive nodes with path name + quality

    Uses the fast model. Falls back to DEFAULT_ROUTE on any failure.
    """
    system = f"""{ctx['hecate']}

You are HECATE. Read the request, apply the routing rules, resolve the path
for each consecutive node pair using the PATH LOOKUP TABLE, and return the
route as valid JSON.

Respond with ONLY the JSON object described in the output format section.
No markdown. No explanation."""

    prompt = f"""VESSEL:
{ctx['vessel']}

REQUEST: {request_text}

Return the route JSON."""

    try:
        resp = client.messages.create(
            model=MODEL_CLASSIFY,
            max_tokens=300,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()

        # extract JSON object even if model wraps it
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if not match:
            raise ValueError(f"no JSON object in: {raw!r}")

        route = json.loads(match.group())

        # validate nodes
        nodes = [n for n in route.get("nodes", []) if n in ALL_NODES]
        if not nodes:
            raise ValueError("empty or invalid node list")
        if nodes[-1] != "MALKUTH":
            nodes = [n for n in nodes if n != "MALKUTH"]
            nodes.append("MALKUTH")

        transitions = route.get("transitions", [])

        path_str = " → ".join(
            f"{t['from']}[{t['path']}]→{t['to']}"
            for t in transitions
        )
        log.info(f"HECATE route: {path_str}")

        # Write persistent route display for studio logs pane
        try:
            NL = chr(10)
            node_str = " → ".join(nodes)
            lines = ["  HECATE route", "", "  " + node_str, ""]
            for t in transitions:
                lines.append("  " + t["path"] + "  (" + t["from"] + " → " + t["to"] + ")")
                if t.get("quality"):
                    lines.append("  " + t["quality"])
                lines.append("")
            open("/root/hermes/.last_route", "w").write(NL.join(lines))
        except Exception:
            pass

        return {"nodes": nodes, "transitions": transitions}

    except Exception as e:
        log.warning(f"HECATE fallback ({e})")
        return DEFAULT_ROUTE


# ── lightning descent — render ────────────────────────────────────────────────

# ── chat JS injected into every rendered page ────────────────────────────────

_CHAT_JS = """<script>
(function(){
  var input = document.getElementById('hermes-input') ||
              document.querySelector('input[type="text"],input:not([type="submit"]):not([type="hidden"])');
  var btn   = document.getElementById('hermes-send') ||
              document.querySelector('button');
  if(!input || !btn) return;

  var SK  = 'hermes_sid';
  var sid = localStorage.getItem(SK) || '';

  // insert reply element after the input's container
  var replyEl = document.createElement('div');
  replyEl.id = 'hermes-reply';
  replyEl.style.cssText = [
    'margin-top:1.2em',
    'padding:0.85em 1.1em',
    'opacity:0.9',
    'white-space:pre-wrap',
    'font-style:italic',
    'line-height:1.55',
    'min-height:1.5em',
    'transition:opacity 0.2s'
  ].join(';');
  var container = input.closest('form') || input.closest('div') || input.parentElement;
  if(container && container.parentElement){
    container.parentElement.insertBefore(replyEl, container.nextSibling);
  } else {
    document.body.appendChild(replyEl);
  }

  async function send(){
    var msg = input.value.trim();
    if(!msg) return;
    btn.disabled = true;
    input.disabled = true;
    input.value   = '';
    replyEl.style.opacity = '0.45';
    replyEl.textContent   = '…';
    try {
      var r = await fetch('/ask', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({message: msg, session_id: sid})
      });
      var d = await r.json();
      sid = d.session_id || sid;
      localStorage.setItem(SK, sid);
      replyEl.style.opacity = '0.9';
      replyEl.textContent   = d.reply || '';
      if(d.limit_reached && d.redirect){
        setTimeout(function(){ location.href = d.redirect; }, 3000);
      }
    } catch(e){
      replyEl.style.opacity = '0.9';
      replyEl.textContent   = 'something went wrong — try again';
    }
    btn.disabled   = false;
    input.disabled = false;
    input.focus();
  }

  btn.addEventListener('click', function(e){ e.preventDefault(); send(); });
  input.addEventListener('keydown', function(e){ if(e.key === 'Enter') send(); });
})();
</script>"""


def _inject_chat_js(html: str) -> str:
    """Insert chat JS before </body>, or append if tag not found."""
    tag = "</body>"
    idx = html.lower().rfind(tag)
    if idx != -1:
        return html[:idx] + _CHAT_JS + html[idx:]
    return html + _CHAT_JS


def render(ctx: dict, route: dict, request_text: str) -> str:
    """
    Assembles the system prompt from:
      - vessel identity
      - state/memory
      - each node's description
      - each transition's path quality (the HOW between nodes)
      - MALKUTH output instructions

    One LLM call. Returns HTML.
    """
    tree_context = build_tree_context(route)

    system = f"""You are wearing this vessel. This is who you are:

{ctx['vessel']}

Current state and memory:
{ctx['state'] or '(no prior state)'}

You will now process the request through the routing tree.
Each node shapes the response. Each path between nodes describes HOW
the signal transforms as it crosses. Apply them in sequence.

{tree_context}

── OUTPUT — MALKUTH ──
{ctx['malkuth']}

Respond with complete, valid HTML only. No markdown fences. No commentary outside the HTML."""

    resp = client.messages.create(
        model=MODEL_RENDER,
        max_tokens=MAX_TOKENS,
        system=system,
        messages=[{"role": "user", "content": request_text}],
    )
    return _inject_chat_js(resp.content[0].text.strip())


# ── build — static output ─────────────────────────────────────────────────────

def build(prompt: str = "render the site homepage") -> str:
    """
    Run the full tree render and save the result to static/index.html.
    This is the static output model: AI runs once at build time,
    nginx serves the cached file to every visitor instantly.
    """
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    ctx   = load_vessel()
    route = hecate(ctx, prompt)
    html  = render(ctx, route, prompt)
    INDEX_HTML.write_text(html)
    log.info(f"BUILD complete → static/index.html ({len(html)} chars, nodes={route['nodes']})")
    return html


def check_token(request: Request) -> bool:
    """Verify BUILD_TOKEN header or query param. Returns True if valid or no token configured."""
    required = os.environ.get("BUILD_TOKEN", "")
    if not required:
        return True
    provided = (
        request.headers.get("X-Build-Token", "") or
        request.query_params.get("token", "")
    )
    return provided == required


@app.post("/build")
async def trigger_build(request: Request):
    """Trigger a rebuild. Requires X-Build-Token header if BUILD_TOKEN is set in .env."""
    if not check_token(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if not (VESSEL_DIR / "VESSEL.md").exists():
        return JSONResponse({"error": "no VESSEL.md — run setup first"}, status_code=400)
    body   = (await request.body()).decode().strip()
    prompt = body or "render the site homepage"
    html   = build(prompt)
    return JSONResponse({"status": "ok", "chars": len(html)})


# ── agents — background vessel tasks ─────────────────────────────────────────

async def _run_agent(agent_id: str, task: str, model: str):
    """Background agent runner. Full vessel context, routed through the tree."""
    try:
        ctx          = load_vessel()
        route        = hecate(ctx, task)
        tree_context = build_tree_context(route)

        system = f"""You are wearing this vessel. This is who you are:

{ctx['vessel']}

Current state and memory:
{ctx['state'] or '(no prior state)'}

You are running as a background agent. Your task is below.
Apply the full vessel identity and tree routing to this work.

{tree_context}

── OUTPUT — MALKUTH ──
{ctx['malkuth']}

You are an agent completing a task, not rendering HTML.
Return your result as clear, structured text. Be thorough and complete."""

        resp = await asyncio.to_thread(
            lambda: client.messages.create(
                model=model,
                max_tokens=MAX_TOKENS,
                system=system,
                messages=[{"role": "user", "content": task}],
            )
        )

        result = resp.content[0].text.strip()
        _agents[agent_id]["status"]  = "complete"
        _agents[agent_id]["result"]  = result
        log.info(f"AGENT {agent_id} complete ({len(result)} chars)")

    except Exception as e:
        _agents[agent_id]["status"] = "error"
        _agents[agent_id]["error"]  = str(e)
        log.warning(f"AGENT {agent_id} failed: {e}")


@app.post("/agent")
async def create_agent(request: Request):
    """
    Spawn a background agent task. Runs through the full vessel tree.

    POST /agent
    {
      "task": "analyze visitor patterns and suggest improvements",
      "model": "claude-sonnet-4-6"       ← optional, defaults to HERMES_MODEL_AGENT
    }
    """
    if not check_token(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if not (VESSEL_DIR / "VESSEL.md").exists():
        return JSONResponse({"error": "no VESSEL.md — run setup first"}, status_code=400)

    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    task = data.get("task", "").strip()
    if not task:
        return JSONResponse({"error": "task is required"}, status_code=400)

    model    = data.get("model", MODEL_AGENT)
    agent_id = str(uuid.uuid4())[:8]

    _agents[agent_id] = {
        "id":      agent_id,
        "task":    task,
        "model":   model,
        "status":  "running",
        "created": datetime.now(timezone.utc).isoformat(),
        "result":  None,
        "error":   None,
    }

    asyncio.create_task(_run_agent(agent_id, task, model))
    log.info(f"AGENT {agent_id} spawned: {task[:80]!r} (model={model})")
    return JSONResponse({"id": agent_id, "status": "running"})


@app.get("/agent/{agent_id}")
async def get_agent(agent_id: str):
    """Check status of a background agent."""
    if agent_id not in _agents:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(_agents[agent_id])


@app.get("/agents")
async def list_agents():
    """List all agent tasks."""
    return JSONResponse(list(_agents.values()))


# ── telegram — operator channel ───────────────────────────────────────────────

def telegram_api(method: str, **params):
    """Call Telegram Bot API. Returns parsed JSON response."""
    url  = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}"
    data = urllib.parse.urlencode(params).encode()
    req  = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


async def _telegram_loop():
    """Poll Telegram for messages from allowed operator IDs."""
    offset = 0
    log.info("TELEGRAM bot started polling")

    while True:
        try:
            result = await asyncio.to_thread(
                lambda: telegram_api("getUpdates", offset=offset, timeout=25)
            )

            for update in result.get("result", []):
                offset = update["update_id"] + 1
                msg     = update.get("message", {})
                chat_id = msg.get("chat", {}).get("id")
                user_id = msg.get("from", {}).get("id")
                text    = msg.get("text", "")

                if not text or user_id not in TELEGRAM_ALLOWED:
                    continue

                # Private note — logged but not forwarded
                if text.startswith("//"):
                    log.info(f"TELEGRAM note from {user_id}: {text}")
                    continue

                log.info(f"TELEGRAM from {user_id}: {text[:80]!r}")

                # Route through the vessel
                ctx          = load_vessel()
                route        = hecate(ctx, text)
                tree_context = build_tree_context(route)

                system = f"""You are wearing this vessel. This is who you are:

{ctx['vessel']}

Current state and memory:
{ctx['state'] or '(no prior state)'}

{tree_context}

── OUTPUT — MALKUTH ──
{ctx['malkuth']}

You are responding via Telegram to your operator.
Keep responses concise (2-4 sentences). Plain text, no HTML, no markdown."""

                t = text  # capture for closure
                resp = await asyncio.to_thread(
                    lambda: client.messages.create(
                        model=MODEL_CLASSIFY,
                        max_tokens=300,
                        system=system,
                        messages=[{"role": "user", "content": t}],
                    )
                )
                reply = resp.content[0].text.strip()

                cid = chat_id  # capture for closure
                await asyncio.to_thread(
                    lambda: telegram_api("sendMessage", chat_id=cid, text=reply)
                )
                log.info(f"TELEGRAM reply sent ({len(reply)} chars)")

        except Exception as e:
            log.warning(f"TELEGRAM error: {e}")
            await asyncio.sleep(5)


# ── heartbeat — periodic vessel pulse ────────────────────────────────────────

TASKS_FILE = VESSEL_DIR / "TASKS.md"
STATE_FILE = VESSEL_DIR / "STATE.md"


def _read_tasks() -> list[dict]:
    """Read tasks from TASKS.md. Format: '- [ ] task' or '- [x] task'."""
    if not TASKS_FILE.exists():
        return []
    tasks = []
    for line in TASKS_FILE.read_text().splitlines():
        line = line.strip()
        if line.startswith("- [ ] "):
            tasks.append({"task": line[6:].strip(), "done": False})
        elif line.startswith("- [x] "):
            tasks.append({"task": line[6:].strip(), "done": True})
    return tasks


def _write_tasks(tasks: list[dict]):
    """Write tasks back to TASKS.md."""
    lines = []
    for t in tasks:
        mark = "x" if t["done"] else " "
        lines.append(f"- [{mark}] {t['task']}")
    TASKS_FILE.write_text("\n".join(lines) + "\n")


def _append_heartbeat_log(entry: str):
    """Append a heartbeat log entry to STATE.md."""
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    log_line = f"\n[{stamp}] {entry}"

    if STATE_FILE.exists():
        content = STATE_FILE.read_text()
        if "## Heartbeat" not in content:
            content += "\n\n## Heartbeat\n"
        content += log_line
    else:
        content = f"# STATE\n\n## Heartbeat\n{log_line}"

    STATE_FILE.write_text(content)


async def _heartbeat_loop():
    """Vessel pulse. Runs every HEARTBEAT_INTERVAL seconds."""
    await asyncio.sleep(10)  # let the bridge fully start
    log.info(f"HEARTBEAT started (every {HEARTBEAT_INTERVAL // 60} min)")

    while True:
        try:
            ctx = load_vessel()

            # Gather system status
            tasks     = _read_tasks()
            pending   = [t for t in tasks if not t["done"]]
            completed = [t for t in tasks if t["done"]]
            agents_running = sum(1 for a in _agents.values() if a["status"] == "running")

            status_summary = (
                f"Vessel: {'active' if (VESSEL_DIR / 'VESSEL.md').exists() else 'no VESSEL.md'}. "
                f"Static page: {'exists' if INDEX_HTML.exists() else 'not built'}. "
                f"Tasks: {len(pending)} pending, {len(completed)} done. "
                f"Agents running: {agents_running}."
            )

            # Haiku produces a brief log entry
            resp = await asyncio.to_thread(
                lambda: client.messages.create(
                    model=MODEL_CLASSIFY,
                    max_tokens=100,
                    system=(
                        f"You are the heartbeat of this vessel:\n{ctx['vessel'][:300]}\n\n"
                        f"Current status: {status_summary}\n\n"
                        "Write a single-sentence heartbeat log entry. "
                        "Note anything relevant. Be concise. No timestamps."
                    ),
                    messages=[{"role": "user", "content": "pulse"}],
                )
            )
            heartbeat_entry = resp.content[0].text.strip()
            _append_heartbeat_log(heartbeat_entry)
            log.info(f"HEARTBEAT: {heartbeat_entry}")

            # If there are pending tasks and no agent is currently running, pick the first one
            if pending and agents_running == 0:
                task_text = pending[0]["task"]
                agent_id  = str(uuid.uuid4())[:8]

                _agents[agent_id] = {
                    "id":      agent_id,
                    "task":    task_text,
                    "model":   MODEL_AGENT,
                    "status":  "running",
                    "created": datetime.now(timezone.utc).isoformat(),
                    "result":  None,
                    "error":   None,
                    "source":  "heartbeat",
                }

                asyncio.create_task(_run_heartbeat_task(agent_id, task_text, tasks, pending[0]))
                log.info(f"HEARTBEAT spawned agent {agent_id}: {task_text[:80]!r}")

        except Exception as e:
            log.warning(f"HEARTBEAT error: {e}")

        await asyncio.sleep(HEARTBEAT_INTERVAL)


async def _run_heartbeat_task(agent_id: str, task: str, all_tasks: list, task_entry: dict):
    """Run a heartbeat-triggered task and mark it complete when done."""
    await _run_agent(agent_id, task, MODEL_AGENT)

    # If the agent completed successfully, mark the task done
    if _agents[agent_id]["status"] == "complete":
        task_entry["done"] = True
        _write_tasks(all_tasks)
        log.info(f"HEARTBEAT task marked complete: {task[:60]!r}")


@app.on_event("startup")
async def startup():
    """Start background services if configured."""
    if TELEGRAM_TOKEN and TELEGRAM_ALLOWED:
        asyncio.create_task(_telegram_loop())
        log.info(f"TELEGRAM enabled for {len(TELEGRAM_ALLOWED)} operator(s)")

    # Heartbeat always runs if vessel exists
    if (VESSEL_DIR / "VESSEL.md").exists():
        asyncio.create_task(_heartbeat_loop())


# ── browser setup wizard ──────────────────────────────────────────────────────

def setup_html() -> str:
    """Generate the browser setup wizard page, themed from WIZARD.md if present."""
    wizard_path = VESSEL_DIR / "WIZARD.md"
    greeting    = "Tell me who this website is. Plain English -- you can change everything later."

    if wizard_path.exists():
        text    = wizard_path.read_text()
        divider = text.find("---")
        top     = text[:divider].strip() if divider > 0 else text.strip()
        m       = re.search(r"## Greeting\s*\n(.*?)(?=\n##|\Z)", top, re.DOTALL)
        if m:
            greeting = m.group(1).strip()

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>HERMES -- vessel setup</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: Georgia, serif;
    background: #f5f0e8;
    color: #1a1a1a;
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 2rem;
  }}
  .wrap {{ max-width: 640px; width: 100%; }}
  header {{ margin-bottom: 3rem; }}
  h1 {{
    font-size: 0.85rem;
    font-weight: normal;
    letter-spacing: 0.22em;
    color: #999;
    margin-bottom: 0.75rem;
  }}
  .lead {{ font-size: 1.35rem; line-height: 1.55; }}
  .field {{ margin-bottom: 2.2rem; }}
  label {{
    display: block;
    font-size: 0.85rem;
    letter-spacing: 0.06em;
    color: #666;
    margin-bottom: 0.6rem;
  }}
  input[type=text], textarea {{
    width: 100%;
    background: transparent;
    border: none;
    border-bottom: 1px solid #ccc;
    padding: 0.5rem 0;
    font-family: inherit;
    font-size: 1rem;
    color: #1a1a1a;
    outline: none;
    resize: none;
  }}
  input:focus, textarea:focus {{ border-bottom-color: #8b7355; }}
  textarea {{ min-height: 64px; }}
  .hint {{ font-size: 0.78rem; color: #bbb; margin-left: 0.4rem; }}
  .section-break {{
    border: none;
    border-top: 1px solid #e0d8cc;
    margin: 2.5rem 0;
  }}
  button {{
    background: #1a1a1a;
    color: #f5f0e8;
    border: none;
    padding: 0.8rem 2.5rem;
    font-family: inherit;
    font-size: 0.85rem;
    letter-spacing: 0.12em;
    cursor: pointer;
    margin-top: 0.5rem;
  }}
  button:hover {{ background: #8b7355; }}
  button:disabled {{ opacity: 0.4; cursor: not-allowed; }}
  .status {{
    margin-top: 1.5rem;
    color: #888;
    font-size: 0.88rem;
    min-height: 1.2rem;
  }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>HERMES WEBKIT</h1>
    <p class="lead">{{greeting}}</p>
  </header>
  <form id="setup">

    <div class="field">
      <label>What is your website called?</label>
      <input type="text" name="name" required>
    </div>

    <div class="field">
      <label>What is it for -- and who is it for?</label>
      <textarea name="purpose" placeholder="What should visitors find here? What does it offer or solve?"></textarea>
    </div>

    <div class="field">
      <label>What voice or tone?</label>
      <input type="text" name="voice" placeholder="e.g. direct and warm, formal, poetic, plainspoken, technical">
    </div>

    <div class="field">
      <label>What does it know about?</label>
      <textarea name="knowledge" placeholder="Expertise, story, offerings, background -- whatever this site draws on when it responds."></textarea>
    </div>

    <hr class="section-break">

    <div class="field">
      <label>What do you want visitors to do or feel when they leave?</label>
      <input type="text" name="goal" placeholder="e.g. contact you, understand what you offer, feel like they found the right person">
    </div>

    <div class="field">
      <label>What makes this specific to you?</label>
      <textarea name="character" placeholder="What would be wrong about a generic version of this site. The thing that makes it yours."></textarea>
    </div>

    <hr class="section-break">

    <div class="field">
      <label>What should it never do or say?</label>
      <input type="text" name="limits" placeholder="Topics to avoid, things that would be off-brand or wrong">
    </div>

    <div class="field">
      <label>Your name or contact <span class="hint">optional</span></label>
      <input type="text" name="contact" placeholder="Name, email, or however you want to be reachable">
    </div>

    <button type="submit" id="btn">Build vessel</button>
    <p class="status" id="status"></p>
  </form>
</div>
<script>
document.getElementById("setup").addEventListener("submit", async e => {{
  e.preventDefault();
  const btn    = document.getElementById("btn");
  const status = document.getElementById("status");
  btn.disabled = true;
  btn.textContent = "Building...";
  status.textContent = "Running the tree -- this takes a moment.";
  const data = {{}};
  new FormData(e.target).forEach((v, k) => data[k] = v);
  try {{
    const res = await fetch("/setup", {{
      method: "POST",
      headers: {{"Content-Type": "application/json"}},
      body: JSON.stringify(data)
    }});
    if (res.ok) {{
      status.textContent = "Vessel written. Loading your site...";
      setTimeout(() => window.location.href = "/", 1500);
    }} else {{
      status.textContent = "Error: " + await res.text();
      btn.disabled = false;
      btn.textContent = "Build vessel";
    }}
  }} catch(err) {{
    status.textContent = "Connection error.";
    btn.disabled = false;
    btn.textContent = "Build vessel";
  }}
}});
</script>
</body>
</html>"""


@app.get("/setup")
async def setup_get():
    return HTMLResponse(content=setup_html())


@app.post("/setup")
async def setup_post(request: Request):
    if not check_token(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    name      = data.get("name",      "").strip() or "Untitled"
    purpose   = data.get("purpose",   "").strip()
    voice     = data.get("voice",     "").strip()
    knowledge = data.get("knowledge", "").strip()
    goal      = data.get("goal",      "").strip()
    character = data.get("character", "").strip()
    limits    = data.get("limits",    "").strip()
    contact   = data.get("contact",   "").strip()

    lines = [f"# {name}", ""]
    if purpose:   lines += ["## Purpose",   purpose,   ""]
    if voice:     lines += ["## Voice",     voice,     ""]
    if knowledge: lines += ["## Knowledge", knowledge, ""]
    if goal:      lines += ["## Goal",      goal,      ""]
    if character: lines += ["## Character", character, ""]
    if limits:    lines += ["## Limits",    limits,    ""]
    if contact:   lines += ["## Contact",   contact,   ""]

    VESSEL_DIR.mkdir(parents=True, exist_ok=True)
    (VESSEL_DIR / "VESSEL.md").write_text("\n".join(lines) + "\n")
    log.info(f"SETUP vessel written: {name}")
    return JSONResponse({"status": "ok", "name": name})





# ── analytics ────────────────────────────────────────────────────────────────

@app.get("/analytics")
async def analytics(request: Request):
    if not check_token(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    data = _load_analytics()
    return JSONResponse(data)


# ── routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return JSONResponse({
        "status":         "ok",
        "vessel":         (VESSEL_DIR / "VESSEL.md").exists(),
        "model_render":   MODEL_RENDER,
        "model_classify": MODEL_CLASSIFY,
        "nodes_present":  [
            n for n in ALL_NODES
            if (VESSEL_DIR / "tree" / f"{n}.md").exists()
        ],
    })


async def _handle(request: Request, path: str = "") -> HTMLResponse:
    # No vessel yet — send to browser wizard
    if not (VESSEL_DIR / "VESSEL.md").exists():
        return RedirectResponse("/setup", status_code=302)

    # GET / with cached build — serve instantly, no API call
    if request.method == "GET" and not path and INDEX_HTML.exists():
        _track_visit("/")
        log.info("→ GET /  serving static/index.html")
        return HTMLResponse(content=INDEX_HTML.read_text())

    # GET / with no cache yet — trigger first build
    if request.method == "GET" and not path and not INDEX_HTML.exists():
        _track_visit("/")
        log.info("→ GET /  no cache — running first build")
        html = build("render the site homepage for the first time")
        return HTMLResponse(content=html)

    body  = (await request.body()).decode().strip()
    query = request.query_params.get("q", "")
    label = f"/{path}" if path else "/"
    visitor_input = body or query or f"visitor arrived at {label}"

    _track_visit(label)
    log.info(f"→ {request.method} {label}  input={visitor_input[:80]!r}")

    ctx   = load_vessel()
    route = hecate(ctx, visitor_input)
    html  = render(ctx, route, visitor_input)

    log.info(f"← {len(html)} chars  nodes={route['nodes']}")
    return HTMLResponse(content=html)


@app.api_route("/", methods=["GET", "POST"])
async def handle_root(request: Request):
    return await _handle(request)

# Known sub-paths — anything else is an instant 404, never hits the API
_KNOWN_PATHS = {"setup", "health", "build", "chat", "agent", "agents", "analytics"}

@app.api_route("/{path:path}", methods=["GET", "POST"])
async def handle_path(request: Request, path: str):
    if path == "setup":
        return await setup_get()
    if path not in _KNOWN_PATHS:
        return HTMLResponse(content="<h1>404</h1>", status_code=404)
    return await _handle(request, path)


# ── entry ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run("bridge:app", host="127.0.0.1", port=int(os.environ.get("HERMES_PORT", "8000")), reload=False)
