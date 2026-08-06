from __future__ import annotations

from typing import Any


EXCLUDED_ACCOUNT_EMAILS = frozenset({"1745627971@qq.com"})


def normalized_email(value: Any) -> str:
    return str(value or "").strip().lower()


def is_excluded_account(value: Any) -> bool:
    return normalized_email(value) in EXCLUDED_ACCOUNT_EMAILS
