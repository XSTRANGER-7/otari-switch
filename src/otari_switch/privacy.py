"""Mask secrets and personal data in recorded prompts before any replay leaves the machine."""

from __future__ import annotations

import copy
import re
from typing import Any

PATTERNS = [
    ("KEY", re.compile(r"\b(?:sk-[A-Za-z0-9_\-]{16,}|otk_[A-Za-z0-9_\-]{12,}|ghp_[A-Za-z0-9]{20,}|"
                       r"gho_[A-Za-z0-9]{20,}|xox[abpr]-[A-Za-z0-9\-]{10,}|AKIA[0-9A-Z]{16}|"
                       r"dpka_[A-Za-z0-9_\-]{12,}|AIza[0-9A-Za-z_\-]{30,})")),
    ("EMAIL", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    ("CARD", re.compile(r"\b(?:\d[ \-]?){13,19}\b")),
    ("PHONE", re.compile(r"(?<![\w])\+?\d[\d\s().\-]{8,}\d(?![\w])")),
]


def _luhn(digits: str) -> bool:
    d = [int(c) for c in digits if c.isdigit()]
    if not 13 <= len(d) <= 19:
        return False
    total = 0
    for i, n in enumerate(reversed(d)):
        if i % 2:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


class Masker:
    """Consistent placeholders inside one recording: the same email becomes <EMAIL_1> every time."""

    def __init__(self):
        self.seen: dict[tuple[str, str], str] = {}
        self.count = 0

    def mask_text(self, text: str) -> str:
        for kind, rx in PATTERNS:
            def repl(m, kind=kind):
                val = m.group(0)
                if kind == "CARD" and not _luhn(val):
                    return val
                if kind == "PHONE":
                    digits = sum(c.isdigit() for c in val)
                    # a bare run of digits is more likely an id than a phone number
                    if digits < 10 or digits > 15 or (val.isdigit() and not val.startswith("+")):
                        return val
                key = (kind, val)
                if key not in self.seen:
                    n = sum(1 for k in self.seen if k[0] == kind) + 1
                    self.seen[key] = f"<{kind}_{n}>"
                    self.count += 1
                return self.seen[key]
            text = rx.sub(repl, text)
        return text

    def mask(self, obj: Any) -> Any:
        if isinstance(obj, str):
            return self.mask_text(obj)
        if isinstance(obj, list):
            return [self.mask(x) for x in obj]
        if isinstance(obj, dict):
            return {k: (v if k in {"model", "role", "type", "name"} else self.mask(v)) for k, v in obj.items()}
        return obj


def mask_recording(request: dict, frontier_text: str) -> tuple[dict, str, int]:
    m = Masker()
    req = m.mask(copy.deepcopy(request))
    text = m.mask_text(frontier_text or "")
    return req, text, m.count


def prompt_text(request: dict) -> str:
    """A readable flattening of the prompt for previews and the judge."""
    parts = []
    if isinstance(request.get("system"), str):
        parts.append(f"[system] {request['system']}")
    elif isinstance(request.get("system"), list):
        parts.append("[system] " + " ".join(b.get("text", "") for b in request["system"] if isinstance(b, dict)))
    if isinstance(request.get("instructions"), str):
        parts.append(f"[system] {request['instructions']}")
    msgs = request.get("messages") or []
    if not msgs and request.get("input") is not None:
        inp = request["input"]
        msgs = inp if isinstance(inp, list) else [{"role": "user", "content": inp}]
    for msg in msgs:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if isinstance(content, list):
            content = " ".join(c.get("text", "") if isinstance(c, dict) else str(c) for c in content)
        parts.append(f"[{msg.get('role', 'user')}] {content}")
    return "\n".join(parts)
