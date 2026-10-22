"""Message fingerprint: the message with its variable parts masked (SPEC §2.5).

Errors that differ only in ids, addresses or numbers share a fingerprint and
are therefore counted together. The substitutions run in a fixed order, from
the most specific pattern to the most generic one, and the result contains no
digits, so applying the function twice changes nothing.
"""

import re
from typing import Final

_SUBSTITUTIONS: Final = (
    (
        re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I),
        "<uuid>",
    ),
    (
        re.compile(r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?::[0-9]{1,5})?\b"),
        "<ip>",
    ),
    (
        # At least 8 hex characters including both a digit and a letter a-f.
        re.compile(r"\b(?:0x)?(?=[0-9a-f]*[0-9])(?=[0-9a-f]*[a-f])[0-9a-f]{8,}\b", re.I),
        "<hex>",
    ),
    (
        re.compile(r"[0-9]+(?:\.[0-9]+)?"),
        "<num>",
    ),
    (
        re.compile(r"\s+"),
        " ",
    ),
)


def fingerprint(message: str) -> str:
    for pattern, replacement in _SUBSTITUTIONS:
        message = pattern.sub(replacement, message)
    return message.strip()
