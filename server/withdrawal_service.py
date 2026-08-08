from __future__ import annotations

import asyncio
import json
import random
import smtplib
import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr
from typing import Any, Callable
from zoneinfo import ZoneInfo

try:
    from .account_policy import is_excluded_account
    from .withdrawal_email import render_withdrawal_email_html
    from .withdrawals import (
        NOTIFICATION_RECIPIENTS,
        plan_withdrawal,
        render_withdrawal_email,
        settlement_for,
    )
except ImportError:
    from account_policy import is_excluded_account
    from withdrawal_email import render_withdrawal_email_html
    from withdrawals import (
        NOTIFICATION_RECIPIENTS,
        plan_withdrawal,
        render_withdrawal_email,
        settlement_for,
    )


RUNNING_STALE_SECONDS = 5 * 60
RUNNING_RECHECK_SECONDS = 5.0
WITHDRAWAL_MIN_DELAY_SECONDS = 20 * 60
WITHDRAWAL_MAX_DELAY_SECONDS = 60 * 60
WITHDRAWAL_FINISH_BUFFER_SECONDS = 5 * 60
WITHDRAWAL_ITEM_RESERVE_SECONDS = 2 * 60
WITHDRAWAL_TIMEZONE = ZoneInfo("Asia/Shanghai")


def withdrawal_delay_seconds(
    now: datetime,
    remaining_items: int,
    randint: Callable[[int, int], int] = random.randint,
) -> float:
    """Keep randomized serial withdrawals inside the current Shanghai day."""
    remaining = max(int(remaining_items), 0)
    if remaining == 0:
        return 0.0
    aware_now = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    local_now = aware_now.astimezone(WITHDRAWAL_TIMEZONE)
    midnight = (local_now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    seconds_left = max(int((midnight - local_now).total_seconds()), 0)
    reserved = WITHDRAWAL_FINISH_BUFFER_SECONDS + remaining * WITHDRAWAL_ITEM_RESERVE_SECONDS
    per_gap_budget = max(seconds_left - reserved, 0) // remaining
    upper = min(WITHDRAWAL_MAX_DELAY_SECONDS, per_gap_budget)
    if upper <= 0:
        return 0.0
    lower = WITHDRAWAL_MIN_DELAY_SECONDS if upper >= WITHDRAWAL_MIN_DELAY_SECONDS else max(1, upper // 2)
    return float(randint(lower, upper))


def build_notification_message(
    *, subject: str, body: str, html_body: str | None = None,
    username: str, sender_name: str, recipient: str
) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr((sender_name, username))
    message["To"] = recipient
    message["Auto-Submitted"] = "auto-generated"
    message["Precedence"] = "bulk"
    message["X-Auto-Response-Suppress"] = "All"
    message.set_content(body)
    if html_body:
        message.add_alternative(html_body, subtype="html")
    return message


def initialize_withdrawal_schema(conn: Any) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS withdrawal_jobs (
            job_id TEXT PRIMARY KEY,
            mode TEXT NOT NULL,
            requested_amount REAL NOT NULL,
            total_amount REAL NOT NULL,
            cost REAL NOT NULL,
            balance_snapshot_at TEXT NOT NULL,
            balance_snapshot_total REAL,
            cost_history TEXT NOT NULL DEFAULT '[]',
            cost_history_total REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL,
            current_sequence INTEGER NOT NULL DEFAULT 0,
            next_run_at TEXT,
            error TEXT,
            settlement TEXT NOT NULL DEFAULT '{}',
            post_withdrawal_cost REAL,
            post_withdrawal_balance REAL,
            discounted_profit REAL,
            cost_cleared_at TEXT,
            cost_cleared_amount REAL NOT NULL DEFAULT 0,
            cost_settlement_status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS withdrawal_items (
            item_id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            sequence INTEGER NOT NULL,
            email TEXT NOT NULL,
            target_id TEXT NOT NULL,
            owner TEXT NOT NULL,
            owner_label TEXT NOT NULL,
            payment_method TEXT NOT NULL,
            balance REAL NOT NULL,
            amount INTEGER NOT NULL,
            status TEXT NOT NULL,
            status_label TEXT NOT NULL DEFAULT '',
            error TEXT,
            submitted_at TEXT,
            cost_recovered_amount REAL NOT NULL DEFAULT 0,
            cost_recovered_at TEXT,
            remaining_cost_after REAL,
            response TEXT NOT NULL DEFAULT '{}',
            UNIQUE(job_id, sequence)
        );
        CREATE TABLE IF NOT EXISTS withdrawal_emails (
            email_id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            recipient TEXT NOT NULL,
            subject TEXT NOT NULL,
            body TEXT NOT NULL,
            status TEXT NOT NULL,
            error TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_withdrawal_jobs_status_next_run
            ON withdrawal_jobs(status, next_run_at);
        CREATE INDEX IF NOT EXISTS idx_withdrawal_items_job_sequence
            ON withdrawal_items(job_id, sequence);
        """
    )
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(withdrawal_jobs)").fetchall()}
    migrations = {
        "balance_snapshot_total": "ALTER TABLE withdrawal_jobs ADD COLUMN balance_snapshot_total REAL",
        "cost_history": "ALTER TABLE withdrawal_jobs ADD COLUMN cost_history TEXT NOT NULL DEFAULT '[]'",
        "cost_history_total": "ALTER TABLE withdrawal_jobs ADD COLUMN cost_history_total REAL NOT NULL DEFAULT 0",
        "post_withdrawal_cost": "ALTER TABLE withdrawal_jobs ADD COLUMN post_withdrawal_cost REAL",
        "post_withdrawal_balance": "ALTER TABLE withdrawal_jobs ADD COLUMN post_withdrawal_balance REAL",
        "discounted_profit": "ALTER TABLE withdrawal_jobs ADD COLUMN discounted_profit REAL",
        "cost_cleared_at": "ALTER TABLE withdrawal_jobs ADD COLUMN cost_cleared_at TEXT",
        "cost_cleared_amount": "ALTER TABLE withdrawal_jobs ADD COLUMN cost_cleared_amount REAL NOT NULL DEFAULT 0",
        "cost_settlement_status": "ALTER TABLE withdrawal_jobs ADD COLUMN cost_settlement_status TEXT NOT NULL DEFAULT 'pending'",
    }
    for column, statement in migrations.items():
        if column not in columns:
            conn.execute(statement)

    item_columns = {row["name"] for row in conn.execute("PRAGMA table_info(withdrawal_items)").fetchall()}
    item_migrations = {
        "cost_recovered_amount": "ALTER TABLE withdrawal_items ADD COLUMN cost_recovered_amount REAL NOT NULL DEFAULT 0",
        "cost_recovered_at": "ALTER TABLE withdrawal_items ADD COLUMN cost_recovered_at TEXT",
        "remaining_cost_after": "ALTER TABLE withdrawal_items ADD COLUMN remaining_cost_after REAL",
    }
    for column, statement in item_migrations.items():
        if column not in item_columns:
            conn.execute(statement)

    # Older jobs predate the auditable cost snapshot. Backfill only active jobs
    # so a currently running queue gets the same cost ledger it was created from;
    # completed historical jobs are left unchanged rather than rewritten.
    try:
        cost_rows = conn.execute(
            "SELECT id, date, note, amount, created_at FROM cost_additions ORDER BY date ASC, created_at ASC"
        ).fetchall()
    except Exception:
        # Standalone service tests may initialize only the withdrawal tables.
        cost_rows = []
    cost_snapshot = [
        {
            "id": row["id"],
            "date": row["date"],
            "note": row["note"],
            "amount": row["amount"],
            "createdAt": row["created_at"],
        }
        for row in cost_rows
    ]
    if cost_snapshot:
        cost_total = round(sum(float(item["amount"] or 0) for item in cost_snapshot), 2)
        conn.execute(
            """UPDATE withdrawal_jobs
               SET cost_history = ?, cost_history_total = ?
               WHERE status IN ('queued', 'waiting', 'running')
                 AND (cost_history IS NULL OR cost_history = '[]')""",
            (json.dumps(cost_snapshot, ensure_ascii=False), cost_total),
        )

    # Recover the balance total for active jobs created before the field was
    # introduced. The closest snapshot before job creation is deterministic.
    try:
        active_jobs = conn.execute(
            "SELECT job_id, created_at FROM withdrawal_jobs "
            "WHERE status IN ('queued', 'waiting', 'running') AND balance_snapshot_total IS NULL"
        ).fetchall()
        for job in active_jobs:
            balance = conn.execute(
                "SELECT total FROM balance_history WHERE date <= ? ORDER BY date DESC, id DESC LIMIT 1",
                (job["created_at"],),
            ).fetchone()
            if balance:
                conn.execute(
                    "UPDATE withdrawal_jobs SET balance_snapshot_total = ? WHERE job_id = ?",
                    (balance["total"], job["job_id"]),
                )
    except Exception:
        # The main application creates balance_history before this schema.
        pass


class WithdrawalService:
    """Persistent, single-flight withdrawal queue and its business rules."""

    def __init__(
        self,
        *,
        connect: Callable[..., Any],
        dumps: Callable[[Any], str],
        loads: Callable[[str | None, Any], Any],
        utc_now: Callable[[], str],
        parse_time: Callable[[str | None], datetime | None],
        flexible_number: Callable[[Any, float], float],
        get_setting: Callable[[str, Any], Any],
        normalize_smtp_settings: Callable[[dict[str, Any]], dict[str, Any]],
        latest_balance_snapshot: Callable[[], dict[str, Any] | None],
        get_pixel_manager: Callable[[], Any],
        initialize_pixel_manager: Callable[[], None],
        wake_event: asyncio.Event,
        cost_additions_snapshot: Callable[[], list[dict[str, Any]]] | None = None,
        recover_cost_additions_if_snapshot: Callable[[Any, list[dict[str, Any]], float], dict[str, Any]] | None = None,
    ) -> None:
        self._connect = connect
        self._dumps = dumps
        self._loads = loads
        self._utc_now = utc_now
        self._parse_time = parse_time
        self._flexible_number = flexible_number
        self._get_setting = get_setting
        self._normalize_smtp_settings = normalize_smtp_settings
        self._latest_balance_snapshot = latest_balance_snapshot
        self._get_pixel_manager = get_pixel_manager
        self._initialize_pixel_manager = initialize_pixel_manager
        self.wake_event = wake_event
        self._cost_additions_snapshot = cost_additions_snapshot or (lambda: [])
        self._recover_cost_additions_if_snapshot = recover_cost_additions_if_snapshot

    def latest_plan(self, mode: str, requested_amount: Any | None = None) -> dict[str, Any]:
        snapshot = self._latest_balance_snapshot()
        if not snapshot:
            raise ValueError("暂无余额快照，无法创建提现任务")
        stored = self._get_setting("stored_state", {})
        cost_history = self._cost_additions_snapshot()
        balances = [
            {"email": email, "balance": amount}
            for email, amount in zip(snapshot.get("accounts") or [], snapshot.get("amounts") or [])
            if not is_excluded_account(email)
        ]
        plan = plan_withdrawal(
            mode,
            self._flexible_number(stored.get("cost"), 0),
            balances,
            requested_amount,
        )
        plan["balanceSnapshotTotal"] = round(self._flexible_number(snapshot.get("total"), 0), 2)
        plan["costHistory"] = cost_history
        plan["costHistoryTotal"] = round(
            sum(self._flexible_number(item.get("amount"), 0) for item in cost_history), 2
        )
        return plan

    @staticmethod
    def settlement(plan: dict[str, Any]) -> dict[str, Any]:
        return settlement_for(plan)

    @staticmethod
    def target_ids(manager: Any) -> dict[str, str]:
        return {
            str(target.email).strip().lower(): target.id
            for target in manager.config.targets.values()
            if str(target.email).strip() and not is_excluded_account(target.email)
        }

    @staticmethod
    def item_label(status: str) -> str:
        return {
            "queued": "待执行",
            "waiting": "等待中",
            "running": "提交中",
            "submitted": "已提交",
            "skipped": "已跳过",
            "failed": "失败",
        }.get(status, status)

    def job_detail(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            job = conn.execute("SELECT * FROM withdrawal_jobs WHERE job_id = ?", (job_id,)).fetchone()
            if not job:
                return None
            items = conn.execute(
                "SELECT * FROM withdrawal_items WHERE job_id = ? ORDER BY sequence ASC", (job_id,)
            ).fetchall()
        result = {
            "jobId": job["job_id"],
            "mode": job["mode"],
            "requestedAmount": job["requested_amount"],
            "totalAmount": job["total_amount"],
            "cost": job["cost"],
            "balanceSnapshotAt": job["balance_snapshot_at"],
            "balanceSnapshotTotal": job["balance_snapshot_total"],
            "costHistory": self._loads(job["cost_history"], []),
            "costHistoryTotal": job["cost_history_total"],
            "status": job["status"],
            "currentSequence": job["current_sequence"],
            "nextRunAt": job["next_run_at"],
            "error": job["error"],
            "settlement": self._loads(job["settlement"], {}),
            "postWithdrawalCost": job["post_withdrawal_cost"],
            "postWithdrawalBalance": job["post_withdrawal_balance"],
            "discountedProfit": job["discounted_profit"],
            "costClearedAt": job["cost_cleared_at"],
            "costClearedAmount": job["cost_cleared_amount"],
            "costSettlementStatus": job["cost_settlement_status"],
            "createdAt": job["created_at"],
            "updatedAt": job["updated_at"],
            "items": [],
        }
        result["items"] = [
            {
                "itemId": item["item_id"],
                "sequence": item["sequence"],
                "email": item["email"],
                "targetId": item["target_id"],
                "owner": item["owner"],
                "ownerLabel": item["owner_label"],
                "paymentMethod": item["payment_method"],
                "balance": item["balance"],
                "amount": item["amount"],
                "status": item["status"],
                "statusLabel": self.item_label(item["status"]),
                "error": item["error"],
                "submittedAt": item["submitted_at"],
                "costRecoveredAmount": item["cost_recovered_amount"],
                "costRecoveredAt": item["cost_recovered_at"],
                "remainingCostAfter": item["remaining_cost_after"],
            }
            for item in items
        ]
        return result

    def active_job(self) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT job_id FROM withdrawal_jobs "
                "WHERE status IN ('queued', 'waiting', 'running') "
                "ORDER BY created_at ASC LIMIT 1"
            ).fetchone()
        return self.job_detail(row["job_id"]) if row else None

    def current_job(self) -> dict[str, Any] | None:
        active = self.active_job()
        if active:
            return active
        with self._connect() as conn:
            row = conn.execute(
                "SELECT job_id FROM withdrawal_jobs ORDER BY created_at DESC, rowid DESC LIMIT 1"
            ).fetchone()
        return self.job_detail(row["job_id"]) if row else None

    def job_history(self, limit: int = 20, offset: int = 0) -> dict[str, Any]:
        page_size = min(max(int(limit), 1), 100)
        page_offset = max(int(offset), 0)
        with self._connect() as conn:
            total = int(conn.execute("SELECT COUNT(*) FROM withdrawal_jobs").fetchone()[0])
            rows = conn.execute(
                "SELECT job_id FROM withdrawal_jobs "
                "ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?",
                (page_size, page_offset),
            ).fetchall()
        jobs = [detail for row in rows if (detail := self.job_detail(row["job_id"])) is not None]
        return {"jobs": jobs, "total": total, "limit": page_size, "offset": page_offset}

    def create_job(self, plan: dict[str, Any], target_ids: dict[str, str]) -> dict[str, Any]:
        job_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        now = self._utc_now()
        settlement = settlement_for(plan)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            active = conn.execute(
                "SELECT 1 FROM withdrawal_jobs WHERE status IN ('queued', 'waiting', 'running') LIMIT 1"
            ).fetchone()
            if active:
                raise RuntimeError("已有提现任务正在执行")
            conn.execute(
                """INSERT INTO withdrawal_jobs(
                    job_id, mode, requested_amount, total_amount, cost, balance_snapshot_at,
                    balance_snapshot_total, cost_history, cost_history_total,
                    status, current_sequence, next_run_at, error, settlement,
                    post_withdrawal_cost, post_withdrawal_balance, discounted_profit,
                    cost_cleared_at, cost_cleared_amount, cost_settlement_status,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', 0, NULL, NULL, ?, NULL, NULL, NULL, NULL, 0, 'pending', ?, ?)""",
                (
                    job_id,
                    plan["mode"],
                    plan["requestedAmount"],
                    plan["totalAmount"],
                    plan["cost"],
                    plan["balanceSnapshotAt"],
                    plan.get("balanceSnapshotTotal"),
                    self._dumps(plan.get("costHistory") or []),
                    plan.get("costHistoryTotal", 0),
                    self._dumps(settlement),
                    now,
                    now,
                ),
            )
            for sequence, item in enumerate(plan["items"], start=1):
                excluded = is_excluded_account(item.get("email"))
                status = "skipped" if excluded else item["status"]
                conn.execute(
                    """INSERT INTO withdrawal_items(
                        job_id, sequence, email, target_id, owner, owner_label, payment_method,
                        balance, amount, status, status_label, error
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        job_id,
                        sequence,
                        item["email"],
                        "" if excluded else target_ids.get(item["email"].lower(), ""),
                        item["owner"],
                        item["ownerLabel"],
                        item["paymentMethod"],
                        item["balance"],
                        item["amount"],
                        status,
                        self.item_label(status),
                        "账号已从 91 永久排除" if excluded else item.get("error"),
                    ),
                )
        return self.job_detail(job_id) or {}

    def update_job(self, job_id: str, **fields: Any) -> None:
        allowed = {
            "status", "current_sequence", "next_run_at", "error", "settlement",
            "post_withdrawal_cost", "post_withdrawal_balance", "discounted_profit",
            "cost_cleared_at", "cost_cleared_amount", "cost_settlement_status",
        }
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return
        updates["updated_at"] = self._utc_now()
        assignments = ", ".join(f"{key} = ?" for key in updates)
        values = [self._dumps(value) if key == "settlement" else value for key, value in updates.items()]
        values.append(job_id)
        with self._connect() as conn:
            conn.execute(f"UPDATE withdrawal_jobs SET {assignments} WHERE job_id = ?", values)

    def update_item(self, item_id: int, **fields: Any) -> None:
        allowed = {"status", "status_label", "error", "submitted_at", "response"}
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return
        assignments = ", ".join(f"{key} = ?" for key in updates)
        values = [self._dumps(value) if key == "response" else value for key, value in updates.items()]
        values.append(item_id)
        with self._connect() as conn:
            conn.execute(f"UPDATE withdrawal_items SET {assignments} WHERE item_id = ?", values)

    @staticmethod
    def _submitted_total(items: list[dict[str, Any]]) -> float:
        return round(sum(float(item.get("amount") or 0) for item in items if item.get("status") == "submitted"), 2)

    def _live_cost(self, conn: Any, fallback: float = 0.0) -> float:
        row = conn.execute("SELECT value FROM settings WHERE key = 'stored_state'").fetchone()
        state = self._loads(row["value"], {}) if row else {}
        value = state.get("cost") if state.get("cost") is not None else fallback
        return round(max(float(value or 0), 0.0), 2)

    def _apply_item_cost_recovery(self, conn: Any, job: Any, item: Any, now: str) -> dict[str, Any]:
        """Recover cost for one submitted item exactly once inside its transaction."""
        if item["cost_recovered_at"]:
            return {
                "recovered": round(float(item["cost_recovered_amount"] or 0), 2),
                "cumulative": round(float(job["cost_cleared_amount"] or 0), 2),
                "remainingCost": self._live_cost(conn, float(job["cost"] or 0)),
                "status": str(job["cost_settlement_status"] or "pending"),
            }

        job_cost = round(max(float(job["cost"] or 0), 0.0), 2)
        cumulative = round(max(float(job["cost_cleared_amount"] or 0), 0.0), 2)
        outstanding = round(max(job_cost - cumulative, 0.0), 2)
        requested = round(min(max(float(item["amount"] or 0), 0.0), outstanding), 2)
        remaining_cost = self._live_cost(conn, job_cost)
        recovered = 0.0
        if requested > 0 and self._recover_cost_additions_if_snapshot:
            result = self._recover_cost_additions_if_snapshot(
                conn,
                self._loads(job["cost_history"], []),
                requested,
            )
            recovered = round(max(float(result.get("recoveredAmount") or 0), 0.0), 2)
            remaining_cost = round(max(float(result.get("remainingCost") or 0), 0.0), 2)

        cumulative = round(min(cumulative + recovered, job_cost), 2)
        if job_cost <= 0:
            cost_status = "not_applicable"
        elif cumulative >= job_cost:
            cost_status = "cleared"
        elif cumulative > 0:
            cost_status = "partial"
        else:
            cost_status = "pending"
        cleared_at = job["cost_cleared_at"] or (now if cost_status == "cleared" else None)

        conn.execute(
            """UPDATE withdrawal_items SET
               cost_recovered_amount = ?, cost_recovered_at = ?, remaining_cost_after = ?
               WHERE item_id = ?""",
            (recovered, now, remaining_cost, item["item_id"]),
        )
        conn.execute(
            """UPDATE withdrawal_jobs SET
               cost_cleared_amount = ?, cost_cleared_at = ?, cost_settlement_status = ?
               WHERE job_id = ?""",
            (cumulative, cleared_at, cost_status, job["job_id"]),
        )
        return {
            "recovered": recovered,
            "cumulative": cumulative,
            "remainingCost": remaining_cost,
            "status": cost_status,
        }

    def _sync_submitted_cost_recovery(self, job_id: str) -> None:
        """Backfill a submitted item after a restart without double recovery."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            job_row = conn.execute(
                "SELECT * FROM withdrawal_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if not job_row:
                return
            job = dict(job_row)
            items = conn.execute(
                """SELECT * FROM withdrawal_items
                   WHERE job_id = ? AND status = 'submitted' AND cost_recovered_at IS NULL
                   ORDER BY sequence ASC""",
                (job_id,),
            ).fetchall()
            if not items:
                return
            now = self._utc_now()
            for item in items:
                result = self._apply_item_cost_recovery(conn, job, item, now)
                job["cost_cleared_amount"] = result["cumulative"]
                job["cost_settlement_status"] = result["status"]
                if result["status"] == "cleared" and not job.get("cost_cleared_at"):
                    job["cost_cleared_at"] = now
            conn.execute(
                "UPDATE withdrawal_jobs SET updated_at = ? WHERE job_id = ?",
                (now, job_id),
            )

    def _refresh_financials(self, job_id: str, *, final: bool = False) -> None:
        self._sync_submitted_cost_recovery(job_id)
        job = self.job_detail(job_id)
        if not job:
            return
        settlement = settlement_for(job)
        submitted_total = self._submitted_total(job.get("items") or [])
        with self._connect() as conn:
            remaining_cost = self._live_cost(conn, float(job.get("cost") or 0))
        post_balance = job.get("balanceSnapshotTotal")
        if post_balance is not None:
            post_balance = round(max(float(post_balance) - submitted_total, 0), 2)
        discounted_profit = round((post_balance or 0) - remaining_cost, 2) if post_balance is not None else None

        job_cost = round(max(float(job.get("cost") or 0), 0.0), 2)
        recovered = round(max(float(job.get("costClearedAmount") or 0), 0.0), 2)
        if job_cost <= 0:
            cost_status = "not_applicable"
        elif recovered >= job_cost:
            cost_status = "cleared"
        elif recovered > 0:
            cost_status = "partial"
        elif final:
            cost_status = "not_recovered"
        else:
            cost_status = "pending"
        self.update_job(
            job_id,
            settlement=settlement,
            post_withdrawal_cost=remaining_cost,
            post_withdrawal_balance=post_balance,
            discounted_profit=discounted_profit,
            cost_settlement_status=cost_status,
        )

    def _finalize_financials(self, job_id: str) -> None:
        """Persist final totals after all per-item recoveries have been applied."""
        job = self.job_detail(job_id)
        if not job or job.get("status") not in {"completed", "failed"}:
            return
        self._refresh_financials(job_id, final=True)

    def _refresh_settlement(self, job_id: str) -> None:
        job = self.job_detail(job_id)
        if job:
            self.update_job(job_id, settlement=settlement_for(job))

    def _fail_running_item(self, job_id: str, item_id: int, message: str) -> bool:
        now = self._utc_now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            job = conn.execute(
                "SELECT * FROM withdrawal_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            item = conn.execute(
                "SELECT * FROM withdrawal_items WHERE item_id = ? AND job_id = ?",
                (item_id, job_id),
            ).fetchone()
            if not job or not item or job["status"] != "running" or item["status"] != "running":
                return False
            conn.execute(
                "UPDATE withdrawal_items SET status = 'failed', status_label = ?, error = ? WHERE item_id = ?",
                (self.item_label("failed"), message, item_id),
            )
            conn.execute(
                "UPDATE withdrawal_jobs SET status = 'failed', next_run_at = NULL, error = ?, updated_at = ? "
                "WHERE job_id = ?",
                (message, now, job_id),
            )
        self._refresh_settlement(job_id)
        self._finalize_financials(job_id)
        return True

    def _record_submitted_item(
        self,
        job_id: str,
        item_id: int,
        response: dict[str, Any],
    ) -> tuple[str, float]:
        now = self._utc_now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            job = conn.execute(
                "SELECT * FROM withdrawal_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            item = conn.execute(
                "SELECT * FROM withdrawal_items WHERE item_id = ? AND job_id = ?",
                (item_id, job_id),
            ).fetchone()
            if not job or not item or job["status"] != "running" or item["status"] != "running":
                return "lost", RUNNING_RECHECK_SECONDS
            conn.execute(
                "UPDATE withdrawal_items SET status = 'submitted', status_label = ?, submitted_at = ?, response = ? "
                "WHERE item_id = ?",
                (self.item_label("submitted"), now, self._dumps(response), item_id),
            )
            self._apply_item_cost_recovery(conn, job, item, now)
            remaining_count = int(conn.execute(
                "SELECT COUNT(*) AS count FROM withdrawal_items WHERE job_id = ? AND status = 'queued'",
                (job_id,),
            ).fetchone()["count"])
            if remaining_count:
                schedule_now = datetime.now(timezone.utc)
                delay = withdrawal_delay_seconds(schedule_now, remaining_count)
                next_run_at = (schedule_now + timedelta(seconds=delay)).isoformat()
                conn.execute(
                    "UPDATE withdrawal_jobs SET status = 'waiting', next_run_at = ?, error = NULL, updated_at = ? "
                    "WHERE job_id = ?",
                    (next_run_at, now, job_id),
                )
                outcome = "waiting"
            else:
                conn.execute(
                    "UPDATE withdrawal_jobs SET status = 'completed', next_run_at = NULL, error = NULL, updated_at = ? "
                    "WHERE job_id = ?",
                    (now, job_id),
                )
                outcome, delay = "completed", 1.0
        if outcome == "completed":
            self._finalize_financials(job_id)
        else:
            self._refresh_financials(job_id)
        return outcome, delay

    def _complete_job_without_queued_items(self, job_id: str) -> bool:
        now = self._utc_now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            job = conn.execute(
                "SELECT status FROM withdrawal_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            pending = conn.execute(
                "SELECT 1 FROM withdrawal_items WHERE job_id = ? AND status IN ('queued', 'running') LIMIT 1",
                (job_id,),
            ).fetchone()
            if not job or job["status"] not in {"queued", "waiting", "running"} or pending:
                return False
            conn.execute(
                "UPDATE withdrawal_jobs SET status = 'completed', next_run_at = NULL, error = NULL, updated_at = ? "
                "WHERE job_id = ?",
                (now, job_id),
            )
        self._refresh_settlement(job_id)
        self._finalize_financials(job_id)
        return True

    def _recover_submitted_transition(self, job_id: str) -> float:
        now = self._utc_now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            running = conn.execute(
                "SELECT 1 FROM withdrawal_items WHERE job_id = ? AND status = 'running' LIMIT 1",
                (job_id,),
            ).fetchone()
            queued_count = int(conn.execute(
                "SELECT COUNT(*) AS count FROM withdrawal_items WHERE job_id = ? AND status = 'queued'",
                (job_id,),
            ).fetchone()["count"])
            updated = 0
            delay = RUNNING_RECHECK_SECONDS
            if not running and queued_count:
                schedule_now = datetime.now(timezone.utc)
                delay = withdrawal_delay_seconds(schedule_now, queued_count)
                next_run_at = (schedule_now + timedelta(seconds=delay)).isoformat()
                updated = conn.execute(
                    "UPDATE withdrawal_jobs SET status = 'waiting', next_run_at = ?, updated_at = ? "
                    "WHERE job_id = ? AND status = 'running'",
                    (next_run_at, now, job_id),
                ).rowcount
        return delay if updated else RUNNING_RECHECK_SECONDS

    def accelerate_job(self, job_id: str) -> dict[str, Any] | None:
        now = self._utc_now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            job = conn.execute(
                "SELECT status FROM withdrawal_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if not job:
                return None
            if job["status"] != "waiting":
                raise RuntimeError("当前任务没有可加速的等待步骤")
            updated = conn.execute(
                "UPDATE withdrawal_jobs SET next_run_at = ?, updated_at = ? "
                "WHERE job_id = ? AND status = 'waiting'",
                (now, now, job_id),
            ).rowcount
            if not updated:
                raise RuntimeError("当前任务没有可加速的等待步骤")
        self.wake_event.set()
        return self.job_detail(job_id)

    def _claim_item(self, job_id: str, item: dict[str, Any]) -> bool:
        """Claim one queued item in SQLite before making the external request."""
        now = self._utc_now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            running = conn.execute(
                "SELECT 1 FROM withdrawal_items WHERE job_id = ? AND status = 'running' LIMIT 1",
                (job_id,),
            ).fetchone()
            if running:
                return False
            updated = conn.execute(
                "UPDATE withdrawal_items SET status = 'running', status_label = ? "
                "WHERE item_id = ? AND status = 'queued'",
                (self.item_label("running"), item["itemId"]),
            ).rowcount
            if not updated:
                return False
            job_updated = conn.execute(
                "UPDATE withdrawal_jobs SET status = 'running', current_sequence = ?, next_run_at = NULL, updated_at = ? "
                "WHERE job_id = ? AND status IN ('queued', 'waiting')",
                (item["sequence"], now, job_id),
            ).rowcount
            if not job_updated:
                conn.execute(
                    "UPDATE withdrawal_items SET status = 'queued', status_label = ? WHERE item_id = ?",
                    (self.item_label("queued"), item["itemId"]),
                )
                return False
            return True

    def _skip_excluded_item(self, job_id: str, item: dict[str, Any]) -> bool:
        now = self._utc_now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status, email FROM withdrawal_items WHERE item_id = ? AND job_id = ?",
                (item["itemId"], job_id),
            ).fetchone()
            if not row or row["status"] not in {"queued", "running"} or not is_excluded_account(row["email"]):
                return False
            conn.execute(
                "UPDATE withdrawal_items SET status = 'skipped', status_label = '已排除', error = ? WHERE item_id = ?",
                ("账号已从 91 永久排除", item["itemId"]),
            )
            if row["status"] == "running":
                conn.execute(
                    "UPDATE withdrawal_jobs SET status = 'queued', next_run_at = NULL, updated_at = ? "
                    "WHERE job_id = ? AND status = 'running'",
                    (now, job_id),
                )
        return True

    def send_notification(self, job_id: str, status: str) -> None:
        job = self.job_detail(job_id)
        if not job:
            return
        subject, body = render_withdrawal_email(job, status)
        html_body = render_withdrawal_email_html(job, status)
        settings = self._normalize_smtp_settings(self._get_setting("smtp_settings", {}))
        now = self._utc_now()
        if not settings["host"] or not settings["username"] or not settings["password"]:
            self._record_email_failures(job_id, subject, body, "邮件通知 SMTP 未配置完整", now)
            return
        try:
            with smtplib.SMTP_SSL(settings["host"], int(settings["port"]), timeout=20) as smtp:
                smtp.login(settings["username"], settings["password"])
                for recipient in NOTIFICATION_RECIPIENTS:
                    message = build_notification_message(
                        subject=subject,
                        body=body,
                        html_body=html_body,
                        username=settings["username"],
                        sender_name=settings.get("senderName", ""),
                        recipient=recipient,
                    )
                    try:
                        smtp.send_message(message)
                        email_status, email_error = "sent", None
                    except Exception as exc:  # Preserve one recipient failure in the audit trail.
                        email_status, email_error = "failed", str(exc)
                    self._record_email(job_id, recipient, subject, body, email_status, email_error, now)
        except Exception as exc:
            self._record_email_failures(job_id, subject, body, str(exc), now)

    def _record_email(
        self, job_id: str, recipient: str, subject: str, body: str,
        status: str, error: str | None, created_at: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO withdrawal_emails(job_id, recipient, subject, body, status, error, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (job_id, recipient, subject, body, status, error, created_at),
            )

    def _record_email_failures(
        self, job_id: str, subject: str, body: str, error: str, created_at: str,
    ) -> None:
        for recipient in NOTIFICATION_RECIPIENTS:
            self._record_email(job_id, recipient, subject, body, "failed", error, created_at)

    async def process_once(self) -> float:
        job = self.active_job()
        if not job:
            return 1.0
        next_item = next((item for item in job["items"] if item["status"] == "queued"), None)
        running_item = next((item for item in job["items"] if item["status"] == "running"), None)
        if running_item and is_excluded_account(running_item.get("email")):
            self._skip_excluded_item(job["jobId"], running_item)
            return 0.5
        if running_item:
            updated_at = self._parse_time(job.get("updatedAt"))
            if updated_at and (datetime.now(timezone.utc) - updated_at).total_seconds() < RUNNING_STALE_SECONDS:
                return RUNNING_RECHECK_SECONDS
            message = "任务在上次进程中断时仍处于提交状态，已暂停以避免重复提现"
            if self._fail_running_item(job["jobId"], running_item["itemId"], message):
                await asyncio.to_thread(self.send_notification, job["jobId"], "执行失败")
            return 60.0
        if job["status"] == "running" and next_item is not None:
            self._refresh_financials(job["jobId"])
            return self._recover_submitted_transition(job["jobId"])
        if next_item is None:
            if self._complete_job_without_queued_items(job["jobId"]):
                await asyncio.to_thread(self.send_notification, job["jobId"], "已完成")
            return 1.0
        if is_excluded_account(next_item.get("email")):
            self._skip_excluded_item(job["jobId"], next_item)
            return 0.5
        if job["status"] == "waiting" and job.get("nextRunAt"):
            next_time = self._parse_time(job["nextRunAt"])
            if next_time and next_time > datetime.now(timezone.utc):
                return max((next_time - datetime.now(timezone.utc)).total_seconds(), 0.5)
        if not self._claim_item(job["jobId"], next_item):
            return 1.0
        try:
            manager = self._get_pixel_manager()
            if manager is None:
                self._initialize_pixel_manager()
                manager = self._get_pixel_manager()
            if manager is None:
                raise RuntimeError("账号池管理配置不可用")
            response = await manager.submit_withdrawal(
                next_item["targetId"], next_item["amount"], next_item["paymentMethod"]
            )
        except Exception as exc:
            message = getattr(exc, "public_message", None) or str(exc)
            if self._fail_running_item(job["jobId"], next_item["itemId"], message):
                await asyncio.to_thread(self.send_notification, job["jobId"], "执行失败")
            return 60.0
        outcome, delay = self._record_submitted_item(job["jobId"], next_item["itemId"], response)
        if outcome == "completed":
            await asyncio.to_thread(self.send_notification, job["jobId"], "已完成")
        return delay

    async def run_worker(self) -> None:
        while True:
            self.wake_event.clear()
            delay = await self.process_once()
            try:
                await asyncio.wait_for(self.wake_event.wait(), timeout=max(delay, 0.5))
            except asyncio.TimeoutError:
                pass
