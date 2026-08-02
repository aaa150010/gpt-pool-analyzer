import asyncio
import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

try:
    from .analytics import build_analytics
    from .cost_ledger import CostLedger
    from .pixel_routes import create_pixel_router
    from .pixel_manager import (
        MAX_UPLOAD_BYTES,
        PixelConfigError,
        PixelExportJobs,
        PixelImportJobs,
        PixelManager,
        PixelManagerError,
        _safe_text,
        load_config as load_pixel_manager_config,
    )
    from .withdrawal_service import WithdrawalService, initialize_withdrawal_schema
    from .withdrawal_routes import create_withdrawal_router
except ImportError:
    from analytics import build_analytics
    from cost_ledger import CostLedger
    from pixel_routes import create_pixel_router
    from pixel_manager import (
        MAX_UPLOAD_BYTES,
        PixelConfigError,
        PixelExportJobs,
        PixelImportJobs,
        PixelManager,
        PixelManagerError,
        _safe_text,
        load_config as load_pixel_manager_config,
    )
    from withdrawal_service import WithdrawalService, initialize_withdrawal_schema
    from withdrawal_routes import create_withdrawal_router


API_PREFIX = "/gpt-api"
ALLOWED_ORIGINS = [
    "tauri://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
    "http://127.0.0.1:1420",
    "http://localhost:1420",
]
DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))
DB_PATH = DATA_DIR / "app.db"
POLL_INTERVAL_SECONDS = 300
STATE_HISTORY_SECONDS = 86400
POOL_DASHBOARD_URL = "https://cf.ai-pixel.online/api/v1/accounts/quota-dashboard?timezone=Asia%2FShanghai"
POOL_LOGIN_URL = "https://cf.ai-pixel.online/api/v1/auth/login"
PIXEL_MANAGER_CONFIG_PATH = Path(os.getenv("PIXEL_MANAGER_CONFIG_PATH", "/data/pixel_manager.json"))
PIXEL_HTTP_PROXY = str(os.getenv("PIXEL_HTTP_PROXY", "")).strip()
pixel_manager: PixelManager | None = None
pixel_import_jobs: PixelImportJobs | None = None
pixel_export_jobs: PixelExportJobs | None = None
withdrawal_worker_task: asyncio.Task[Any] | None = None
withdrawal_wake_event = asyncio.Event()


def initialize_pixel_manager() -> None:
    global pixel_manager, pixel_import_jobs, pixel_export_jobs
    try:
        config = load_pixel_manager_config(PIXEL_MANAGER_CONFIG_PATH)
    except PixelConfigError:
        pixel_manager = None
        pixel_import_jobs = None
        pixel_export_jobs = None
        return
    direct_client_factory = lambda: httpx.AsyncClient(
        timeout=httpx.Timeout(30.0), follow_redirects=False
    )
    proxy_client_factory = (
        lambda: httpx.AsyncClient(
            proxies=PIXEL_HTTP_PROXY,
            timeout=httpx.Timeout(30.0),
            follow_redirects=False,
        )
        if PIXEL_HTTP_PROXY
        else direct_client_factory
    )
    pixel_manager = PixelManager(
        config,
        client_factory=proxy_client_factory,
        fallback_client_factory=direct_client_factory if PIXEL_HTTP_PROXY else None,
    )
    pixel_import_jobs = PixelImportJobs(pixel_manager, record_callback=save_pixel_import_record)
    pixel_export_jobs = PixelExportJobs(pixel_manager, DATA_DIR / "pixel_exports")


def set_pixel_import_jobs(value: PixelImportJobs) -> None:
    global pixel_import_jobs
    pixel_import_jobs = value


def set_pixel_export_jobs(value: PixelExportJobs) -> None:
    global pixel_export_jobs
    pixel_export_jobs = value


def require_pixel_manager(
    manager_key: str | None = Header(default=None, alias="X-91-Manager-Key"),
) -> PixelManager:
    if pixel_manager is None:
        initialize_pixel_manager()
    if pixel_manager is None:
        raise HTTPException(status_code=503, detail="账号池管理配置不可用")
    if not pixel_manager.authorized(manager_key):
        raise HTTPException(status_code=401, detail="账号池管理认证失败")
    return pixel_manager


def pixel_http_error(exc: PixelManagerError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.public_message)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS balance_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                total REAL NOT NULL,
                amounts TEXT NOT NULL,
                accounts TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS pool_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                group_name TEXT NOT NULL,
                status TEXT NOT NULL,
                total INTEGER NOT NULL,
                active INTEGER NOT NULL,
                schedulable INTEGER NOT NULL,
                remaining5h INTEGER,
                remaining7d INTEGER,
                utilization5h REAL,
                utilization7d REAL,
                concurrent_available INTEGER NOT NULL,
                concurrent_total INTEGER NOT NULL,
                limited INTEGER NOT NULL,
                quota_protected INTEGER NOT NULL,
                error INTEGER NOT NULL,
                disabled INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS cost_additions (
                id TEXT PRIMARY KEY,
                date TEXT NOT NULL,
                note TEXT NOT NULL,
                amount REAL NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS pixel_import_records (
                record_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                source_file_name TEXT NOT NULL,
                source_file_names TEXT NOT NULL DEFAULT '[]',
                source_count INTEGER NOT NULL,
                targets TEXT NOT NULL,
                delete_status TEXT NOT NULL DEFAULT 'active',
                deleted_at TEXT,
                last_delete_results TEXT NOT NULL DEFAULT '[]'
            );
            """
        )
        initialize_withdrawal_schema(conn)
        pool_columns = {row["name"] for row in conn.execute("PRAGMA table_info(pool_history)").fetchall()}
        migrations = {
            "capacity_5h": "ALTER TABLE pool_history ADD COLUMN capacity_5h INTEGER",
            "capacity_7d": "ALTER TABLE pool_history ADD COLUMN capacity_7d INTEGER",
            "remaining_capacity_5h": "ALTER TABLE pool_history ADD COLUMN remaining_capacity_5h REAL",
            "remaining_capacity_7d": "ALTER TABLE pool_history ADD COLUMN remaining_capacity_7d REAL",
        }
        for column, statement in migrations.items():
            if column not in pool_columns:
                conn.execute(statement)
        import_record_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(pixel_import_records)").fetchall()
        }
        if "source_file_names" not in import_record_columns:
            conn.execute(
                "ALTER TABLE pixel_import_records ADD COLUMN source_file_names TEXT NOT NULL DEFAULT '[]'"
            )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pool_history_group_date_id ON pool_history(group_name, date, id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_balance_history_date_id ON balance_history(date, id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pixel_import_records_created_at ON pixel_import_records(created_at, record_id)")
        conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES('initialized', 'false')")


def _public_import_target(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "targetId": str(result.get("targetId") or ""),
        "email": str(result.get("email") or ""),
        "generatedFileName": str(result.get("generatedFileName") or ""),
        "sourceCount": flexible_int(result.get("sourceCount")),
        "created": flexible_int(result.get("created")),
        "updated": flexible_int(result.get("updated")),
        "failed": flexible_int(result.get("failed")),
        "shared": flexible_int(result.get("shared")),
        "shareFailed": flexible_int(result.get("shareFailed")),
        "importErrors": [
            {
                "index": _safe_text(item.get("index"), 32),
                "name": _safe_text(item.get("name"), 160),
                "message": _safe_text(item.get("message"), 300),
            }
            for item in result.get("importErrors") or []
            if isinstance(item, dict) and _safe_text(item.get("message"), 300)
        ],
        "status": str(result.get("status") or "failed"),
        "message": _safe_text(result.get("message") or ""),
        "generatedNames": [
            str(value).strip().lower()
            for value in result.get("generatedNames") or []
            if str(value).strip()
        ],
    }


def save_pixel_import_record(record: dict[str, Any]) -> None:
    targets = [
        _public_import_target(item)
        for item in record.get("targets") or []
        if isinstance(item, dict)
    ]
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO pixel_import_records(
                record_id, created_at, source_file_name, source_file_names, source_count, targets,
                delete_status, deleted_at, last_delete_results
            ) VALUES(?, ?, ?, ?, ?, ?, 'active', NULL, '[]')
            ON CONFLICT(record_id) DO UPDATE SET
                source_file_name = excluded.source_file_name,
                source_file_names = excluded.source_file_names,
                source_count = excluded.source_count,
                targets = excluded.targets
            """,
            (
                str(record.get("recordId") or ""),
                str(record.get("createdAt") or utc_now()),
                str(record.get("sourceFileName") or "accounts.json"),
                dumps(record.get("sourceFileNames") or [record.get("sourceFileName") or "accounts.json"]),
                flexible_int(record.get("sourceCount")),
                dumps(targets),
            ),
        )


def pixel_import_record_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "recordId": row["record_id"],
        "createdAt": row["created_at"],
        "sourceFileName": row["source_file_name"],
        "sourceFileNames": loads(row["source_file_names"], []) or [row["source_file_name"]],
        "sourceCount": row["source_count"],
        "targetCount": len(loads(row["targets"], [])),
        "targets": loads(row["targets"], []),
        "deleteStatus": row["delete_status"],
        "deletedAt": row["deleted_at"],
        "lastDeleteResults": loads(row["last_delete_results"], []),
    }


def pixel_import_records() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM pixel_import_records ORDER BY created_at DESC, record_id DESC"
        ).fetchall()
    return [pixel_import_record_row(row) for row in rows]


def pixel_import_record(record_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM pixel_import_records WHERE record_id = ?",
            (str(record_id or ""),),
        ).fetchone()
    return pixel_import_record_row(row) if row else None


def update_pixel_import_record_delete(record_id: str, result: dict[str, Any]) -> dict[str, Any]:
    delete_status = "deleted" if result.get("status") == "success" else "partial"
    deleted_at = utc_now() if delete_status == "deleted" else None
    with connect() as conn:
        conn.execute(
            """
            UPDATE pixel_import_records
            SET delete_status = ?, deleted_at = ?, last_delete_results = ?
            WHERE record_id = ?
            """,
            (delete_status, deleted_at, dumps(result.get("results") or []), str(record_id or "")),
        )
    updated = pixel_import_record(record_id)
    if updated is None:
        raise HTTPException(status_code=404, detail="导入记录不存在")
    return updated


def update_pixel_import_record_share(
    record_id: str,
    target_id: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    record = pixel_import_record(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="导入记录不存在")
    targets = list(record.get("targets") or [])
    updated = False
    failed_ids = list(result.get("failedIds") or [])
    for target in targets:
        if str(target.get("targetId") or "") != str(target_id):
            continue
        target["shareFailed"] = len(failed_ids)
        target["shared"] = max(
            0,
            flexible_int(target.get("created")) - target["shareFailed"],
        )
        share_failed = target["shareFailed"]
        target["status"] = "success" if share_failed == 0 and not flexible_int(target.get("failed")) else "partial"
        target["message"] = (
            "导入完成，新增账号已开启公共共享"
            if target["status"] == "success"
            else f"导入完成，但有 {share_failed} 个未开启公共共享"
        )
        updated = True
        break
    if not updated:
        raise HTTPException(status_code=404, detail="导入记录中的平台不存在")
    with connect() as conn:
        conn.execute(
            "UPDATE pixel_import_records SET targets = ? WHERE record_id = ?",
            (dumps(targets), str(record_id or "")),
        )
    updated_record = pixel_import_record(record_id)
    if updated_record is None:
        raise HTTPException(status_code=404, detail="导入记录不存在")
    return updated_record


def get_meta(key: str, default: str = "") -> str:
    with connect() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_meta(key: str, value: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def initialized() -> bool:
    return get_meta("initialized", "false") == "true"


def set_setting(key: str, value: Any) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, dumps(value)),
        )


def get_setting(key: str, default: Any) -> Any:
    with connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return loads(row["value"], default) if row else default


def update_stored_cost(delta: float) -> None:
    stored = get_setting("stored_state", {})
    stored["cost"] = max(flexible_number(stored.get("cost")) + delta, 0)
    set_setting("stored_state", stored)


def flexible_number(value: Any, default: float = 0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def flexible_int(value: Any, default: int = 0) -> int:
    return int(flexible_number(value, default))


def compact_stored_state(raw: dict[str, Any]) -> dict[str, Any]:
    state = dict(raw)
    state["partnerCost"] = 0
    state.pop("history", None)
    state.pop("costAdditions", None)
    return state


def compact_pool_state(raw: dict[str, Any]) -> dict[str, Any]:
    secret_keys = {"accessToken", "refreshToken", "access_token", "refresh_token"}

    def strip_tokens(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: strip_tokens(item) for key, item in value.items() if key not in secret_keys}
        if isinstance(value, list):
            return [strip_tokens(item) for item in value]
        return value

    state = strip_tokens(dict(raw))
    state.pop("history", None)
    return state


def normalize_pool_credentials(raw: dict[str, Any]) -> dict[str, str]:
    return {
        "email": str(raw.get("email") or "").strip(),
        "password": str(raw.get("password") or "").strip(),
    }


def normalize_smtp_settings(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "host": str(raw.get("host") or "smtp.qq.com").strip(),
        "port": flexible_int(raw.get("port"), 465),
        "username": str(raw.get("username") or "").strip(),
        "password": str(raw.get("password") or "").strip(),
        "senderName": str(raw.get("senderName") or "").strip(),
        "recipient": str(raw.get("recipient") or "").strip(),
    }


def public_balance_accounts(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    accounts: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        api_key = str(item.get("apiKey") or item.get("api_key") or "").strip()
        accounts.append(
            {
                "name": str(item.get("name") or "").strip(),
                "baseURL": str(item.get("baseURL") or item.get("base_url") or "").strip(),
                "apiKey": "",
                "hasApiKey": bool(api_key),
            }
        )
    return accounts


def public_pool_credentials(raw: Any) -> dict[str, Any]:
    credentials = normalize_pool_credentials(raw if isinstance(raw, dict) else {})
    return {
        "email": "",
        "password": "",
        "hasEmail": bool(credentials["email"]),
        "hasPassword": bool(credentials["password"]),
    }


def public_smtp_settings(raw: Any) -> dict[str, Any]:
    settings = normalize_smtp_settings(raw if isinstance(raw, dict) else {})
    return {
        "host": settings["host"],
        "port": settings["port"],
        "username": "",
        "password": "",
        "senderName": settings["senderName"],
        "recipient": "",
        "hasUsername": bool(settings["username"]),
        "hasPassword": bool(settings["password"]),
        "hasRecipient": bool(settings["recipient"]),
    }


def insert_balance_snapshot(snapshot: dict[str, Any]) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO balance_history(date, total, amounts, accounts) VALUES(?, ?, ?, ?)",
            (
                snapshot.get("date") or utc_now(),
                flexible_number(snapshot.get("total")),
                dumps(snapshot.get("amounts") or []),
                dumps(snapshot.get("accounts") or []),
            ),
        )


def insert_pool_snapshot(snapshot: dict[str, Any]) -> None:
    group_name = snapshot.get("groupName") or snapshot.get("group_name") or ""
    snapshot_date = snapshot.get("date") or utc_now()
    values = (
        snapshot.get("status") or "",
        flexible_int(snapshot.get("total")),
        flexible_int(snapshot.get("active")),
        flexible_int(snapshot.get("schedulable")),
        snapshot.get("remaining5h"),
        snapshot.get("remaining7d"),
        snapshot.get("capacity5h"),
        snapshot.get("capacity7d"),
        snapshot.get("remainingCapacity5h"),
        snapshot.get("remainingCapacity7d"),
        snapshot.get("utilization5h"),
        snapshot.get("utilization7d"),
        flexible_int(snapshot.get("concurrentAvailable")),
        flexible_int(snapshot.get("concurrentTotal")),
        flexible_int(snapshot.get("limited")),
        flexible_int(snapshot.get("quotaProtected")),
        flexible_int(snapshot.get("error")),
        flexible_int(snapshot.get("disabled")),
    )
    with connect() as conn:
        latest = conn.execute(
            "SELECT * FROM pool_history WHERE group_name = ? ORDER BY date DESC, id DESC LIMIT 1",
            (group_name,),
        ).fetchone()
        latest_time = parse_time(latest["date"]) if latest else None
        current_time = parse_time(snapshot_date)
        if latest and latest_time and current_time and abs((current_time - latest_time).total_seconds()) <= 60:
            latest_values = (
                latest["status"],
                latest["total"],
                latest["active"],
                latest["schedulable"],
                latest["remaining5h"],
                latest["remaining7d"],
                latest["capacity_5h"],
                latest["capacity_7d"],
                latest["remaining_capacity_5h"],
                latest["remaining_capacity_7d"],
                latest["utilization5h"],
                latest["utilization7d"],
                latest["concurrent_available"],
                latest["concurrent_total"],
                latest["limited"],
                latest["quota_protected"],
                latest["error"],
                latest["disabled"],
            )
            if latest_values == values:
                return
        conn.execute(
            """
            INSERT INTO pool_history(
                date, group_name, status, total, active, schedulable, remaining5h, remaining7d,
                capacity_5h, capacity_7d, remaining_capacity_5h, remaining_capacity_7d,
                utilization5h, utilization7d, concurrent_available, concurrent_total, limited,
                quota_protected, error, disabled
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_date,
                group_name,
                *values,
            ),
        )


cost_ledger = CostLedger(
    connect=connect,
    dumps=dumps,
    loads=loads,
    utc_now=utc_now,
    number=flexible_number,
)
insert_cost_addition = cost_ledger.insert
clear_cost_additions = cost_ledger.clear_all
cost_additions_snapshot = cost_ledger.list
clear_cost_additions_if_snapshot = cost_ledger.clear_snapshot


def balance_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "date": row["date"],
        "total": row["total"],
        "amounts": loads(row["amounts"], []),
        "accounts": loads(row["accounts"], []),
    }


def latest_balance_snapshot_for_withdrawal() -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM balance_history ORDER BY date DESC, id DESC LIMIT 1").fetchone()
    return balance_row(row) if row else None


def pool_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "date": row["date"],
        "groupName": row["group_name"],
        "status": row["status"],
        "total": row["total"],
        "active": row["active"],
        "schedulable": row["schedulable"],
        "remaining5h": row["remaining5h"],
        "remaining7d": row["remaining7d"],
        "capacity5h": row["capacity_5h"],
        "capacity7d": row["capacity_7d"],
        "remainingCapacity5h": row["remaining_capacity_5h"],
        "remainingCapacity7d": row["remaining_capacity_7d"],
        "utilization5h": row["utilization5h"],
        "utilization7d": row["utilization7d"],
        "concurrentAvailable": row["concurrent_available"],
        "concurrentTotal": row["concurrent_total"],
        "limited": row["limited"],
        "quotaProtected": row["quota_protected"],
        "error": row["error"],
        "disabled": row["disabled"],
    }


def recent_cutoff() -> str:
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=STATE_HISTORY_SECONDS)).replace(microsecond=0)
    return cutoff.isoformat().replace("+00:00", "Z")


def balance_history(since: str | None = None) -> list[dict[str, Any]]:
    query = "SELECT * FROM balance_history"
    parameters: tuple[Any, ...] = ()
    if since:
        query += " WHERE date >= ?"
        parameters = (since,)
    query += " ORDER BY date ASC, id ASC"
    with connect() as conn:
        rows = conn.execute(query, parameters).fetchall()
    return [balance_row(row) for row in rows]


def pool_history(group_name: str | None = None, since: str | None = None) -> list[dict[str, Any]]:
    conditions: list[str] = []
    parameters: list[Any] = []
    if group_name:
        conditions.append("group_name = ?")
        parameters.append(group_name)
    if since:
        conditions.append("date >= ?")
        parameters.append(since)
    query = "SELECT * FROM pool_history"
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY date ASC, id ASC"
    with connect() as conn:
        rows = conn.execute(query, tuple(parameters)).fetchall()
    return [pool_row(row) for row in rows]


def balance_history_page(cursor: int | None, limit: int) -> dict[str, Any]:
    query = "SELECT * FROM balance_history"
    parameters: list[Any] = []
    if cursor is not None:
        query += " WHERE id < ?"
        parameters.append(cursor)
    query += " ORDER BY id DESC LIMIT ?"
    parameters.append(limit + 1)
    with connect() as conn:
        rows = conn.execute(query, tuple(parameters)).fetchall()
    has_more = len(rows) > limit
    page_rows = rows[:limit]
    items = [balance_row(row) for row in reversed(page_rows)]
    return {"items": items, "nextCursor": page_rows[-1]["id"] if has_more and page_rows else None, "hasMore": has_more}


def pool_history_page(group_name: str, cursor: int | None, limit: int) -> dict[str, Any]:
    query = "SELECT * FROM pool_history WHERE group_name = ?"
    parameters: list[Any] = [group_name]
    if cursor is not None:
        query += " AND id < ?"
        parameters.append(cursor)
    query += " ORDER BY id DESC LIMIT ?"
    parameters.append(limit + 1)
    with connect() as conn:
        rows = conn.execute(query, tuple(parameters)).fetchall()
    has_more = len(rows) > limit
    page_rows = rows[:limit]
    items = [pool_row(row) for row in reversed(page_rows)]
    return {"items": items, "nextCursor": page_rows[-1]["id"] if has_more and page_rows else None, "hasMore": has_more}


cost_additions = cost_ledger.list


def current_state() -> dict[str, Any]:
    stored = get_setting("stored_state", None)
    raw_pool = get_setting("pool_state", None)
    pool = compact_pool_state(raw_pool) if isinstance(raw_pool, dict) else None
    cutoff = recent_cutoff()
    if stored is not None:
        stored["history"] = balance_history(cutoff)
        stored["costAdditions"] = cost_additions()
    if pool is not None:
        pool["history"] = pool_history(since=cutoff)
    return {
        "initialized": initialized(),
        "storedState": stored,
        "poolState": pool,
        "balanceAccounts": [],
    }


def extract_balance(source: dict[str, Any]) -> float | None:
    for key in ["balance", "remaining", "totalBalance", "total_balance", "available_balance", "amount"]:
        if key in source:
            return flexible_number(source[key])
    infos = source.get("balance_infos")
    if isinstance(infos, list) and infos:
        first = infos[0]
        if isinstance(first, dict):
            for key in ["total_balance", "balance", "remaining", "topped_up_balance"]:
                if key in first:
                    return flexible_number(first[key])
    return None


async def poll_balances(client: httpx.AsyncClient) -> None:
    accounts = get_setting("balance_accounts", [])
    if not accounts:
        return
    amounts: list[float] = []
    names: list[str] = []
    for account in accounts:
        base_url = str(account.get("baseURL") or "").rstrip("/")
        api_key = account.get("apiKey") or ""
        name = account.get("name") or base_url
        if not base_url or not api_key:
            continue
        response = await client.get(
            f"{base_url}/v1/usage",
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json", "User-Agent": "gpt-analyzer-server/1.0"},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        source = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        balance = extract_balance(source)
        if balance is None:
            raise RuntimeError(f"balance field missing for {name}")
        amounts.append(balance)
        names.append(name)
    if amounts:
        insert_balance_snapshot({"date": utc_now(), "total": sum(amounts), "amounts": amounts, "accounts": names})


async def login_pool(client: httpx.AsyncClient) -> str | None:
    credentials = get_setting("pool_credentials", {})
    email = credentials.get("email") or ""
    password = credentials.get("password") or ""
    if not email or not password:
        return None
    response = await client.post(
        POOL_LOGIN_URL,
        json={"email": email, "password": password, "login_agreement_revision": "a90464c54fba46d4"},
        headers={"Accept": "application/json"},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    token = ((payload.get("data") or {}).get("access_token") or (payload.get("data") or {}).get("accessToken"))
    if token:
        set_setting("pool_access_token", token)
    return token


def usage_window(summary: dict[str, Any], name: str) -> dict[str, Any]:
    for item in summary.get("usage_windows") or []:
        if item.get("window") == name:
            return item
    return {}


def remaining_capacity(window: dict[str, Any]) -> int | None:
    remaining = raw_remaining_capacity(window)
    if remaining is None:
        return None
    return int(round(remaining))


def raw_remaining_capacity(window: dict[str, Any]) -> float | None:
    percent = window.get("remaining_capacity_percent")
    if percent is None:
        return None
    return flexible_number(percent) / 100


def window_account_count(window: dict[str, Any]) -> int | None:
    value = window.get("account_count")
    return None if value is None else flexible_int(value)


def pool_snapshot_from_summary(summary: dict[str, Any]) -> dict[str, Any]:
    window5h = usage_window(summary, "5h")
    window7d = usage_window(summary, "7d")
    limited = flexible_int(summary.get("rate_limited_account_count"))
    quota_protected = flexible_int(summary.get("codex_quota_protected_account_count"))
    error = flexible_int(summary.get("error_account_count"))
    disabled = flexible_int(summary.get("disabled_account_count"))
    schedulable = flexible_int(summary.get("schedulable_account_count"))
    return {
        "date": utc_now(),
        "groupName": summary.get("group_name") or "",
        "status": summary.get("group_status") or "",
        "total": flexible_int(summary.get("account_count")),
        "active": flexible_int(summary.get("active_account_count")),
        "schedulable": schedulable,
        "remaining5h": remaining_capacity(window5h),
        "remaining7d": remaining_capacity(window7d),
        "capacity5h": window_account_count(window5h),
        "capacity7d": window_account_count(window7d),
        "remainingCapacity5h": raw_remaining_capacity(window5h),
        "remainingCapacity7d": raw_remaining_capacity(window7d),
        "utilization5h": window5h.get("average_utilization"),
        "utilization7d": window7d.get("average_utilization"),
        "concurrentAvailable": max(schedulable - limited - quota_protected - error - disabled, 0),
        "concurrentTotal": schedulable,
        "limited": limited,
        "quotaProtected": quota_protected,
        "error": error,
        "disabled": disabled,
    }


async def poll_pools(client: httpx.AsyncClient) -> None:
    token = get_setting("pool_access_token", "")
    if not token:
        token = await login_pool(client)
    if not token:
        return
    response = await client.get(POOL_DASHBOARD_URL, headers={"Authorization": f"Bearer {token}"}, timeout=20)
    if response.status_code == 401:
        token = await login_pool(client)
        if not token:
            return
        response = await client.get(POOL_DASHBOARD_URL, headers={"Authorization": f"Bearer {token}"}, timeout=20)
    response.raise_for_status()
    summaries = (((response.json().get("data") or {}).get("platform") or {}).get("group_summaries") or [])
    pool_state = compact_pool_state(get_setting("pool_state", {}))
    pool_state["availableGroups"] = [item.get("group_name") for item in summaries if item.get("group_name")]
    set_setting("pool_state", pool_state)
    for summary in summaries:
        snapshot = pool_snapshot_from_summary(summary)
        insert_pool_snapshot(snapshot)


async def poll_once() -> None:
    if not initialized():
        return
    async with httpx.AsyncClient() as client:
        for task in [poll_balances, poll_pools]:
            try:
                await task(client)
            except Exception as exc:
                set_setting("last_poll_error", {"time": utc_now(), "message": str(exc)})
        set_setting("last_poll_at", utc_now())


async def poll_loop() -> None:
    while True:
        await poll_once()
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


withdrawal_service = WithdrawalService(
    connect=connect,
    dumps=dumps,
    loads=loads,
    utc_now=utc_now,
    parse_time=parse_time,
    flexible_number=flexible_number,
    get_setting=get_setting,
    normalize_smtp_settings=normalize_smtp_settings,
    latest_balance_snapshot=latest_balance_snapshot_for_withdrawal,
    get_pixel_manager=lambda: pixel_manager,
    initialize_pixel_manager=initialize_pixel_manager,
    wake_event=withdrawal_wake_event,
    cost_additions_snapshot=cost_additions_snapshot,
    clear_cost_additions_if_snapshot=clear_cost_additions_if_snapshot,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    global withdrawal_worker_task
    init_db()
    initialize_pixel_manager()
    task = asyncio.create_task(poll_loop())
    withdrawal_worker_task = asyncio.create_task(withdrawal_service.run_worker())
    try:
        yield
    finally:
        task.cancel()
        withdrawal_worker_task.cancel()
        await asyncio.gather(task, withdrawal_worker_task, return_exceptions=True)


app = FastAPI(title="91 Server", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=[
        "Content-Disposition",
        "X-Pixel-Source-Count",
        "X-Pixel-Deduplicated-Count",
        "X-Pixel-Duplicate-Count",
        "X-Pixel-Batch-Count",
    ],
)


@app.get(f"{API_PREFIX}/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "initialized": initialized(), "time": utc_now()}


@app.get(f"{API_PREFIX}/state")
async def get_state() -> dict[str, Any]:
    return current_state()


@app.get(f"{API_PREFIX}/pool-analytics")
async def get_pool_analytics(
    group_name: str = Query(alias="groupName", min_length=1),
    days: int = Query(default=7, ge=7, le=90),
) -> dict[str, Any]:
    if not initialized():
        raise HTTPException(status_code=409, detail="Not initialized")
    lookback = (datetime.now(timezone.utc) - timedelta(days=max(days, 90) + 2)).replace(microsecond=0)
    rows = pool_history(group_name=group_name, since=lookback.isoformat().replace("+00:00", "Z"))
    if not rows:
        raise HTTPException(status_code=404, detail="group history not found")
    return build_analytics(group_name, rows, days)


@app.get(f"{API_PREFIX}/pool-history")
async def get_pool_history_page(
    group_name: str = Query(alias="groupName", min_length=1),
    cursor: int | None = Query(default=None, ge=1),
    limit: int = Query(default=200, ge=1, le=500),
) -> dict[str, Any]:
    if not initialized():
        raise HTTPException(status_code=409, detail="Not initialized")
    return pool_history_page(group_name, cursor, limit)


@app.get(f"{API_PREFIX}/balance-history")
async def get_balance_history_page(
    cursor: int | None = Query(default=None, ge=1),
    limit: int = Query(default=200, ge=1, le=500),
) -> dict[str, Any]:
    if not initialized():
        raise HTTPException(status_code=409, detail="Not initialized")
    return balance_history_page(cursor, limit)


withdrawal_router, withdrawal_route_handlers = create_withdrawal_router(
    api_prefix=API_PREFIX,
    require_manager=require_pixel_manager,
    pixel_http_error=pixel_http_error,
    service=withdrawal_service,
)
app.include_router(withdrawal_router)
withdrawal_plan_for_request = withdrawal_route_handlers.plan_for_request
preview_withdrawal = withdrawal_route_handlers.preview
list_withdrawals = withdrawal_route_handlers.list_jobs
list_withdrawal_history = withdrawal_route_handlers.history
create_withdrawal = withdrawal_route_handlers.create
get_withdrawal = withdrawal_route_handlers.get
accelerate_withdrawal = withdrawal_route_handlers.accelerate


pixel_router = create_pixel_router(
    api_prefix=API_PREFIX,
    require_manager=require_pixel_manager,
    pixel_http_error=pixel_http_error,
    connect=connect,
    import_records=pixel_import_records,
    import_record=pixel_import_record,
    update_import_delete=update_pixel_import_record_delete,
    update_import_share=update_pixel_import_record_share,
    save_import_record=save_pixel_import_record,
    get_import_jobs=lambda: pixel_import_jobs,
    set_import_jobs=set_pixel_import_jobs,
    get_export_jobs=lambda: pixel_export_jobs,
    set_export_jobs=set_pixel_export_jobs,
    get_data_dir=lambda: DATA_DIR,
    get_max_upload_bytes=lambda: MAX_UPLOAD_BYTES,
)
app.include_router(pixel_router)


@app.post(f"{API_PREFIX}/bootstrap")
async def bootstrap(payload: dict[str, Any]) -> dict[str, Any]:
    if initialized():
        raise HTTPException(status_code=409, detail="Already initialized")

    stored_state = payload.get("storedState") or {}
    pool_state = payload.get("poolState") or {}
    set_setting("stored_state", compact_stored_state(stored_state))
    set_setting("pool_state", compact_pool_state(pool_state))
    set_setting("balance_accounts", payload.get("balanceAccounts") or [])
    set_setting("pool_credentials", payload.get("poolCredentials") or {})
    set_setting("smtp_settings", payload.get("smtpSettings") or {})

    for snapshot in stored_state.get("history") or []:
        insert_balance_snapshot(snapshot)
    for snapshot in pool_state.get("history") or []:
        insert_pool_snapshot(snapshot)
    for item in stored_state.get("costAdditions") or []:
        insert_cost_addition(item)

    set_meta("initialized", "true")
    set_setting("initialized_at", utc_now())
    await poll_once()
    return {"ok": True, "initialized": True}


@app.post(f"{API_PREFIX}/refresh")
async def refresh() -> dict[str, Any]:
    if not initialized():
        raise HTTPException(status_code=409, detail="Not initialized")
    await poll_once()
    return {"ok": True, "state": current_state()}


@app.get(f"{API_PREFIX}/balance-accounts")
async def get_balance_accounts() -> dict[str, Any]:
    if not initialized():
        raise HTTPException(status_code=409, detail="Not initialized")
    return {"accounts": public_balance_accounts(get_setting("balance_accounts", []))}


@app.put(f"{API_PREFIX}/balance-accounts")
async def update_balance_accounts(payload: dict[str, Any]) -> dict[str, Any]:
    if not initialized():
        raise HTTPException(status_code=409, detail="Not initialized")
    accounts = payload.get("accounts") or payload.get("balanceAccounts") or []
    if not isinstance(accounts, list) or not accounts:
        raise HTTPException(status_code=400, detail="accounts required")
    existing_accounts = get_setting("balance_accounts", [])
    existing_accounts = existing_accounts if isinstance(existing_accounts, list) else []
    normalized = []
    for item in accounts:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        base_url = str(item.get("baseURL") or item.get("base_url") or "").strip().rstrip("/")
        api_key = str(item.get("apiKey") or item.get("api_key") or "").strip()
        if not api_key:
            exact_match = next(
                (
                    existing
                    for existing in existing_accounts
                    if isinstance(existing, dict)
                    and str(existing.get("name") or "").strip() == name
                    and str(existing.get("baseURL") or existing.get("base_url") or "").strip().rstrip("/") == base_url
                ),
                None,
            )
            if exact_match is not None:
                api_key = str(exact_match.get("apiKey") or exact_match.get("api_key") or "").strip()
        if name and base_url and api_key:
            normalized.append({"name": name, "baseURL": base_url, "apiKey": api_key})
    if not normalized:
        raise HTTPException(status_code=400, detail="no valid accounts")
    set_setting("balance_accounts", normalized)
    return {"ok": True, "count": len(normalized)}


@app.get(f"{API_PREFIX}/pool-credentials")
async def get_pool_credentials() -> dict[str, Any]:
    if not initialized():
        raise HTTPException(status_code=409, detail="Not initialized")
    return {"credentials": public_pool_credentials(get_setting("pool_credentials", {}))}


@app.put(f"{API_PREFIX}/pool-credentials")
async def update_pool_credentials(payload: dict[str, Any]) -> dict[str, Any]:
    if not initialized():
        raise HTTPException(status_code=409, detail="Not initialized")
    credentials = normalize_pool_credentials(payload.get("credentials") or payload)
    existing = normalize_pool_credentials(get_setting("pool_credentials", {}))
    credentials["email"] = credentials["email"] or existing["email"]
    credentials["password"] = credentials["password"] or existing["password"]
    if not credentials["email"] or not credentials["password"]:
        raise HTTPException(status_code=400, detail="credentials required")
    set_setting("pool_credentials", credentials)
    set_setting("pool_access_token", "")
    return {"ok": True}


@app.get(f"{API_PREFIX}/smtp-settings")
async def get_smtp_settings() -> dict[str, Any]:
    if not initialized():
        raise HTTPException(status_code=409, detail="Not initialized")
    return {"settings": public_smtp_settings(get_setting("smtp_settings", {}))}


@app.put(f"{API_PREFIX}/smtp-settings")
async def update_smtp_settings(payload: dict[str, Any]) -> dict[str, Any]:
    if not initialized():
        raise HTTPException(status_code=409, detail="Not initialized")
    settings = normalize_smtp_settings(payload.get("settings") or payload)
    existing = normalize_smtp_settings(get_setting("smtp_settings", {}))
    settings["username"] = settings["username"] or existing["username"]
    settings["password"] = settings["password"] or existing["password"]
    settings["senderName"] = settings["senderName"] or existing["senderName"]
    settings["recipient"] = settings["recipient"] or existing["recipient"]
    set_setting("smtp_settings", settings)
    return {"ok": True}


@app.put(f"{API_PREFIX}/stored-state")
async def update_stored_state(payload: dict[str, Any]) -> dict[str, Any]:
    if not initialized():
        raise HTTPException(status_code=409, detail="Not initialized")
    stored_state = payload.get("storedState") or payload
    set_setting("stored_state", compact_stored_state(stored_state))
    return {"ok": True, "state": current_state()}


@app.put(f"{API_PREFIX}/pool-state")
async def update_pool_state(payload: dict[str, Any]) -> dict[str, Any]:
    if not initialized():
        raise HTTPException(status_code=409, detail="Not initialized")
    incoming = compact_pool_state(payload.get("poolState") or payload)
    incoming["pollingMinutes"] = 5
    existing = compact_pool_state(get_setting("pool_state", {}))
    existing.update(incoming)
    set_setting("pool_state", existing)
    return {"ok": True, "state": current_state()}


@app.post(f"{API_PREFIX}/cost-additions")
async def add_cost_addition(payload: dict[str, Any]) -> dict[str, Any]:
    if not initialized():
        raise HTTPException(status_code=409, detail="Not initialized")
    insert_cost_addition(payload)
    update_stored_cost(flexible_number(payload.get("amount")))
    return {"ok": True, "state": current_state()}


@app.delete(f"{API_PREFIX}/cost-additions")
async def delete_cost_additions() -> dict[str, Any]:
    if not initialized():
        raise HTTPException(status_code=409, detail="Not initialized")
    total = clear_cost_additions()
    update_stored_cost(-total)
    return {"ok": True, "state": current_state()}
