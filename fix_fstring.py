#!/usr/bin/env python3
"""Fix the broken f-string in bridge.py caused by literal newlines in patch."""
import re

path = "/root/hermes/bridge.py"
src = open(path).read()

# The broken line looks like: CHAT_CONTEXT_FILE.write_text(f"# Operator Context
# followed by blank lines and {summary} etc.
# Replace it with a properly escaped version using concatenation instead.

broken = re.search(
    r'CHAT_CONTEXT_FILE\.write_text\(f"# Operator Context[\s\S]*?"\)',
    src
)
if broken:
    print("Found broken section:", repr(broken.group()[:80]))
    src = src[:broken.start()] + 'CHAT_CONTEXT_FILE.write_text("# Operator Context\\n\\n" + summary + "\\n")' + src[broken.end():]
    open(path, "w").write(src)
    print("Fixed.")
else:
    print("Pattern not found — checking nearby lines:")
    for i, line in enumerate(src.splitlines()):
        if "Operator Context" in line:
            print(f"  line {i}: {repr(line)}")
