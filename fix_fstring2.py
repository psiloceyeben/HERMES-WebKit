#!/usr/bin/env python3
"""Fix backslash-in-f-string-expression errors in bridge.py."""

path = "/root/hermes/bridge.py"
src = open(path).read()

# Fix 1: the ternary with \n inside f-string expression
old1 = '''    prompt = f"""You are summarizing an operator conversation for a vessel.
The vessel identity: {vessel_text[:400]}

{"Existing context summary:\\n" + existing + "\\n\\n" if existing else ""}New conversation to add to the summary:
{transcript}

Write a concise running summary of what the operator has been working on, decisions made,
features built, and anything the vessel should remember going forward.
Plain text. No headers. 3-6 sentences."""'''

new1 = '''    existing_block = ("Existing context summary:\\n" + existing + "\\n\\n") if existing else ""
    prompt = (
        "You are summarizing an operator conversation for a vessel.\\n"
        "The vessel identity: " + vessel_text[:400] + "\\n\\n"
        + existing_block
        + "New conversation to add to the summary:\\n"
        + transcript
        + "\\n\\nWrite a concise running summary of what the operator has been working on, "
        "decisions made, features built, and anything the vessel should remember going forward. "
        "Plain text. No headers. 3-6 sentences."
    )'''

if old1 in src:
    src = src.replace(old1, new1, 1)
    print("Fixed prompt f-string")
else:
    # Try to find what's there
    import re
    m = re.search(r'prompt = f""".*?3-6 sentences\."""', src, re.DOTALL)
    if m:
        print("Found prompt block:", repr(m.group()[:120]))
        src = src[:m.start()] + new1 + src[m.end():]
        print("Fixed via regex")
    else:
        print("ERROR: could not find prompt block")

open(path, "w").write(src)
print("Done")
