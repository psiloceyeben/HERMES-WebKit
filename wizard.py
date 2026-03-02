#!/usr/bin/env python3
"""
HERMES WEBKIT — terminal setup wizard

Reads vessel/WIZARD.md for atmosphere and voice.

If ANTHROPIC_API_KEY is set, uses the model to:
  - generate an ASCII banner in the wizard's style
  - rephrase each question in the wizard's voice

Otherwise applies default visual theming with the greeting from WIZARD.md.

Writes vessel/VESSEL.md from your answers.

Run directly:    python3 wizard.py
Called by run:   ./run
Reconfigure:     python3 wizard.py --reconfigure
"""

import json
import os
import re
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

HERMES_DIR  = Path(__file__).parent
VESSEL_DIR  = Path(os.environ.get("VESSEL_DIR", HERMES_DIR / "vessel"))
WIZARD_MD   = VESSEL_DIR / "WIZARD.md"
VESSEL_FILE = VESSEL_DIR / "VESSEL.md"

# ── ANSI ──────────────────────────────────────────────────────────────────────
R  = "\033[0m"
BD = "\033[1m"
CY = "\033[0;36m"
GR = "\033[0;32m"
DM = "\033[2m"
YL = "\033[0;33m"

# ── defaults ──────────────────────────────────────────────────────────────────

DEFAULT_BANNER = f"""\
{CY}
  ██╗  ██╗███████╗██████╗ ███╗   ███╗███████╗███████╗
  ██║  ██║██╔════╝██╔══██╗████╗ ████║██╔════╝██╔════╝
  ███████║█████╗  ██████╔╝██╔████╔██║█████╗  ███████╗
  ██╔══██║██╔══╝  ██╔══██╗██║╚██╔╝██║██╔══╝  ╚════██║
  ██║  ██║███████╗██║  ██║██║ ╚═╝ ██║███████╗███████║
  ╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝╚═╝     ╚═╝╚══════╝╚══════╝
{R}"""

DEFAULT_GREETING   = "Setting up your vessel. Answer in plain English — you can change everything later."
DEFAULT_COMPLETION = "Your vessel is ready."

QUESTIONS = [
    ("name",      "What is your website called?"),
    ("purpose",   "What is it for? What should visitors find here?"),
    ("voice",     "What voice or tone? (e.g. warm, direct, formal, poetic, technical)"),
    ("knowledge", "What does it know about? Your expertise, story, or offerings:"),
    ("limits",    "What should it never do or say? Any hard limits:"),
    ("contact",   "Your name or contact — press enter to skip:"),
]


# ── WIZARD.md parsing ─────────────────────────────────────────────────────────

def load_wizard_md() -> str | None:
    if WIZARD_MD.exists():
        # Return only the top section — stop at the HOW TO THEME divider
        text = WIZARD_MD.read_text()
        divider = text.find("---")
        return text[:divider].strip() if divider > 0 else text.strip()
    return None


def section_value(text: str, heading: str) -> str:
    """Extract content under a ## heading."""
    match = re.search(rf"## {heading}\s*\n(.*?)(?=\n##|\Z)", text, re.DOTALL)
    return match.group(1).strip() if match else ""


# ── model calls ───────────────────────────────────────────────────────────────

def api_call(client, prompt: str, max_tokens: int = 512) -> str:
    model = os.environ.get("HERMES_MODEL_HECATE", "claude-haiku-4-5-20251001")
    resp  = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text.strip()


def get_banner(client, wizard_text: str) -> str:
    prompt = f"""You are designing a terminal ASCII art banner for a setup wizard.

The atmosphere of this wizard is:

{wizard_text}

Create an ASCII art banner (max 8 lines tall, max 70 chars wide) that makes
someone feel like they have stepped into that world when they open their terminal.

Use only standard ASCII characters — no Unicode, no colour codes.
The banner should be evocative, not just decorative.

Return ONLY the ASCII art. Nothing else."""
    try:
        return api_call(client, prompt, 300)
    except Exception:
        return ""


def get_questions(client, wizard_text: str) -> list[tuple[str, str]]:
    base = json.dumps([{"key": k, "question": q} for k, q in QUESTIONS], indent=2)
    prompt = f"""You are running a setup wizard with this atmosphere:

{wizard_text}

Rephrase these questions so they feel like they come from that world.
Keep the same meaning — you still need the same information.
Make them feel like invitations or natural conversation from inside that atmosphere,
not form fields.

Keep the "contact" question clearly optional and gentle.

Original questions:
{base}

Return ONLY a valid JSON array with keys "key" and "question". No markdown fences."""
    try:
        raw   = api_call(client, prompt, 600)
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if match:
            items  = json.loads(match.group())
            result = [(i["key"], i["question"]) for i in items
                      if "key" in i and "question" in i]
            if len(result) == len(QUESTIONS):
                return result
    except Exception:
        pass
    return QUESTIONS


def get_completion(client, wizard_text: str) -> str:
    prompt = f"""Write a single short completion message (1–2 sentences) for a setup wizard
with this atmosphere:

{wizard_text}

The setup is complete. The vessel has been written and the website is ready to build.
Speak entirely from within the atmosphere — no meta-commentary.

Return ONLY the message."""
    try:
        return api_call(client, prompt, 150)
    except Exception:
        return DEFAULT_COMPLETION


# ── wizard ────────────────────────────────────────────────────────────────────

def run(reconfigure: bool = False):

    # Guard
    if VESSEL_FILE.exists() and not reconfigure:
        print(f"\n  {YL}VESSEL.md already exists.{R}")
        print(f"  Run with --reconfigure to overwrite it.")
        print(f"  Or edit it directly: {VESSEL_FILE}\n")
        sys.exit(0)

    wizard_text = load_wizard_md()
    api_key     = os.environ.get("ANTHROPIC_API_KEY", "")
    client      = None

    if api_key and wizard_text:
        try:
            from anthropic import Anthropic
            client = Anthropic(api_key=api_key)
        except ImportError:
            pass

    # ── prepare ──────────────────────────────────────────────────────────────
    banner     = DEFAULT_BANNER
    questions  = QUESTIONS
    completion = DEFAULT_COMPLETION
    greeting   = section_value(wizard_text, "Greeting") if wizard_text else DEFAULT_GREETING

    if client and wizard_text:
        print(f"\n  {DM}stepping into your space...{R}", end="\r", flush=True)
        raw_banner  = get_banner(client, wizard_text)
        questions   = get_questions(client, wizard_text)
        completion  = get_completion(client, wizard_text)
        print(" " * 50, end="\r")  # clear

        if raw_banner:
            banner = f"{CY}" + "\n".join(
                f"  {line}" for line in raw_banner.split("\n")
            ) + f"{R}"

    # ── banner ────────────────────────────────────────────────────────────────
    print(banner)
    print(f"  {greeting}\n")

    if not client and wizard_text:
        print(f"  {DM}(add an API key to enable atmospheric mode){R}\n")

    # ── questions ─────────────────────────────────────────────────────────────
    answers = {}
    for key, question in questions:
        print(f"  {BD}{question}{R}")
        try:
            answer = input(f"  {CY}›{R} ").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n\n  {DM}Interrupted — nothing written.{R}\n")
            sys.exit(0)
        answers[key] = answer
        print()

    # ── write VESSEL.md ───────────────────────────────────────────────────────
    VESSEL_DIR.mkdir(parents=True, exist_ok=True)

    lines = [
        f"# {answers.get('name', 'Untitled')}",
        "",
        "## Purpose",
        answers.get("purpose", ""),
        "",
        "## Voice",
        answers.get("voice", ""),
        "",
        "## Knowledge",
        answers.get("knowledge", ""),
        "",
        "## Limits",
        answers.get("limits", ""),
    ]

    contact = answers.get("contact", "").strip()
    if contact:
        lines += ["", "## Contact", contact]

    VESSEL_FILE.write_text("\n".join(lines) + "\n")

    # ── initialise STATE.md if absent ─────────────────────────────────────────
    state_file = VESSEL_DIR / "STATE.md"
    if not state_file.exists():
        from datetime import date
        state_file.write_text(
            f"# STATE\n\nLaunched: {date.today()}\nStatus: live\n\n## Memory\nNothing recorded yet.\n"
        )

    # ── done ──────────────────────────────────────────────────────────────────
    print(f"  {GR}{completion}{R}")
    print(f"  {DM}{VESSEL_FILE}{R}\n")


if __name__ == "__main__":
    reconfigure = "--reconfigure" in sys.argv or "-r" in sys.argv
    run(reconfigure=reconfigure)
