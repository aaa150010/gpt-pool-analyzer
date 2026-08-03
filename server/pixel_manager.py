from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import math
import re
import secrets
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlparse

import httpx


MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_SOURCE_ACCOUNTS = 100_000
MAX_SHARE_ACCOUNTS = 100_000
MAX_BULK_ACCOUNTS = 100
IMPORT_CHUNK_SIZE = 100
SHARE_CHUNK_SIZE = 100
ACCOUNT_PAGE_SIZE = 100
MAX_ACCOUNT_PAGES = 200
MAX_RETAINED_IMPORT_JOBS = 50
MAX_RETAINED_EXPORT_JOBS = 20
PUBLIC_SHARE_CONCURRENCY = 10
TOKEN_EXPIRY_SKEW_SECONDS = 60
DEFAULT_PLATFORM_TIMEOUT_SECONDS = 30.0
LONG_OPERATION_TIMEOUT_SECONDS = 120.0
ACCOUNT_TEST_TIMEOUT_SECONDS = 60.0
ACCOUNT_STATUS_FILTERS = {
    "",
    "active",
    "codex_quota_protected",
    "rate_limited",
    "error",
}
TARGET_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
SENSITIVE_TEXT_PATTERN = re.compile(
    r"(?i)(access[_ -]?token|refresh[_ -]?token|authorization|password|secret)"
    r"(?:\\?[\"'])?\s*[:=]\s*(?:\\?[\"'])?[^\s,;}\]\"']+"
)
BEARER_TOKEN_PATTERN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")


class PixelManagerError(RuntimeError):
    def __init__(self, public_message: str, status_code: int = 502) -> None:
        super().__init__(public_message)
        self.public_message = public_message
        self.status_code = status_code


class PixelConfigError(PixelManagerError):
    def __init__(self, public_message: str = "账号池管理配置不可用") -> None:
        super().__init__(public_message, 503)


class PixelValidationError(PixelManagerError):
    def __init__(self, public_message: str) -> None:
        super().__init__(public_message, 400)


@dataclass(frozen=True)
class ImportDefaults:
    platform: str = "openai"
    share_mode: str = "private"
    account_level: str = "plus"
    priority: int = 1
    concurrency: int = 3
    group_ids: tuple[int, ...] = ()
    auto_pause_on_expired: bool = True


@dataclass(frozen=True)
class PixelTarget:
    id: str
    email: str
    base_url: str
    login_agreement_revision: str = ""
    refresh_path: str = "/api/v1/auth/refresh"
    import_defaults: ImportDefaults = field(default_factory=ImportDefaults)
    password: str = field(default="", repr=False)


@dataclass(frozen=True)
class PixelManagerConfig:
    manager_key: str = field(repr=False)
    targets: dict[str, PixelTarget]
    allow_open_access: bool = True


@dataclass
class TokenState:
    access_token: str = field(repr=False)
    refresh_token: str = field(default="", repr=False)
    expires_at: float = 0.0


@dataclass(frozen=True)
class CredentialBundle:
    source_file_name: str
    source_count: int
    source_payload: dict[str, Any] = field(repr=False)
    source_file_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class TargetCredentialBundle:
    generated_file_name: str
    source_count: int
    contents: tuple[str, ...] = field(repr=False)
    chunk_sizes: tuple[int, ...]
    generated_names: tuple[str, ...] = field(repr=False)


@dataclass(frozen=True)
class ExportBundle:
    content: bytes = field(repr=False)
    source_count: int
    deduplicated_count: int
    duplicate_count: int
    batch_count: int


@dataclass(frozen=True)
class DeleteTargetResult:
    target_id: str
    email: str
    total: int
    deleted: int
    failed: int
    failed_ids: tuple[int, ...] = field(default_factory=tuple)
    status: str = "success"
    message: str = ""


def _first(mapping: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return default


def _positive_int(value: Any, default: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return parsed if parsed >= 0 else default


def _optional_number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _safe_text(value: Any, limit: int = 500) -> str:
    text = str(value or "").replace("\x00", " ").strip()
    text = BEARER_TOKEN_PATTERN.sub("Bearer [redacted]", text)
    text = SENSITIVE_TEXT_PATTERN.sub(lambda match: f"{match.group(1)}=[redacted]", text)
    return text[:limit]


def _account_ids(values: Iterable[Any], maximum: int = MAX_BULK_ACCOUNTS) -> list[int]:
    ids = list(
        dict.fromkeys(
            account_id
            for account_id in (_positive_int(value, -1) for value in values)
            if account_id > 0
        )
    )
    if not ids or len(ids) > maximum:
        raise PixelValidationError(f"每次必须选择 1-{maximum} 个账号")
    return ids


def _account_status_filter(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in ACCOUNT_STATUS_FILTERS:
        raise PixelValidationError("账号状态筛选无效")
    return normalized


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalized_path(value: Any, default: str) -> str:
    path = str(value or default).strip()
    if not path.startswith("/") or path.startswith("//"):
        raise PixelConfigError()
    return path


def _parse_import_defaults(raw: Any) -> ImportDefaults:
    source = raw if isinstance(raw, dict) else {}
    group_values = _first(source, "groupIds", "group_ids", default=[])
    if not isinstance(group_values, list):
        group_values = []
    group_ids = tuple(
        dict.fromkeys(
            value
            for value in (_positive_int(item, -1) for item in group_values)
            if value >= 0
        )
    )
    return ImportDefaults(
        platform=_safe_text(_first(source, "platform", default="openai"), 40) or "openai",
        share_mode=_safe_text(_first(source, "shareMode", "share_mode", default="private"), 40) or "private",
        account_level=_safe_text(_first(source, "accountLevel", "account_level", default="plus"), 40) or "plus",
        priority=max(min(_positive_int(_first(source, "priority", default=1), 1), 100), 0),
        concurrency=max(min(_positive_int(_first(source, "concurrency", default=3), 3), 100), 1),
        group_ids=group_ids,
        auto_pause_on_expired=bool(
            _first(source, "autoPauseOnExpired", "auto_pause_on_expired", default=True)
        ),
    )


def load_config(path: str | Path) -> PixelManagerConfig:
    config_path = Path(path)
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PixelConfigError() from exc
    if not isinstance(raw, dict):
        raise PixelConfigError()

    manager_key = str(_first(raw, "managerKey", "manager_key", default="") or "").strip()
    target_values = raw.get("targets")
    if len(manager_key) < 24 or not isinstance(target_values, list) or not target_values:
        raise PixelConfigError()

    targets: dict[str, PixelTarget] = {}
    for item in target_values:
        if not isinstance(item, dict):
            raise PixelConfigError()
        target_id = str(item.get("id") or "").strip()
        email = str(item.get("email") or "").strip()
        password = str(item.get("password") or "")
        base_url = str(_first(item, "baseUrl", "base_url", default="") or "").strip().rstrip("/")
        parsed_url = urlparse(base_url)
        if (
            not TARGET_ID_PATTERN.fullmatch(target_id)
            or target_id in targets
            or not email
            or not password
            or parsed_url.scheme != "https"
            or not parsed_url.netloc
            or parsed_url.username
            or parsed_url.password
            or parsed_url.query
            or parsed_url.fragment
        ):
            raise PixelConfigError()
        targets[target_id] = PixelTarget(
            id=target_id,
            email=email,
            password=password,
            base_url=base_url,
            login_agreement_revision=str(
                _first(item, "loginAgreementRevision", "login_agreement_revision", default="") or ""
            ).strip(),
            refresh_path=_normalized_path(
                _first(item, "refreshPath", "refresh_path", default=None),
                "/api/v1/auth/refresh",
            ),
            import_defaults=_parse_import_defaults(
                _first(item, "importDefaults", "import_defaults", default={})
            ),
        )
    return PixelManagerConfig(
        manager_key=manager_key,
        targets=targets,
        allow_open_access=bool(_first(raw, "allowOpenAccess", "allow_open_access", default=True)),
    )


def parse_credential_bundle(file_name: str, payload: bytes) -> CredentialBundle:
    safe_name = Path(file_name or "accounts.json").name
    if not safe_name.lower().endswith(".json"):
        raise PixelValidationError("只支持 JSON 文件")
    if not payload:
        raise PixelValidationError("JSON 文件为空")
    if len(payload) > MAX_UPLOAD_BYTES:
        raise PixelManagerError("JSON 文件不能超过 50 MB", 413)
    try:
        source = json.loads(payload.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PixelValidationError("JSON 文件格式无效") from exc

    if isinstance(source, list):
        source_payload: dict[str, Any] = {
            "exported_at": _utc_now(),
            "proxies": [],
            "accounts": source,
        }
    elif isinstance(source, dict) and isinstance(source.get("accounts"), list):
        source_payload = source
    elif isinstance(source, dict) and isinstance(source.get("contents"), list):
        values: list[Any] = []
        for item in source["contents"]:
            if isinstance(item, str):
                try:
                    item = json.loads(item)
                except json.JSONDecodeError as exc:
                    raise PixelValidationError("contents 中存在无效 JSON") from exc
            if isinstance(item, dict) and isinstance(item.get("accounts"), list):
                values.extend(item["accounts"])
            else:
                values.append(item)
        source_payload = {"exported_at": _utc_now(), "proxies": [], "accounts": values}
    else:
        raise PixelValidationError("JSON 必须包含 accounts 数组")

    values = source_payload["accounts"]
    if not values or len(values) > MAX_SOURCE_ACCOUNTS:
        raise PixelValidationError(f"JSON 必须包含 1-{MAX_SOURCE_ACCOUNTS} 个账号对象")
    for value in values:
        if not isinstance(value, dict):
            raise PixelValidationError("每个账号凭据必须是 JSON 对象")
    if not isinstance(source_payload.get("proxies"), list):
        source_payload["proxies"] = []
    return CredentialBundle(
        source_file_name=safe_name,
        source_count=len(values),
        source_payload=source_payload,
        source_file_names=(safe_name,),
    )


def merge_credential_bundles(bundles: Iterable[CredentialBundle]) -> CredentialBundle:
    values = list(bundles)
    if not values:
        raise PixelValidationError("至少选择一个 JSON 文件")
    if len(values) == 1:
        return values[0]

    source_file_names = tuple(
        name
        for bundle in values
        for name in (bundle.source_file_names or (bundle.source_file_name,))
    )
    accounts: list[dict[str, Any]] = []
    proxies: list[dict[str, Any]] = []
    for bundle in values:
        accounts.extend(copy.deepcopy(bundle.source_payload.get("accounts") or []))
        proxies.extend(copy.deepcopy(bundle.source_payload.get("proxies") or []))
    if len(accounts) > MAX_SOURCE_ACCOUNTS:
        raise PixelValidationError(f"JSON 合计不能超过 {MAX_SOURCE_ACCOUNTS} 个账号")
    return CredentialBundle(
        source_file_name=f"批量导入（{len(source_file_names)}个JSON文件）",
        source_count=len(accounts),
        source_payload={"exported_at": _utc_now(), "proxies": proxies, "accounts": accounts},
        source_file_names=source_file_names,
    )


def _collect_emails(value: Any, found: set[str]) -> None:
    if isinstance(value, dict):
        for item in value.values():
            _collect_emails(item, found)
    elif isinstance(value, list):
        for item in value:
            _collect_emails(item, found)
    elif isinstance(value, str) and "@" in value:
        candidate = value.strip().lower()
        if re.fullmatch(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", candidate):
            found.add(candidate)


def _account_email_domain(account: dict[str, Any]) -> str:
    candidates: list[Any] = [account.get("name")]
    credentials = account.get("credentials")
    extra = account.get("extra")
    if isinstance(credentials, dict):
        candidates.append(credentials.get("email"))
    if isinstance(extra, dict):
        candidates.append(extra.get("email"))
    for candidate in candidates:
        if isinstance(candidate, str) and "@" in candidate:
            domain = candidate.rsplit("@", 1)[-1].strip().lower()
            if re.fullmatch(r"[A-Za-z0-9.-]+\.[A-Za-z]{2,}", domain):
                return domain
    return "example.com"


def _random_email(domain: str, used_emails: set[str]) -> str:
    while True:
        candidate = f"acct-{secrets.token_hex(6)}@{domain}".lower()
        if candidate not in used_emails:
            used_emails.add(candidate)
            return candidate


def _replace_email_fields(value: Any, replacement: str) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() == "email" and isinstance(item, str):
                value[key] = replacement
            else:
                _replace_email_fields(item, replacement)
    elif isinstance(value, list):
        for item in value:
            _replace_email_fields(item, replacement)


def _set_plan_type_plus(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "plan_type":
                value[key] = "plus"
            else:
                _set_plan_type_plus(item)
    elif isinstance(value, list):
        for item in value:
            _set_plan_type_plus(item)


def _remove_chatgpt_field_source(value: Any) -> None:
    if isinstance(value, dict):
        value.pop("chatgpt_field_source", None)
        for item in value.values():
            _remove_chatgpt_field_source(item)
    elif isinstance(value, list):
        for item in value:
            _remove_chatgpt_field_source(item)


# PixelAPI applies the same credential-safety scan to every nested field in an
# imported account. These fields are metadata for other exporters, not OAuth
# material required by PixelAPI, so remove them before sending the account.
PIXEL_IMPORT_BLOCKED_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "x_api_key",
        "xapikey",
        "authorization",
        "authorization_header",
        "authorizationheader",
        "base_url",
        "baseurl",
        "api_base_url",
        "api_baseurl",
        "custom_base_url",
        "custom_baseurl",
        "custom_base_url_enabled",
        "custom_baseurl_enabled",
        "upstream",
        "upstream_url",
        "upstreamurl",
        "upstream_base_url",
        "upstream_baseurl",
        "upstream_endpoint",
        "upstreamendpoint",
        "endpoint",
        "endpoint_url",
        "endpointurl",
        "url",
        "host",
        "proxy_url",
        "proxyurl",
        "cookie",
        "cookies",
        "set_cookie",
        "setcookie",
        "auth_mode",
        "authmode",
        "aws_access_key_id",
        "awsaccesskeyid",
        "aws_secret_access_key",
        "awssecretaccesskey",
        "aws_session_token",
        "awssessiontoken",
        "access_key_id",
        "accesskeyid",
        "secret_access_key",
        "secretaccesskey",
    }
)


def _normalized_import_key(value: Any) -> str:
    return re.sub(r"[-.]", "_", str(value or "").strip().lower())


def _is_agent_identity_account(account: dict[str, Any]) -> bool:
    credentials = account.get("credentials")
    if not isinstance(credentials, dict):
        return False
    auth_mode = str(
        credentials.get("auth_mode") or credentials.get("authMode") or ""
    ).strip().lower()
    return auth_mode == "agentidentity" or isinstance(
        credentials.get("agent_identity") or credentials.get("agentIdentity"), dict
    )


def _remove_pixel_import_blocked_fields(
    value: Any,
    *,
    preserve_agent_auth_mode: bool = False,
    preserve_auth_mode_here: bool = False,
) -> None:
    if isinstance(value, dict):
        for key in list(value):
            normalized_key = _normalized_import_key(key)
            if normalized_key in PIXEL_IMPORT_BLOCKED_KEYS and not (
                preserve_agent_auth_mode
                and preserve_auth_mode_here
                and normalized_key == "auth_mode"
            ):
                value.pop(key, None)
                continue
            _remove_pixel_import_blocked_fields(
                value[key],
                preserve_agent_auth_mode=preserve_agent_auth_mode,
                preserve_auth_mode_here=(
                    preserve_agent_auth_mode and normalized_key == "credentials"
                ),
            )
    elif isinstance(value, list):
        for item in value:
            _remove_pixel_import_blocked_fields(
                item,
                preserve_agent_auth_mode=preserve_agent_auth_mode,
                preserve_auth_mode_here=False,
            )


def _ensure_openai_oauth_plan_type(payload: dict[str, Any]) -> None:
    accounts = payload.get("accounts")
    if not isinstance(accounts, list):
        return
    for account in accounts:
        if not isinstance(account, dict):
            continue
        platform = str(account.get("platform") or "").strip().lower()
        account_type = str(account.get("type") or "").strip().lower()
        if platform != "openai" or account_type != "oauth":
            continue
        credentials = account.get("credentials")
        if not isinstance(credentials, dict):
            credentials = {}
            account["credentials"] = credentials
        credentials["plan_type"] = "plus"


def build_target_credential_bundle(
    bundle: CredentialBundle,
    used_emails: set[str],
) -> TargetCredentialBundle:
    payload = copy.deepcopy(bundle.source_payload)
    _remove_chatgpt_field_source(payload)
    _set_plan_type_plus(payload)
    _ensure_openai_oauth_plan_type(payload)
    generated_names: list[str] = []
    accounts = payload["accounts"]
    for account in accounts:
        _remove_pixel_import_blocked_fields(
            account,
            preserve_agent_auth_mode=_is_agent_identity_account(account),
        )
        replacement = _random_email(_account_email_domain(account), used_emails)
        account["name"] = replacement
        _replace_email_fields(account, replacement)
        generated_names.append(replacement)

    common = {key: copy.deepcopy(value) for key, value in payload.items() if key != "accounts"}
    contents: list[str] = []
    chunk_sizes: list[int] = []
    for start in range(0, len(accounts), IMPORT_CHUNK_SIZE):
        chunk_accounts = accounts[start : start + IMPORT_CHUNK_SIZE]
        chunk = {**copy.deepcopy(common), "accounts": chunk_accounts}
        contents.append(json.dumps(chunk, ensure_ascii=False, separators=(",", ":")))
        chunk_sizes.append(len(chunk_accounts))
    return TargetCredentialBundle(
        generated_file_name=f"pixel-import-{secrets.token_hex(8)}.json",
        source_count=bundle.source_count,
        contents=tuple(contents),
        chunk_sizes=tuple(chunk_sizes),
        generated_names=tuple(generated_names),
    )


def _jwt_signature_hashes(value: Any, key_name: str = "") -> set[str]:
    hashes: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            hashes.update(_jwt_signature_hashes(item, str(key)))
    elif isinstance(value, list):
        for item in value:
            hashes.update(_jwt_signature_hashes(item, key_name))
    elif isinstance(value, str) and "token" in key_name.lower():
        parts = value.strip().split(".")
        if len(parts) == 3 and all(parts):
            hashes.add(hashlib.sha256(parts[2].encode("utf-8")).hexdigest())
    return hashes


def _merge_export_payload(
    payload: dict[str, Any],
    merged_accounts: list[dict[str, Any]],
    merged_proxies: list[dict[str, Any]],
    seen_signatures: set[str],
    seen_proxies: set[str],
) -> tuple[int, int]:
    source_count = duplicate_count = 0
    for proxy in payload.get("proxies") or []:
        if not isinstance(proxy, dict):
            continue
        proxy_key = str(proxy.get("proxy_key") or "") or hashlib.sha256(
            json.dumps(proxy, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        if proxy_key not in seen_proxies:
            seen_proxies.add(proxy_key)
            merged_proxies.append(copy.deepcopy(proxy))
    for account in payload.get("accounts") or []:
        if not isinstance(account, dict):
            continue
        source_count += 1
        signatures = _jwt_signature_hashes(account.get("credentials") or {})
        if signatures and signatures.intersection(seen_signatures):
            duplicate_count += 1
            continue
        seen_signatures.update(signatures)
        merged_accounts.append(copy.deepcopy(account))
    return source_count, duplicate_count


def merge_export_payloads(payloads: Iterable[dict[str, Any]]) -> tuple[dict[str, Any], int, int]:
    merged_accounts: list[dict[str, Any]] = []
    merged_proxies: list[dict[str, Any]] = []
    seen_signatures: set[str] = set()
    seen_proxies: set[str] = set()
    source_count = duplicate_count = 0
    for payload in payloads:
        added, duplicates = _merge_export_payload(
            payload, merged_accounts, merged_proxies, seen_signatures, seen_proxies
        )
        source_count += added
        duplicate_count += duplicates
    merged = {"exported_at": _utc_now(), "proxies": merged_proxies, "accounts": merged_accounts}
    merged["account_batches"] = _export_account_batches(merged_accounts)
    return (merged, source_count, duplicate_count)


def _export_account_batches(accounts: list[dict[str, Any]], batch_size: int = 100) -> list[dict[str, Any]]:
    batches: list[dict[str, Any]] = []
    for start in range(0, len(accounts), batch_size):
        chunk = accounts[start : start + batch_size]
        batches.append(
            {
                "index": len(batches) + 1,
                "count": len(chunk),
                "accounts": copy.deepcopy(chunk),
            }
        )
    return batches


def sanitize_account(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    account_id = _positive_int(item.get("id"), -1)
    if account_id < 0:
        return None
    groups: list[dict[str, Any]] = []
    for group in item.get("groups") or []:
        if not isinstance(group, dict):
            continue
        group_id = _positive_int(group.get("id"), -1)
        if group_id >= 0:
            groups.append({"id": group_id, "name": _safe_text(group.get("name"), 120)})
    return {
        "id": account_id,
        "name": _safe_text(item.get("name"), 240),
        "platform": _safe_text(item.get("platform"), 40),
        "accountLevel": _safe_text(_first(item, "account_level", "accountLevel"), 40),
        "type": _safe_text(item.get("type"), 40),
        "shareMode": _safe_text(_first(item, "share_mode", "shareMode"), 40),
        "shareStatus": _safe_text(_first(item, "share_status", "shareStatus"), 40),
        "concurrency": _positive_int(item.get("concurrency")),
        "currentConcurrency": _positive_int(
            _first(item, "current_concurrency", "currentConcurrency")
        ),
        "priority": _positive_int(item.get("priority")),
        "status": _safe_text(item.get("status"), 40),
        "schedulable": bool(item.get("schedulable")),
        "credentialsStatus": _safe_text(
            _first(item, "credentials_status", "credentialsStatus"), 80
        ),
        "errorMessage": _safe_text(_first(item, "error_message", "errorMessage")),
        "errorSince": _first(item, "error_since", "errorSince"),
        "expiresAt": _first(item, "expires_at", "expiresAt"),
        "createdAt": _first(item, "created_at", "createdAt"),
        "updatedAt": _first(item, "updated_at", "updatedAt"),
        "codex5hLimitPercent": _optional_number(
            _first(item, "codex_5h_limit_percent", "codex5hLimitPercent")
        ),
        "codex7dLimitPercent": _optional_number(
            _first(item, "codex_7d_limit_percent", "codex7dLimitPercent")
        ),
        "rateLimitedAt": _first(item, "rate_limited_at", "rateLimitedAt"),
        "rateLimitResetAt": _first(item, "rate_limit_reset_at", "rateLimitResetAt"),
        "codexQuotaProtectionReason": _safe_text(
            _first(
                item,
                "codex_quota_protection_reason",
                "codexQuotaProtectionReason",
            )
        ),
        "codexQuotaProtectionResetAt": _first(
            item,
            "codex_quota_protection_reset_at",
            "codexQuotaProtectionResetAt",
        ),
        "groups": groups,
    }


def _bulk_operation_result(payload: dict[str, Any], account_ids: list[int]) -> dict[str, Any]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        data = {}
    requested = set(account_ids)
    outcomes: dict[int, dict[str, Any]] = {}
    for raw_id in data.get("success_ids") or data.get("successIds") or []:
        account_id = _positive_int(raw_id, -1)
        if account_id in requested:
            outcomes[account_id] = {"accountId": account_id, "success": True, "message": ""}
    for raw_id in data.get("failed_ids") or data.get("failedIds") or []:
        account_id = _positive_int(raw_id, -1)
        if account_id in requested:
            outcomes[account_id] = {"accountId": account_id, "success": False, "message": "操作失败"}
    for item in data.get("results") or []:
        if not isinstance(item, dict):
            continue
        account_id = _positive_int(_first(item, "account_id", "accountId"), -1)
        if account_id not in requested:
            continue
        success = bool(item.get("success"))
        outcomes[account_id] = {
            "accountId": account_id,
            "success": success,
            "message": _safe_text(_first(item, "error", "message")) if not success else "",
        }
    reported_success = _positive_int(data.get("success"), -1)
    reported_failed = _positive_int(data.get("failed"), -1)
    if not outcomes and reported_success == len(account_ids) and reported_failed == 0:
        outcomes = {
            account_id: {"accountId": account_id, "success": True, "message": ""}
            for account_id in account_ids
        }
    results = [
        outcomes.get(
            account_id,
            {"accountId": account_id, "success": False, "message": "平台未返回该账号的操作结果"},
        )
        for account_id in account_ids
    ]
    success = sum(1 for item in results if item["success"])
    return {
        "ok": success == len(results),
        "total": len(results),
        "success": success,
        "failed": len(results) - success,
        "successIds": [item["accountId"] for item in results if item["success"]],
        "failedIds": [item["accountId"] for item in results if not item["success"]],
        "results": results,
    }


def _bulk_response_has_account_outcomes(payload: dict[str, Any]) -> bool:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        return False
    return bool(
        data.get("success_ids")
        or data.get("successIds")
        or data.get("failed_ids")
        or data.get("failedIds")
        or data.get("results")
    )


def _import_error_details(data: dict[str, Any]) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    for item in data.get("errors") or []:
        if not isinstance(item, dict):
            continue
        detail = {
            "index": _safe_text(_first(item, "index", "account_index"), 32),
            "name": _safe_text(item.get("name"), 160),
            "message": _safe_text(_first(item, "message", "error"), 300),
        }
        if detail["message"]:
            errors.append(detail)
    return errors


class PixelManager:
    def __init__(
        self,
        config: PixelManagerConfig,
        *,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
        fallback_client_factory: Callable[[], httpx.AsyncClient] | None = None,
        clock: Callable[[], float] = time.monotonic,
        inter_target_delay_seconds: float = 30.0,
        sleeper: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        self.config = config
        self._clock = clock
        self._client_factory = client_factory or (
            lambda: httpx.AsyncClient(timeout=httpx.Timeout(30.0), follow_redirects=False)
        )
        self._fallback_client_factory = fallback_client_factory
        self._tokens: dict[str, TokenState] = {}
        self._token_locks = {target_id: asyncio.Lock() for target_id in config.targets}
        self._operation_locks = {target_id: asyncio.Lock() for target_id in config.targets}
        self._inter_target_delay_seconds = max(float(inter_target_delay_seconds), 0.0)
        self._sleeper = sleeper
        self._target_status: dict[str, dict[str, Any]] = {
            target_id: {
                "connected": False,
                "accountCount": None,
                "lastCheckedAt": None,
                "error": None,
            }
            for target_id in config.targets
        }

    def authorized(self, provided_key: str | None) -> bool:
        if self.config.allow_open_access:
            return True
        candidate = str(provided_key or "")
        return bool(candidate) and secrets.compare_digest(candidate, self.config.manager_key)

    def _target(self, target_id: str) -> PixelTarget:
        target = self.config.targets.get(target_id)
        if target is None:
            raise PixelManagerError("平台账号不存在", 404)
        return target

    def _public_target(self, target: PixelTarget) -> dict[str, Any]:
        return {"id": target.id, "email": target.email, **self._target_status[target.id]}

    def targets(self) -> list[dict[str, Any]]:
        return [self._public_target(target) for target in self.config.targets.values()]

    def validate_target_ids(self, target_ids: Iterable[str]) -> list[str]:
        normalized = list(dict.fromkeys(str(value).strip() for value in target_ids if str(value).strip()))
        if not normalized:
            raise PixelValidationError("至少选择一个平台账号")
        for target_id in normalized:
            self._target(target_id)
        return normalized

    async def _decode_response(self, response: httpx.Response) -> dict[str, Any]:
        if not response.is_success:
            raise PixelManagerError(f"平台请求失败（HTTP {response.status_code}）")
        try:
            payload = response.json()
        except ValueError as exc:
            raise PixelManagerError("平台返回了无效数据") from exc
        if not isinstance(payload, dict):
            raise PixelManagerError("平台返回了无效数据")
        return payload

    async def _authenticate(
        self,
        target: PixelTarget,
        client: httpx.AsyncClient,
        *,
        force: bool = False,
    ) -> TokenState:
        async with self._token_locks[target.id]:
            cached = self._tokens.get(target.id)
            if cached and not force and cached.expires_at > self._clock():
                return cached

            if cached and cached.refresh_token:
                try:
                    response = await client.post(
                        f"{target.base_url}{target.refresh_path}",
                        json={"refresh_token": cached.refresh_token},
                        headers={"Accept": "application/json", "x-user-ui-request": "1"},
                    )
                    if response.is_success:
                        refreshed = self._token_from_payload(response.json(), cached.refresh_token)
                        self._tokens[target.id] = refreshed
                        return refreshed
                except (httpx.HTTPError, ValueError, PixelManagerError):
                    pass

            login_payload: dict[str, Any] = {"email": target.email, "password": target.password}
            if target.login_agreement_revision:
                login_payload["login_agreement_revision"] = target.login_agreement_revision
            try:
                response = await client.post(
                    f"{target.base_url}/api/v1/auth/login",
                    json=login_payload,
                    headers={"Accept": "application/json", "x-user-ui-request": "1"},
                )
            except httpx.HTTPError:
                raise
            if not response.is_success:
                raise PixelManagerError(f"平台登录失败（HTTP {response.status_code}）")
            try:
                token = self._token_from_payload(response.json())
            except (ValueError, PixelManagerError) as exc:
                raise PixelManagerError("平台登录返回了无效数据") from exc
            self._tokens[target.id] = token
            return token

    def _token_from_payload(self, payload: Any, fallback_refresh: str = "") -> TokenState:
        if not isinstance(payload, dict):
            raise PixelManagerError("认证数据无效")
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        access_token = str(_first(data, "access_token", "accessToken", "token", default="") or "")
        refresh_token = str(
            _first(data, "refresh_token", "refreshToken", default=fallback_refresh) or fallback_refresh
        )
        if not access_token:
            raise PixelManagerError("认证数据无效")
        expires_in = max(_positive_int(_first(data, "expires_in", "expiresIn", default=3600), 3600), 1)
        usable_seconds = max(expires_in - TOKEN_EXPIRY_SKEW_SECONDS, 1)
        return TokenState(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=self._clock() + usable_seconds,
        )

    async def _request(
        self,
        target: PixelTarget,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        timeout: float | None = None,
        retry_transient: bool = False,
    ) -> dict[str, Any]:
        factories = [self._client_factory]
        if self._fallback_client_factory is not None:
            factories.append(self._fallback_client_factory)
        for index, factory in enumerate(factories):
            try:
                return await self._request_once(
                    factory,
                    target,
                    method,
                    path,
                    params=params,
                    json_body=json_body,
                    timeout=timeout,
                    retry_transient=retry_transient,
                )
            except httpx.HTTPError as exc:
                if index + 1 == len(factories):
                    raise PixelManagerError("平台连接失败") from exc
        raise PixelManagerError("平台连接失败")

    async def _request_once(
        self,
        client_factory: Callable[[], httpx.AsyncClient],
        target: PixelTarget,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        timeout: float | None = None,
        retry_transient: bool = False,
    ) -> dict[str, Any]:
        try:
            async with client_factory() as client:
                token = await self._authenticate(target, client)
                attempts = 3 if retry_transient else 1
                request_timeout = timeout if timeout is not None else DEFAULT_PLATFORM_TIMEOUT_SECONDS
                response: httpx.Response | None = None
                for attempt in range(attempts):
                    response = await client.request(
                        method,
                        f"{target.base_url}{path}",
                        params=params,
                        json=json_body,
                        timeout=request_timeout,
                        headers={
                            "Accept": "application/json",
                            "Authorization": f"Bearer {token.access_token}",
                            "x-user-ui-request": "1",
                        },
                    )
                    if response.status_code == 401:
                        token = await self._authenticate(target, client, force=True)
                        response = await client.request(
                            method,
                            f"{target.base_url}{path}",
                            params=params,
                            json=json_body,
                            timeout=request_timeout,
                            headers={
                                "Accept": "application/json",
                                "Authorization": f"Bearer {token.access_token}",
                                "x-user-ui-request": "1",
                            },
                        )
                        if response.status_code == 401:
                            async with self._token_locks[target.id]:
                                self._tokens.pop(target.id, None)
                            token = await self._authenticate(target, client)
                            response = await client.request(
                                method,
                                f"{target.base_url}{path}",
                                params=params,
                                json=json_body,
                                timeout=request_timeout,
                                headers={
                                    "Accept": "application/json",
                                    "Authorization": f"Bearer {token.access_token}",
                                    "x-user-ui-request": "1",
                                },
                            )
                    if (
                        retry_transient
                        and response.status_code in {429, 500, 502, 503, 504}
                        and attempt + 1 < attempts
                    ):
                        retry_after = _optional_number(response.headers.get("Retry-After"))
                        delay = min(max(retry_after or (0.5 * (2**attempt)), 0.1), 5.0)
                        await asyncio.sleep(delay)
                        continue
                    break
                if response is None:
                    raise PixelManagerError("平台连接失败")
                return await self._decode_response(response)
        except PixelManagerError:
            raise
        except httpx.HTTPError:
            raise

    async def _account_page(
        self,
        target: PixelTarget,
        page: int,
        page_size: int,
        search: str = "",
        status: str = "",
    ) -> dict[str, Any]:
        normalized_search = str(search or "").strip()
        normalized_status = _account_status_filter(status)
        params: dict[str, Any] = {
            "page": page,
            "page_size": page_size,
            "sort_by": "created_at",
            "sort_order": "desc",
            "timezone": "Asia/Shanghai",
        }
        if normalized_search:
            params["search"] = normalized_search
        if normalized_status:
            params["status"] = normalized_status
        payload = await self._request(
            target,
            "GET",
            "/api/v1/accounts",
            params=params,
        )
        data = payload.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise PixelManagerError("平台账号列表格式无效")
        return data

    async def list_accounts(
        self,
        target_id: str,
        page: int,
        page_size: int,
        search: str = "",
        status: str = "",
    ) -> dict[str, Any]:
        target = self._target(target_id)
        page = max(page, 1)
        page_size = max(min(page_size, 100), 1)
        normalized_search = str(search or "").strip()
        normalized_status = _account_status_filter(status)
        try:
            data = await self._account_page(
                target,
                page,
                page_size,
                normalized_search,
                normalized_status,
            )
        except PixelManagerError as exc:
            self._target_status[target.id].update(
                {"connected": False, "lastCheckedAt": _utc_now(), "error": exc.public_message}
            )
            raise
        items = [sanitized for item in data["items"] if (sanitized := sanitize_account(item))]
        total = _positive_int(data.get("total"), len(items))
        pages = _positive_int(data.get("pages"), math.ceil(total / page_size) if total else 0)
        response_page = max(_positive_int(data.get("page"), page), 1)
        response_size = max(min(_positive_int(data.get("page_size"), page_size), 100), 1)
        status_update: dict[str, Any] = {
            "connected": True,
            "lastCheckedAt": _utc_now(),
            "error": None,
        }
        if not normalized_search and not normalized_status:
            status_update["accountCount"] = total
        self._target_status[target.id].update(status_update)
        return {
            "items": items,
            "total": total,
            "page": response_page,
            "pageSize": response_size,
            "pages": pages,
            "target": self._public_target(target),
        }

    async def account_usage(self, target_id: str, account_id: int) -> dict[str, Any]:
        target = self._target(target_id)
        normalized_id = _account_ids([account_id], 1)[0]
        payload = await self._request(
            target,
            "GET",
            f"/api/v1/accounts/{normalized_id}/usage",
            params={"source": "local"},
        )
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        if not isinstance(data, dict):
            raise PixelManagerError("平台额度数据格式无效")

        def utilization(key: str) -> float | None:
            window = data.get(key)
            if not isinstance(window, dict):
                return None
            value = _optional_number(window.get("utilization"))
            return max(value, 0.0) if value is not None else None

        return {
            "accountId": normalized_id,
            "codex5hLimitPercent": utilization("five_hour"),
            "codex7dLimitPercent": utilization("seven_day"),
            "source": _safe_text(data.get("source"), 20) or "local",
            "updatedAt": data.get("updated_at"),
        }

    async def submit_withdrawal(
        self, target_id: str, amount: int, payment_method: str
    ) -> dict[str, Any]:
        if amount < 1:
            raise PixelValidationError("提现金额必须至少为 1 元")
        if payment_method not in {"wechat", "alipay"}:
            raise PixelValidationError("提现方式无效")
        target = self._target(target_id)
        async with self._operation_locks[target.id]:
            return await self._request(
                target,
                "POST",
                "/api/v1/user/withdrawals",
                json_body={"amount": amount, "payment_method": payment_method},
                timeout=30,
            )

    async def receipt_code(self, target_id: str, payment_method: str) -> dict[str, Any] | None:
        if payment_method not in {"wechat", "alipay"}:
            raise PixelValidationError("提现方式无效")
        target = self._target(target_id)
        payload = await self._request(
            target,
            "GET",
            "/api/v1/user/receipt-code",
            params={"payment_method": payment_method},
        )
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        return data if isinstance(data, dict) and data else None

    async def relogin(self, target_id: str) -> dict[str, Any]:
        target = self._target(target_id)
        async with self._operation_locks[target.id]:
            async with self._token_locks[target.id]:
                self._tokens.pop(target.id, None)
            try:
                async with self._client_factory() as client:
                    await self._authenticate(target, client)
            except PixelManagerError as exc:
                self._target_status[target.id].update(
                    {"connected": False, "lastCheckedAt": _utc_now(), "error": exc.public_message}
                )
                raise
            self._target_status[target.id].update(
                {"connected": True, "lastCheckedAt": _utc_now(), "error": None}
            )
            return {"ok": True, "target": self._public_target(target)}

    async def bulk_delete_accounts(
        self, target_id: str, account_ids: Iterable[int]
    ) -> dict[str, Any]:
        target = self._target(target_id)
        ids = _account_ids(account_ids)
        async with self._operation_locks[target.id]:
            result = await self._bulk_delete_ids_unlocked(target, ids)
        current_count = self._target_status[target.id].get("accountCount")
        if isinstance(current_count, int):
            self._target_status[target.id]["accountCount"] = max(
                current_count - result["success"], 0
            )
        return result

    async def _bulk_delete_ids_unlocked(
        self, target: PixelTarget, ids: list[int]
    ) -> dict[str, Any]:
        success_ids: list[int] = []
        failed_ids: list[int] = []
        for start in range(0, len(ids), MAX_BULK_ACCOUNTS):
            chunk = ids[start : start + MAX_BULK_ACCOUNTS]
            try:
                payload = await self._request(
                    target,
                    "POST",
                    "/api/v1/accounts/bulk-delete",
                    json_body={"account_ids": chunk},
                    timeout=LONG_OPERATION_TIMEOUT_SECONDS,
                )
                result = _bulk_operation_result(payload, chunk)
                success_ids.extend(result["successIds"])
                failed_ids.extend(result["failedIds"])
            except PixelManagerError:
                failed_ids.extend(chunk)
        return {
            "ok": not failed_ids,
            "success": len(success_ids),
            "failed": len(failed_ids),
            "successIds": success_ids,
            "failedIds": failed_ids,
        }

    async def delete_import_record(
        self, record: dict[str, Any]
    ) -> dict[str, Any]:
        target_records = record.get("targets")
        if not isinstance(target_records, list) or not target_records:
            raise PixelValidationError("导入记录没有可删除的平台账号")

        results: list[dict[str, Any]] = []
        for target_record in target_records:
            if not isinstance(target_record, dict):
                continue
            target_id = str(target_record.get("targetId") or "").strip()
            target = self._target(target_id)
            source_names = {
                str(value or "").strip().lower()
                for value in target_record.get("generatedNames") or []
                if str(value or "").strip()
            }
            base = {
                "targetId": target.id,
                "email": target.email,
                "requested": len(source_names),
                "matched": 0,
                "deleted": 0,
                "failed": 0,
                "deletedNames": [],
                "missingNames": [],
                "ambiguousNames": [],
                "failedIds": [],
                "status": "success",
                "message": "",
            }
            if not source_names:
                base["message"] = "该平台没有实际新增账号"
                results.append(base)
                continue

            async with self._operation_locks[target.id]:
                try:
                    accounts = await self._all_accounts_by_id(target)
                    ids_by_name: dict[str, list[int]] = {}
                    for account_id, name in accounts.items():
                        ids_by_name.setdefault(name, []).append(account_id)
                    missing = sorted(source_names - set(ids_by_name))
                    ambiguous = sorted(
                        name for name in source_names if len(ids_by_name.get(name, [])) > 1
                    )
                    matched_ids = sorted(
                        account_id
                        for name in source_names
                        if name not in ambiguous
                        for account_id in ids_by_name.get(name, [])
                    )
                    delete_result = await self._bulk_delete_ids_unlocked(target, matched_ids) if matched_ids else {
                        "success": 0,
                        "failed": 0,
                        "successIds": [],
                        "failedIds": [],
                    }
                    names_by_id = {
                        account_id: name
                        for name in source_names
                        if name not in ambiguous
                        for account_id in ids_by_name.get(name, [])
                    }
                    previous_result = next(
                        (
                            item
                            for item in record.get("lastDeleteResults") or []
                            if isinstance(item, dict) and item.get("targetId") == target.id
                        ),
                        {},
                    )
                    previous_deleted_names = {
                        str(value or "").strip().lower()
                        for value in previous_result.get("deletedNames") or []
                        if str(value or "").strip()
                    }
                    deleted_names = previous_deleted_names | {
                        names_by_id[account_id]
                        for account_id in delete_result["successIds"]
                        if account_id in names_by_id
                    }
                    missing = [name for name in missing if name not in previous_deleted_names]
                    base.update(
                        {
                            "matched": len(matched_ids),
                            "deleted": delete_result["success"],
                            "failed": delete_result["failed"],
                            "deletedNames": sorted(deleted_names),
                            "missingNames": missing,
                            "ambiguousNames": ambiguous,
                            "failedIds": delete_result["failedIds"],
                        }
                    )
                    if base["failed"] or ambiguous or missing:
                        base["status"] = "partial"
                        problems = []
                        if base["failed"]:
                            problems.append(f"删除失败 {base['failed']} 个")
                        if ambiguous:
                            problems.append(f"重复名称 {len(ambiguous)} 个")
                        if missing:
                            problems.append(f"未找到 {len(missing)} 个")
                        base["message"] = "；".join(problems)
                    else:
                        base["message"] = f"已删除 {base['deleted']} 个账号"
                except PixelManagerError as exc:
                    base.update({"status": "failed", "message": exc.public_message})
            results.append(base)
        deleted = sum(_positive_int(item.get("deleted")) for item in results)
        failed = sum(_positive_int(item.get("failed")) for item in results)
        has_issues = any(
            item.get("status") != "success"
            or item.get("missingNames")
            or item.get("ambiguousNames")
            for item in results
        )
        return {
            "recordId": str(record.get("recordId") or ""),
            "status": "partial" if has_issues else "success",
            "deleted": deleted,
            "failed": failed,
            "results": results,
            "message": "删除完成，但存在未处理账号" if has_issues else f"已删除 {deleted} 个账号",
        }

    async def _delete_all_accounts_unlocked(self, target: PixelTarget) -> dict[str, Any]:
        try:
            accounts = await self._all_accounts_by_id(target)
        except PixelManagerError as exc:
            return {
                "targetId": target.id,
                "email": target.email,
                "total": 0,
                "deleted": 0,
                "failed": 0,
                "failedIds": [],
                "status": "failed",
                "message": exc.public_message,
            }
        ids = sorted(accounts)
        deleted = failed = 0
        failed_ids: list[int] = []
        request_error = ""
        for start in range(0, len(ids), MAX_BULK_ACCOUNTS):
            chunk = ids[start : start + MAX_BULK_ACCOUNTS]
            try:
                payload = await self._request(
                    target,
                    "POST",
                    "/api/v1/accounts/bulk-delete",
                    json_body={"account_ids": chunk},
                    timeout=LONG_OPERATION_TIMEOUT_SECONDS,
                )
                result = _bulk_operation_result(payload, chunk)
                deleted += _positive_int(result.get("success"))
                failed += _positive_int(result.get("failed"))
                failed_ids.extend(result.get("failedIds") or [])
            except PixelManagerError as exc:
                failed += len(chunk)
                failed_ids.extend(chunk)
                request_error = request_error or exc.public_message
        self._target_status[target.id].update(
            {
                "connected": True,
                "accountCount": max(len(ids) - deleted, 0),
                "lastCheckedAt": _utc_now(),
                "error": None if not request_error else request_error,
            }
        )
        status = "success" if failed == 0 else ("failed" if deleted == 0 and ids else "partial")
        message = "已清空平台账号" if status == "success" else f"已删除 {deleted} 个，失败 {failed} 个"
        if request_error:
            message += f"；{request_error}"
        return {
            "targetId": target.id,
            "email": target.email,
            "total": len(ids),
            "deleted": deleted,
            "failed": failed,
            "failedIds": failed_ids,
            "status": status,
            "message": message,
        }

    async def delete_all_target_accounts(
        self,
        progress_callback: Callable[[dict[str, Any]], Any] | None = None,
    ) -> list[dict[str, Any]]:
        targets = list(self.config.targets.values())
        results: list[dict[str, Any]] = []
        for index, target in enumerate(targets):
            if progress_callback:
                update = progress_callback(
                    {
                        "phase": "deleting",
                        "currentTargetId": target.id,
                        "completedTargets": index,
                        "totalTargets": len(targets),
                        "deleteResults": list(results),
                    }
                )
                if asyncio.iscoroutine(update):
                    await update
            async with self._operation_locks[target.id]:
                results.append(await self._delete_all_accounts_unlocked(target))
        return results

    async def bulk_test_accounts(
        self, target_id: str, account_ids: Iterable[int]
    ) -> dict[str, Any]:
        target = self._target(target_id)
        ids = _account_ids(account_ids)

        async def test_one(account_id: int, semaphore: asyncio.Semaphore) -> dict[str, Any]:
            async with semaphore:
                try:
                    payload = await self._request(
                        target,
                        "POST",
                        f"/api/v1/accounts/{account_id}/test",
                        json_body={},
                        timeout=ACCOUNT_TEST_TIMEOUT_SECONDS,
                    )
                    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
                    if not isinstance(data, dict):
                        raise PixelManagerError("平台测试结果格式无效")
                    status = str(data.get("status") or "").strip().lower()
                    success = bool(data.get("success")) or status == "success"
                    latency = _optional_number(_first(data, "latency", "latency_ms", "latencyMs"))
                    return {
                        "accountId": account_id,
                        "success": success,
                        "message": _safe_text(data.get("message")) if not success else "连接正常",
                        "latencyMs": latency,
                    }
                except PixelManagerError as exc:
                    return {
                        "accountId": account_id,
                        "success": False,
                        "message": _safe_text(exc.public_message),
                        "latencyMs": None,
                    }

        async with self._operation_locks[target.id]:
            semaphore = asyncio.Semaphore(3)
            results = await asyncio.gather(*(test_one(account_id, semaphore) for account_id in ids))
        success = sum(1 for item in results if item["success"])
        return {
            "ok": success == len(results),
            "total": len(results),
            "success": success,
            "failed": len(results) - success,
            "successIds": [item["accountId"] for item in results if item["success"]],
            "failedIds": [item["accountId"] for item in results if not item["success"]],
            "results": results,
        }

    async def bulk_update_accounts(
        self,
        target_id: str,
        account_ids: Iterable[int],
        *,
        share_mode: str | None = None,
        concurrency: int | None = None,
    ) -> dict[str, Any]:
        target = self._target(target_id)
        ids = _account_ids(account_ids)
        normalized_share_mode = str(share_mode or "").strip().lower()
        if normalized_share_mode not in {"", "public"}:
            raise PixelValidationError("批量编辑只支持设置为公共")
        if concurrency is not None:
            if isinstance(concurrency, bool) or not isinstance(concurrency, int) or not 3 <= concurrency <= 50:
                raise PixelValidationError("并发数必须是 3-50 的整数")
        if not normalized_share_mode and concurrency is None:
            raise PixelValidationError("至少选择一项批量编辑内容")

        async with self._operation_locks[target.id]:
            if normalized_share_mode and concurrency is None:
                shared = await self._share_accounts_unlocked(target, ids)
                results = [
                    {
                        "accountId": account_id,
                        "success": account_id in set(shared["successIds"]),
                        "message": "" if account_id in set(shared["successIds"]) else "设置公共失败",
                    }
                    for account_id in ids
                ]
                success = sum(1 for item in results if item["success"])
                return {
                    "ok": success == len(ids),
                    "total": len(ids),
                    "success": success,
                    "failed": len(ids) - success,
                    "successIds": [item["accountId"] for item in results if item["success"]],
                    "failedIds": [item["accountId"] for item in results if not item["success"]],
                    "results": results,
                }

            body: dict[str, Any] = {"account_ids": ids}
            if normalized_share_mode:
                body["share_mode"] = "public"
            if concurrency is not None:
                body["concurrency"] = concurrency
            payload = await self._request(
                target,
                "POST",
                "/api/v1/accounts/bulk-update",
                json_body=body,
                timeout=LONG_OPERATION_TIMEOUT_SECONDS,
            )
            return _bulk_operation_result(payload, ids)

    async def _all_accounts_by_id(self, target: PixelTarget) -> dict[int, str]:
        result: dict[int, str] = {}
        for page in range(1, MAX_ACCOUNT_PAGES + 1):
            data = await self._account_page(target, page, ACCOUNT_PAGE_SIZE)
            items = data["items"]
            for item in items:
                if isinstance(item, dict):
                    account_id = _positive_int(item.get("id"), -1)
                    if account_id >= 0:
                        result[account_id] = str(item.get("name") or "").strip().lower()
            pages = _positive_int(data.get("pages"), page)
            if not items or page >= pages:
                return result
        raise PixelManagerError("平台账号数量超过安全扫描上限")

    async def _public_account_ids(
        self,
        target: PixelTarget,
        ids: list[int],
        concurrency: int,
    ) -> set[int]:
        requested = set(ids)
        for attempt in range(3):
            public_ids: set[int] = set()
            for page in range(1, MAX_ACCOUNT_PAGES + 1):
                data = await self._account_page(target, page, ACCOUNT_PAGE_SIZE)
                for item in data["items"]:
                    if not isinstance(item, dict):
                        continue
                    account_id = _positive_int(item.get("id"), -1)
                    share_mode = str(_first(item, "share_mode", "shareMode") or "").strip().lower()
                    account_concurrency = _positive_int(item.get("concurrency"), -1)
                    if (
                        account_id in requested
                        and share_mode == "public"
                        and account_concurrency == concurrency
                    ):
                        public_ids.add(account_id)
                pages = _positive_int(data.get("pages"), page)
                if not data["items"] or page >= pages:
                    break
            if public_ids == requested or attempt == 2:
                return public_ids
            await asyncio.sleep(0.3)
        return set()

    async def _share_chunk(self, target: PixelTarget, ids: list[int]) -> dict[str, Any]:
        idempotency_key = str(uuid.uuid4())
        payload = await self._request(
            target,
            "POST",
            "/api/v1/accounts/external-placement:convert-batch",
            json_body={
                "account_ids": ids,
                "target": "public_pool",
                "idempotency_key": idempotency_key,
            },
            timeout=LONG_OPERATION_TIMEOUT_SECONDS,
            retry_transient=True,
        )
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        requested = set(ids)
        success_ids = [
            value
            for value in dict.fromkeys(_positive_int(item, -1) for item in data.get("success_ids") or [])
            if value in requested
        ]
        failed_ids = [
            value
            for value in dict.fromkeys(_positive_int(item, -1) for item in data.get("failed_ids") or [])
            if value in requested and value not in success_ids
        ]
        if not success_ids and not failed_ids:
            for item in data.get("results") or []:
                if not isinstance(item, dict):
                    continue
                account_id = _positive_int(item.get("account_id"), -1)
                if account_id not in requested:
                    continue
                (success_ids if item.get("success") else failed_ids).append(account_id)
        accounted = set(success_ids) | set(failed_ids)
        failed_ids.extend(account_id for account_id in ids if account_id not in accounted)
        return {"successIds": success_ids, "failedIds": failed_ids}

    async def _share_accounts_unlocked(
        self,
        target: PixelTarget,
        ids: list[int],
        *,
        concurrency: int | None = None,
    ) -> dict[str, Any]:
        # PixelAPI exposes public sharing through bulk-update; keep the
        # account concurrency explicit for both import and retry paths.
        concurrency = PUBLIC_SHARE_CONCURRENCY if concurrency is None else concurrency
        if concurrency is not None:
            if isinstance(concurrency, bool) or not isinstance(concurrency, int) or not 1 <= concurrency <= 50:
                raise PixelValidationError("公共共享并发数必须是 1-50 的整数")
            success_ids: list[int] = []
            failed_ids: list[int] = []
            for start in range(0, len(ids), MAX_BULK_ACCOUNTS):
                chunk = ids[start : start + MAX_BULK_ACCOUNTS]
                verify_after_request = False
                try:
                    payload = await self._request(
                        target,
                        "POST",
                        "/api/v1/accounts/bulk-update",
                        json_body={
                            "account_ids": chunk,
                            "share_mode": "public",
                            "concurrency": concurrency,
                        },
                        timeout=LONG_OPERATION_TIMEOUT_SECONDS,
                        retry_transient=True,
                    )
                    result = _bulk_operation_result(payload, chunk)
                    success_ids.extend(result["successIds"])
                    failed_ids.extend(result["failedIds"])
                    verify_after_request = not _bulk_response_has_account_outcomes(payload)
                except PixelManagerError:
                    failed_ids.extend(chunk)
                    verify_after_request = True
                if verify_after_request:
                    try:
                        verified_ids = await self._public_account_ids(target, chunk, concurrency)
                    except PixelManagerError:
                        verified_ids = set()
                    success_ids.extend(account_id for account_id in chunk if account_id in verified_ids)
                    failed_ids = [account_id for account_id in failed_ids if account_id not in verified_ids]
            success_ids = list(dict.fromkeys(account_id for account_id in ids if account_id in set(success_ids)))
            failed_ids = [account_id for account_id in ids if account_id not in set(success_ids)]
            return {
                "ok": not failed_ids,
                "success": len(success_ids),
                "failed": len(failed_ids),
                "successIds": success_ids,
                "failedIds": failed_ids,
            }

        success_ids: list[int] = []
        failed_ids: list[int] = []
        for start in range(0, len(ids), SHARE_CHUNK_SIZE):
            result = await self._share_chunk(target, ids[start : start + SHARE_CHUNK_SIZE])
            success_ids.extend(result["successIds"])
            failed_ids.extend(result["failedIds"])
        return {
            "ok": not failed_ids,
            "success": len(success_ids),
            "failed": len(failed_ids),
            "successIds": success_ids,
            "failedIds": failed_ids,
        }

    async def share_accounts(self, target_id: str, account_ids: Iterable[int]) -> dict[str, Any]:
        target = self._target(target_id)
        ids = _account_ids(account_ids, MAX_SHARE_ACCOUNTS)
        async with self._operation_locks[target.id]:
            return await self._share_accounts_unlocked(
                target,
                ids,
                concurrency=PUBLIC_SHARE_CONCURRENCY,
            )

    async def _import_target(
        self, target: PixelTarget, bundle: TargetCredentialBundle
    ) -> dict[str, Any]:
        base_result = {
            "targetId": target.id,
            "email": target.email,
            "generatedFileName": bundle.generated_file_name,
            "sourceCount": bundle.source_count,
            "created": 0,
            "updated": 0,
            "failed": 0,
            "shared": 0,
            "shareFailed": 0,
            "failedShareIds": [],
            "importErrors": [],
            "generatedNames": [],
        }
        async with self._operation_locks[target.id]:
            try:
                before_accounts = await self._all_accounts_by_id(target)
            except PixelManagerError as exc:
                return {**base_result, "status": "failed", "message": exc.public_message}

            defaults = target.import_defaults
            created = updated = failed = 0
            request_error = ""
            for index, content in enumerate(bundle.contents):
                try:
                    payload = await self._request(
                        target,
                        "POST",
                        "/api/v1/accounts/import-credentials",
                        json_body={
                            "contents": [content],
                            "platform": defaults.platform,
                            "share_mode": defaults.share_mode,
                            "concurrency": defaults.concurrency,
                            "priority": defaults.priority,
                            "group_ids": list(defaults.group_ids),
                            "auto_pause_on_expired": defaults.auto_pause_on_expired,
                            "account_level": defaults.account_level,
                        },
                        timeout=LONG_OPERATION_TIMEOUT_SECONDS,
                    )
                    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
                    if not isinstance(data, dict):
                        raise PixelManagerError("平台导入返回了无效数据")
                    created += _positive_int(data.get("created"))
                    updated += _positive_int(data.get("updated"))
                    failed += _positive_int(data.get("failed"))
                    import_errors = _import_error_details(data)
                    base_result["importErrors"].extend(import_errors)
                    failed = max(failed, len(base_result["importErrors"]))
                except PixelManagerError as exc:
                    failed += sum(bundle.chunk_sizes[index:])
                    request_error = exc.public_message
                    break

            try:
                after_accounts = await self._all_accounts_by_id(target)
            except PixelManagerError as exc:
                base_result.update({"created": created, "updated": updated, "failed": failed})
                return {**base_result, "status": "failed", "message": exc.public_message}

            before_ids = set(before_accounts)
            generated_names = set(bundle.generated_names)
            imported_ids = sorted(
                account_id
                for account_id, name in after_accounts.items()
                if account_id not in before_ids and name in generated_names
            )
            generated_names = [after_accounts[account_id] for account_id in imported_ids]
            created = max(created, len(imported_ids))
            share_result = {"success": 0, "failed": 0, "failedIds": []}
            if imported_ids:
                try:
                    share_result = await self._share_accounts_unlocked(
                        target,
                        imported_ids,
                        concurrency=PUBLIC_SHARE_CONCURRENCY,
                    )
                except PixelManagerError as exc:
                    share_result = {
                        "success": 0,
                        "failed": len(imported_ids),
                        "failedIds": imported_ids,
                    }
                    request_error = request_error or exc.public_message

            unresolved_created = max(created - len(imported_ids), 0)
            share_failed = _positive_int(share_result.get("failed")) + unresolved_created
            import_error_summary = "; ".join(
                item["message"]
                for item in base_result["importErrors"][:3]
                if item.get("message")
            )
            base_result.update(
                {
                    "created": created,
                    "updated": updated,
                    "failed": failed,
                    "shared": _positive_int(share_result.get("success")),
                    "shareFailed": share_failed,
                    "failedShareIds": list(share_result.get("failedIds") or []),
                    "generatedNames": generated_names,
                }
            )
            if created + updated == 0 and (failed > 0 or request_error):
                status = "failed"
                message = request_error or f"导入失败 {failed} 个账号"
            elif failed or share_failed or request_error:
                status = "partial"
                message = f"导入完成，但有 {failed} 个导入失败、{share_failed} 个未开启公共共享"
                if request_error:
                    message += f"；{request_error}"
            else:
                status = "success"
                message = "导入完成，新增账号已开启公共共享"
            if import_error_summary:
                message += f"；平台明细：{import_error_summary}"
            return {**base_result, "status": status, "message": message}

    async def import_bundle(
        self,
        bundle: CredentialBundle,
        target_ids: Iterable[str],
        progress_callback: Callable[[dict[str, Any]], Any] | None = None,
    ) -> dict[str, Any]:
        normalized = self.validate_target_ids(target_ids)
        used_emails: set[str] = set()
        _collect_emails(bundle.source_payload, used_emails)
        results: list[dict[str, Any]] = []
        for index, target_id in enumerate(normalized):
            if progress_callback:
                update = progress_callback(
                    {
                        "phase": "processing",
                        "currentTargetId": target_id,
                        "completedTargets": index,
                        "totalTargets": len(normalized),
                        "results": list(results),
                    }
                )
                if asyncio.iscoroutine(update):
                    await update
            prepared = build_target_credential_bundle(bundle, used_emails)
            results.append(await self._import_target(self._target(target_id), prepared))
            if index + 1 < len(normalized) and self._inter_target_delay_seconds:
                wait_handled = False
                if progress_callback:
                    update = progress_callback(
                        {
                            "phase": "waiting",
                            "currentTargetId": normalized[index + 1],
                            "completedTargets": index + 1,
                            "totalTargets": len(normalized),
                            "waitSeconds": self._inter_target_delay_seconds,
                            "results": list(results),
                        }
                    )
                    if asyncio.iscoroutine(update):
                        update = await update
                    wait_handled = update is True
                if not wait_handled:
                    await self._sleeper(self._inter_target_delay_seconds)
        return {
            "ok": all(item["status"] == "success" for item in results),
            "sourceFileName": bundle.source_file_name,
            "sourceCount": bundle.source_count,
            "results": results,
        }

    async def _export_target(self, target: PixelTarget) -> dict[str, Any]:
        payload = await self._request(target, "GET", "/api/v1/accounts/data")
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        if not isinstance(data, dict) or not isinstance(data.get("accounts"), list):
            raise PixelManagerError("平台导出数据格式无效")
        if not isinstance(data.get("proxies"), list):
            data = {**data, "proxies": []}
        return data

    async def export_all(self) -> ExportBundle:
        merged_accounts: list[dict[str, Any]] = []
        merged_proxies: list[dict[str, Any]] = []
        seen_signatures: set[str] = set()
        seen_proxies: set[str] = set()
        source_count = duplicate_count = 0
        for target in self.config.targets.values():
            payload = await self._export_target(target)
            added, duplicates = _merge_export_payload(
                payload,
                merged_accounts,
                merged_proxies,
                seen_signatures,
                seen_proxies,
            )
            source_count += added
            duplicate_count += duplicates
        merged = {
            "exported_at": _utc_now(),
            "proxies": merged_proxies,
            "accounts": merged_accounts,
        }
        merged["account_batches"] = _export_account_batches(merged_accounts)
        content = json.dumps(merged, ensure_ascii=False, indent=2).encode("utf-8")
        return ExportBundle(
            content=content,
            source_count=source_count,
            deduplicated_count=len(merged["accounts"]),
            duplicate_count=duplicate_count,
            batch_count=len(merged["account_batches"]),
        )


class PixelImportJobs:
    def __init__(
        self,
        manager: PixelManager,
        record_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.manager = manager
        self.record_callback = record_callback
        self._jobs: dict[str, dict[str, Any]] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()

    def _public_job(self, job: dict[str, Any]) -> dict[str, Any]:
        return {
            key: copy.deepcopy(value)
            for key, value in job.items()
            if not key.startswith("_")
        }

    def _prune_completed_jobs(self) -> None:
        completed_ids = [
            job_id for job_id, task in self._tasks.items() if task.done()
        ]
        overflow = max(len(completed_ids) - (MAX_RETAINED_IMPORT_JOBS - 1), 0)
        for job_id in completed_ids[:overflow]:
            self._tasks.pop(job_id, None)
            self._jobs.pop(job_id, None)

    async def create(
        self, bundle: CredentialBundle, target_ids: Iterable[str]
    ) -> dict[str, Any]:
        normalized = self.manager.validate_target_ids(target_ids)
        async with self._lock:
            self._prune_completed_jobs()
            if any(not task.done() for task in self._tasks.values()):
                raise PixelManagerError("已有导入任务正在运行，请等待完成", 409)
            job_id = uuid.uuid4().hex
            now = _utc_now()
            job = {
                "jobId": job_id,
                "status": "queued",
                "phase": "queued",
                "createdAt": now,
                "updatedAt": now,
                "sourceFileName": bundle.source_file_name,
                "sourceFileNames": list(bundle.source_file_names or (bundle.source_file_name,)),
                "sourceCount": bundle.source_count,
                "currentTargetId": None,
                "completedTargets": 0,
                "totalTargets": len(normalized),
                "waitSeconds": 0,
                "nextRunAt": None,
                "results": [],
                "error": None,
                "_accelerateEvent": asyncio.Event(),
            }
            self._jobs[job_id] = job
            self._tasks[job_id] = asyncio.create_task(
                self._run(job_id, bundle, normalized),
                name=f"pixel-import-{job_id}",
            )
            return self._public_job(job)

    async def _run(
        self, job_id: str, bundle: CredentialBundle, target_ids: list[str]
    ) -> None:
        job = self._jobs[job_id]
        job.update({"status": "running", "phase": "processing", "updatedAt": _utc_now()})

        async def progress(update: dict[str, Any]) -> bool:
            if update.get("phase") != "waiting":
                job.update(update)
                job["status"] = "running"
                job["nextRunAt"] = None
                job["updatedAt"] = _utc_now()
                return False

            wait_seconds = max(float(update.get("waitSeconds") or 0), 0.0)
            accelerate_event: asyncio.Event = job["_accelerateEvent"]
            accelerate_event.clear()
            job.update(update)
            job["status"] = "running"
            job["updatedAt"] = _utc_now()
            job["nextRunAt"] = datetime.fromtimestamp(
                time.time() + wait_seconds,
                tz=timezone.utc,
            ).isoformat()
            try:
                await asyncio.wait_for(accelerate_event.wait(), timeout=wait_seconds)
            except asyncio.TimeoutError:
                pass
            finally:
                accelerate_event.clear()
                job["waitSeconds"] = 0
                job["nextRunAt"] = None
                job["updatedAt"] = _utc_now()
            return True

        try:
            result = await self.manager.import_bundle(bundle, target_ids, progress)
            if self.record_callback:
                try:
                    self.record_callback(
                        {
                            "recordId": job_id,
                            "createdAt": job["createdAt"],
                            "sourceFileName": bundle.source_file_name,
                            "sourceFileNames": list(bundle.source_file_names or (bundle.source_file_name,)),
                            "sourceCount": bundle.source_count,
                            "targets": result["results"],
                        }
                    )
                except Exception:
                    # Import success must not be changed by audit persistence errors.
                    pass
            job.update(
                {
                    "status": "completed",
                    "phase": "completed",
                    "currentTargetId": None,
                    "completedTargets": len(target_ids),
                    "waitSeconds": 0,
                    "nextRunAt": None,
                    "results": result["results"],
                    "updatedAt": _utc_now(),
                }
            )
        except asyncio.CancelledError:
            job.update(
                {
                    "status": "failed",
                    "phase": "failed",
                    "error": "服务重启，导入任务已中止",
                    "updatedAt": _utc_now(),
                }
            )
            raise
        except PixelManagerError as exc:
            job.update(
                {
                    "status": "failed",
                    "phase": "failed",
                    "error": exc.public_message,
                    "updatedAt": _utc_now(),
                }
            )
        except Exception:
            job.update(
                {
                    "status": "failed",
                    "phase": "failed",
                    "error": "导入任务执行失败",
                    "updatedAt": _utc_now(),
                }
            )

    def get(self, job_id: str) -> dict[str, Any]:
        job = self._jobs.get(str(job_id or ""))
        if job is None:
            raise PixelManagerError("导入任务不存在", 404)
        public = self._public_job(job)
        if public.get("phase") == "waiting" and public.get("nextRunAt"):
            try:
                deadline = datetime.fromisoformat(str(public["nextRunAt"]).replace("Z", "+00:00"))
                public["waitSeconds"] = max(
                    math.ceil((deadline - datetime.now(timezone.utc)).total_seconds()),
                    0,
                )
            except (TypeError, ValueError):
                pass
        return public

    async def accelerate(self, job_id: str) -> dict[str, Any]:
        async with self._lock:
            job = self._jobs.get(str(job_id or ""))
            if job is None:
                raise PixelManagerError("导入任务不存在", 404)
            if job.get("status") != "running" or job.get("phase") != "waiting":
                raise PixelManagerError("当前导入任务没有可加速的等待步骤", 409)
            accelerate_event = job.get("_accelerateEvent")
            if not isinstance(accelerate_event, asyncio.Event):
                raise PixelManagerError("当前导入任务无法加速", 409)
            job["waitSeconds"] = 0
            job["nextRunAt"] = _utc_now()
            job["updatedAt"] = _utc_now()
            accelerate_event.set()
            return self._public_job(job)


class PixelExportJobs:
    def __init__(self, manager: PixelManager, backup_dir: str | Path) -> None:
        self.manager = manager
        self.backup_dir = Path(backup_dir)
        self._jobs: dict[str, dict[str, Any]] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()

    def _public_job(self, job: dict[str, Any]) -> dict[str, Any]:
        return {
            key: copy.deepcopy(value)
            for key, value in job.items()
            if not key.startswith("_")
        }

    def _prune_completed_jobs(self) -> None:
        completed_ids = [
            job_id for job_id, task in self._tasks.items() if task.done()
        ]
        overflow = max(len(completed_ids) - (MAX_RETAINED_EXPORT_JOBS - 1), 0)
        for job_id in completed_ids[:overflow]:
            self._tasks.pop(job_id, None)
            self._jobs.pop(job_id, None)

    async def create_rebuild(self, target_ids: Iterable[str]) -> dict[str, Any]:
        normalized = self.manager.validate_target_ids(target_ids)
        async with self._lock:
            self._prune_completed_jobs()
            if any(not task.done() for task in self._tasks.values()):
                raise PixelManagerError("已有汇总整理任务正在运行，请等待完成", 409)
            job_id = uuid.uuid4().hex
            now = _utc_now()
            job = {
                "jobId": job_id,
                "status": "queued",
                "phase": "queued",
                "createdAt": now,
                "updatedAt": now,
                "mode": "export_delete_reimport",
                "currentTargetId": None,
                "completedTargets": 0,
                "totalTargets": len(self.manager.config.targets),
                "waitSeconds": 0,
                "backupFileName": None,
                "export": None,
                "deleteResults": [],
                "results": [],
                "error": None,
            }
            self._jobs[job_id] = job
            self._tasks[job_id] = asyncio.create_task(
                self._run_rebuild(job_id, normalized),
                name=f"pixel-export-rebuild-{job_id}",
            )
            return self._public_job(job)

    async def _save_backup(self, job_id: str, content: bytes) -> tuple[str, Path]:
        try:
            self.backup_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            file_name = f"pixel-accounts-{stamp}-{job_id[:8]}.json"
            path = self.backup_dir / file_name
            path.write_bytes(content)
            path.chmod(0o600)
            return file_name, path
        except OSError as exc:
            raise PixelManagerError("导出备份保存失败，已停止删除", 500) from exc

    async def _run_rebuild(self, job_id: str, target_ids: list[str]) -> None:
        job = self._jobs[job_id]
        job.update({"status": "running", "phase": "exporting", "updatedAt": _utc_now()})

        async def delete_progress(update: dict[str, Any]) -> None:
            job.update(update)
            job["phase"] = "deleting"
            job["status"] = "running"
            job["updatedAt"] = _utc_now()

        async def import_progress(update: dict[str, Any]) -> None:
            job.update(update)
            job["phase"] = "waiting" if update.get("phase") == "waiting" else "importing"
            job["status"] = "running"
            job["updatedAt"] = _utc_now()

        try:
            export = await self.manager.export_all()
            if export.deduplicated_count <= 0:
                raise PixelValidationError("汇总导出没有账号，已停止删除")
            job.update(
                {
                    "phase": "backing_up",
                    "export": {
                        "sourceCount": export.source_count,
                        "deduplicatedCount": export.deduplicated_count,
                        "duplicateCount": export.duplicate_count,
                        "batchCount": export.batch_count,
                    },
                    "updatedAt": _utc_now(),
                }
            )
            backup_file_name, backup_path = await self._save_backup(job_id, export.content)
            bundle = parse_credential_bundle(backup_file_name, export.content)
            job.update(
                {
                    "backupFileName": backup_file_name,
                    "_backupPath": str(backup_path),
                    "phase": "deleting",
                    "updatedAt": _utc_now(),
                }
            )
            delete_results = await self.manager.delete_all_target_accounts(delete_progress)
            job["deleteResults"] = delete_results
            successful_deleted_targets = {
                item["targetId"]
                for item in delete_results
                if item.get("status") == "success"
            }
            import_targets = [target_id for target_id in target_ids if target_id in successful_deleted_targets]
            skipped_targets = [target_id for target_id in target_ids if target_id not in successful_deleted_targets]
            if import_targets:
                result = await self.manager.import_bundle(bundle, import_targets, import_progress)
                import_results = result["results"]
            else:
                import_results = []
            if skipped_targets:
                target_by_id = {target.id: target for target in self.manager.config.targets.values()}
                import_results.extend(
                    {
                        "targetId": target_id,
                        "email": target_by_id[target_id].email,
                        "generatedFileName": backup_file_name,
                        "sourceCount": bundle.source_count,
                        "created": 0,
                        "updated": 0,
                        "failed": bundle.source_count,
                        "shared": 0,
                        "shareFailed": 0,
                        "failedShareIds": [],
                        "status": "failed",
                        "message": "该平台账号未完全清空，已跳过重新导入，避免重复账号",
                    }
                    for target_id in skipped_targets
                )
            job.update(
                {
                    "status": "completed",
                    "phase": "completed",
                    "currentTargetId": None,
                    "completedTargets": len(target_ids),
                    "totalTargets": len(target_ids),
                    "waitSeconds": 0,
                    "deleteResults": delete_results,
                    "results": import_results,
                    "updatedAt": _utc_now(),
                }
            )
        except asyncio.CancelledError:
            job.update(
                {
                    "status": "failed",
                    "phase": "failed",
                    "error": "服务重启，汇总整理任务已中止",
                    "updatedAt": _utc_now(),
                }
            )
            raise
        except PixelManagerError as exc:
            job.update(
                {
                    "status": "failed",
                    "phase": "failed",
                    "error": exc.public_message,
                    "updatedAt": _utc_now(),
                }
            )
        except Exception:
            job.update(
                {
                    "status": "failed",
                    "phase": "failed",
                    "error": "汇总整理任务执行失败",
                    "updatedAt": _utc_now(),
                }
            )

    def get(self, job_id: str) -> dict[str, Any]:
        job = self._jobs.get(str(job_id or ""))
        if job is None:
            raise PixelManagerError("汇总整理任务不存在", 404)
        return self._public_job(job)

    def backup_content(self, job_id: str) -> tuple[str, bytes]:
        job = self._jobs.get(str(job_id or ""))
        if job is None:
            raise PixelManagerError("汇总整理任务不存在", 404)
        path_value = job.get("_backupPath")
        file_name = str(job.get("backupFileName") or "pixel-accounts-backup.json")
        if not path_value:
            raise PixelManagerError("导出备份尚未生成", 404)
        path = Path(path_value)
        try:
            return file_name, path.read_bytes()
        except OSError as exc:
            raise PixelManagerError("导出备份读取失败", 404) from exc
