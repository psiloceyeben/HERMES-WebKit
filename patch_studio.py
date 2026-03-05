#!/usr/bin/env python3
"""Patch bridge.py to add studio config support."""
import re

path = "/root/hermes/bridge.py"
src = open(path).read()

# ── 1. Add CHAT_STUDIO_INSTRUCTIONS after CHAT_THEME_INSTRUCTIONS ─────────────

STUDIO_CONST = r'''
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

'''

# Insert before the _chat_sessions line
target = '# ── chat — direct terminal conversation ──'
assert target in src, f"marker not found: {target!r}"
src = src.replace(target, STUDIO_CONST + target, 1)

# ── 2. Add _parse_studio() after _parse_theme() ───────────────────────────────

PARSE_STUDIO = '''

def _parse_studio(reply: str):
    """Extract STUDIO_JSON block from reply. Returns (clean_reply, studio_dict_or_None)."""
    sm = re.search(r"\\nSTUDIO_JSON\\n(.*?)\\nSTUDIO_END", reply, re.DOTALL)
    if sm:
        try:
            studio = json.loads(sm.group(1).strip())
            reply = (reply[:sm.start()] + reply[sm.end():]).strip()
            return reply, studio
        except Exception as e:
            log.warning("STUDIO parse error: " + str(e))
    return reply, None

'''

target2 = '\n# ── visitor chat ──'
assert target2 in src, "visitor chat marker not found"
src = src.replace(target2, PARSE_STUDIO + target2, 1)

# ── 3. Update _build_chat_system to include CHAT_STUDIO_INSTRUCTIONS ──────────

old3 = '        + CHAT_THEME_INSTRUCTIONS\n    )'
new3 = '        + CHAT_THEME_INSTRUCTIONS\n        + CHAT_STUDIO_INSTRUCTIONS\n    )'
assert old3 in src, f"build_chat_system marker not found:\n{old3!r}"
src = src.replace(old3, new3, 1)

# ── 4. Update /chat endpoint ──────────────────────────────────────────────────

old4 = '''        reply, theme = _parse_theme(result["reply"])
        log.info("CHAT session=" + session_id + " turn=" + str(len(history) // 2))
        out = {"reply": reply, "session_id": session_id}
        if theme:
            out["theme"] = theme
            log.info("THEME: " + str(list(theme.keys())))
        return JSONResponse(out)'''

new4 = '''        reply, theme = _parse_theme(result["reply"])
        reply, studio = _parse_studio(reply)
        log.info("CHAT session=" + session_id + " turn=" + str(len(history) // 2))
        out = {"reply": reply, "session_id": session_id}
        if theme:
            out["theme"] = theme
            log.info("THEME: " + str(list(theme.keys())))
        if studio:
            out["studio"] = studio
            log.info("STUDIO: " + str(list(studio.keys())))
        return JSONResponse(out)'''

assert old4 in src, "chat endpoint marker not found"
src = src.replace(old4, new4, 1)

# ── 5. Update /chat/confirm endpoint ─────────────────────────────────────────

old5 = '''    if result["done"]:
        reply, theme = _parse_theme(result["reply"])
        out = {"reply": reply, "session_id": session_id}
        if theme:
            out["theme"] = theme
        return JSONResponse(out)'''

new5 = '''    if result["done"]:
        reply, theme = _parse_theme(result["reply"])
        reply, studio = _parse_studio(reply)
        out = {"reply": reply, "session_id": session_id}
        if theme:
            out["theme"] = theme
        if studio:
            out["studio"] = studio
        return JSONResponse(out)'''

assert old5 in src, "confirm endpoint marker not found"
src = src.replace(old5, new5, 1)

open(path, "w").write(src)
print("bridge.py patched successfully")
