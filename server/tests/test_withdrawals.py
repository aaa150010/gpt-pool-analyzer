import asyncio
import json
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.cost_ledger import CostLedger
from server.withdrawal_routes import create_withdrawal_router
from server.withdrawal_email import render_withdrawal_email_html
from server.withdrawal_service import (
    UNKNOWN_OUTCOME_RETRY_GRACE_SECONDS,
    WITHDRAWAL_FINISH_BUFFER_SECONDS,
    WITHDRAWAL_ITEM_RESERVE_SECONDS,
    WithdrawalService,
    build_notification_message,
    initialize_withdrawal_schema,
    withdrawal_delay_seconds,
)
from server.withdrawals import (
    NOTIFICATION_RECIPIENTS,
    WITHDRAWAL_ACCOUNTS,
    plan_withdrawal,
    render_withdrawal_email,
    settlement_for,
)


def balances(values: list[float]) -> list[dict[str, float | str]]:
    return [
        {"email": account.email, "balance": value}
        for account, value in zip(WITHDRAWAL_ACCOUNTS, values)
    ]


class WithdrawalPlanningTests(unittest.TestCase):
    def test_random_wait_compresses_to_finish_before_shanghai_midnight(self) -> None:
        now = datetime(2026, 8, 7, 15, 30, tzinfo=timezone.utc)
        remaining = 6
        delay = withdrawal_delay_seconds(now, remaining, randint=lambda _low, high: high)
        midnight = datetime(2026, 8, 7, 16, 0, tzinfo=timezone.utc)

        self.assertLess(delay, 20 * 60)
        projected_finish = now + timedelta(
            seconds=(delay + WITHDRAWAL_ITEM_RESERVE_SECONDS) * remaining
            + WITHDRAWAL_FINISH_BUFFER_SECONDS
        )
        self.assertLessEqual(projected_finish, midnight)

    def test_random_wait_becomes_immediate_when_only_submission_time_remains(self) -> None:
        now = datetime(2026, 8, 7, 15, 50, tzinfo=timezone.utc)

        self.assertEqual(withdrawal_delay_seconds(now, 6), 0)

    def test_random_wait_keeps_normal_range_when_day_has_enough_time(self) -> None:
        now = datetime(2026, 8, 7, 4, 0, tzinfo=timezone.utc)
        delay = withdrawal_delay_seconds(now, 6, randint=lambda low, _high: low)

        self.assertEqual(delay, 20 * 60)

    def test_notification_headers_suppress_automatic_replies(self) -> None:
        message = build_notification_message(
            subject="[91] test",
            body="test body",
            html_body="<html><body><strong>test body</strong></body></html>",
            username="sender@example.com",
            sender_name="Sender",
            recipient="recipient@example.com",
        )
        self.assertEqual(message["From"], "Sender <sender@example.com>")
        self.assertEqual(message["Auto-Submitted"], "auto-generated")
        self.assertEqual(message["Precedence"], "bulk")
        self.assertEqual(message["X-Auto-Response-Suppress"], "All")
        self.assertEqual(message.get_body(preferencelist=("plain",)).get_content_type(), "text/plain")
        self.assertEqual(message.get_body(preferencelist=("html",)).get_content_type(), "text/html")

    def test_cost_allocation_prefers_five_multiples_then_fills_remainder(self) -> None:
        plan = plan_withdrawal("cost", 324.5, balances([20, 80, 65, 60, 100, 0]), 320)
        self.assertEqual([item["amount"] for item in plan["items"]], [20, 80, 65, 60, 95, 0])

        rounded = plan_withdrawal("cost", 324.5, balances([20, 80, 65, 60, 100, 0]))
        self.assertEqual([item["amount"] for item in rounded["items"]], [20, 80, 65, 60, 100, 0])

    def test_full_allocation_truncates_each_balance_to_integer(self) -> None:
        plan = plan_withdrawal("full", 324.5, balances([81.5, 65.9, 62.8, 105.7, 1.9, 86.2]))
        self.assertEqual([item["amount"] for item in plan["items"]], [81, 65, 62, 105, 1, 86])
        self.assertEqual(plan["items"][-1]["paymentMethod"], "alipay")

    def test_full_allocation_supports_partial_per_account_overrides_and_zero_skip(self) -> None:
        first, second = WITHDRAWAL_ACCOUNTS[:2]
        plan = plan_withdrawal(
            "full",
            0,
            balances([10.9, 20.9, 30.9, 40.9, 50.9, 60.9]),
            account_amounts={first.email.upper(): 7, second.email: 0},
        )

        self.assertEqual([item["amount"] for item in plan["items"]], [7, 0, 30, 40, 50, 60])
        self.assertEqual(plan["items"][1]["status"], "skipped")
        self.assertEqual(plan["items"][1]["error"], "已手动设为 0 元")

    def test_per_account_overrides_require_known_non_negative_integers_within_capacity(self) -> None:
        email = WITHDRAWAL_ACCOUNTS[0].email
        values = balances([10, 0, 0, 0, 0, 0])
        invalid_cases = (
            ({email: -1}, "必须为非负整数"),
            ({email: 1.5}, "必须为非负整数"),
            ({email: float("inf")}, "必须为非负整数"),
            ({"missing@example.com": 1}, "提现账号不存在"),
            ({email: 11}, "超过可提现整数余额"),
        )
        for overrides, message in invalid_cases:
            with self.subTest(overrides=overrides):
                with self.assertRaisesRegex(ValueError, message):
                    plan_withdrawal("full", 0, values, account_amounts=overrides)
        with self.assertRaisesRegex(ValueError, "只有全部提现"):
            plan_withdrawal("cost", 1, values, 1, {email: 1})

    def test_first_withdrawal_fee_reduces_integer_capacity(self) -> None:
        email = WITHDRAWAL_ACCOUNTS[0].email
        one_yuan = plan_withdrawal(
            "full", 0, [{"email": email, "balance": 1.00, "feeAmount": 0.10}]
        )
        one_yuan_ten = plan_withdrawal(
            "full", 0, [{"email": email, "balance": 1.10, "feeAmount": 0.10}]
        )

        self.assertEqual(one_yuan["items"][0]["amount"], 0)
        self.assertEqual(one_yuan_ten["items"][0]["amount"], 1)
        self.assertEqual(one_yuan_ten["items"][0]["feeAmount"], 0.10)
        self.assertEqual(one_yuan_ten["items"][0]["totalDeducted"], 1.10)

    def test_preview_items_have_sequence_chinese_status_and_updated_labels(self) -> None:
        plan = plan_withdrawal("full", 324.5, balances([81, 65, 62, 105, 0.4, 86]))
        self.assertEqual([item["sequence"] for item in plan["items"]], list(range(1, 7)))
        self.assertEqual([item["statusLabel"] for item in plan["items"]], ["待执行"] * 4 + ["已跳过", "待执行"])
        self.assertEqual(
            [item["ownerLabel"] for item in plan["items"]],
            ["自己支付宝", "老弟微信", "老弟支付宝", "老弟支付宝", "社会哥微信", "社会哥支付宝"],
        )
        self.assertEqual([item["owner"] for item in plan["items"][:4]], ["owner"] * 4)

    def test_cost_plan_rejects_insufficient_integer_balance(self) -> None:
        with self.assertRaisesRegex(ValueError, "最多 8 元"):
            plan_withdrawal("cost", 10, balances([4.9, 4.9, 0, 0, 0, 0, 0]))

    def test_cost_plan_requires_a_positive_integer_amount(self) -> None:
        with self.assertRaisesRegex(ValueError, "必须大于 0"):
            plan_withdrawal("cost", 324.5, balances([400, 0, 0, 0, 0, 0, 0]), 0)
        with self.assertRaisesRegex(ValueError, "必须为整数"):
            plan_withdrawal("cost", 324.5, balances([400, 0, 0, 0, 0, 0, 0]), 320.5)

    def test_cost_plan_is_disabled_when_there_is_no_cost_to_recover(self) -> None:
        with self.assertRaisesRegex(ValueError, "当前没有待回收成本"):
            plan_withdrawal("cost", 0, balances([0, 0, 0, 0, 0, 1.6, 0]), 1)
        full = plan_withdrawal("full", 0, balances([0, 0, 0, 0, 0, 1.6, 0]))
        self.assertEqual(full["totalAmount"], 1)

    def test_cost_rounding_remainder_belongs_to_owner(self) -> None:
        plan = plan_withdrawal("cost", 324.5, balances([20, 80, 65, 60, 100, 0, 0]))
        settlement = settlement_for(plan)
        self.assertEqual(settlement["gross"], 325.0)
        self.assertEqual(settlement["ownerExpected"], 325.0)
        self.assertEqual(settlement["partnerExpected"], 0.0)
        self.assertEqual(settlement["roundingRemainder"], 0.5)

    def test_full_settlement_uses_sixty_forty_profit_split(self) -> None:
        plan = plan_withdrawal("full", 324.5, balances([81, 65, 62, 105, 1, 86]))
        settlement = settlement_for(plan)
        self.assertEqual(settlement["gross"], 400.0)
        self.assertEqual(settlement["profit"], 75.5)
        self.assertEqual(settlement["ownerExpected"], 369.8)
        self.assertEqual(settlement["partnerExpected"], 30.2)
        self.assertEqual(settlement["partnerToOwner"], 56.8)

    def test_cost_settlement_exposes_unrecovered_manual_amount(self) -> None:
        plan = plan_withdrawal("cost", 324.5, balances([20, 80, 65, 60, 95, 0, 0]), 320)
        settlement = settlement_for(plan)
        self.assertEqual(settlement["costRecovery"], 320.0)
        self.assertEqual(settlement["unrecoveredCost"], 4.5)

    def test_email_templates_name_both_fixed_recipients_and_formula(self) -> None:
        plan = plan_withdrawal("full", 324.5, balances([81, 65, 62, 105, 1, 86]))
        plan["jobId"] = "20260802-001"
        plan["balanceSnapshotTotal"] = 423.33
        plan["postWithdrawalCost"] = 0
        plan["postWithdrawalBalance"] = 3.33
        plan["discountedProfit"] = 3.33
        plan["costHistory"] = [
            {
                "id": "cost-1",
                "date": "2026-07-30T16:00:00Z",
                "amount": 324.5,
                "note": "测试成本",
                "createdAt": "2026-07-31T02:35:25Z",
            }
        ]
        plan["costHistoryTotal"] = 324.5
        plan["costSettlementStatus"] = "cleared"
        plan["costClearedAmount"] = 324.5
        plan["settlement"] = settlement_for(plan)
        subject, body = render_withdrawal_email(plan)
        self.assertEqual(NOTIFICATION_RECIPIENTS, ("252715669@qq.com",))
        self.assertIn("[91] 全部提现任务 #20260802-001", subject)
        self.assertIn("星星应得 = 324.50 + 75.50 × 60% = 369.80 元", body)
        self.assertIn("社会哥需要转给星星：56.80 元", body)
        self.assertIn("成本日期：2026-07-31 00:00:00", body)
        self.assertIn("金额：324.50 元 | 备注：测试成本", body)
        self.assertIn("录入时间：2026-07-31 10:35:25", body)
        self.assertIn("提现后总成本：0.00 元", body)
        self.assertIn("提现后总余额：3.33 元", body)
        self.assertIn("提现后折后利润：3.33 元", body)
        self.assertGreater(body.index("实际提现到星星账号"), body.index("提现后折后利润"))
        self.assertTrue(body.endswith("星星需要转给社会哥：0.00 元"))
        html_body = render_withdrawal_email_html(plan)
        self.assertIn("<table", html_body)
        self.assertIn("成本历史明细", html_body)
        self.assertIn("账号提现明细", html_body)
        self.assertGreater(html_body.index("实际到账归属"), html_body.index("提现后汇总"))
        self.assertGreater(html_body.index("结算转账"), html_body.index("实际到账归属"))

    def test_loss_email_does_not_apply_profit_formula(self) -> None:
        plan = plan_withdrawal("full", 324.5, balances([20, 30, 0, 0, 0, 0, 0]))
        plan["jobId"] = "20260802-loss"
        plan["settlement"] = settlement_for(plan)
        _, body = render_withdrawal_email(plan)
        self.assertIn("社会哥应得 = 0.00 元", body)
        self.assertIn("亏损 274.50 元由星星承担", body)
        self.assertNotIn("-274.50 × 60%", body)

    def test_failed_job_settlement_counts_only_submitted_items(self) -> None:
        plan = plan_withdrawal("cost", 100, balances([50, 50, 0, 0, 0, 0, 0]), 100)
        plan["status"] = "failed"
        plan["items"][0]["status"] = "submitted"
        plan["items"][1]["status"] = "failed"
        settlement = settlement_for(plan)
        self.assertEqual(settlement["gross"], 50.0)
        self.assertEqual(settlement["unrecoveredCost"], 50.0)


class WithdrawalRouteTests(unittest.TestCase):
    def test_get_preview_accepts_account_amounts_query(self) -> None:
        recorded: dict[str, object] = {}
        account = WITHDRAWAL_ACCOUNTS[0]

        class PreviewService:
            wake_event = SimpleNamespace(set=lambda: None)

            async def preview_plan(self, mode, requested_amount, account_amounts, _manager):
                recorded.update(
                    {
                        "mode": mode,
                        "requested_amount": requested_amount,
                        "account_amounts": account_amounts,
                    }
                )
                return {
                    "mode": mode,
                    "cost": 0,
                    "requestedAmount": 7,
                    "totalAmount": 7,
                    "items": [
                        {
                            "email": account.email,
                            "amount": 7,
                            "status": "queued",
                        }
                    ],
                }

            def target_ids(self, _manager):
                return {account.email.lower(): "target-0"}

        router, _handlers = create_withdrawal_router(
            api_prefix="/gpt-api",
            require_manager=lambda: object(),
            pixel_http_error=lambda exc: exc,
            service=PreviewService(),
        )
        app = FastAPI()
        app.include_router(router)

        with TestClient(app) as client:
            response = client.get(
                "/gpt-api/withdrawals/preview",
                params={
                    "mode": "full",
                    "accountAmounts": f'{{"{account.email}":7}}',
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(recorded["mode"], "full")
        self.assertEqual(recorded["account_amounts"], {account.email: 7})


class WithdrawalSchemaMigrationTests(unittest.TestCase):
    def test_v212_failed_item_requires_history_reconciliation_after_migration(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.executescript(
            """
            CREATE TABLE withdrawal_jobs (
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
            CREATE TABLE withdrawal_items (
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
            CREATE TABLE withdrawal_emails (
                email_id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                recipient TEXT NOT NULL,
                subject TEXT NOT NULL,
                body TEXT NOT NULL,
                status TEXT NOT NULL,
                error TEXT,
                created_at TEXT NOT NULL
            );
            """
        )
        attempted_at = "2026-08-15T08:09:10Z"
        connection.execute(
            """INSERT INTO withdrawal_jobs(
                   job_id, mode, requested_amount, total_amount, cost,
                   balance_snapshot_at, balance_snapshot_total, cost_history,
                   cost_history_total, status, current_sequence, error,
                   settlement, cost_cleared_amount, cost_settlement_status,
                   created_at, updated_at
               ) VALUES (?, 'cost', 1, 1, 1, ?, 1, '[]', 0, 'failed', 1, ?,
                         '{}', 0, 'not_recovered', ?, ?)""",
            (
                "legacy-failed",
                "2026-08-15T08:00:00Z",
                "平台连接失败",
                "2026-08-15T08:00:00Z",
                attempted_at,
            ),
        )
        connection.execute(
            """INSERT INTO withdrawal_items(
                   job_id, sequence, email, target_id, owner, owner_label,
                   payment_method, balance, amount, status, status_label,
                   error, response
               ) VALUES (?, 1, ?, 'target-0', 'owner', '自己支付宝',
                         'alipay', 1, 1, 'failed', '失败', ?, '{}')""",
            ("legacy-failed", WITHDRAWAL_ACCOUNTS[0].email, "平台连接失败"),
        )

        initialize_withdrawal_schema(connection)
        migrated = connection.execute(
            "SELECT attempted_at, outcome_unknown, retry_reconciliation_required "
            "FROM withdrawal_items WHERE job_id = 'legacy-failed'"
        ).fetchone()

        self.assertEqual(migrated["attempted_at"], attempted_at)
        self.assertEqual(migrated["outcome_unknown"], 1)
        self.assertEqual(migrated["retry_reconciliation_required"], 1)

        initialize_withdrawal_schema(connection)
        repeated = connection.execute(
            "SELECT attempted_at, outcome_unknown, retry_reconciliation_required "
            "FROM withdrawal_items WHERE job_id = 'legacy-failed'"
        ).fetchone()
        self.assertEqual(dict(repeated), dict(migrated))
        connection.close()


class BlockingWithdrawalManager:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0
        self.in_flight = 0
        self.max_in_flight = 0
        self.profile_balances = {f"target-{index}": 10_000 for index, _ in enumerate(WITHDRAWAL_ACCOUNTS)}
        self.histories = {
            f"target-{index}": [
                {
                    "id": 10_000 + index,
                    "amount": 1,
                    "payment_method": account.payment_method,
                    "status": "SETTLED",
                    "created_at": "2020-01-01T00:00:00Z",
                }
            ]
            for index, account in enumerate(WITHDRAWAL_ACCOUNTS)
        }
        self.settings = {
            "withdrawal_management_enabled": True,
            "withdrawal_rate_limit_window_days": 7,
            "withdrawal_rate_limit_max": 3,
            "withdrawal_rate_limit_exempt_amount": 499.99,
        }
        self.config = SimpleNamespace(
            targets={
                f"target-{index}": SimpleNamespace(id=f"target-{index}", email=account.email)
                for index, account in enumerate(WITHDRAWAL_ACCOUNTS)
            }
        )

    async def withdrawal_settings(self, target_id: str) -> dict[str, object]:
        return dict(self.settings)

    async def withdrawal_profile(self, target_id: str) -> dict[str, object]:
        return {"balance": self.profile_balances.get(target_id, 0)}

    async def withdrawal_history(self, target_id: str, page: int = 1, page_size: int = 1000) -> dict[str, object]:
        history = self.histories.get(target_id, [])
        start = (page - 1) * page_size
        items = history[start:start + page_size]
        pages = max(1, (len(history) + page_size - 1) // page_size)
        return {"items": items, "total": len(history), "page": page, "page_size": page_size, "pages": pages}

    async def submit_withdrawal(self, target_id: str, amount: int, payment_method: str) -> dict[str, object]:
        self.calls += 1
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        self.started.set()
        await self.release.wait()
        self.in_flight -= 1
        return {
            "id": self.calls,
            "amount": amount,
            "fee_amount": 0,
            "total_deducted": amount,
            "payment_method": payment_method,
            "status": "PENDING",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }


class FailingWithdrawalManager(BlockingWithdrawalManager):
    def __init__(self) -> None:
        super().__init__()
        self.release.set()

    async def submit_withdrawal(self, target_id: str, amount: int, payment_method: str) -> dict[str, object]:
        raise RuntimeError("模拟提现失败")


class PixelBusinessError(RuntimeError):
    def __init__(self, reason: str, metadata: dict[str, str] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.metadata = metadata or {}
        self.public_message = reason
        self.outcome_unknown = False


class WithdrawalServiceConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "withdrawals.db"

        @contextmanager
        def connect():
            connection = sqlite3.connect(self.db_path, timeout=2)
            connection.row_factory = sqlite3.Row
            try:
                with connection:
                    yield connection
            finally:
                connection.close()

        self.connect = connect
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE cost_additions (
                    id TEXT PRIMARY KEY,
                    date TEXT NOT NULL,
                    note TEXT NOT NULL,
                    amount REAL NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE balance_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    total REAL NOT NULL,
                    amounts TEXT NOT NULL,
                    accounts TEXT NOT NULL
                );
                """
            )
            initialize_withdrawal_schema(connection)
            connection.execute(
                "INSERT INTO settings(key, value) VALUES('stored_state', ?)",
                (json.dumps({"cost": 1}),),
            )
            connection.execute(
                "INSERT INTO cost_additions(id, date, note, amount, created_at) VALUES(?, ?, ?, ?, ?)",
                ("cost-1", "2026-08-01T00:00:00Z", "初始成本", 1, "2026-08-01T00:01:00Z"),
            )
        self.ledger = CostLedger(
            connect=self.connect,
            dumps=lambda value: json.dumps(value, ensure_ascii=False),
            loads=lambda value, default: json.loads(value) if value else default,
            utc_now=lambda: datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            number=lambda value, default=0: float(value) if value is not None else default,
        )
        self.manager = BlockingWithdrawalManager()
        self.balance_values = [1, 0, 0, 0, 0, 0, 0]

        def get_setting(key: str, default):
            with self.connect() as connection:
                row = connection.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            return json.loads(row["value"]) if row else default

        self.service = WithdrawalService(
            connect=self.connect,
            dumps=lambda value: json.dumps(value, ensure_ascii=False),
            loads=lambda value, default: json.loads(value) if value else default,
            utc_now=lambda: datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            parse_time=lambda value: datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None,
            flexible_number=lambda value, default=0: float(value) if value is not None else default,
            get_setting=get_setting,
            normalize_smtp_settings=lambda raw: {
                "host": "smtp.qq.com",
                "port": 465,
                "username": "",
                "password": "",
            },
            latest_balance_snapshot=lambda: {
                "accounts": [account.email for account in WITHDRAWAL_ACCOUNTS],
                "amounts": self.balance_values,
                "total": sum(self.balance_values),
            },
            get_pixel_manager=lambda: self.manager,
            initialize_pixel_manager=lambda: None,
            wake_event=asyncio.Event(),
            cost_additions_snapshot=self.ledger.list,
            recover_cost_additions_if_snapshot=self.ledger.recover_snapshot,
        )
        self.target_ids = {account.email: f"target-{index}" for index, account in enumerate(WITHDRAWAL_ACCOUNTS)}

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    async def test_only_one_concurrent_job_creation_succeeds(self) -> None:
        plan = self.service.latest_plan("cost", 1)
        results = await asyncio.gather(
            asyncio.to_thread(self.service.create_job, plan, self.target_ids),
            asyncio.to_thread(self.service.create_job, plan, self.target_ids),
            return_exceptions=True,
        )
        self.assertEqual(sum(isinstance(result, dict) for result in results), 1)
        self.assertEqual(sum(isinstance(result, RuntimeError) for result in results), 1)

    async def test_job_history_is_paged_and_keeps_account_details(self) -> None:
        plan = self.service.latest_plan("cost", 1)
        created = self.service.create_job(plan, self.target_ids)
        history = self.service.job_history(limit=10, offset=0)
        self.assertEqual(history["total"], 1)
        self.assertEqual(history["jobs"][0]["jobId"], created["jobId"])
        self.assertEqual(len(history["jobs"][0]["items"]), len(WITHDRAWAL_ACCOUNTS))
        self.assertEqual(history["jobs"][0]["items"][0]["statusLabel"], "待执行")
        self.assertNotIn("response", history["jobs"][0]["items"][0])

    async def test_preview_matches_pixel_rolling_limit_exemption_and_pending_priority(self) -> None:
        now = datetime.now(timezone.utc)
        self.manager.histories["target-0"] = [
            {
                "id": index + 1,
                "amount": amount,
                "payment_method": "alipay",
                "status": status,
                "created_at": (now - timedelta(days=index + 1)).isoformat(),
            }
            for index, (amount, status) in enumerate(
                ((1, "SETTLED"), (499.99, "CANCELLED"), (499, "REJECTED"))
            )
        ]
        account_amounts = {account.email: 0 for account in WITHDRAWAL_ACCOUNTS}
        first_email = WITHDRAWAL_ACCOUNTS[0].email

        account_amounts[first_email] = 499
        limited = await self.service.preview_plan("full", None, account_amounts, self.manager)
        limited_item = limited["items"][0]
        self.assertEqual(limited_item["status"], "skipped")
        self.assertEqual(limited_item["eligibility"]["reasonCode"], "WITHDRAWAL_RATE_LIMIT_EXCEEDED")
        self.assertEqual(limited_item["eligibility"]["recentRequests"], 3)
        self.assertEqual(
            limited_item["error"],
            "近 7 天已有 3 次计入限额的提现申请，本笔 499 元未超过 499.99 元，已自动跳过",
        )

        account_amounts[first_email] = 500
        exempt = await self.service.preview_plan("full", None, account_amounts, self.manager)
        self.assertEqual(exempt["items"][0]["status"], "queued")
        self.assertTrue(exempt["items"][0]["eligibility"]["exempt"])

        self.manager.histories["target-0"].append(
            {
                "id": 10,
                "amount": 600,
                "payment_method": "alipay",
                "status": "PENDING",
                "created_at": now.isoformat(),
            }
        )
        pending = await self.service.preview_plan("full", None, account_amounts, self.manager)
        self.assertEqual(pending["items"][0]["status"], "skipped")
        self.assertEqual(pending["items"][0]["eligibility"]["reasonCode"], "WITHDRAWAL_PENDING_EXISTS")
        self.assertIn("待结算", pending["items"][0]["error"])

    async def test_preview_applies_first_ever_fee_at_integer_boundary(self) -> None:
        for target_id in self.manager.histories:
            self.manager.histories[target_id] = []
            self.manager.profile_balances[target_id] = 0
        self.manager.profile_balances["target-0"] = 1.00

        one_yuan = await self.service.preview_plan("full", None, None, self.manager)
        self.assertEqual(one_yuan["items"][0]["amount"], 0)
        self.assertTrue(one_yuan["items"][0]["eligibility"]["firstWithdrawal"])
        self.assertEqual(one_yuan["items"][0]["eligibility"]["maxIntegerAmount"], 0)

        self.manager.profile_balances["target-0"] = 1.10
        one_yuan_ten = await self.service.preview_plan("full", None, None, self.manager)
        self.assertEqual(one_yuan_ten["items"][0]["amount"], 1)
        self.assertEqual(one_yuan_ten["items"][0]["feeAmount"], 0.10)
        self.assertEqual(one_yuan_ten["items"][0]["totalDeducted"], 1.10)

    async def test_management_disabled_skips_without_profile_or_history(self) -> None:
        self.manager.settings["withdrawal_management_enabled"] = False
        calls = {"profile": 0, "history": 0}

        async def unavailable_profile(_target_id: str) -> dict[str, object]:
            calls["profile"] += 1
            raise RuntimeError("管理关闭时不应读取余额")

        async def unavailable_history(
            _target_id: str, _page: int = 1, _page_size: int = 1000
        ) -> dict[str, object]:
            calls["history"] += 1
            raise RuntimeError("管理关闭时不应读取历史")

        self.manager.withdrawal_profile = unavailable_profile
        self.manager.withdrawal_history = unavailable_history

        plan = await self.service.preview_plan("full", None, None, self.manager)

        self.assertEqual(calls, {"profile": 0, "history": 0})
        self.assertEqual(plan["items"][0]["status"], "skipped")
        self.assertEqual(
            plan["items"][0]["eligibility"]["reasonCode"],
            "WITHDRAWAL_MANAGEMENT_DISABLED",
        )
        self.assertIn("已关闭提现管理", plan["items"][0]["error"])

    async def test_incomplete_history_never_passes_preflight(self) -> None:
        async def incomplete_history(
            _target_id: str, _page: int = 1, _page_size: int = 1000
        ) -> dict[str, object]:
            return {
                "items": [
                    {
                        "id": 1,
                        "amount": 1,
                        "payment_method": "alipay",
                        "status": "SETTLED",
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    }
                ],
                "total": 2,
                "pages": 1,
            }

        self.manager.withdrawal_history = incomplete_history

        plan = await self.service.preview_plan("full", None, None, self.manager)

        first = plan["items"][0]
        self.assertEqual(first["status"], "queued")
        self.assertEqual(first["eligibility"]["status"], "unknown")
        self.assertEqual(
            first["eligibility"]["reasonCode"],
            "WITHDRAWAL_HISTORY_INCOMPLETE",
        )
        self.assertIn("历史读取不完整", first["eligibility"]["reason"])
        self.assertIn("本次不会提交", first["eligibility"]["reason"])
        self.assertNotIn("recentRequests", first["eligibility"])
        self.assertNotIn("pending", first["eligibility"])
        self.assertNotIn("firstWithdrawal", first["eligibility"])

        self.balance_values = [1, 0, 0, 0, 0, 0, 0]
        job = self.service.create_job(self.service.latest_plan("cost", 1), self.target_ids)
        await self.service.process_once()

        failed = self.service.job_detail(job["jobId"])
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["items"][0]["status"], "failed")
        self.assertIn("历史读取不完整", failed["items"][0]["error"])
        self.assertEqual(self.manager.calls, 0)

    async def test_worker_business_limit_error_skips_account_and_continues(self) -> None:
        self.balance_values = [1, 1, 0, 0, 0, 0, 0]
        job = self.service.create_job(self.service.latest_plan("cost", 2), self.target_ids)
        calls: list[str] = []

        async def submit(target_id: str, amount: int, payment_method: str) -> dict[str, object]:
            calls.append(target_id)
            if target_id == "target-0":
                raise PixelBusinessError(
                    "WITHDRAWAL_RATE_LIMIT_EXCEEDED",
                    {"window_days": "7", "max": "3"},
                )
            return {
                "id": 200,
                "amount": amount,
                "fee_amount": 0,
                "total_deducted": amount,
                "payment_method": payment_method,
                "status": "PENDING",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }

        self.manager.submit_withdrawal = submit
        await self.service.process_once()
        after_skip = self.service.job_detail(job["jobId"])
        self.assertEqual(after_skip["status"], "queued")
        self.assertEqual(after_skip["items"][0]["status"], "skipped")
        self.assertEqual(
            after_skip["items"][0]["eligibility"]["reasonCode"],
            "WITHDRAWAL_RATE_LIMIT_EXCEEDED",
        )
        self.assertIn("已自动跳过", after_skip["items"][0]["error"])
        self.assertIn("本笔 1 元未超过 499.99 元", after_skip["items"][0]["error"])
        self.assertEqual(after_skip["totalAmount"], 1)

        await self.service.process_once()
        completed = self.service.job_detail(job["jobId"])
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["items"][1]["status"], "submitted")
        self.assertEqual(completed["totalAmount"], 1)
        self.assertEqual(calls, ["target-0", "target-1"])

    async def test_rolling_window_includes_the_exact_boundary(self) -> None:
        fixed_now = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)
        self.service._utc_now = lambda: fixed_now.isoformat().replace("+00:00", "Z")
        self.manager.settings["withdrawal_rate_limit_max"] = 1
        self.manager.histories["target-0"] = [
            {
                "id": 1,
                "amount": 1,
                "payment_method": "alipay",
                "status": "CANCELLED",
                "created_at": (fixed_now - timedelta(days=7)).isoformat(),
            }
        ]
        amounts = {account.email: 0 for account in WITHDRAWAL_ACCOUNTS}
        amounts[WITHDRAWAL_ACCOUNTS[0].email] = 1

        at_boundary = await self.service.preview_plan("full", None, amounts, self.manager)
        self.assertEqual(at_boundary["items"][0]["status"], "skipped")
        self.assertEqual(at_boundary["items"][0]["eligibility"]["recentRequests"], 1)

        self.manager.histories["target-0"][0]["created_at"] = (
            fixed_now - timedelta(days=7, microseconds=1)
        ).isoformat()
        before_boundary = await self.service.preview_plan("full", None, amounts, self.manager)
        self.assertEqual(before_boundary["items"][0]["status"], "queued")
        self.assertEqual(before_boundary["items"][0]["eligibility"]["recentRequests"], 0)

    async def test_worker_does_not_submit_when_preflight_is_unknown(self) -> None:
        job = self.service.create_job(self.service.latest_plan("cost", 1), self.target_ids)
        self.manager.release.set()

        async def unavailable(_target_id: str) -> dict[str, object]:
            raise RuntimeError("公开配置暂时不可用")

        self.manager.withdrawal_settings = unavailable
        await self.service.process_once()

        failed = self.service.job_detail(job["jobId"])
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["items"][0]["status"], "failed")
        self.assertFalse(failed["items"][0]["outcomeUnknown"])
        self.assertIsNone(failed["items"][0]["attemptedAt"])
        self.assertEqual(self.manager.calls, 0)

    async def test_generic_http_429_is_a_failure_not_a_business_skip(self) -> None:
        job = self.service.create_job(self.service.latest_plan("cost", 1), self.target_ids)

        class Generic429(RuntimeError):
            status_code = 429
            reason = ""
            public_message = "too many requests"
            outcome_unknown = False

        async def submit(_target_id: str, _amount: int, _payment_method: str) -> dict[str, object]:
            raise Generic429()

        self.manager.submit_withdrawal = submit
        await self.service.process_once()

        failed = self.service.job_detail(job["jobId"])
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["items"][0]["status"], "failed")
        self.assertNotEqual(failed["items"][0]["status"], "skipped")

    def test_unknown_attempt_match_has_a_bounded_time_window(self) -> None:
        attempted_at = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)
        item = {"amount": 1, "paymentMethod": "alipay"}
        observed_ids: set[str] = set()
        near = {
            "id": 1,
            "amount": 1,
            "payment_method": "alipay",
            "created_at": (attempted_at + timedelta(minutes=10)).isoformat(),
        }
        late = {**near, "id": 2, "created_at": (attempted_at + timedelta(minutes=10, microseconds=1)).isoformat()}
        self.assertTrue(self.service._record_matches_unknown_attempt(near, item, attempted_at, observed_ids))
        self.assertFalse(self.service._record_matches_unknown_attempt(late, item, attempted_at, observed_ids))

    async def test_unknown_post_result_reconciles_history_without_second_submission(self) -> None:
        job = self.service.create_job(self.service.latest_plan("cost", 1), self.target_ids)
        calls = 0

        async def submit(_target_id: str, amount: int, payment_method: str) -> dict[str, object]:
            nonlocal calls
            calls += 1
            return {
                "id": 300,
                "amount": amount,
                "payment_method": payment_method,
                "status": "SETTLED",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }

        self.manager.submit_withdrawal = submit
        await self.service.process_once()
        failed = self.service.job_detail(job["jobId"])
        self.assertEqual(failed["status"], "failed")
        self.assertTrue(failed["items"][0]["outcomeUnknown"])
        self.assertIsNotNone(failed["items"][0]["attemptedAt"])
        with self.assertRaisesRegex(RuntimeError, "没有可加速"):
            self.service.accelerate_job(job["jobId"])

        self.manager.histories["target-0"].append(
            {
                "id": 301,
                "amount": 1,
                "payment_method": "alipay",
                "status": "SETTLED",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        reconciled = await self.service.retry_job(job["jobId"], self.manager)
        self.assertEqual(reconciled["status"], "completed")
        self.assertEqual(reconciled["items"][0]["status"], "submitted")
        self.assertEqual(reconciled["items"][0]["platformWithdrawalId"], "301")
        self.assertEqual(reconciled["items"][0]["platformStatus"], "SETTLED")
        self.assertEqual(reconciled["items"][0]["retryCount"], 1)
        self.assertEqual(calls, 1)

    async def _assert_terminal_unknown_history_is_audited(self, platform_status: str) -> None:
        self.balance_values = [1, 1, 0, 0, 0, 0, 0]
        job = self.service.create_job(self.service.latest_plan("full"), self.target_ids)

        async def ambiguous_submit(
            _target_id: str, amount: int, payment_method: str
        ) -> dict[str, object]:
            return {
                "id": 410,
                "amount": amount,
                "payment_method": payment_method,
                "status": "SETTLED",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }

        self.manager.submit_withdrawal = ambiguous_submit
        await self.service.process_once()
        failed = self.service.job_detail(job["jobId"])
        self.assertTrue(failed["items"][0]["outcomeUnknown"])

        self.manager.histories["target-0"].append(
            {
                "id": 411,
                "amount": 1,
                "payment_method": "alipay",
                "status": platform_status,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        audited = await self.service.retry_job(job["jobId"], self.manager)

        self.assertEqual(audited["status"], "failed")
        self.assertEqual(audited["items"][0]["status"], "failed")
        self.assertEqual(audited["items"][0]["platformStatus"], platform_status)
        self.assertEqual(audited["items"][0]["platformWithdrawalId"], "411")
        self.assertFalse(audited["items"][0]["outcomeUnknown"])
        self.assertEqual(audited["items"][0]["retryCount"], 1)
        self.assertEqual(audited["items"][0]["costRecoveredAmount"], 0)
        self.assertEqual(audited["items"][1]["status"], "skipped")
        self.assertIn("封存不再执行", audited["items"][1]["error"])
        self.assertEqual(audited["costClearedAmount"], 0)
        self.assertIn("未冲减成本", audited["items"][0]["error"])
        self.assertEqual([item["id"] for item in self.ledger.list()], ["cost-1"])
        with self.connect() as connection:
            response = connection.execute(
                "SELECT response FROM withdrawal_items WHERE item_id = ?",
                (audited["items"][0]["itemId"],),
            ).fetchone()["response"]
        self.assertEqual(json.loads(response)["status"], platform_status)

        resumed = await self.service.retry_job(job["jobId"], self.manager)
        self.assertEqual(resumed["status"], "queued")
        self.assertEqual(resumed["items"][0]["retryCount"], 2)

    async def test_unknown_history_rejected_is_failed_without_cost_recovery(self) -> None:
        await self._assert_terminal_unknown_history_is_audited("REJECTED")

    async def test_unknown_history_cancelled_is_failed_without_cost_recovery(self) -> None:
        await self._assert_terminal_unknown_history_is_audited("CANCELLED")

    async def test_unknown_post_result_cannot_be_resubmitted_during_history_grace(self) -> None:
        job = self.service.create_job(self.service.latest_plan("cost", 1), self.target_ids)
        calls = 0

        async def ambiguous_submit(_target_id: str, amount: int, payment_method: str) -> dict[str, object]:
            nonlocal calls
            calls += 1
            return {
                "id": 302,
                "amount": amount,
                "payment_method": payment_method,
                "status": "SETTLED",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }

        self.manager.submit_withdrawal = ambiguous_submit
        await self.service.process_once()
        failed = self.service.job_detail(job["jobId"])
        self.assertTrue(failed["items"][0]["outcomeUnknown"])
        attempted_at = datetime.fromisoformat(failed["items"][0]["attemptedAt"].replace("Z", "+00:00"))

        with self.assertRaisesRegex(RuntimeError, "避免重复提交"):
            await self.service.retry_job(job["jobId"], self.manager)
        self.assertEqual(self.service.job_detail(job["jobId"])["status"], "failed")
        self.assertEqual(calls, 1)

        after_grace = attempted_at + timedelta(seconds=UNKNOWN_OUTCOME_RETRY_GRACE_SECONDS + 1)
        self.service._utc_now = lambda: after_grace.isoformat().replace("+00:00", "Z")
        resumed = await self.service.retry_job(job["jobId"], self.manager)
        self.assertEqual(resumed["status"], "queued")
        self.assertEqual(resumed["items"][0]["retryCount"], 1)

    async def test_first_fee_reduces_post_balance_but_not_gross_or_cost_recovery(self) -> None:
        for target_id in self.manager.histories:
            self.manager.histories[target_id] = []
            self.manager.profile_balances[target_id] = 0
        self.manager.profile_balances["target-0"] = 1.10
        plan = await self.service.preview_plan("full", None, None, self.manager)
        job = self.service.create_job(plan, self.target_ids)

        async def submit(_target_id: str, amount: int, payment_method: str) -> dict[str, object]:
            return {
                "id": 400,
                "amount": amount,
                "fee_amount": 0.10,
                "total_deducted": 1.10,
                "payment_method": payment_method,
                "status": "PENDING",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }

        self.manager.submit_withdrawal = submit
        await self.service.process_once()
        completed = self.service.job_detail(job["jobId"])
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["items"][0]["feeAmount"], 0.10)
        self.assertEqual(completed["items"][0]["totalDeducted"], 1.10)
        self.assertEqual(completed["settlement"]["gross"], 1)
        self.assertEqual(completed["costClearedAmount"], 1)
        self.assertEqual(completed["postWithdrawalBalance"], 0)

    async def test_two_workers_never_submit_accounts_at_the_same_time(self) -> None:
        plan = self.service.latest_plan("cost", 1)
        self.service.create_job(plan, self.target_ids)

        first_worker = asyncio.create_task(self.service.process_once())
        await asyncio.wait_for(self.manager.started.wait(), timeout=1)
        second_delay = await self.service.process_once()

        self.assertEqual(second_delay, 5.0)
        self.assertEqual(self.manager.calls, 1)
        self.assertEqual(self.manager.max_in_flight, 1)
        self.assertEqual(self.service.current_job()["status"], "running")

        self.manager.release.set()
        await asyncio.wait_for(first_worker, timeout=2)
        current = self.service.current_job()
        self.assertEqual(current["status"], "completed")
        self.assertEqual(self.manager.calls, 1)
        self.assertEqual(self.manager.max_in_flight, 1)
        with self.connect() as connection:
            emails = connection.execute("SELECT recipient, status FROM withdrawal_emails ORDER BY email_id").fetchall()
        self.assertEqual(len(emails), len(NOTIFICATION_RECIPIENTS))
        self.assertEqual({row["recipient"] for row in emails}, set(NOTIFICATION_RECIPIENTS))

    async def test_accounts_wait_then_accelerate_without_parallel_submission(self) -> None:
        self.balance_values = [1, 1, 0, 0, 0, 0, 0]
        plan = self.service.latest_plan("cost", 2)
        job = self.service.create_job(plan, self.target_ids)
        self.manager.release.set()

        delay = await self.service.process_once()
        waiting = self.service.job_detail(job["jobId"])
        self.assertGreaterEqual(delay, 20 * 60)
        self.assertLessEqual(delay, 60 * 60)
        self.assertEqual(waiting["status"], "waiting")
        self.assertEqual(self.manager.calls, 1)
        self.assertEqual([item["status"] for item in waiting["items"][:2]], ["submitted", "queued"])

        accelerated = self.service.accelerate_job(job["jobId"])
        self.assertEqual(accelerated["status"], "waiting")
        await self.service.process_once()
        completed = self.service.job_detail(job["jobId"])
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(self.manager.calls, 2)
        self.assertEqual(self.manager.max_in_flight, 1)

    async def test_completed_job_clears_only_frozen_cost_rows_once(self) -> None:
        plan = self.service.latest_plan("cost", 1)
        job = self.service.create_job(plan, self.target_ids)
        self.ledger.insert(
            {
                "id": "cost-new",
                "date": "2026-08-02T00:00:00Z",
                "note": "任务期间新增",
                "amount": 0.5,
                "createdAt": "2026-08-02T00:01:00Z",
            }
        )
        with self.connect() as connection:
            connection.execute(
                "UPDATE settings SET value = ? WHERE key = 'stored_state'",
                (json.dumps({"cost": 1.5}),),
            )
        self.manager.release.set()

        await self.service.process_once()
        completed = self.service.job_detail(job["jobId"])
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["costSettlementStatus"], "cleared")
        self.assertEqual(completed["costClearedAmount"], 1)
        self.assertEqual([item["id"] for item in self.ledger.list()], ["cost-new"])
        with self.connect() as connection:
            stored = json.loads(connection.execute("SELECT value FROM settings WHERE key = 'stored_state'").fetchone()[0])
        self.assertEqual(stored["cost"], 0.5)

        self.service._finalize_financials(job["jobId"])
        self.assertEqual([item["id"] for item in self.ledger.list()], ["cost-new"])
        with self.connect() as connection:
            stored = json.loads(connection.execute("SELECT value FROM settings WHERE key = 'stored_state'").fetchone()[0])
        self.assertEqual(stored["cost"], 0.5)

    async def test_deleted_frozen_cost_row_never_consumes_new_cost(self) -> None:
        plan = self.service.latest_plan("cost", 1)
        job = self.service.create_job(plan, self.target_ids)
        self.assertEqual(self.ledger.delete("cost-1"), 1)
        self.ledger.insert(
            {
                "id": "cost-new",
                "date": "2026-08-02T00:00:00Z",
                "note": "任务期间新增",
                "amount": 0.5,
                "createdAt": "2026-08-02T00:01:00Z",
            }
        )
        with self.connect() as connection:
            connection.execute(
                "UPDATE settings SET value = ? WHERE key = 'stored_state'",
                (json.dumps({"cost": 0.5}),),
            )
        self.manager.release.set()

        await self.service.process_once()
        completed = self.service.job_detail(job["jobId"])
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["costClearedAmount"], 0)
        self.assertEqual(completed["costSettlementStatus"], "not_recovered")
        self.assertEqual(completed["postWithdrawalCost"], 0.5)
        self.assertEqual([(item["id"], item["amount"]) for item in self.ledger.list()], [("cost-new", 0.5)])

    async def test_each_submitted_account_immediately_reduces_cost(self) -> None:
        with self.connect() as connection:
            connection.execute("UPDATE cost_additions SET amount = 324.5 WHERE id = 'cost-1'")
            connection.execute(
                "UPDATE settings SET value = ? WHERE key = 'stored_state'",
                (json.dumps({"cost": 324.5}),),
            )
        self.balance_values = [20, 304, 0, 0, 0, 0, 0]
        plan = self.service.latest_plan("cost", 324)
        self.assertEqual([item["amount"] for item in plan["items"][:2]], [20, 304])
        job = self.service.create_job(plan, self.target_ids)
        self.manager.release.set()

        await self.service.process_once()
        waiting = self.service.job_detail(job["jobId"])
        self.assertEqual(waiting["status"], "waiting")
        self.assertEqual(waiting["costClearedAmount"], 20)
        self.assertEqual(waiting["costSettlementStatus"], "partial")
        self.assertEqual(waiting["postWithdrawalCost"], 304.5)
        self.assertEqual(waiting["items"][0]["costRecoveredAmount"], 20)
        self.assertEqual(waiting["items"][0]["remainingCostAfter"], 304.5)
        self.assertEqual(self.ledger.list()[0]["amount"], 304.5)

        self.service.accelerate_job(job["jobId"])
        await self.service.process_once()
        completed = self.service.job_detail(job["jobId"])
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["costClearedAmount"], 324)
        self.assertEqual(completed["costSettlementStatus"], "partial")
        self.assertEqual(completed["postWithdrawalCost"], 0.5)
        self.assertEqual(completed["items"][1]["costRecoveredAmount"], 304)
        self.assertEqual(completed["items"][1]["remainingCostAfter"], 0.5)
        self.assertEqual(self.ledger.list()[0]["amount"], 0.5)

        self.service._finalize_financials(job["jobId"])
        self.assertEqual(self.service.job_detail(job["jobId"])["costClearedAmount"], 324)
        self.assertEqual(self.ledger.list()[0]["amount"], 0.5)

    async def test_failed_job_never_clears_cost_history(self) -> None:
        self.manager = FailingWithdrawalManager()
        plan = self.service.latest_plan("cost", 1)
        job = self.service.create_job(plan, self.target_ids)

        await self.service.process_once()
        failed = self.service.job_detail(job["jobId"])
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["costSettlementStatus"], "not_recovered")
        self.assertEqual([item["id"] for item in self.ledger.list()], ["cost-1"])

    async def test_local_persistence_failure_pauses_unknown_attempt_without_killing_worker(self) -> None:
        self.manager.release.set()

        def fail_recovery(_conn, _snapshot, _amount, _job_cost):
            raise RuntimeError("模拟成本落库失败")

        self.service._recover_cost_additions_if_snapshot = fail_recovery
        job = self.service.create_job(self.service.latest_plan("cost", 1), self.target_ids)
        worker = asyncio.create_task(self.service.run_worker())
        try:
            for _ in range(100):
                await asyncio.sleep(0.01)
                current = self.service.job_detail(job["jobId"])
                if current and current["status"] == "failed":
                    break
            else:
                self.fail("本地落库失败后任务未进入安全暂停状态")

            failed = self.service.job_detail(job["jobId"])
            self.assertFalse(worker.done())
            self.assertEqual(self.manager.calls, 1)
            self.assertEqual(failed["items"][0]["status"], "failed")
            self.assertTrue(failed["items"][0]["outcomeUnknown"])
            self.assertIn("本地记录失败", failed["items"][0]["error"])
            self.assertEqual(failed["costClearedAmount"], 0)
            self.assertEqual([item["id"] for item in self.ledger.list()], ["cost-1"])
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    async def test_failed_job_can_resume_unsubmitted_item(self) -> None:
        self.manager = FailingWithdrawalManager()
        plan = self.service.latest_plan("cost", 1)
        job = self.service.create_job(plan, self.target_ids)

        await self.service.process_once()
        failed = self.service.job_detail(job["jobId"])
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["items"][0]["status"], "failed")

        resumed = await self.service.retry_job(job["jobId"], self.manager)
        self.assertEqual(resumed["status"], "queued")
        self.assertIsNone(resumed["error"])
        self.assertEqual(resumed["items"][0]["status"], "queued")
        self.assertEqual(resumed["items"][0]["retryCount"], 1)

    async def test_failed_job_retry_seals_original_queue_tail(self) -> None:
        self.manager = FailingWithdrawalManager()
        self.balance_values = [1, 1, 1, 0, 0, 0, 0]
        job = self.service.create_job(self.service.latest_plan("full"), self.target_ids)

        await self.service.process_once()
        failed = self.service.job_detail(job["jobId"])
        self.assertEqual([item["status"] for item in failed["items"][:3]], ["failed", "queued", "queued"])
        self.assertEqual(failed["totalAmount"], 0)

        resumed = await self.service.retry_job(job["jobId"], self.manager)
        self.assertEqual([item["status"] for item in resumed["items"][:3]], ["queued", "skipped", "skipped"])
        self.assertEqual(resumed["totalAmount"], 1)
        self.assertTrue(all("封存不再执行" in item["error"] for item in resumed["items"][1:3]))

        self.manager = BlockingWithdrawalManager()
        self.manager.release.set()
        await self.service.process_once()
        completed = self.service.job_detail(job["jobId"])

        self.assertEqual(completed["status"], "completed")
        self.assertEqual([item["status"] for item in completed["items"][:3]], ["submitted", "skipped", "skipped"])
        self.assertEqual(completed["totalAmount"], 1)
        self.assertEqual(completed["settlement"]["gross"], 1)
        self.assertEqual(self.manager.calls, 1)

    async def test_legacy_failed_history_match_never_posts_or_runs_queue_tail(self) -> None:
        self.manager = FailingWithdrawalManager()
        self.balance_values = [1, 1, 1, 0, 0, 0, 0]
        job = self.service.create_job(self.service.latest_plan("full"), self.target_ids)

        await self.service.process_once()
        failed = self.service.job_detail(job["jobId"])
        attempted_at = failed["items"][0]["attemptedAt"]
        with self.connect() as connection:
            connection.execute(
                "UPDATE withdrawal_items SET outcome_unknown = 1, retry_reconciliation_required = 1 "
                "WHERE item_id = ?",
                (failed["items"][0]["itemId"],),
            )

        self.manager = BlockingWithdrawalManager()
        self.manager.release.set()
        self.manager.histories["target-0"].append(
            {
                "id": 19_001,
                "amount": 1,
                "payment_method": "alipay",
                "status": "SETTLED",
                "created_at": attempted_at,
            }
        )

        reconciled = await self.service.retry_job(job["jobId"], self.manager)

        self.assertEqual(reconciled["status"], "completed")
        self.assertEqual([item["status"] for item in reconciled["items"][:3]], ["submitted", "skipped", "skipped"])
        self.assertEqual(reconciled["items"][0]["platformWithdrawalId"], "19001")
        self.assertEqual(reconciled["totalAmount"], 1)
        self.assertEqual(reconciled["settlement"]["gross"], 1)
        self.assertEqual(self.manager.calls, 0)

    async def test_restart_recovers_submitted_item_before_wait_state_was_saved(self) -> None:
        self.balance_values = [1, 1, 0, 0, 0, 0, 0]
        plan = self.service.latest_plan("cost", 2)
        job = self.service.create_job(plan, self.target_ids)
        with self.connect() as connection:
            first_item = connection.execute(
                "SELECT item_id FROM withdrawal_items WHERE job_id = ? AND sequence = 1",
                (job["jobId"],),
            ).fetchone()
            connection.execute(
                "UPDATE withdrawal_items SET status = 'submitted', status_label = '已提交' WHERE item_id = ?",
                (first_item["item_id"],),
            )
            connection.execute(
                "UPDATE withdrawal_jobs SET status = 'running', current_sequence = 1 WHERE job_id = ?",
                (job["jobId"],),
            )

        delay = await self.service.process_once()
        recovered = self.service.job_detail(job["jobId"])
        self.assertGreaterEqual(delay, 20 * 60)
        self.assertLessEqual(delay, 60 * 60)
        self.assertEqual(recovered["status"], "waiting")
        self.assertEqual(self.manager.calls, 0)

    async def test_legacy_running_item_uses_job_timestamp_and_reconciles_before_retry(self) -> None:
        job = self.service.create_job(self.service.latest_plan("cost", 1), self.target_ids)
        legacy_attempted_at = "2020-02-03T04:05:06Z"
        with self.connect() as connection:
            connection.execute(
                "UPDATE withdrawal_items SET status = 'running', status_label = '提交中', "
                "attempted_at = NULL, outcome_unknown = 0 "
                "WHERE job_id = ? AND sequence = 1",
                (job["jobId"],),
            )
            connection.execute(
                "UPDATE withdrawal_jobs SET status = 'running', current_sequence = 1, "
                "created_at = ?, updated_at = ? WHERE job_id = ?",
                ("2020-02-03T03:00:00Z", legacy_attempted_at, job["jobId"]),
            )
            initialize_withdrawal_schema(connection)

        migrated = self.service.job_detail(job["jobId"])
        self.assertEqual(migrated["items"][0]["attemptedAt"], legacy_attempted_at)
        self.assertTrue(migrated["items"][0]["outcomeUnknown"])

        await self.service.process_once()
        paused = self.service.job_detail(job["jobId"])
        self.assertEqual(paused["status"], "failed")
        self.assertTrue(paused["items"][0]["outcomeUnknown"])

        self.manager.histories["target-0"].append(
            {
                "id": 900,
                "amount": 1,
                "payment_method": "alipay",
                "status": "SETTLED",
                "created_at": legacy_attempted_at,
            }
        )
        reconciled = await self.service.retry_job(job["jobId"], self.manager)
        self.assertEqual(reconciled["status"], "completed")
        self.assertEqual(reconciled["items"][0]["platformWithdrawalId"], "900")
        self.assertEqual(self.manager.calls, 0)

    async def test_worker_skips_retired_legacy_item_before_submit(self) -> None:
        plan = self.service.latest_plan("cost", 1)
        job = self.service.create_job(plan, self.target_ids)
        with self.connect() as connection:
            connection.execute(
                "UPDATE withdrawal_items SET email = ?, target_id = ? WHERE job_id = ? AND sequence = 1",
                ("1745627971@QQ.COM", "retired-target", job["jobId"]),
            )

        delay = await self.service.process_once()
        current = self.service.job_detail(job["jobId"])

        self.assertEqual(delay, 0.5)
        self.assertEqual(self.manager.calls, 0)
        self.assertEqual(current["items"][0]["status"], "skipped")
        self.assertIn("永久排除", current["items"][0]["error"])

        await self.service.process_once()
        self.assertEqual(self.service.job_detail(job["jobId"])["status"], "completed")


if __name__ == "__main__":
    unittest.main()
