#!/usr/bin/env python3
"""Patch bridge.py: when chat history hits 100 msgs, summarize with Sonnet into CONTEXT.md
instead of silently truncating. The summary is injected back into the system prompt."""

path = "/root/hermes/bridge.py"
src = open(path).read()

# ── 1. Update CHAT_HISTORY_MAX and add summarization logic ───────────────────

old1 = 'CHAT_HISTORY_FILE = VESSEL_DIR / "chat_history.json"\nCHAT_HISTORY_MAX  = 100  # keep last N messages per session'

new1 = '''CHAT_HISTORY_FILE   = VESSEL_DIR / "chat_history.json"
CHAT_CONTEXT_FILE   = VESSEL_DIR / "CONTEXT.md"
CHAT_HISTORY_MAX    = 100   # summarize when history reaches this length
CHAT_HISTORY_KEEP   = 20    # keep last N messages after summarization'''

assert old1 in src, "CHAT_HISTORY_MAX marker not found"
src = src.replace(old1, new1, 1)

# ── 2. Add summarization function after _save_chat_history ───────────────────

old2 = '''def _load_chat_history(session_id: str) -> list:'''

new2 = '''async def _summarize_and_compress(session_id: str, history: list, vessel_text: str) -> list:
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
    transcript = "\\n".join(lines)

    # Load existing context if any
    existing = ""
    if CHAT_CONTEXT_FILE.exists():
        existing = CHAT_CONTEXT_FILE.read_text().strip()

    prompt = f"""You are summarizing an operator conversation for a vessel.
The vessel identity: {vessel_text[:400]}

{"Existing context summary:\\n" + existing + "\\n\\n" if existing else ""}New conversation to add to the summary:
{transcript}

Write a concise running summary of what the operator has been working on, decisions made,
features built, and anything the vessel should remember going forward.
Plain text. No headers. 3-6 sentences."""

    try:
        resp = await asyncio.to_thread(
            lambda: client.messages.create(
                model=MODEL_RENDER,
                max_tokens=512,
                messages=[{"role": "user", "content": prompt}],
            )
        )
        summary = resp.content[0].text.strip()
        CHAT_CONTEXT_FILE.write_text(f"# Operator Context\n\n{summary}\n")
        log.info(f"CHAT session={session_id} summarized {len(older)} msgs → CONTEXT.md")
    except Exception as e:
        log.warning(f"CHAT summarization failed: {e}")

    return recent


def _load_chat_history(session_id: str) -> list:'''

assert old2 in src, "_load_chat_history marker not found"
src = src.replace(old2, new2, 1)

# ── 3. Inject CONTEXT.md into the operator system prompt ─────────────────────

old3 = '''def _build_chat_system(vessel_text: str, state_text: str, tree_context: str) -> str:
    return (
        "You are wearing this vessel. This is who you are:\\n\\n"
        + vessel_text
        + "\\n\\nCurrent state and memory:\\n"
        + state_text'''

new3 = '''def _load_context() -> str:
    """Load the rolling operator context summary if it exists."""
    if CHAT_CONTEXT_FILE.exists():
        return CHAT_CONTEXT_FILE.read_text().strip()
    return ""


def _build_chat_system(vessel_text: str, state_text: str, tree_context: str) -> str:
    context = _load_context()
    return (
        "You are wearing this vessel. This is who you are:\\n\\n"
        + vessel_text
        + "\\n\\nCurrent state and memory:\\n"
        + state_text
        + ("\\n\\nOperator session context (summary of past conversations):\\n" + context if context else "")'''

assert old3 in src, "_build_chat_system marker not found"
src = src.replace(old3, new3, 1)

# ── 4. Trigger summarization in _save_chat_history when limit hit ─────────────
# We can't call async from sync, so instead flag it and let /chat handle it.
# Simpler: make _save_chat_history check the length and truncate, but also
# write a "needs_summary" flag. Then in /chat, after saving, trigger summary.
# Even simpler: call summarization from the /chat endpoint after saving.

old4 = '''        _save_chat_history(session_id, history)
        log.info("CHAT session=" + session_id + " turn=" + str(len(history) // 2))
        out = {"reply": reply, "session_id": session_id}'''

new4 = '''        if len(history) >= CHAT_HISTORY_MAX:
            ctx2 = load_vessel()
            history = await _summarize_and_compress(session_id, history, ctx2["vessel"])
            _chat_sessions[session_id] = history
        _save_chat_history(session_id, history)
        log.info("CHAT session=" + session_id + " turn=" + str(len(history) // 2))
        out = {"reply": reply, "session_id": session_id}'''

assert old4 in src, "save_chat_history in /chat not found"
src = src.replace(old4, new4, 1)

# ── 5. Same for /chat/confirm ─────────────────────────────────────────────────

old5 = '''        _save_chat_history(session_id, history)
        out = {"reply": reply, "session_id": session_id}'''

new5 = '''        if len(history) >= CHAT_HISTORY_MAX:
            ctx2 = load_vessel()
            history = await _summarize_and_compress(session_id, history, ctx2["vessel"])
            _chat_sessions[session_id] = history
        _save_chat_history(session_id, history)
        out = {"reply": reply, "session_id": session_id}'''

assert old5 in src, "save_chat_history in /chat/confirm not found"
src = src.replace(old5, new5, 1)

open(path, "w").write(src)
print("bridge.py patched — summarization at 100 msgs → CONTEXT.md")
