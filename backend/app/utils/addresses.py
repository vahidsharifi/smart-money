from __future__ import annotations

import re
import secrets
from typing import Any


_EVM_ADDRESS_RE = re.compile(r"^0x[0-9a-f]{40}$")


def normalize_evm_address(value: Any) -> str | None:
    if not value:
        return None
    text = str(value).strip().lower()
    if not text.startswith("0x"):
        text = f"0x{text}"
    if _EVM_ADDRESS_RE.match(text):
        return text
    return None


def is_valid_evm_address(value: Any) -> bool:
    return normalize_evm_address(value) is not None


def random_evm_address() -> str:
    return f"0x{secrets.token_hex(20)}"
