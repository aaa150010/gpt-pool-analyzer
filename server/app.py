import asyncio
import json
import os
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException


API_PREFIX = "/gpt-api"
DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))
DB_PATH = DATA_DIR / "app.db"
POLL_INTERVAL_SECONDS = max(int(os.getenv("POLL_INTERVAL_SECONDS", "300")), 60)
DATA_RETENTION_SECONDS = max(int(os.getenv("DATA_RETENTION_SECONDS", "86400")), 3600)
POOL_DASHBOARD_URL = "https://cf.ai-pixel.online/api/v1/accounts/quota-dashboard?timezone=Asia%2FShanghai"
POOL_LOGIN_URL = "https://cf.ai-pixel.online/api/v1/auth/login"


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


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


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
            """
        )
        conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES('initialized', 'false')")


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
    state.pop("history", None)
    state.pop("costAdditions", None)
    return state


def compact_pool_state(raw: dict[str, Any]) -> dict[str, Any]:
    state = dict(raw)
    state.pop("history", None)
    return state


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


def cleanup_old_data() -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=DATA_RETENTION_SECONDS)).replace(microsecond=0)
    cutoff_text = cutoff.isoformat().replace("+00:00", "Z")
    with connect() as conn:
        conn.execute("DELETE FROM balance_history WHERE date < ?", (cutoff_text,))
        conn.execute("DELETE FROM pool_history WHERE date < ?", (cutoff_text,))
        conn.execute("DELETE FROM cost_additions WHERE created_at < ?", (cutoff_text,))


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
                utilization5h, utilization7d, concurrent_available, concurrent_total, limited,
                quota_protected, error, disabled
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_date,
                group_name,
                *values,
            ),
        )


def insert_cost_addition(item: dict[str, Any]) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO cost_additions(id, date, note, amount, created_at)
            VALUES(?, ?, ?, ?, ?)
            """,
            (
                item.get("id") or f"server-{utc_now()}",
                item.get("date") or utc_now(),
                item.get("note") or "",
                flexible_number(item.get("amount")),
                item.get("createdAt") or item.get("created_at") or utc_now(),
            ),
        )


def clear_cost_additions() -> float:
    with connect() as conn:
        row = conn.execute("SELECT COALESCE(SUM(amount), 0) AS total FROM cost_additions").fetchone()
        total = flexible_number(row["total"] if row else 0)
        conn.execute("DELETE FROM cost_additions")
    return total


def balance_history() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM balance_history ORDER BY date ASC, id ASC").fetchall()
    return [
        {
            "date": row["date"],
            "total": row["total"],
            "amounts": loads(row["amounts"], []),
            "accounts": loads(row["accounts"], []),
        }
        for row in rows
    ]


def pool_history() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM pool_history ORDER BY date ASC, id ASC").fetchall()
    return [
        {
            "date": row["date"],
            "groupName": row["group_name"],
            "status": row["status"],
            "total": row["total"],
            "active": row["active"],
            "schedulable": row["schedulable"],
            "remaining5h": row["remaining5h"],
            "remaining7d": row["remaining7d"],
            "utilization5h": row["utilization5h"],
            "utilization7d": row["utilization7d"],
            "concurrentAvailable": row["concurrent_available"],
            "concurrentTotal": row["concurrent_total"],
            "limited": row["limited"],
            "quotaProtected": row["quota_protected"],
            "error": row["error"],
            "disabled": row["disabled"],
        }
        for row in rows
    ]


def cost_additions() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM cost_additions ORDER BY date ASC, created_at ASC").fetchall()
    return [
        {
            "id": row["id"],
            "date": row["date"],
            "note": row["note"],
            "amount": row["amount"],
            "createdAt": row["created_at"],
        }
        for row in rows
    ]


def current_state() -> dict[str, Any]:
    stored = get_setting("stored_state", None)
    pool = get_setting("pool_state", None)
    if stored is not None:
        stored["history"] = balance_history()
        stored["costAdditions"] = cost_additions()
    if pool is not None:
        pool["history"] = pool_history()
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
    percent = window.get("remaining_capacity_percent")
    if percent is None:
        return None
    return int(round(flexible_number(percent) / 100))


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
    pool_state = get_setting("pool_state", {})
    pool_state["availableGroups"] = [item.get("group_name") for item in summaries if item.get("group_name")]
    set_setting("pool_state", pool_state)
    for summary in summaries:
        group_name = summary.get("group_name") or ""
        insert_pool_snapshot(pool_snapshot_from_summary(summary))


async def poll_once() -> None:
    if not initialized():
        return
    cleanup_old_data()
    async with httpx.AsyncClient() as client:
        for task in [poll_balances, poll_pools]:
            try:
                await task(client)
            except Exception as exc:
                set_setting("last_poll_error", {"time": utc_now(), "message": str(exc)})
        set_setting("last_poll_at", utc_now())
    cleanup_old_data()


async def poll_loop() -> None:
    while True:
        await poll_once()
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    cleanup_old_data()
    task = asyncio.create_task(poll_loop())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title="GPT Analyzer Server", lifespan=lifespan)


@app.get(f"{API_PREFIX}/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "initialized": initialized(), "time": utc_now()}


@app.get(f"{API_PREFIX}/state")
async def get_state() -> dict[str, Any]:
    return current_state()


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
    return {"accounts": get_setting("balance_accounts", [])}


@app.put(f"{API_PREFIX}/balance-accounts")
async def update_balance_accounts(payload: dict[str, Any]) -> dict[str, Any]:
    if not initialized():
        raise HTTPException(status_code=409, detail="Not initialized")
    accounts = payload.get("accounts") or payload.get("balanceAccounts") or []
    if not isinstance(accounts, list) or not accounts:
        raise HTTPException(status_code=400, detail="accounts required")
    normalized = []
    for item in accounts:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        base_url = str(item.get("baseURL") or item.get("base_url") or "").strip().rstrip("/")
        api_key = str(item.get("apiKey") or item.get("api_key") or "").strip()
        if name and base_url and api_key:
            normalized.append({"name": name, "baseURL": base_url, "apiKey": api_key})
    if not normalized:
        raise HTTPException(status_code=400, detail="no valid accounts")
    set_setting("balance_accounts", normalized)
    return {"ok": True, "count": len(normalized)}


@app.put(f"{API_PREFIX}/stored-state")
async def update_stored_state(payload: dict[str, Any]) -> dict[str, Any]:
    if not initialized():
        raise HTTPException(status_code=409, detail="Not initialized")
    stored_state = payload.get("storedState") or payload
    set_setting("stored_state", compact_stored_state(stored_state))
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
