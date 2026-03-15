#!/usr/bin/env python3
"""
patch_bridge.py — Add Yesod habits system + error handling safety net to all bridge.py files.

Patches:
  PART 1 — Habits system (load/save/match/blacklist/record, hecate integration, build integration)
  PART 2 — Error handling (FALLBACK_HTML, try/except wraps, timeouts, _get_text helper)

Usage: scp to server, run with python3 patch_bridge.py
"""

import glob
import json
import os
import re
import py_compile
import shutil
from pathlib import Path

# ══════════════════════════════════════════════════════════════════════════════
# CODE BLOCKS TO INSERT
# ══════════════════════════════════════════════════════════════════════════════

# ── Habits system functions (inserted before _repair_json) ───────────────────

HABITS_BLOCK = '''
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

'''

# ── FALLBACK_HTML constant ───────────────────────────────────────────────────

FALLBACK_HTML_BLOCK = '''
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

'''

# ── _get_text helper ─────────────────────────────────────────────────────────

GET_TEXT_HELPER = '''
def _get_text(resp) -> str:
    """Safely extract text from an Anthropic API response."""
    if resp and hasattr(resp, "content") and resp.content:
        for block in resp.content:
            if hasattr(block, "text") and block.text:
                return block.text.strip()
    return ""

'''


# ══════════════════════════════════════════════════════════════════════════════
# PATCHING FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

def patch_read_helper(code):
    """Add errors='replace' to read() helper to prevent UnicodeDecodeError."""
    old = 'def read(path: Path) -> str:\n    return path.read_text().strip() if path.exists() else ""'
    new = 'def read(path: Path) -> str:\n    return path.read_text(errors="replace").strip() if path.exists() else ""'
    if old in code:
        code = code.replace(old, new)
    return code


def patch_add_habits_block(code):
    """Insert habits system functions before _repair_json."""
    if "HABITS_FILE" in code:
        return code  # already patched

    repair_idx = code.find("\ndef _repair_json")
    if repair_idx == -1:
        repair_idx = code.find("def _repair_json")
    if repair_idx == -1:
        print("  WARNING: could not find _repair_json for habits insertion")
        return code

    code = code[:repair_idx] + HABITS_BLOCK + code[repair_idx:]
    return code


def patch_add_fallback_html(code):
    """Add FALLBACK_HTML constant after DEFAULT_ROUTE."""
    if "FALLBACK_HTML" in code:
        return code
    marker = "\n# \u2500\u2500 file helpers"
    idx = code.find(marker)
    if idx == -1:
        marker = "\n# -- file helpers"
        idx = code.find(marker)
    if idx == -1:
        # Try broader search
        idx = code.find("\ndef read(path: Path)")
        if idx != -1:
            # Back up to find the section comment
            prev_newline = code.rfind("\n\n", 0, idx)
            if prev_newline != -1:
                idx = prev_newline
    if idx == -1:
        print("  WARNING: could not find insertion point for FALLBACK_HTML")
        return code
    code = code[:idx] + "\n" + FALLBACK_HTML_BLOCK + code[idx:]
    return code


def patch_add_get_text(code):
    """Add _get_text() helper before HECATE section."""
    if "def _get_text(" in code:
        return code
    # Insert right before the HECATE marker
    marker = "# \u2500\u2500 HECATE"
    idx = code.find(marker)
    if idx == -1:
        print("  WARNING: could not find HECATE marker for _get_text")
        return code
    code = code[:idx] + GET_TEXT_HELPER + "\n" + code[idx:]
    return code


def patch_hecate_habits(code):
    """Modify hecate() to check habits first before classification."""
    # Find end of hecate docstring
    match = re.search(
        r'(def hecate\(ctx: dict, request_text: str\) -> dict:\s*""".*?""")',
        code, re.DOTALL
    )
    if not match:
        print("  WARNING: could not find hecate function for habits patch")
        return code

    # Check if hecate already has habits check (look in function body)
    hecate_start = match.start()
    next_def = code.find("\ndef ", hecate_start + 10)
    hecate_body = code[hecate_start:next_def] if next_def != -1 else code[hecate_start:]
    if "load_habits()" in hecate_body and "match_habit(" in hecate_body:
        return code

    end_of_docstring = match.end()
    next_newline = code.find('\n', end_of_docstring)
    if next_newline == -1:
        return code

    habits_check = (
        '\n'
        '    # ── YESOD: check habits before classification ──\n'
        '    habits = load_habits()\n'
        '\n'
        '    # Check blacklist — avoid known bad paths\n'
        '    blacklisted = check_blacklist(request_text, habits)\n'
        '    if blacklisted:\n'
        '        log.info("HECATE: avoiding blacklisted route: %s", blacklisted.get("failure_mode", "unknown"))\n'
        '\n'
        '    # Check for proven habit — skip classification\n'
        '    habit_key, habit = match_habit(request_text, habits)\n'
        '    if habit and habit.get("status") == "proven":\n'
        '        log.info("HECATE: using proven habit %s (confidence=%.2f, successes=%d)", habit_key, habit.get("confidence", 0), habit.get("successes", 0))\n'
        '        return {"nodes": habit["path"], "transitions": [], "_habit": True, "_habit_key": habit_key}\n'
    )

    code = code[:next_newline + 1] + habits_check + code[next_newline + 1:]
    return code


def patch_build_habits(code):
    """Modify build() to record habit outcomes."""
    # Find the build function
    build_pattern = 'def build(prompt: str = "render the site homepage") -> str:'
    build_idx = code.find(build_pattern)
    if build_idx == -1:
        print("  WARNING: could not find build function for habits patch")
        return code

    # Find the end of build — next def at column 0
    next_func = code.find("\ndef _build_product_pages(", build_idx + 1)
    if next_func == -1:
        print("  WARNING: could not find end of build function")
        return code

    # Check if build() specifically already has habit recording
    build_body = code[build_idx:next_func]
    if "record_success(" in build_body or "record_failure(" in build_body:
        return code

    new_build = '''def build(prompt: str = "render the site homepage") -> str:
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

'''

    code = code[:build_idx] + new_build + code[next_func:]
    return code


def patch_render_safety(code):
    """Replace resp.content[0].text with _get_text(resp) in render()."""
    # Only target the one in render (after the retry loop break)
    old = "    raw = resp.content[0].text.strip()\n\n    # Strip markdown"
    new = "    raw = _get_text(resp)\n    if not raw:\n        raise ValueError('render returned empty response from API')\n\n    # Strip markdown"
    if old in code:
        code = code.replace(old, new, 1)
    return code


def patch_visitor_reply_safety(code):
    """Wrap _visitor_reply in try/except."""
    # Check if already wrapped
    if "_visitor_reply" in code and "vessel is resting" in code:
        return code

    old_func = (
        'async def _visitor_reply(history: list, system: str) -> str:\n'
        '    """Single-turn visitor response \\u2014 no tools, no agentic loop."""\n'
        '    resp = await asyncio.to_thread(\n'
    )
    if old_func not in code:
        # Try without the em-dash
        old_func = (
            'async def _visitor_reply(history: list, system: str) -> str:\n'
            '    """Single-turn visitor response -- no tools, no agentic loop."""\n'
            '    resp = await asyncio.to_thread(\n'
        )
    if old_func not in code:
        # Try more flexible match
        vr_match = re.search(
            r'(async def _visitor_reply\(history: list, system: str\) -> str:\s*""".*?"""\s*)\n(\s+resp = await asyncio\.to_thread\()',
            code, re.DOTALL
        )
        if vr_match:
            old_func = vr_match.group(0)
        else:
            return code

    new_func = old_func.replace(
        'resp = await asyncio.to_thread(',
        'try:\n        resp = await asyncio.to_thread('
    )
    code = code.replace(old_func, new_func)

    # Now wrap the return
    old_return = '    return " ".join(b.text for b in resp.content if hasattr(b, "text")).strip()'
    new_return = (
        '        reply = _get_text(resp)\n'
        '        return reply if reply else "(vessel is resting)"\n'
        '    except Exception as e:\n'
        '        log.warning(f"VISITOR reply error: {e}")\n'
        '        return "(vessel is resting)"'
    )
    if old_return in code:
        code = code.replace(old_return, new_return)

    return code


def patch_handle_safety(code):
    """Add try/except to _handle render path."""
    # The dynamic render block in _handle
    old_block = (
        '    ctx   = load_vessel()\n'
        '    route = hecate(ctx, visitor_input)\n'
        '    html  = render(ctx, route, visitor_input)\n'
        '\n'
    )
    # Find it specifically inside _handle (not build)
    handle_idx = code.find('async def _handle(')
    if handle_idx == -1:
        return code

    block_idx = code.find(old_block, handle_idx)
    if block_idx == -1:
        return code

    # Find the return after it
    return_after = code.find('    return HTMLResponse(content=html)', block_idx)
    if return_after == -1:
        return code

    end_of_return = return_after + len('    return HTMLResponse(content=html)')

    old_section = code[block_idx:end_of_return]
    new_section = (
        '    try:\n'
        '        ctx   = load_vessel()\n'
        '        route = hecate(ctx, visitor_input)\n'
        '        html  = render(ctx, route, visitor_input)\n'
        '        log.info(f"<- {len(html)} chars  nodes={route[\'nodes\']}")\n'
        '        return HTMLResponse(content=html)\n'
        '    except Exception as e:\n'
        '        log.error(f"HANDLE render error: {e}")\n'
        '        if INDEX_HTML.exists():\n'
        '            return HTMLResponse(content=INDEX_HTML.read_text())\n'
        '        return HTMLResponse(content=FALLBACK_HTML)'
    )

    # Also remove the log.info line that follows
    log_line_end = code.find('\n', end_of_return)
    # Check if next line is the log line
    next_line_start = end_of_return
    remaining = code[end_of_return:end_of_return+100]
    if "log.info(f" in remaining:
        # Find end of that log line
        next_newline = code.find('\n', end_of_return + 1)
        if next_newline != -1:
            second_newline = code.find('\n', next_newline + 1)
            if second_newline != -1:
                end_of_return = second_newline

    code = code[:block_idx] + new_section + code[end_of_return:]
    return code


def patch_handle_first_build(code):
    """Wrap the first-build path in _handle with try/except."""
    if "First build failed" in code:
        return code

    # Find the first build section
    marker = 'html = build("render the site homepage for the first time")'
    idx = code.find(marker)
    if idx == -1:
        return code

    # Find the return after it
    ret_marker = 'return HTMLResponse(content=html)'
    ret_idx = code.find(ret_marker, idx)
    if ret_idx == -1:
        return code

    # Find the log line before it
    log_marker = 'no cache'
    log_idx = code.rfind(log_marker, 0, idx)
    if log_idx == -1:
        return code
    line_start = code.rfind('\n', 0, log_idx) + 1

    end_of_section = ret_idx + len(ret_marker)
    old_section = code[line_start:end_of_section]

    # Get indentation
    indent = '        '
    new_section = (
        indent + 'try:\n' +
        indent + '    html = build("render the site homepage for the first time")\n' +
        indent + '    return HTMLResponse(content=html)\n' +
        indent + 'except Exception as e:\n' +
        indent + '    log.error(f"First build failed: {e}")\n' +
        indent + '    return HTMLResponse(content=FALLBACK_HTML)'
    )

    code = code[:line_start] + new_section + code[end_of_section:]
    return code


def patch_hecate_timeout(code):
    """Reduce hecate timeout from 180 to 30 and use _get_text."""
    code = code.replace('timeout=180,\n            )\n            raw = resp.content[0].text.strip()',
                        'timeout=30,\n            )\n            raw = _get_text(resp)\n            if not raw:\n                raise ValueError("HECATE returned empty response")')
    # Also handle if _get_text was already used
    code = code.replace('timeout=180,\n            )\n            raw = _get_text(resp)',
                        'timeout=30,\n            )\n            raw = _get_text(resp)')
    return code


def patch_visitor_timeout(code):
    """Reduce visitor reply timeout from 180 to 60."""
    # This is inside the lambda in _visitor_reply
    code = code.replace('timeout=180,\n        )\n    )', 'timeout=60,\n        )\n    )')
    # Handle already-wrapped version
    code = code.replace('timeout=180,\n            )\n        )', 'timeout=60,\n            )\n        )')
    return code


def patch_env_parsing(code):
    """Guard int() parsing at startup with defaults."""
    old_hb = 'HEARTBEAT_INTERVAL = int(os.environ.get("HERMES_HEARTBEAT_MIN", "30")) * 60'
    new_hb = 'try:\n    HEARTBEAT_INTERVAL = int(os.environ.get("HERMES_HEARTBEAT_MIN", "30")) * 60\nexcept (ValueError, TypeError):\n    HEARTBEAT_INTERVAL = 1800'
    if old_hb in code and 'try:\n    HEARTBEAT_INTERVAL' not in code:
        code = code.replace(old_hb, new_hb)

    old_mt = 'MAX_TOKENS     = int(os.environ.get("HERMES_MAX_TOKENS",  "4096"))'
    new_mt = 'try:\n    MAX_TOKENS = int(os.environ.get("HERMES_MAX_TOKENS", "4096"))\nexcept (ValueError, TypeError):\n    MAX_TOKENS = 4096'
    if old_mt in code and 'try:\n    MAX_TOKENS' not in code:
        code = code.replace(old_mt, new_mt)
    return code


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def patch_file(bp):
    """Apply all patches to a single bridge.py file. Returns True if successful."""
    print(f"\nPatching: {bp}")

    backup = bp + ".bak"
    shutil.copy2(bp, backup)

    with open(bp, 'r', errors='replace') as f:
        code = f.read()

    original = code

    # PART 1 — Habits system
    code = patch_add_habits_block(code)
    code = patch_hecate_habits(code)
    code = patch_build_habits(code)

    # PART 2 — Error handling
    code = patch_add_fallback_html(code)
    code = patch_add_get_text(code)
    code = patch_read_helper(code)
    code = patch_render_safety(code)
    code = patch_visitor_reply_safety(code)
    code = patch_handle_safety(code)
    code = patch_handle_first_build(code)
    code = patch_hecate_timeout(code)
    code = patch_visitor_timeout(code)
    code = patch_env_parsing(code)

    if code == original:
        print(f"  SKIP: no changes needed")
        os.remove(backup)
        return True

    # Write patched file
    with open(bp, 'w') as f:
        f.write(code)

    # Validate syntax
    try:
        py_compile.compile(bp, doraise=True)
        print(f"  OK: syntax valid")
        os.remove(backup)
        return True
    except py_compile.PyCompileError as e:
        print(f"  SYNTAX ERROR: {e}")
        print(f"  Restoring backup...")
        shutil.copy2(backup, bp)
        os.remove(backup)
        return False


def create_habits_json(vessel_dir):
    """Create empty habits.json for a vessel."""
    vessel_subdir = os.path.join(vessel_dir, "vessel")
    if not os.path.isdir(vessel_subdir):
        print(f"  No vessel/ dir in {vessel_dir}, skipping habits.json")
        return

    habits_path = os.path.join(vessel_subdir, "habits.json")
    if os.path.exists(habits_path):
        print(f"  habits.json already exists: {habits_path}")
        return

    empty = {"version": 1, "routes": {}, "blacklist": {}}
    with open(habits_path, 'w') as f:
        json.dump(empty, f, indent=2)
    print(f"  Created: {habits_path}")


if __name__ == "__main__":
    bridges = glob.glob('/root/hermes/vessels/*/bridge.py')
    if not bridges:
        print("No bridge.py files found!")
        exit(1)

    print(f"Found {len(bridges)} bridge.py files")

    success = 0
    failed = 0

    for bp in sorted(bridges):
        if patch_file(bp):
            success += 1
            vessel_dir = os.path.dirname(bp)
            create_habits_json(vessel_dir)
        else:
            failed += 1

    print(f"\n{'='*60}")
    print(f"Results: {success} patched, {failed} failed")
    if failed > 0:
        print("WARNING: Some files failed — check output above")
    else:
        print("All files patched successfully!")

    # Restart all services
    print("\nRestarting hermes services...")
    import subprocess
    result = subprocess.run(
        ["bash", "-c",
         "for svc in /etc/systemd/system/hermes-*.service; do "
         "name=$(basename $svc .service); "
         "systemctl restart $name && echo \"Restarted: $name\" || echo \"FAILED: $name\"; "
         "done"],
        capture_output=True, text=True
    )
    print(result.stdout)
    if result.stderr:
        print("STDERR:", result.stderr)

    print("\nDone!")
