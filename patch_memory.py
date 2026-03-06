#!/usr/bin/env python3
"""Patch bridge.py to persist operator chat history across sessions and restarts."""

path = "/root/hermes/bridge.py"
src = open(path).read()

# ── 1. Add history persistence helpers after _chat_sessions declaration ───────

old1 = '_chat_sessions: dict = {}  # session_id -> conversation history\n_chat_pending:  dict = {}  # session_id -> pending tool calls awaiting confirmation'

new1 = '''_chat_sessions: dict = {}  # session_id -> conversation history
_chat_pending:  dict = {}  # session_id -> pending tool calls awaiting confirmation

CHAT_HISTORY_FILE = VESSEL_DIR / "chat_history.json"
CHAT_HISTORY_MAX  = 100  # keep last N messages per session


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
        log.warning(f"CHAT history save error: {e}")'''

assert old1 in src, "chat_sessions marker not found"
src = src.replace(old1, new1, 1)

# ── 2. Load history from disk when starting or resuming a session ─────────────

old2 = '''    if not session_id or session_id not in _chat_sessions:
        session_id = str(uuid.uuid4())[:8]
        _chat_sessions[session_id] = []

    history = _chat_sessions[session_id]
    history.append({"role": "user", "content": message})'''

new2 = '''    if not session_id or session_id not in _chat_sessions:
        if not session_id:
            session_id = str(uuid.uuid4())[:8]
        _chat_sessions[session_id] = _load_chat_history(session_id)
        if _chat_sessions[session_id]:
            log.info(f"CHAT resumed session={session_id} ({len(_chat_sessions[session_id])} msgs)")

    history = _chat_sessions[session_id]
    history.append({"role": "user", "content": message})'''

assert old2 in src, "session init marker not found"
src = src.replace(old2, new2, 1)

# ── 3. Save history after every completed reply ───────────────────────────────

old3 = '''        reply, theme = _parse_theme(result["reply"])
        reply, studio = _parse_studio(reply)
        log.info("CHAT session=" + session_id + " turn=" + str(len(history) // 2))
        out = {"reply": reply, "session_id": session_id}'''

new3 = '''        reply, theme = _parse_theme(result["reply"])
        reply, studio = _parse_studio(reply)
        _save_chat_history(session_id, history)
        log.info("CHAT session=" + session_id + " turn=" + str(len(history) // 2))
        out = {"reply": reply, "session_id": session_id}'''

assert old3 in src, "chat reply marker not found"
src = src.replace(old3, new3, 1)

# ── 4. Save history after confirm replies too ─────────────────────────────────

old4 = '''    if result["done"]:
        reply, theme = _parse_theme(result["reply"])
        reply, studio = _parse_studio(reply)
        out = {"reply": reply, "session_id": session_id}'''

new4 = '''    if result["done"]:
        reply, theme = _parse_theme(result["reply"])
        reply, studio = _parse_studio(reply)
        _save_chat_history(session_id, history)
        out = {"reply": reply, "session_id": session_id}'''

assert old4 in src, "confirm reply marker not found"
src = src.replace(old4, new4, 1)

open(path, "w").write(src)
print("bridge.py patched — chat history will persist to disk")
