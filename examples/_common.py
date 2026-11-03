"""Shared bits for the examples: import Ken from the repository root, print nicely.

Every example runs OFFLINE: no API key, no WhatsApp, no Google account. They use the local hash
embedder instead of OpenAI embeddings and made-up data, so the numbers differ from the real app,
but the code paths are the same ones Ken uses.
"""

from __future__ import annotations

import sys
from pathlib import Path

# ----------------------------------------------------------------------------
# Make `import assistant` work when an example is run as `python examples/xx.py`
# ----------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Windows consoles default to a legacy code page; Ken's data is full of Arabic.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass


# ----------------------------------------------------------------------------
# Printing helpers
# ----------------------------------------------------------------------------

def title(text: str) -> None:
    print("\n" + text + "\n" + "─" * min(len(text), 78))


def show(label: str, value: object) -> None:
    print(f"  {label:<26} {value}")
