"""Message fingerprint used to group similar errors (SPEC §2.5)."""

import pytest

from log_tracer.analysis.fingerprint import fingerprint

CASES = [
    # SPEC example
    (
        "Payment 7f3c9a12 failed after 2500 ms (attempt 3)",
        "Payment <hex> failed after <num> ms (attempt <num>)",
    ),
    # UUIDs, in any case, before the hex rule
    (
        "Order 3F2504E0-4F89-11D3-9A0C-0305E82C3301 not found",
        "Order <uuid> not found",
    ),
    ("user 123e4567-e89b-12d3-a456-426614174000 locked", "user <uuid> locked"),
    # IPv4 with and without port, before the number rule
    ("connection refused 10.0.4.12:5432", "connection refused <ip>"),
    ("from 192.168.1.1 denied", "from <ip> denied"),
    # Hex: at least 8 chars with both digits and a-f letters, optional 0x
    ("trace 4bf92f3577b34da6 dropped", "trace <hex> dropped"),
    ("pointer 0x7ffe4a2c1b30", "pointer <hex>"),
    ("word deadbeef stays", "word deadbeef stays"),  # no digit
    ("short a1b2c3 hex", "short a<num>b<num>c<num> hex"),  # fewer than 8 chars
    # Numbers anywhere, including inside identifiers
    ("Timeout after 2.5 s", "Timeout after <num> s"),
    ("request req-184 failed", "request req-<num> failed"),
    ("user42 missing", "user<num> missing"),
    ("12345678 items", "<num> items"),  # digits only: a number, not hex
    # Whitespace is collapsed and trimmed
    ("  too   many\tspaces  ", "too many spaces"),
    # Messages without variable parts are unchanged
    ("Payment gateway timeout", "Payment gateway timeout"),
    ("", ""),
]


@pytest.mark.parametrize(("message", "expected"), CASES)
def test_variable_parts_are_replaced(message: str, expected: str) -> None:
    assert fingerprint(message) == expected


@pytest.mark.parametrize(("message", "_expected"), CASES)
def test_fingerprint_is_idempotent(message: str, _expected: str) -> None:
    once = fingerprint(message)
    assert fingerprint(once) == once


def test_messages_differing_only_in_ids_share_a_fingerprint() -> None:
    assert fingerprint("Payment 7f3c9a12 failed after 2500 ms") == fingerprint(
        "Payment 9a8b7c6d5e failed after 310 ms"
    )
