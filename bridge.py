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

import yaml
import uvicorn
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

# ── Multi-LLM Provider Support ──────────────────────────────────────────────
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "anthropic").lower()  # anthropic | openai | ollama
OLLAMA_HOST  = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

if LLM_PROVIDER == "anthropic":
    from anthropic import Anthropic
    client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"), max_retries=1)
elif LLM_PROVIDER in ("openai", "ollama"):
    from openai import OpenAI as _OpenAI
    if LLM_PROVIDER == "ollama":
        _oai_client = _OpenAI(base_url=OLLAMA_HOST + "/v1", api_key="ollama")
    else:
        _oai_client = _OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

    # Model mapping for non-Anthropic providers
    _MODEL_MAP = {
        "openai": {
            "main":    os.environ.get("HERMES_MODEL",        "gpt-4.1"),
            "hecate":  os.environ.get("HERMES_MODEL_HECATE", "gpt-4.1-mini"),
        },
        "ollama": {
            "main":    os.environ.get("HERMES_MODEL",        "qwen2.5-coder:7b"),
            "hecate":  os.environ.get("HERMES_MODEL_HECATE", "qwen2.5:1.5b"),
        },
    }

    def _resolve_model(anthropic_model: str) -> str:
        """Map an Anthropic model name to the equivalent for current provider."""
        m = anthropic_model.lower()
        if "haiku" in m or "classify" in m:
            return _MODEL_MAP[LLM_PROVIDER]["hecate"]
        return _MODEL_MAP[LLM_PROVIDER]["main"]

    def _convert_tools_to_openai(tools: list) -> list:
        """Convert Anthropic tool format to OpenAI function-calling format."""
        if not tools:
            return []
        out = []
        for t in tools:
            out.append({
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get("input_schema", {}),
                },
            })
        return out

    def _convert_messages_to_openai(system: str, messages: list) -> list:
        """Convert Anthropic message format to OpenAI format."""
        oai_msgs = []
        if system:
            oai_msgs.append({"role": "system", "content": system})
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if isinstance(content, str):
                oai_msgs.append({"role": role, "content": content})
            elif isinstance(content, list):
                # Anthropic uses content blocks — convert them
                text_parts = []
                tool_calls = []
                tool_results = []
                for block in content:
                    if isinstance(block, dict):
                        btype = block.get("type", "")
                        if btype == "text":
                            text_parts.append(block.get("text", ""))
                        elif btype == "tool_use":
                            tool_calls.append({
                                "id": block.get("id", ""),
                                "type": "function",
                                "function": {
                                    "name": block.get("name", ""),
                                    "arguments": json.dumps(block.get("input", {})),
                                },
                            })
                        elif btype == "tool_result":
                            result_content = block.get("content", "")
                            if isinstance(result_content, list):
                                result_content = " ".join(
                                    b.get("text", "") for b in result_content if isinstance(b, dict)
                                ) or str(result_content)
                            tool_results.append({
                                "role": "tool",
                                "tool_call_id": block.get("tool_use_id", ""),
                                "content": str(result_content),
                            })
                    elif hasattr(block, "type"):
                        # Could be an Anthropic SDK object
                        if block.type == "text":
                            text_parts.append(block.text)
                        elif block.type == "tool_use":
                            tool_calls.append({
                                "id": block.id,
                                "type": "function",
                                "function": {
                                    "name": block.name,
                                    "arguments": json.dumps(block.input),
                                },
                            })

                if role == "user" and tool_results:
                    # Send tool results as separate messages
                    if text_parts:
                        oai_msgs.append({"role": "user", "content": "\n".join(text_parts)})
                    for tr in tool_results:
                        oai_msgs.append(tr)
                elif role == "assistant" and tool_calls:
                    msg = {"role": "assistant", "tool_calls": tool_calls}
                    if text_parts:
                        msg["content"] = "\n".join(text_parts)
                    oai_msgs.append(msg)
                else:
                    oai_msgs.append({"role": role, "content": "\n".join(text_parts) or ""})
        return oai_msgs

    class _AnthropicShim:
        """Wraps OpenAI/Ollama client to return Anthropic-compatible responses."""

        class messages:
            @staticmethod
            def create(*, model="", max_tokens=4096, system="", messages=None,
                       tools=None, timeout=120, **kwargs):
                actual_model = _resolve_model(model)
                oai_msgs = _convert_messages_to_openai(system, messages or [])
                oai_tools = _convert_tools_to_openai(tools) if tools else None

                call_kwargs = {
                    "model": actual_model,
                    "max_tokens": max_tokens,
                    "messages": oai_msgs,
                    "timeout": timeout,
                }
                if oai_tools:
                    call_kwargs["tools"] = oai_tools

                resp = _oai_client.chat.completions.create(**call_kwargs)
                choice = resp.choices[0]

                # Convert response back to Anthropic format
                content_blocks = []
                if choice.message.content:
                    content_blocks.append(type("TextBlock", (), {
                        "type": "text",
                        "text": choice.message.content,
                    })())

                if choice.message.tool_calls:
                    for tc in choice.message.tool_calls:
                        try:
                            inp = json.loads(tc.function.arguments)
                        except (json.JSONDecodeError, TypeError):
                            inp = {}
                        content_blocks.append(type("ToolUseBlock", (), {
                            "type": "tool_use",
                            "id": tc.id,
                            "name": tc.function.name,
                            "input": inp,
                        })())

                stop = "end_turn"
                if choice.finish_reason == "tool_calls":
                    stop = "tool_use"
                elif choice.finish_reason == "length":
                    stop = "max_tokens"

                return type("Response", (), {
                    "content": content_blocks,
                    "stop_reason": stop,
                    "model": actual_model,
                    "usage": type("Usage", (), {
                        "input_tokens": getattr(resp.usage, "prompt_tokens", 0),
                        "output_tokens": getattr(resp.usage, "completion_tokens", 0),
                    })(),
                })()

    client = _AnthropicShim()
else:
    from anthropic import Anthropic
    client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"), max_retries=1)
    LLM_PROVIDER = "anthropic"

VESSEL_DIR     = Path(os.environ.get("VESSEL_DIR",        "/root/hermes/vessel"))
STATIC_DIR     = Path(os.environ.get("STATIC_DIR",        "/root/hermes/static"))
VESSEL_HOME    = Path(os.environ.get("VESSEL_DIR", "/root/hermes/vessel")).parent  # sandbox root
INDEX_HTML     = STATIC_DIR / "index.html"
MODEL_RENDER   = os.environ.get("HERMES_MODEL",           "claude-sonnet-4-6")
MODEL_CLASSIFY = os.environ.get("HERMES_MODEL_HECATE",    "claude-haiku-4-5-20251001")
try:
    MAX_TOKENS = int(os.environ.get("HERMES_MAX_TOKENS", "4096"))
except (ValueError, TypeError):
    MAX_TOKENS = 4096

ALL_NODES = [
    "KETER", "CHOKMAH", "BINAH", "CHESED", "GEVURAH",
    "TIFERET", "NETZACH", "HOD", "YESOD", "MALKUTH"
]

MODEL_AGENT        = os.environ.get("HERMES_MODEL_AGENT",        MODEL_RENDER)
try:
    HEARTBEAT_INTERVAL = int(os.environ.get("HERMES_HEARTBEAT_MIN", "30")) * 60
except (ValueError, TypeError):
    HEARTBEAT_INTERVAL = 1800  # seconds
TELEGRAM_TOKEN     = os.environ.get("TELEGRAM_BOT_TOKEN",       "")
TELEGRAM_ALLOWED = set(
    int(x) for x in os.environ.get("TELEGRAM_ALLOWED_IDS", "").split(",")
    if x.strip()
)

# ── commerce configuration ────────────────────────────────────────────────────
PRODUCTS_DIR           = VESSEL_DIR / "products"
ORDERS_DIR             = VESSEL_DIR / "orders"
SUPPLIERS_DIR          = VESSEL_DIR / "suppliers"
GENERATED_DIR          = VESSEL_DIR / "generated"
STRIPE_SECRET_KEY      = os.environ.get("STRIPE_SECRET_KEY",      "")
STRIPE_WEBHOOK_SECRET  = os.environ.get("STRIPE_WEBHOOK_SECRET",  "")
STRIPE_SUCCESS_URL     = os.environ.get("STRIPE_SUCCESS_URL",     "/order-complete")
STRIPE_CANCEL_URL      = os.environ.get("STRIPE_CANCEL_URL",      "/cart")

# ── room rental configuration ────────────────────────────────────────────────
ROOMS_DIR = VESSEL_DIR / "rooms"

ROOM_PLANS = {
    "3days":   {"label": "3 Days",    "price": 500,   "days": 3,   "currency": "usd"},
    "5days":   {"label": "5 Days",    "price": 1500,  "days": 5,   "currency": "usd"},
    "10days":  {"label": "10 Days",   "price": 3000,  "days": 10,  "currency": "usd"},
}
PREMIUM_FLOOR_SURCHARGE = 500  # extra $5 for floors 7-12

# Per-god identity prompts — each vessel gets a distinct personality
VESSEL_ROLE_PROMPTS = {
    "HERMES": "You are Hermes, the messenger god. You manage the user's main website and act as their primary command center. Focus on site identity, navigation, homepage design, and overall web presence.",
    "ATHENA": "You are Athena, goddess of wisdom. You specialize in knowledge bases, documentation, wikis, research pages, and scholarly content. Help build structured, informative sites.",
    "APOLLO": "You are Apollo, god of arts and light. You focus on creative portfolios, galleries, photography, music pages, and artistic expression. Help build beautiful, visually striking sites.",
    "DEMETER": "You are Demeter, goddess of harvest and commerce. You specialize in online shops, product catalogs, e-commerce, pricing pages, and business sites. Help build effective storefronts.",
    "ARES": "You are Ares, god of war and defense. You focus on security, server hardening, firewalls, monitoring dashboards, and system protection. Help secure and fortify the user's infrastructure.",
    "ARTEMIS": "You are Artemis, goddess of the hunt and nature. You specialize in wellness tracking, health dashboards, fitness pages, habit trackers, and outdoor/nature content.",
    "DIONYSUS": "You are Dionysus, god of celebration. You focus on events pages, entertainment, social gatherings, party planning, community pages, and fun interactive content.",
    "HEPHAESTUS": "You are Hephaestus, god of the forge. You specialize in tools, scripts, utilities, developer dashboards, API documentation, and technical workshops.",
    "HESTIA": "You are Hestia, goddess of hearth and home. You focus on personal homepages, blogs, family pages, journals, and warm, inviting personal web spaces.",
    "IRIS": "You are Iris, goddess of the rainbow and messaging. You specialize in webhooks, notifications, integrations, communication dashboards, and connecting services together.",
    "PERSEPHONE": "You are Persephone, queen of the underworld. You focus on migration, importing/exporting content, data transformation, backup systems, and transitioning between platforms.",
    "THEMIS": "You are Themis, goddess of justice and law. You specialize in legal pages, terms of service, privacy policies, compliance documentation, and governance frameworks.",
}




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
                timeout=60,
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
        "name": "edit_file",
        "description": (
            "Make a targeted edit to a file using search and replace. "
            "Much more efficient than read+write for small changes. "
            "Finds old_text in the file and replaces it with new_text. "
            "Requires operator confirmation before executing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path":        {"type": "string", "description": "Absolute file path"},
                "old_text":    {"type": "string", "description": "Exact text to find in the file (must be unique)"},
                "new_text":    {"type": "string", "description": "Replacement text"},
                "description": {"type": "string", "description": "Plain English: what this change does"},
            },
            "required": ["path", "old_text", "new_text", "description"],
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
            p = Path(inp["path"]).resolve()
            if not str(p).startswith(str(VESSEL_HOME)):
                return "Access denied: can only read files within " + str(VESSEL_HOME)
            if not p.exists():
                return f"File not found: {inp['path']}"
            text = p.read_text(errors="replace")
            if len(text) > 50000:
                text = text[:50000] + f"\n\n... (truncated — {len(text)} total chars)"
            return text
        if name == "list_dir":
            p = Path(inp["path"]).resolve()
            if not str(p).startswith(str(VESSEL_HOME)):
                return "Access denied: can only list within " + str(VESSEL_HOME)
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
            p = Path(inp["path"]).resolve()
            if not str(p).startswith(str(VESSEL_HOME)):
                return "Access denied: can only write files within " + str(VESSEL_HOME)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(inp["content"])
            return f"Written {len(inp['content'])} chars → {inp['path']}"
        if name == "edit_file":
            p = Path(inp["path"]).resolve()
            if not str(p).startswith(str(VESSEL_HOME)):
                return "Access denied: can only edit files within " + str(VESSEL_HOME)
            if not p.exists():
                return f"File not found: {inp['path']}"
            text = p.read_text(errors="replace")
            old_text = inp["old_text"]
            new_text = inp["new_text"]
            count = text.count(old_text)
            if count == 0:
                return f"old_text not found in {inp['path']}. Make sure it matches exactly (including whitespace)."
            if count > 1:
                return f"old_text found {count} times — must be unique. Add more surrounding context to old_text."
            text = text.replace(old_text, new_text, 1)
            p.write_text(text)
            return f"Edited {inp['path']}: replaced {len(old_text)} chars with {len(new_text)} chars"
        if name == "run_command":
            result = _subprocess.run(
                inp["command"],
                shell=True,
                capture_output=True,
                text=True,
                timeout=180,
                cwd=str(VESSEL_HOME),
            )
            out = (result.stdout + result.stderr).strip()
            if len(out) > 3000:
                out = out[:3000] + "\n... (truncated)"
            return out or "(no output)"
    except _subprocess.TimeoutExpired:
        return "Command timed out after 180 seconds."
    except Exception as e:
        return f"Error: {e}"
    return "Unknown tool"



def _trim_history(history: list):
    """Trim large tool content in history to prevent context bloat."""
    for msg in history:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role", "")
        items = msg.get("content", [])
        if not isinstance(items, list):
            continue
        for block in items:
            if not isinstance(block, dict):
                continue
            if (role == "assistant" and block.get("type") == "tool_use"
                    and block.get("name") == "write_file"):
                inp = block.get("input", {})
                if isinstance(inp.get("content"), str) and len(inp["content"]) > 300:
                    path = inp.get("path", "?")
                    sz = len(inp["content"])
                    inp["content"] = "(wrote %d chars to %s)" % (sz, path)
            if (role == "assistant" and block.get("type") == "tool_use"
                    and block.get("name") == "edit_file"):
                inp = block.get("input", {})
                if isinstance(inp.get("old_text"), str) and len(inp["old_text"]) > 200:
                    inp["old_text"] = inp["old_text"][:100] + "...(trimmed)"
                if isinstance(inp.get("new_text"), str) and len(inp["new_text"]) > 200:
                    inp["new_text"] = inp["new_text"][:100] + "...(trimmed)"
            if (role == "user" and block.get("type") == "tool_result"
                    and isinstance(block.get("content"), str)
                    and len(block["content"]) > 500):
                block["content"] = block["content"][:500] + " ... (trimmed)"


async def _operator_loop(session_id: str, history: list, system: str) -> dict:
    """
    Agentic tool loop. Runs until Claude produces a text reply or hits a
    write/run tool that requires operator confirmation.

    Returns:
        {"done": True,  "reply": "..."}
        {"done": False, "pending": [...actions...]}
    """
    MAX_TOOL_TURNS = 15

    # Repair ALL orphaned tool_use blocks in history (prevent 400 errors)
    i = 0
    while i < len(history):
        msg = history[i]
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            i += 1
            continue
        blocks = msg.get("content", [])
        if not isinstance(blocks, list):
            i += 1
            continue
        tool_ids = [b["id"] for b in blocks if isinstance(b, dict) and b.get("type") == "tool_use"]
        if not tool_ids:
            i += 1
            continue
        # Check if next message has matching tool_results
        nxt = history[i + 1] if i + 1 < len(history) else None
        if nxt and isinstance(nxt, dict) and nxt.get("role") == "user":
            nxt_blocks = nxt.get("content", [])
            if isinstance(nxt_blocks, list):
                result_ids = {b.get("tool_use_id") for b in nxt_blocks if isinstance(b, dict) and b.get("type") == "tool_result"}
                missing = [tid for tid in tool_ids if tid not in result_ids]
                if not missing:
                    i += 1
                    continue
                # Some tool_use ids missing — add them to the existing result message
                for tid in missing:
                    nxt_blocks.append({"type": "tool_result", "tool_use_id": tid, "content": "(recovered)"})
                log.warning(f"CHAT repaired {len(missing)} missing tool_results at msg {i}")
                i += 1
                continue
        # No next message or next message is not user — insert tool_results
        repair = {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tid, "content": "(recovered)"}
            for tid in tool_ids
        ]}
        history.insert(i + 1, repair)
        log.warning(f"CHAT inserted tool_results for {len(tool_ids)} orphans at msg {i}")
        i += 2

    for _turn in range(MAX_TOOL_TURNS):
        resp = await asyncio.to_thread(
            lambda: client.messages.create(
                model=MODEL_RENDER,
                max_tokens=4096,
                system=system,
                tools=OPERATOR_TOOLS,
                messages=history,
                timeout=120,
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
            dangerous_calls = [t for t in tool_calls if t.name in ("write_file", "edit_file", "run_command")]

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
                    if tc.name == "edit_file":
                        d = tc.input.get("description", "edit file")
                        descriptions.append(f"edit_file → {tc.input.get('path','?')}: {d}")
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
            _trim_history(history)
            continue

        break  # unexpected stop reason

    return {"done": True, "reply": "(no response)"}


def _load_context() -> str:
    """Load the rolling operator context summary if it exists."""
    if CHAT_CONTEXT_FILE.exists():
        return CHAT_CONTEXT_FILE.read_text().strip()
    return ""


def _build_commerce_context() -> str:
    """Build commerce context string for operator chat."""
    parts = []
    products = load_products()
    if products:
        parts.append(f"\n\nCommerce: {len(products)} products in catalog.")
    orders = load_orders()
    if orders:
        recent = orders[:10]
        parts.append("Recent orders:")
        for o in recent:
            amt = o.get("amount_total", 0)
            parts.append(
                f"  - {o['id']}: {o['status']} — "
                f"${amt/100:.2f}" if isinstance(amt, (int, float)) else f"  - {o['id']}: {o['status']}"
            )
    return "\n".join(parts)


def _build_chat_system(vessel_text: str, state_text: str, tree_context: str, vessel_role: str = "") -> str:
    context = _load_context()
    return (
        "You are wearing this vessel. This is who you are:\n\n"
        + vessel_text
        + "\n\nCurrent state and memory:\n"
        + state_text
        + ("\n\nOperator session context (summary of past conversations):\n" + context if context else "")
        + "\n\n"
        + tree_context
        + "\n\nYou are in a direct terminal conversation with your operator. "
        + "Your website files are at /root/hermes/vessels/thedoorman/static/ — "
        + "the main page is static/index.html. "
        + "The tree context above tells you which files are relevant to this request. "
        + "For small edits (changing text, tweaking styles, updating wording): "
        + "use edit_file with old_text/new_text — no need to read the whole file first. "
        + "For larger changes or new files: read_file once, then write_file. "
        + "Do NOT use run_command to read or write files. Do NOT use list_dir on / or system paths. "
        + "Do NOT call the /build endpoint. The operator handles full rebuilds separately. "
        + "Keep it simple: use edit_file for targeted changes. "
        + "edit_file and write_file require operator confirmation — the system handles that automatically. "
        + "For casual conversation, just reply in plain text. No HTML. No markdown. "
        + "Conversational, direct, and present. Remember the full session.\n"
        + _build_commerce_context()
        + CHAT_THEME_INSTRUCTIONS
        + CHAT_STUDIO_INSTRUCTIONS
        + (("\n\nYOUR IDENTITY:\n" + VESSEL_ROLE_PROMPTS[vessel_role.upper()]) if vessel_role.upper() in VESSEL_ROLE_PROMPTS else "")
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
    # Include product catalog for shopping assistant capability
    products = load_products(active_only=True)
    product_context = ""
    if products:
        lines = []
        for p in products[:20]:
            lines.append(f"- {p['name']}: ${p['price']} — {p.get('description', '')[:100]}")
        product_context = (
            "\n\nProduct catalog (you can reference these in conversation):\n"
            + "\n".join(lines)
            + "\n\nLink products as /products/{slug}.html when relevant. "
            + "Cart page is at /cart."
        )

    return (
        "You are this vessel. This is who you are:\n\n"
        + vessel_text
        + "\n\nCurrent memory:\n"
        + state_text
        + product_context
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
            timeout=60,
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
        vessel_role = data.get("vessel_role", "")
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
    try:
        reply = await _visitor_reply(session["history"], system)
    except Exception as e:
        log.error(f"VISITOR error: {e}")
        reply = "something went wrong — try again"
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
        vessel_role = data.get("vessel_role", "")
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

    no_tools = data.get("no_tools", False) if isinstance(data, dict) else False

    try:
        ctx          = load_vessel()
        route        = hecate(ctx, message)
        tree_context = build_tree_context(route)
        system       = _build_chat_system(ctx["vessel"], ctx["state"] or "(no prior state)", tree_context, vessel_role=vessel_role)

        if no_tools:
            # Simple text response — no tools, no operator_loop
            # Sanitize: strip tool_use/tool_result blocks, keep only text
            clean = []
            for m in history:
                if not isinstance(m, dict):
                    continue
                role = m.get("role", "")
                c = m.get("content", "")
                if isinstance(c, str):
                    if c.strip():
                        clean.append({"role": role, "content": c})
                elif isinstance(c, list):
                    txt = " ".join(
                        (b.get("text","") if isinstance(b,dict) else str(b))
                        for b in c
                        if (isinstance(b,dict) and b.get("type")=="text") or isinstance(b,str)
                    ).strip()
                    if txt:
                        clean.append({"role": role, "content": txt})
            # Ensure valid alternation: merge consecutive same-role msgs
            merged = []
            for m in clean:
                if merged and merged[-1]["role"] == m["role"]:
                    merged[-1]["content"] += " " + m["content"]
                else:
                    merged.append(m)
            # Must start with user, end with user
            if merged and merged[0]["role"] != "user":
                merged = merged[1:]
            if merged and merged[-1]["role"] != "user":
                merged.append({"role": "user", "content": "continue"})
            if not merged:
                merged = [{"role": "user", "content": "hello"}]

            resp = await asyncio.to_thread(
                lambda: client.messages.create(
                    model=MODEL_RENDER,
                    max_tokens=MAX_TOKENS,
                    system=system,
                    messages=merged,
                    timeout=120,
                )
            )
            reply_text = " ".join(b.text for b in resp.content if hasattr(b, "text")).strip()
            if not reply_text:
                reply_text = "(no response)"
            history.append({"role": "assistant", "content": [{"type": "text", "text": reply_text}]})
            result = {"done": True, "reply": reply_text}
        else:
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
    except Exception as e:
        log.error(f"CHAT error session={session_id}: {e}")
        # Remove the failed user message so session stays clean
        if history and history[-1].get("role") == "user":
            history.pop()
        return JSONResponse({"reply": f"(vessel error: {type(e).__name__} — try again)", "session_id": session_id})




@app.post("/chat/clear")
async def chat_clear(request: Request):
    """Clear operator chat session. Requires X-Build-Token header."""
    if not check_token(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await request.json()
    session_id = body.get("session_id", "operator")
    if session_id in _chat_sessions:
        del _chat_sessions[session_id]
    # Also clear persisted history file
    hist_file = VESSEL_DIR / "chat_history.json"
    if hist_file.exists():
        hist_file.unlink()
    # Clear context summary
    if CHAT_CONTEXT_FILE.exists():
        CHAT_CONTEXT_FILE.unlink()
    log.info(f"CHAT session={session_id} cleared")
    return {"status": "cleared", "session_id": session_id}


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
    _trim_history(history)
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



FALLBACK_HTML = (
    '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width,initial-scale=1">'
    '<title>Vessel Resting</title><style>'
    'body{margin:0;min-height:100vh;display:flex;align-items:center;'
    'justify-content:center;font-family:Georgia,serif;background:#0a0a0f;color:#c4b59a}'
    '.wrap{text-align:center;padding:2rem}'
    'h1{font-size:2rem;margin-bottom:1rem;color:#d4a574}'
    'p{font-size:1.1rem;opacity:0.8;max-width:400px;margin:0 auto}'
    '</style></head><body><div class="wrap">'
    '<h1>The vessel is resting</h1>'
    '<p>This site is being prepared. Please return shortly.</p>'
    '</div></body></html>'
)


# ── file helpers ──────────────────────────────────────────────────────────────

def read(path: Path) -> str:
    return path.read_text(errors="replace").strip() if path.exists() else ""


# ── product catalog ──────────────────────────────────────────────────────────

def _parse_product(path: Path) -> dict | None:
    """Parse a product markdown file with YAML frontmatter."""
    text = path.read_text()
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None
    try:
        meta = yaml.safe_load(parts[1])
        if not isinstance(meta, dict):
            return None
        meta["description"] = parts[2].strip()
        meta["slug"] = meta.get("slug", path.stem)
        return meta
    except Exception:
        return None


def load_products(active_only: bool = True) -> list[dict]:
    """Load all product markdown files from vessel/products/."""
    if not PRODUCTS_DIR.exists():
        return []
    products = []
    for f in sorted(PRODUCTS_DIR.glob("*.md")):
        p = _parse_product(f)
        if p and (not active_only or p.get("active", True)):
            products.append(p)
    return products


def load_orders(status: str = None) -> list[dict]:
    """Load orders from vessel/orders/, optionally filtered by status."""
    if not ORDERS_DIR.exists():
        return []
    orders = []
    for f in sorted(ORDERS_DIR.glob("*.json"), reverse=True):
        try:
            order = json.loads(f.read_text())
            if status is None or order.get("status") == status:
                orders.append(order)
        except Exception:
            continue
    return orders


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



# ── YESOD — habit system (procedural memory) ─────────────────────────────────

HABITS_FILE = VESSEL_DIR / "habits.json"

TASK_VOCAB = {
    "build", "render", "homepage", "landing", "product", "add", "create",
    "delete", "remove", "edit", "update", "store", "shop", "cart", "checkout",
    "page", "blog", "post", "gallery", "portfolio", "about", "contact",
    "style", "theme", "color", "font", "layout", "redesign", "reskin",
    "upload", "image", "file", "deploy", "publish", "security", "ssl",
    "analytics", "seo", "email", "template", "invoice", "social",
    "first", "time", "site", "new", "setup", "initial",
}

CONFIDENCE_THRESHOLD = 0.6


def load_habits() -> dict:
    """Load habits from JSON file, return empty structure on any error."""
    if HABITS_FILE.exists():
        try:
            return json.loads(HABITS_FILE.read_text())
        except Exception:
            pass
    return {"version": 1, "routes": {}, "blacklist": {}}


def save_habits(habits: dict):
    """Persist habits to JSON file."""
    try:
        HABITS_FILE.write_text(json.dumps(habits, indent=2))
    except Exception as e:
        log.warning(f"Could not save habits: {e}")


def _extract_signature(text: str) -> list:
    """Extract task-relevant keywords from text."""
    words = set(re.sub(r"[^a-z0-9 ]", " ", text.lower()).split())
    return sorted(words & TASK_VOCAB)


def _make_task_key(text: str) -> str:
    """Create a stable key from request text."""
    sig = _extract_signature(text)
    return "_".join(sig[:4]) if sig else "unknown"


def _calc_confidence(habit: dict) -> float:
    """Weighted moving average: recent results count 2x."""
    recent = habit.get("recent", [])[-10:]
    total_s = habit.get("successes", 0)
    total_f = habit.get("failures", 0)
    old_s = max(total_s - sum(recent), 0)
    old_f = max(total_f - recent.count(0), 0)
    old_total = old_s + old_f
    recent_s = sum(recent)
    recent_total = len(recent)
    if recent_total + old_total == 0:
        return 0.0
    return (recent_s * 2 + old_s) / (recent_total * 2 + max(old_total, 1))


def _check_conditions(conditions: dict, context: dict) -> bool:
    """Evaluate habit conditions against current context."""
    for key, expr in conditions.items():
        val = context.get(key)
        if val is None:
            continue
        try:
            if isinstance(expr, str) and expr.startswith("<") and not (int(val) < int(expr[1:])):
                return False
            if isinstance(expr, str) and expr.startswith(">=") and not (int(val) >= int(expr[2:])):
                return False
        except (ValueError, TypeError):
            continue
    return True


def match_habit(request_text: str, habits: dict, context: dict = None) -> tuple:
    """Find best habit matching this request. Returns (key, habit) or (None, None)."""
    words = set(re.sub(r"[^a-z0-9 ]", " ", request_text.lower()).split())
    best_key = None
    best_habit = None
    best_score = 0.0

    for key, habit in habits.get("routes", {}).items():
        if habit.get("status") not in ("proven", "learning"):
            continue
        if habit.get("confidence", 0) < CONFIDENCE_THRESHOLD:
            continue

        sig_words = set(habit.get("signature", []))
        overlap = len(words & sig_words)
        if overlap < 2:
            continue

        score = overlap * habit.get("confidence", 0.5)

        conditions = habit.get("conditions", {})
        if conditions and context:
            if not _check_conditions(conditions, context):
                continue

        if habit.get("parent"):
            score *= 1.2

        if score > best_score:
            best_score = score
            best_key = key
            best_habit = habit

    return best_key, best_habit


def check_blacklist(request_text: str, habits: dict) -> dict | None:
    """Check if this request matches a blacklisted route. Returns entry or None."""
    words = set(re.sub(r"[^a-z0-9 ]", " ", request_text.lower()).split())
    for key, entry in habits.get("blacklist", {}).items():
        sig_words = set(entry.get("signature", []))
        if len(words & sig_words) >= 2:
            return entry
    return None


def record_success(habits: dict, task_key: str, signature: list, path: list, token_count: int = 0):
    """Record a successful route — reinforce or create new habit."""
    if task_key not in habits["routes"]:
        habits["routes"][task_key] = {
            "signature": signature,
            "path": path,
            "confidence": 0.0,
            "successes": 0,
            "failures": 0,
            "recent": [],
            "status": "learning",
            "conditions": {},
            "forks": [],
        }
    h = habits["routes"][task_key]
    h["successes"] = h.get("successes", 0) + 1
    h["recent"] = (h.get("recent", []) + [1])[-10:]
    h["last_used"] = datetime.now(timezone.utc).isoformat()
    h["confidence"] = _calc_confidence(h)
    if h.get("path") != path:
        h["path"] = path
    if h["successes"] >= 3 and h["confidence"] >= 0.7:
        h["status"] = "proven"
    save_habits(habits)


def record_failure(habits: dict, task_key: str, signature: list, path: list, failure_reason: str, context: dict = None):
    """Record failure — degrade confidence, fork or blacklist."""
    if task_key in habits.get("routes", {}):
        h = habits["routes"][task_key]
        h["failures"] = h.get("failures", 0) + 1
        h["recent"] = (h.get("recent", []) + [0])[-10:]
        h["confidence"] = _calc_confidence(h)

        if h["confidence"] < 0.3:
            h["status"] = "suspended"
            log.warning(f"HABIT suspended: {task_key} (confidence={h['confidence']:.2f})")
        elif h["confidence"] < 0.6 and h.get("status") == "proven":
            h["status"] = "learning"
            log.info(f"HABIT demoted: {task_key} (confidence={h['confidence']:.2f})")

        if context and h.get("status") != "suspended":
            fork_key = task_key + "_fork_" + str(len(h.get("forks", [])))
            h.setdefault("forks", []).append(fork_key)
            habits["routes"][fork_key] = {
                "signature": signature + _extract_signature(failure_reason),
                "path": path,
                "confidence": 0.0,
                "successes": 0,
                "failures": 0,
                "recent": [],
                "status": "learning",
                "conditions": context,
                "parent": task_key,
                "forks": [],
                "assessment": failure_reason,
            }
            log.info(f"HABIT forked: {task_key} -> {fork_key}")
    else:
        habits.setdefault("blacklist", {})[task_key] = {
            "signature": signature,
            "failed_path": path,
            "failure_mode": failure_reason,
            "recorded": datetime.now(timezone.utc).isoformat(),
        }
    save_habits(habits)


def _repair_json(raw):
    import re as _rj
    raw = _rj.sub('^```json\\s*', '', raw.strip())
    raw = _rj.sub('```\\s*$', '', raw.strip())
    raw = _rj.sub(',\\s*([}\\]])', '\\1', raw)
    out = []
    for ln in raw.splitlines():
        if not ln.strip().startswith('//'):
            out.append(ln)
    nl = chr(10)
    return nl.join(out).strip()


def _get_text(resp) -> str:
    """Safely extract text from an Anthropic API response."""
    if resp and hasattr(resp, "content") and resp.content:
        for block in resp.content:
            if hasattr(block, "text") and block.text:
                return block.text.strip()
    return ""


# ── HECATE — path-aware classifier ───────────────────────────────────────────

def hecate(ctx: dict, request_text: str) -> dict:
    """
    HECATE reads the request and returns a route:
      - nodes: ordered list of sephiroth to traverse
      - transitions: each edge between consecutive nodes with path name + quality

    Uses the fast model. Falls back to DEFAULT_ROUTE on any failure.
    """

    # ── YESOD: check habits before classification ──
    habits = load_habits()

    # Check blacklist — avoid known bad paths
    blacklisted = check_blacklist(request_text, habits)
    if blacklisted:
        log.info("HECATE: avoiding blacklisted route: %s", blacklisted.get("failure_mode", "unknown"))

    # Check for proven habit — skip classification
    habit_key, habit = match_habit(request_text, habits)
    if habit and habit.get("status") == "proven":
        log.info("HECATE: using proven habit %s (confidence=%.2f, successes=%d)", habit_key, habit.get("confidence", 0), habit.get("successes", 0))
        return {"nodes": habit["path"], "transitions": [], "_habit": True, "_habit_key": habit_key}
    # Skip HECATE entirely if no routing rules exist (no HECATE.md)
    if not ctx.get('hecate'):
        return DEFAULT_ROUTE

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
            timeout=180,
        )
        raw = resp.content[0].text.strip()

        # extract JSON object even if model wraps it
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if not match:
            raise ValueError(f"no JSON object in: {raw!r}")

        route = json.loads(_repair_json(match.group()))

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


# ── cart JS injected into product and cart pages ─────────────────────────────

_CART_JS = """<script>
(function(){
  var CK='hermes_cart';
  function gc(){try{return JSON.parse(localStorage.getItem(CK))||[];}catch(e){return[];}}
  function sc(c){localStorage.setItem(CK,JSON.stringify(c));ub();}
  function ub(){
    var c=gc(),n=c.reduce(function(s,i){return s+i.qty;},0);
    var b=document.getElementById('hermes-cart-badge');
    if(b){b.textContent=n;b.style.display=n?'inline-block':'none';}
    var ct=document.getElementById('hermes-cart-items');
    if(ct){rc(ct,c);}
    var tt=document.getElementById('hermes-cart-total');
    if(tt){tt.textContent='$'+c.reduce(function(s,i){return s+i.price*i.qty;},0).toFixed(2);}
  }
  function rc(el,c){
    if(!c.length){el.innerHTML='<p style="opacity:0.6">Your cart is empty.</p>';return;}
    var h='';
    c.forEach(function(it,i){
      h+='<div style="display:flex;justify-content:space-between;align-items:center;padding:0.6em 0;border-bottom:1px solid rgba(128,128,128,0.2)">'
        +'<div><strong>'+it.name+'</strong>'+(it.variant?' <span style="opacity:0.6">'+it.variant+'</span>':'')
        +'<br><span style="opacity:0.7">$'+it.price.toFixed(2)+' × '+it.qty+'</span></div>'
        +'<div><button data-cart-remove="'+i+'" style="background:none;border:1px solid;padding:0.3em 0.6em;cursor:pointer;opacity:0.7">remove</button></div>'
        +'</div>';
    });
    el.innerHTML=h;
  }
  document.addEventListener('click',function(e){
    var btn=e.target.closest('[data-slug]');
    if(btn&&!btn.dataset.cartRemove){
      e.preventDefault();
      var c=gc(),s=btn.dataset.slug,v=btn.dataset.variant||'';
      var ex=c.find(function(i){return i.slug===s&&i.variant===v;});
      if(ex){ex.qty++;}else{c.push({slug:s,name:btn.dataset.name||s,price:parseFloat(btn.dataset.price||0),variant:v,qty:1});}
      sc(c);
      var orig=btn.textContent;btn.textContent='Added!';setTimeout(function(){btn.textContent=orig;},800);
    }
    var rm=e.target.closest('[data-cart-remove]');
    if(rm){e.preventDefault();var c=gc();c.splice(parseInt(rm.dataset.cartRemove),1);sc(c);}
    if(e.target.closest('#hermes-checkout')){
      e.preventDefault();var c=gc();if(!c.length)return;
      fetch('/api/checkout',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({items:c})})
      .then(function(r){return r.json();})
      .then(function(d){if(d.url){window.location.href=d.url;}else{alert(d.error||'Checkout error');}})
      .catch(function(){alert('Connection error');});
    }
  });
  ub();
})();
</script>"""


def _inject_cart_js(html: str) -> str:
    """Insert cart JS before </body>."""
    tag = "</body>"
    idx = html.lower().rfind(tag)
    if idx != -1:
        return html[:idx] + _CART_JS + html[idx:]
    return html + _CART_JS


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

    # Retry up to 3 times on API errors (500, 529 overloaded)
    import time as _time
    for _attempt in range(3):
        try:
            resp = client.messages.create(
                model=MODEL_RENDER,
                max_tokens=MAX_TOKENS,
                system=system,
                messages=[{"role": "user", "content": request_text}],
                timeout=90,
            )
            break
        except Exception as _e:
            log.warning(f"RENDER attempt {_attempt+1}/3 failed: {_e}")
            if _attempt < 2:
                _time.sleep(2 ** _attempt)
            else:
                raise
    raw = _get_text(resp)
    if not raw:
        raise ValueError('render returned empty response from API')

    # Strip markdown fences and extract just the HTML
    if '<!DOCTYPE' in raw or '<html' in raw:
        # Find the actual HTML start
        for marker in ['<!DOCTYPE html>', '<!DOCTYPE HTML>', '<!doctype html>', '<html']:
            idx = raw.find(marker)
            if idx >= 0:
                raw = raw[idx:]
                break
        # Trim anything after closing </html>
        end = raw.rfind('</html>')
        if end >= 0:
            raw = raw[:end + 7]

    # Fix truncated HTML — if model hit token limit
    if "</html>" not in raw.lower():
        if "</body>" not in raw.lower():
            raw += "\n</body>\n</html>"
        else:
            raw += "\n</html>"

    return _inject_chat_js(raw)


# ── build — static output ─────────────────────────────────────────────────────

def build(prompt: str = "render the site homepage") -> str:
    """
    Run the full tree render and save the result to static/index.html.
    Records habit outcomes for the Yesod learning system.
    """
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    habits = load_habits()
    route = None

    try:
        ctx   = load_vessel()
        route = hecate(ctx, prompt)
        html  = render(ctx, route, prompt)

        if not html or len(html) < 50:
            raise ValueError("render returned empty or too-short HTML")

        INDEX_HTML.write_text(html)
        log.info(f"BUILD complete -> static/index.html ({len(html)} chars, nodes={route['nodes']})")

        # Record success in habit system
        task_key = _make_task_key(prompt)
        sig = _extract_signature(prompt)
        record_success(habits, task_key, sig, route.get("nodes", []), len(html))

        # Build product pages if products/ exists
        products = load_products()
        if products:
            try:
                _build_product_pages(ctx, products)
                _build_cart_page(ctx)
            except Exception as pe:
                log.warning(f"BUILD product pages error (non-fatal): {pe}")

        return html

    except Exception as e:
        log.error(f"BUILD FAILED: {e}")
        # Record failure in habit system
        task_key = _make_task_key(prompt)
        sig = _extract_signature(prompt)
        route_nodes = route.get("nodes", []) if route else []
        record_failure(habits, task_key, sig, route_nodes, str(e))

        # Preserve existing index.html — don't overwrite with nothing
        if INDEX_HTML.exists():
            log.info("BUILD preserved existing index.html after failure")
            return INDEX_HTML.read_text()
        else:
            log.info("BUILD writing fallback HTML")
            INDEX_HTML.write_text(FALLBACK_HTML)
            return FALLBACK_HTML


def _build_product_pages(ctx: dict, products: list[dict]):
    """Generate individual product pages through the tree."""
    prod_dir = STATIC_DIR / "products"
    prod_dir.mkdir(parents=True, exist_ok=True)

    for product in products:
        slug = product["slug"]
        output = prod_dir / f"{slug}.html"

        # Incremental: skip if product source hasn't changed
        source = PRODUCTS_DIR / f"{slug}.md"
        if output.exists() and source.exists():
            if output.stat().st_mtime > source.stat().st_mtime:
                log.info(f"BUILD skip product {slug} (unchanged)")
                continue

        variants_str = ""
        if product.get("variants"):
            for v in product["variants"]:
                variants_str += f"\nVariant: {v.get('name', '')}: {', '.join(v.get('options', []))}"

        product_prompt = (
            f"Render a product page for: {product['name']}\n"
            f"Price: ${product['price']} {product.get('currency', 'USD')}\n"
            f"Description: {product.get('description', '')}\n"
            f"Images: {product.get('images', [])}"
            f"{variants_str}\n"
            f"Stock: {product.get('stock', 'available')}\n\n"
            f"Include an 'Add to Cart' button with these exact data attributes: "
            f"data-slug='{slug}' data-price='{product['price']}' "
            f"data-name='{product['name']}'. "
            f"Include a link back to / for browsing. "
            f"The cart and chat JS will be injected automatically."
        )
        route = hecate(ctx, product_prompt)
        html  = render(ctx, route, product_prompt)
        html  = _inject_cart_js(html)
        output.write_text(html)
        log.info(f"BUILD product → static/products/{slug}.html")


def _build_cart_page(ctx: dict):
    """Generate the shopping cart page through the tree."""
    cart_prompt = (
        "Render a shopping cart page. "
        "Include a div with id='hermes-cart-items' where cart items will be rendered by JavaScript. "
        "Include a span with id='hermes-cart-total' showing the total. "
        "Include a checkout button with id='hermes-checkout'. "
        "Include a 'Continue Shopping' link back to /. "
        "The cart JS is injected automatically — do not write cart logic. "
        "Just provide the container elements with the correct IDs."
    )
    route = hecate(ctx, cart_prompt)
    html  = render(ctx, route, cart_prompt)
    html  = _inject_cart_js(html)
    (STATIC_DIR / "cart.html").write_text(html)
    log.info("BUILD cart → static/cart.html")


def _build_template(prompt: str, template_type: str) -> str:
    """Render a template (email, invoice, social) through the tree."""
    ctx = load_vessel()

    malkuth_overrides = {
        "email": (
            "Render an email template in HTML. "
            "Use inline CSS only (email clients don't support <style> blocks). "
            "Keep width under 600px. Simple, clean layout. "
            "Include placeholder variables in {{double_braces}} for: "
            "{{customer_name}}, {{order_id}}, {{tracking_url}}, etc. "
            "The email should feel personal, not automated."
        ),
        "invoice": (
            "Render an invoice in HTML. "
            "Professional layout with: company name, invoice number, date, "
            "line items table (item, qty, unit price, total), subtotal, tax, grand total. "
            "Use placeholder variables in {{double_braces}}. "
            "Print-friendly CSS. Clean and authoritative."
        ),
        "social": (
            "Generate social media content as plain text. "
            "Return the post text in the vessel's voice. "
            "Keep within typical character limits (280 for twitter-like, 500 for longer). "
            "Include relevant hashtag suggestions at the end."
        ),
    }

    original_malkuth = ctx["malkuth"]
    override = malkuth_overrides.get(template_type)
    if override:
        ctx["malkuth"] = override

    route  = hecate(ctx, prompt)
    result = render(ctx, route, prompt)

    # Restore and save
    ctx["malkuth"] = original_malkuth
    output_dir = GENERATED_DIR / f"{template_type}s"
    output_dir.mkdir(parents=True, exist_ok=True)
    slug = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    ext  = "html" if template_type in ("email", "invoice") else "txt"
    output_path = output_dir / f"{slug}.{ext}"
    output_path.write_text(result)
    log.info(f"TEMPLATE {template_type} → {output_path}")
    return str(output_path)


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



@app.post("/upload")
async def upload_file(request: Request):
    """Upload a file to the vessel's static/uploads/ directory."""
    if not check_token(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    form = await request.form()
    uploaded = form.get("file")
    if not uploaded:
        return JSONResponse({"error": "no file provided"}, status_code=400)

    uploads_dir = STATIC_DIR / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    # Sanitize filename
    import re as _re
    safe_name = _re.sub(r'[^a-zA-Z0-9._-]', '_', uploaded.filename)
    if not safe_name:
        safe_name = "upload_" + str(int(__import__('time').time()))

    dest = uploads_dir / safe_name
    file_content = await uploaded.read()
    dest.write_bytes(file_content)

    log.info(f"UPLOAD {safe_name} ({len(file_content)} bytes) -> {dest}")
    return JSONResponse({
        "ok": True,
        "filename": safe_name,
        "path": str(dest),
        "size": len(file_content),
    })


@app.post("/build")
async def trigger_build(request: Request):
    """Trigger a rebuild. Requires X-Build-Token header if BUILD_TOKEN is set in .env.
    Accepts JSON body with optional 'prompt' and 'type' fields.
    type: 'page' (default), 'email', 'invoice', 'social'
    """
    if not check_token(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if not (VESSEL_DIR / "VESSEL.md").exists():
        return JSONResponse({"error": "no VESSEL.md — run setup first"}, status_code=400)

    # Parse body — supports both plain text and JSON
    body = (await request.body()).decode().strip()
    build_type = "page"
    prompt = "render the site homepage"

    if body:
        try:
            data = json.loads(body)
            prompt     = data.get("prompt", prompt)
            build_type = data.get("type", "page")
        except (json.JSONDecodeError, AttributeError):
            prompt = body  # plain text prompt

    if build_type == "page":
        try:
            html = build(prompt)
            return JSONResponse({"status": "ok", "chars": len(html), "type": "page"})
        except Exception as _build_err:
            log.error(f"BUILD failed: {_build_err}")
            return JSONResponse({"error": f"Build failed: {str(_build_err)[:200]}"}, status_code=500)
    elif build_type in ("email", "invoice", "social"):
        path = _build_template(prompt, build_type)
        return JSONResponse({"status": "ok", "type": build_type, "path": path})
    else:
        return JSONResponse({"error": f"unknown build type: {build_type}"}, status_code=400)


# ── commerce API ─────────────────────────────────────────────────────────────

@app.get("/api/products")
async def api_products():
    """Public JSON product catalog for client-side cart."""
    products = load_products(active_only=True)
    safe = []
    for p in products:
        safe.append({
            "slug":        p.get("slug"),
            "name":        p.get("name"),
            "price":       p.get("price"),
            "currency":    p.get("currency", "USD"),
            "images":      p.get("images", []),
            "variants":    p.get("variants", []),
            "stock":       p.get("stock"),
            "description": p.get("description", "")[:200],
        })
    return JSONResponse(safe)


# ── room rental endpoints ─────────────────────────────────────────────────────

def _load_room(room_id: str) -> dict | None:
    """Load room config JSON, or None if not found."""
    p = ROOMS_DIR / f"{room_id}.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            return None
    return None


def _save_room(cfg: dict):
    """Save room config JSON."""
    ROOMS_DIR.mkdir(parents=True, exist_ok=True)
    p = ROOMS_DIR / f"{cfg['room_id']}.json"
    p.write_text(json.dumps(cfg, indent=2))


def _is_room_active(cfg: dict) -> bool:
    """Check if a room is active and not expired."""
    if cfg.get("status") != "active":
        return False
    try:
        expires = datetime.fromisoformat(cfg["expires_at"])
        return expires > datetime.now(timezone.utc)
    except Exception:
        return False


def _check_room_expiry():
    """Deactivate expired rooms."""
    if not ROOMS_DIR.exists():
        return
    for p in ROOMS_DIR.glob("*.json"):
        try:
            cfg = json.loads(p.read_text())
        except Exception:
            continue
        if cfg.get("status") == "active":
            try:
                expires = datetime.fromisoformat(cfg["expires_at"])
                if expires < datetime.now(timezone.utc):
                    cfg["status"] = "expired"
                    p.write_text(json.dumps(cfg, indent=2))
                    log.info(f"ROOM {cfg.get('room_id')} expired")
            except Exception:
                pass


@app.get("/api/rooms")
async def api_rooms():
    """Return room availability — 12 floors x 12 rooms = 144 rooms."""
    _check_room_expiry()
    rooms = {}
    for floor in range(1, 13):
        floor_data = {}
        for room in range(1, 13):
            rid = f"{floor}-{room:02d}"
            cfg = _load_room(rid)
            if cfg and _is_room_active(cfg):
                floor_data[f"{room:02d}"] = {
                    "status": "occupied",
                    "url": f"/room/{rid}/",
                }
            else:
                floor_data[f"{room:02d}"] = {"status": "vacant"}
        rooms[str(floor)] = floor_data
    return JSONResponse(rooms)


@app.post("/api/rent")
async def api_rent(request: Request):
    """Create a Stripe Checkout Session for room rental."""
    if not STRIPE_SECRET_KEY:
        return JSONResponse({"error": "payments not configured"}, status_code=503)

    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid request"}, status_code=400)

    room_id = str(data.get("room_id", ""))
    plan_key = data.get("plan", "")
    email = data.get("email", "").strip()

    # Validate
    # Validate floor-room format (e.g. "7-03")
    import re as _re
    if not _re.match(r"^(\d{1,2})-(\d{2})$", room_id):
        return JSONResponse({"error": "invalid room"}, status_code=400)
    _floor, _room = room_id.split("-")
    if not (1 <= int(_floor) <= 12 and 1 <= int(_room) <= 12):
        return JSONResponse({"error": "invalid room"}, status_code=400)
    if plan_key not in ROOM_PLANS:
        return JSONResponse({"error": "invalid plan"}, status_code=400)
    if not email or "@" not in email:
        return JSONResponse({"error": "valid email required"}, status_code=400)

    # Check room isn't occupied
    _check_room_expiry()
    cfg = _load_room(room_id)
    if cfg and _is_room_active(cfg):
        return JSONResponse({"error": "room is occupied"}, status_code=409)

    plan = ROOM_PLANS[plan_key]
    # Calculate price with premium floor surcharge
    price_cents = plan["price"]
    if int(room_id.split("-")[0]) >= 7:
        price_cents += PREMIUM_FLOOR_SURCHARGE

    import stripe
    stripe.api_key = STRIPE_SECRET_KEY

    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            customer_email=email,
            line_items=[{
                "price_data": {
                    "currency": plan["currency"],
                    "product_data": {
                        "name": f"Room {room_id} — {plan['label']}",
                        "description": f"Static page hosting at Grand Internet Hotel for {plan['label'].lower()}",
                    },
                    "unit_amount": price_cents,
                },
                "quantity": 1,
            }],
            mode="payment",
            success_url=f"https://thedoorman.prometheus7.com/room-success.html?room={room_id}&session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url="https://thedoorman.prometheus7.com/#rent",
            metadata={
                "type": "room_rental",
                "room_id": room_id,
                "plan": plan_key,
                "email": email,
            },
        )
        return JSONResponse({"url": session.url})
    except Exception as e:
        log.warning(f"STRIPE rent error: {e}")
        return JSONResponse({"error": "payment service error"}, status_code=502)


@app.post("/api/room/{room_id}/upload-info")
async def room_upload_info(room_id: str, request: Request):
    """Get upload token for a room. Requires matching email."""
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid request"}, status_code=400)

    email = data.get("email", "").strip().lower()
    cfg = _load_room(room_id)

    if not cfg:
        return JSONResponse({"error": "room not found"}, status_code=404)
    if cfg.get("tenant_email", "").lower() != email:
        return JSONResponse({"error": "email does not match"}, status_code=403)
    if not _is_room_active(cfg):
        return JSONResponse({"error": "room is not active"}, status_code=410)

    return JSONResponse({
        "upload_token": cfg.get("upload_token", ""),
        "room_url": f"/room/{room_id}/",
        "expires_at": cfg.get("expires_at", ""),
        "max_storage_mb": cfg.get("max_storage_mb", 50),
    })


@app.post("/room/{room_id}/upload")
async def room_upload(room_id: str, request: Request):
    """Upload a file to a rented room. Auth via X-Upload-Token header."""
    cfg = _load_room(room_id)
    if not cfg:
        return JSONResponse({"error": "room not found"}, status_code=404)
    if not _is_room_active(cfg):
        return JSONResponse({"error": "room not active or expired"}, status_code=410)

    token = request.headers.get("x-upload-token", "")
    if not token or token != cfg.get("upload_token", ""):
        return JSONResponse({"error": "invalid upload token"}, status_code=403)

    # Parse multipart form data
    from fastapi import UploadFile
    form = await request.form()
    uploaded = []

    room_dir = ROOMS_DIR / room_id
    room_dir.mkdir(parents=True, exist_ok=True)

    # Check storage limit
    max_bytes = cfg.get("max_storage_mb", 50) * 1024 * 1024
    current_size = sum(f.stat().st_size for f in room_dir.rglob("*") if f.is_file())

    for key in form:
        upload = form[key]
        if hasattr(upload, 'read'):
            file_data = await upload.read()
            if current_size + len(file_data) > max_bytes:
                return JSONResponse({"error": "storage limit exceeded"}, status_code=413)

            # Sanitize filename
            filename = upload.filename or "upload"
            filename = re.sub(r'[^a-zA-Z0-9._-]', '_', filename)

            # Don't allow path traversal
            if '..' in filename or '/' in filename or '\\' in filename:
                continue

            dest = room_dir / filename
            dest.write_bytes(file_data)
            current_size += len(file_data)
            uploaded.append(filename)

    return JSONResponse({"uploaded": uploaded, "count": len(uploaded)})


def _activate_room(session: dict):
    """Activate a room after successful Stripe payment."""
    metadata = session.get("metadata", {})
    room_id = metadata.get("room_id", "")
    plan_key = metadata.get("plan", "")
    email = metadata.get("email", "")

    if not room_id or plan_key not in ROOM_PLANS:
        log.warning(f"ROOM activation failed: bad metadata {metadata}")
        return

    plan = ROOM_PLANS[plan_key]
    now = datetime.now(timezone.utc)

    from datetime import timedelta
    import secrets

    upload_token = secrets.token_hex(24)

    cfg = {
        "room_id": room_id,
        "tenant_name": session.get("customer_details", {}).get("name", "Guest"),
        "tenant_email": email,
        "plan": plan_key,
        "amount_paid": plan["price"],
        "currency": plan["currency"],
        "paid_at": now.isoformat(),
        "expires_at": (now + timedelta(days=plan["days"])).isoformat(),
        "stripe_session_id": session.get("id", ""),
        "upload_token": upload_token,
        "status": "active",
        "max_storage_mb": 50,
    }

    _save_room(cfg)

    # Create room static directory with default page
    room_dir = ROOMS_DIR / room_id
    room_dir.mkdir(parents=True, exist_ok=True)

    default_html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Room {room_id} — Grand Internet Hotel</title>
<style>
body {{ background: #0a0812; color: #fff5e0; font-family: monospace; display: flex; align-items: center; justify-content: center; min-height: 100vh; text-align: center; }}
h1 {{ color: #ffd700; font-size: 24px; }}
p {{ color: #c2a366; font-size: 14px; line-height: 2; }}
</style>
</head>
<body>
<div>
<h1>ROOM {room_id}</h1>
<p>This room has been checked in.<br>The guest is setting things up.</p>
</div>
</body>
</html>"""

    index = room_dir / "index.html"
    if not index.exists():
        index.write_text(default_html)

    log.info(f"ROOM {room_id} activated: plan={plan_key}, email={email}, expires={cfg['expires_at']}")


@app.post("/api/checkout")
async def api_checkout(request: Request):
    """Create a Stripe Checkout Session from cart items."""
    if not STRIPE_SECRET_KEY:
        return JSONResponse({"error": "payments not configured"}, status_code=503)

    import stripe
    stripe.api_key = STRIPE_SECRET_KEY

    try:
        data  = await request.json()
        items = data.get("items", [])
    except Exception:
        return JSONResponse({"error": "invalid request"}, status_code=400)

    if not items:
        return JSONResponse({"error": "cart is empty"}, status_code=400)

    # Validate against actual catalog
    catalog = {p["slug"]: p for p in load_products()}
    line_items = []
    for item in items:
        slug    = item.get("slug", "")
        product = catalog.get(slug)
        if not product:
            return JSONResponse({"error": f"unknown product: {slug}"}, status_code=400)

        line_items.append({
            "price_data": {
                "currency": product.get("currency", "usd").lower(),
                "product_data": {
                    "name":        product["name"],
                    "description": item.get("variant", "") or product.get("description", "")[:100],
                },
                "unit_amount": int(float(product["price"]) * 100),
            },
            "quantity": item.get("qty", 1),
        })

    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=line_items,
            mode="payment",
            success_url=STRIPE_SUCCESS_URL + "?session_id={CHECKOUT_SESSION_ID}",
            cancel_url=STRIPE_CANCEL_URL,
            metadata={"source": "hermes-webkit"},
        )
        return JSONResponse({"url": session.url})
    except stripe.error.StripeError as e:
        log.warning(f"STRIPE checkout error: {e}")
        return JSONResponse({"error": "payment service error"}, status_code=502)


@app.post("/webhook/stripe")
async def stripe_webhook(request: Request):
    """Receive Stripe webhook events. Writes order JSON on payment success."""
    if not STRIPE_WEBHOOK_SECRET:
        return JSONResponse({"error": "webhook not configured"}, status_code=503)

    import stripe
    stripe.api_key = STRIPE_SECRET_KEY

    payload    = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except (ValueError, stripe.error.SignatureVerificationError):
        return JSONResponse({"error": "invalid signature"}, status_code=400)

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        _create_order(session)
    elif event["type"] == "charge.refunded":
        _handle_refund(event["data"]["object"])

    return JSONResponse({"received": True})


def _create_order(session: dict):
    """Write order JSON from completed Stripe Checkout Session."""
    ORDERS_DIR.mkdir(parents=True, exist_ok=True)

    # Idempotent — check for duplicate
    sid = session.get("id", "")
    for f in ORDERS_DIR.glob("*.json"):
        try:
            existing = json.loads(f.read_text())
            if existing.get("stripe_session_id") == sid:
                log.info(f"ORDER duplicate skipped: {sid}")
                return
        except Exception:
            continue

    order_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + str(uuid.uuid4())[:6]
    order = {
        "id":                    order_id,
        "stripe_session_id":     sid,
        "stripe_payment_intent": session.get("payment_intent"),
        "customer_email":        session.get("customer_details", {}).get("email"),
        "customer_name":         session.get("customer_details", {}).get("name"),
        "amount_total":          session.get("amount_total"),
        "currency":              session.get("currency"),
        "status":                "paid",
        "created":               datetime.now(timezone.utc).isoformat(),
        "items":                 [],
        "supplier_status":       "pending",
    }

    # Retrieve line items from Stripe
    import stripe
    stripe.api_key = STRIPE_SECRET_KEY
    try:
        line_items = stripe.checkout.Session.list_line_items(session["id"])
        order["items"] = [
            {
                "name":     li.get("description", ""),
                "quantity": li.get("quantity", 1),
                "amount":   li.get("amount_total", 0),
            }
            for li in line_items.get("data", [])
        ]
    except Exception as e:
        log.warning(f"ORDER line items fetch failed: {e}")

    (ORDERS_DIR / f"{order_id}.json").write_text(json.dumps(order, indent=2))
    log.info(f"ORDER created: {order_id} (${order['amount_total']/100:.2f})")


def _handle_refund(charge: dict):
    """Update order status on refund."""
    if not ORDERS_DIR.exists():
        return
    pi = charge.get("payment_intent")
    for f in ORDERS_DIR.glob("*.json"):
        try:
            order = json.loads(f.read_text())
            if order.get("stripe_payment_intent") == pi:
                order["status"] = "refunded"
                f.write_text(json.dumps(order, indent=2))
                log.info(f"ORDER refunded: {order['id']}")
                return
        except Exception:
            continue


@app.get("/api/orders")
async def api_orders(request: Request):
    """List orders. Requires BUILD_TOKEN."""
    if not check_token(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return JSONResponse(load_orders()[:50])


# ── agents — background vessel tasks ─────────────────────────────────────────

AGENT_TOOLS = [
    {
        "name": "read_file",
        "description": "Read a file from the vessel directory.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Absolute file path"}},
            "required": ["path"],
        },
    },
    {
        "name": "http_request",
        "description": "Make an HTTP request to a whitelisted supplier API domain.",
        "input_schema": {
            "type": "object",
            "properties": {
                "method":  {"type": "string", "enum": ["GET", "POST", "PUT"]},
                "url":     {"type": "string", "description": "Full URL"},
                "headers": {"type": "object", "description": "Request headers"},
                "body":    {"type": "string", "description": "Request body (JSON string)"},
            },
            "required": ["method", "url"],
        },
    },
    {
        "name": "write_order_status",
        "description": "Update an order's status and add notes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
                "status":   {"type": "string", "enum": ["paid", "forwarded", "shipped", "delivered", "error"]},
                "notes":    {"type": "string", "description": "Status notes or tracking info"},
            },
            "required": ["order_id", "status"],
        },
    },
]


def _get_allowed_domains() -> set:
    """Extract allowed API domains from supplier configs."""
    domains = set()
    if SUPPLIERS_DIR.exists():
        for f in SUPPLIERS_DIR.glob("*.md"):
            try:
                text = f.read_text()
                parts = text.split("---", 2)
                if len(parts) >= 3:
                    meta = yaml.safe_load(parts[1])
                    endpoint = meta.get("api_endpoint", "")
                    if endpoint:
                        domains.add(urllib.parse.urlparse(endpoint).netloc)
            except Exception:
                continue
    return domains


def _exec_agent_tool(name: str, inp: dict) -> str:
    """Execute agent tools with safety constraints."""
    if name == "read_file":
        p = Path(inp.get("path", ""))
        if not str(p.resolve()).startswith(str(VESSEL_DIR.resolve())):
            return "Access denied: agents can only read vessel files"
        if not p.exists():
            return f"Not found: {inp['path']}"
        return p.read_text(errors="replace")[:8000]

    elif name == "http_request":
        url    = inp.get("url", "")
        domain = urllib.parse.urlparse(url).netloc
        allowed = _get_allowed_domains()
        if domain not in allowed:
            return f"Blocked: {domain} is not a whitelisted supplier domain. Allowed: {allowed}"
        method  = inp.get("method", "GET")
        headers = inp.get("headers", {})
        body    = inp.get("body", "").encode() if inp.get("body") else None
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read().decode(errors="replace")[:4000]
        except Exception as e:
            return f"HTTP error: {e}"

    elif name == "write_order_status":
        order_id   = inp.get("order_id", "")
        order_path = ORDERS_DIR / f"{order_id}.json"
        if not order_path.exists():
            return f"Order not found: {order_id}"
        order = json.loads(order_path.read_text())
        order["status"] = inp["status"]
        if inp.get("notes"):
            order.setdefault("notes", []).append({
                "time": datetime.now(timezone.utc).isoformat(),
                "note": inp["notes"],
            })
        order_path.write_text(json.dumps(order, indent=2))
        return f"Order {order_id} status updated to {inp['status']}"

    return f"Unknown tool: {name}"


async def _run_agent(agent_id: str, task: str, model: str, tools: list = None):
    """Background agent runner. Full vessel context, routed through the tree.
    Optionally with tools for supplier integration and order management."""
    try:
        ctx          = load_vessel()
        route        = hecate(ctx, task)
        tree_context = build_tree_context(route)

        tools_note = ""
        if tools:
            tools_note = (
                "\n\nYou have tools available: read_file (vessel files), "
                "http_request (whitelisted supplier APIs), and "
                "write_order_status (update order status)."
            )

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
Return your result as clear, structured text. Be thorough and complete.{tools_note}"""

        messages = [{"role": "user", "content": task}]
        max_iterations = 10

        for _ in range(max_iterations):
            kwargs = {
                "model": model,
                "max_tokens": MAX_TOKENS,
                "system": system,
                "messages": messages,
                "timeout": 120,
            }
            if tools:
                kwargs["tools"] = tools

            resp = await asyncio.to_thread(lambda kw=kwargs: client.messages.create(**kw))

            if resp.stop_reason == "end_turn":
                result = " ".join(b.text for b in resp.content if hasattr(b, "text")).strip()
                _agents[agent_id]["status"] = "complete"
                _agents[agent_id]["result"] = result
                log.info(f"AGENT {agent_id} complete ({len(result)} chars)")
                return

            if resp.stop_reason == "tool_use":
                messages.append({
                    "role": "assistant",
                    "content": [
                        {"type": b.type, "id": b.id, "name": b.name, "input": b.input}
                        if b.type == "tool_use" else {"type": "text", "text": b.text}
                        for b in resp.content
                    ],
                })
                tool_results = []
                for b in resp.content:
                    if b.type == "tool_use":
                        result_text = _exec_agent_tool(b.name, b.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": b.id,
                            "content": result_text,
                        })
                        log.info(f"AGENT {agent_id} tool {b.name}")
                messages.append({"role": "user", "content": tool_results})
                continue

            # Unexpected stop reason — extract what we have
            result = " ".join(b.text for b in resp.content if hasattr(b, "text")).strip()
            _agents[agent_id]["status"] = "complete"
            _agents[agent_id]["result"] = result or "(no output)"
            return

        # Hit max iterations
        _agents[agent_id]["status"] = "complete"
        _agents[agent_id]["result"] = "(agent reached max iterations)"

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
                        timeout=120,
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
                    timeout=120,
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

            # Process new orders — forward to suppliers via tooled agent
            if agents_running == 0 and not pending:
                new_orders = load_orders(status="paid")
                if new_orders and SUPPLIERS_DIR.exists():
                    order = new_orders[0]
                    agent_id = str(uuid.uuid4())[:8]
                    task_text = (
                        f"Process order {order['id']}. "
                        f"Read the order at {ORDERS_DIR / (order['id'] + '.json')}. "
                        f"Find the supplier config in {SUPPLIERS_DIR}/. "
                        f"Forward the order to the supplier API. "
                        f"Update the order status to 'forwarded' when done."
                    )
                    _agents[agent_id] = {
                        "id":      agent_id,
                        "task":    task_text,
                        "model":   MODEL_AGENT,
                        "status":  "running",
                        "created": datetime.now(timezone.utc).isoformat(),
                        "result":  None,
                        "error":   None,
                        "source":  "heartbeat-order",
                    }
                    asyncio.create_task(_run_agent(agent_id, task_text, MODEL_AGENT, tools=AGENT_TOOLS))
                    log.info(f"HEARTBEAT order agent {agent_id}: {order['id']}")

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
        try:
            html = build("render the site homepage for the first time")
            return HTMLResponse(content=html)
        except Exception as e:
            log.error(f"First build failed: {e}")
            return HTMLResponse(content=FALLBACK_HTML)

    body  = (await request.body()).decode().strip()
    query = request.query_params.get("q", "")
    label = f"/{path}" if path else "/"
    visitor_input = body or query or f"visitor arrived at {label}"

    _track_visit(label)
    log.info(f"→ {request.method} {label}  input={visitor_input[:80]!r}")

    try:
        ctx   = load_vessel()
        route = hecate(ctx, visitor_input)
        html  = render(ctx, route, visitor_input)
        log.info(f"<- {len(html)} chars  nodes={route['nodes']}")
        return HTMLResponse(content=html)
    except Exception as e:
        log.error(f"HANDLE render error: {e}")
        if INDEX_HTML.exists():
            return HTMLResponse(content=INDEX_HTML.read_text())
        return HTMLResponse(content=FALLBACK_HTML)


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
