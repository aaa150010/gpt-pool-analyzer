import asyncio
import json
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from server.cost_ledger import CostLedger
from server.withdrawal_service import (
    WithdrawalService,
    build_notification_message,
    initialize_withdrawal_schema,
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
    def test_notification_headers_suppress_automatic_replies(self) -> None:
        message = build_notification_message(
            subject="[91] test",
            body="test body",
            username="sender@example.com",
            sender_name="Sender",
            recipient="recipient@example.com",
        )
        self.assertEqual(message["From"], "Sender <sender@example.com>")
        self.assertEqual(message["Auto-Submitted"], "auto-generated")
        self.assertEqual(message["Precedence"], "bulk")
        self.assertEqual(message["X-Auto-Response-Suppress"], "All")

    def test_cost_allocation_prefers_five_multiples_then_fills_remainder(self) -> None:
        plan = plan_withdrawal("cost", 324.5, balances([20, 80, 65, 60, 100, 0, 0]), 320)
        self.assertEqual([item["amount"] for item in plan["items"]], [20, 80, 65, 60, 95, 0, 0])

        rounded = plan_withdrawal("cost", 324.5, balances([20, 80, 65, 60, 100, 0, 0]))
        self.assertEqual([item["amount"] for item in rounded["items"]], [20, 80, 65, 60, 100, 0, 0])

    def test_full_allocation_truncates_each_balance_to_integer(self) -> None:
        plan = plan_withdrawal("full", 324.5, balances([20.9, 81.5, 65.9, 62.8, 105.7, 1.9, 86.2]))
        self.assertEqual([item["amount"] for item in plan["items"]], [20, 81, 65, 62, 105, 1, 86])
        self.assertEqual(plan["items"][-1]["paymentMethod"], "alipay")

    def test_preview_items_have_sequence_chinese_status_and_updated_labels(self) -> None:
        plan = plan_withdrawal("full", 324.5, balances([20, 81, 65, 62, 105, 0.4, 86]))
        self.assertEqual([item["sequence"] for item in plan["items"]], list(range(1, 8)))
        self.assertEqual([item["statusLabel"] for item in plan["items"]], ["待执行"] * 5 + ["已跳过", "待执行"])
        self.assertEqual(
            [item["ownerLabel"] for item in plan["items"]],
            ["自己微信", "自己支付宝", "老弟微信", "老弟支付宝", "老弟支付宝", "社会哥微信", "社会哥支付宝"],
        )
        self.assertEqual([item["owner"] for item in plan["items"][2:5]], ["owner", "owner", "owner"])

    def test_cost_plan_rejects_insufficient_integer_balance(self) -> None:
        with self.assertRaisesRegex(ValueError, "最多 8 元"):
            plan_withdrawal("cost", 10, balances([4.9, 4.9, 0, 0, 0, 0, 0]))

    def test_cost_plan_requires_a_positive_integer_amount(self) -> None:
        with self.assertRaisesRegex(ValueError, "必须大于 0"):
            plan_withdrawal("cost", 324.5, balances([400, 0, 0, 0, 0, 0, 0]), 0)
        with self.assertRaisesRegex(ValueError, "必须为整数"):
            plan_withdrawal("cost", 324.5, balances([400, 0, 0, 0, 0, 0, 0]), 320.5)

    def test_cost_rounding_remainder_belongs_to_owner(self) -> None:
        plan = plan_withdrawal("cost", 324.5, balances([20, 80, 65, 60, 100, 0, 0]))
        settlement = settlement_for(plan)
        self.assertEqual(settlement["gross"], 325.0)
        self.assertEqual(settlement["ownerExpected"], 325.0)
        self.assertEqual(settlement["partnerExpected"], 0.0)
        self.assertEqual(settlement["roundingRemainder"], 0.5)

    def test_full_settlement_uses_sixty_forty_profit_split(self) -> None:
        plan = plan_withdrawal("full", 324.5, balances([20, 81, 65, 62, 105, 1, 86]))
        settlement = settlement_for(plan)
        self.assertEqual(settlement["gross"], 420.0)
        self.assertEqual(settlement["profit"], 95.5)
        self.assertEqual(settlement["ownerExpected"], 381.8)
        self.assertEqual(settlement["partnerExpected"], 38.2)
        self.assertEqual(settlement["partnerToOwner"], 48.8)

    def test_cost_settlement_exposes_unrecovered_manual_amount(self) -> None:
        plan = plan_withdrawal("cost", 324.5, balances([20, 80, 65, 60, 95, 0, 0]), 320)
        settlement = settlement_for(plan)
        self.assertEqual(settlement["costRecovery"], 320.0)
        self.assertEqual(settlement["unrecoveredCost"], 4.5)

    def test_email_templates_name_both_fixed_recipients_and_formula(self) -> None:
        plan = plan_withdrawal("full", 324.5, balances([20, 81, 65, 62, 105, 1, 86]))
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
        self.assertEqual(NOTIFICATION_RECIPIENTS, ("1745627971@qq.com", "252715669@qq.com"))
        self.assertIn("[91] 全部提现任务 #20260802-001", subject)
        self.assertIn("星星应得 = 324.50 + 95.50 × 60% = 381.80 元", body)
        self.assertIn("社会哥需要转给星星：48.80 元", body)
        self.assertIn("成本日期：2026-07-31 00:00:00", body)
        self.assertIn("金额：324.50 元 | 备注：测试成本", body)
        self.assertIn("录入时间：2026-07-31 10:35:25", body)
        self.assertIn("提现后总成本：0.00 元", body)
        self.assertIn("提现后总余额：3.33 元", body)
        self.assertIn("提现后折后利润：3.33 元", body)

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


class BlockingWithdrawalManager:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0
        self.in_flight = 0
        self.max_in_flight = 0

    async def submit_withdrawal(self, target_id: str, amount: int, payment_method: str) -> dict[str, object]:
        self.calls += 1
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        self.started.set()
        await self.release.wait()
        self.in_flight -= 1
        return {"ok": True, "targetId": target_id, "amount": amount, "paymentMethod": payment_method}


class FailingWithdrawalManager:
    async def submit_withdrawal(self, target_id: str, amount: int, payment_method: str) -> dict[str, object]:
        raise RuntimeError("模拟提现失败")


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
        self.service = WithdrawalService(
            connect=self.connect,
            dumps=lambda value: json.dumps(value, ensure_ascii=False),
            loads=lambda value, default: json.loads(value) if value else default,
            utc_now=lambda: datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            parse_time=lambda value: datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None,
            flexible_number=lambda value, default=0: float(value) if value is not None else default,
            get_setting=lambda key, default: {"cost": 1} if key == "stored_state" else default,
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
            clear_cost_additions_if_snapshot=self.ledger.clear_snapshot,
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
        self.assertEqual(len(history["jobs"][0]["items"]), 7)
        self.assertEqual(history["jobs"][0]["items"][0]["statusLabel"], "待执行")
        self.assertNotIn("response", history["jobs"][0]["items"][0])

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
        self.assertEqual(len(emails), 2)
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

    async def test_failed_job_never_clears_cost_history(self) -> None:
        self.manager = FailingWithdrawalManager()
        plan = self.service.latest_plan("cost", 1)
        job = self.service.create_job(plan, self.target_ids)

        await self.service.process_once()
        failed = self.service.job_detail(job["jobId"])
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["costSettlementStatus"], "not_recovered")
        self.assertEqual([item["id"] for item in self.ledger.list()], ["cost-1"])

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


if __name__ == "__main__":
    unittest.main()
