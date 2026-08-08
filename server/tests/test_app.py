import asyncio
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from fastapi.testclient import TestClient

from server import app


OLD_POOL_SCHEMA = """
CREATE TABLE pool_history (
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
"""


def pool_snapshot(timestamp: str, total: int) -> dict:
    return {
        "date": timestamp,
        "groupName": "PLUS共享号池",
        "status": "active",
        "total": total,
        "active": total,
        "schedulable": total,
        "remaining5h": total - 1,
        "remaining7d": total - 2,
        "capacity5h": total,
        "capacity7d": total,
        "remainingCapacity5h": total - 1.25,
        "remainingCapacity7d": total - 2.5,
        "utilization5h": 1.0,
        "utilization7d": 2.0,
        "concurrentAvailable": total,
        "concurrentTotal": total,
        "limited": 0,
        "quotaProtected": 0,
        "error": 0,
        "disabled": 0,
    }


class DatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_data_dir = app.DATA_DIR
        self.previous_db_path = app.DB_PATH
        app.DATA_DIR = Path(self.temp_dir.name)
        app.DB_PATH = app.DATA_DIR / "app.db"

    def tearDown(self) -> None:
        app.DATA_DIR = self.previous_data_dir
        app.DB_PATH = self.previous_db_path
        self.temp_dir.cleanup()

    def test_old_schema_migrates_idempotently(self) -> None:
        with closing(sqlite3.connect(app.DB_PATH)) as connection:
            connection.executescript(OLD_POOL_SCHEMA)
            connection.commit()
        app.init_db()
        app.init_db()
        with app.connect() as connection:
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(pool_history)").fetchall()}
            indexes = {row["name"] for row in connection.execute("PRAGMA index_list(pool_history)").fetchall()}
        self.assertTrue({"capacity_5h", "capacity_7d", "remaining_capacity_5h", "remaining_capacity_7d"} <= columns)
        self.assertIn("idx_pool_history_group_date_id", indexes)

    def test_init_removes_retired_account_from_live_settings(self) -> None:
        app.init_db()
        app.set_setting(
            "balance_accounts",
            [
                {"name": "1745627971@QQ.COM", "baseURL": "https://old.example", "apiKey": "old-secret"},
                {"name": "active@example.com", "baseURL": "https://active.example", "apiKey": "active-secret"},
            ],
        )
        app.set_setting("pool_credentials", {"email": "1745627971@qq.com", "password": "pool-secret"})

        app.init_db()

        self.assertEqual([item["name"] for item in app.get_setting("balance_accounts", [])], ["active@example.com"])
        self.assertEqual(app.get_setting("pool_credentials", {}), {})

    def test_import_records_are_persistent_and_secret_free(self) -> None:
        app.init_db()
        app.save_pixel_import_record(
            {
                "recordId": "import-1",
                "createdAt": "2026-07-30T08:00:00Z",
                "sourceFileName": "accounts.json",
                "sourceCount": 2,
                "targets": [
                    {
                        "targetId": "pixel-1",
                        "email": "pool@example.com",
                        "sourceCount": 2,
                        "created": 2,
                        "generatedNames": ["acct-one@example.com", "acct-two@example.com"],
                        "message": "access_token=should-not-be-stored",
                    }
                ],
            }
        )

        records = app.pixel_import_records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["targets"][0]["generatedNames"], ["acct-one@example.com", "acct-two@example.com"])
        self.assertNotIn("should-not-be-stored", json.dumps(records))

    def test_raw_capacity_is_persisted_and_returned(self) -> None:
        app.init_db()
        app.insert_pool_snapshot(pool_snapshot("2026-07-28T08:00:00Z", 100))
        rows = app.pool_history("PLUS共享号池")
        self.assertEqual(rows[0]["capacity5h"], 100)
        self.assertEqual(rows[0]["remainingCapacity5h"], 98.75)

    def test_single_cost_addition_delete_updates_cost_and_preserves_other_rows(self) -> None:
        app.init_db()
        app.set_meta("initialized", "true")
        app.set_setting("stored_state", {"cost": 35})
        app.insert_cost_addition(
            {
                "id": "cost-delete",
                "date": "2026-08-07T00:00:00Z",
                "note": "要删除",
                "amount": 10,
                "createdAt": "2026-08-07T00:01:00Z",
            }
        )
        app.insert_cost_addition(
            {
                "id": "cost-keep",
                "date": "2026-08-07T00:02:00Z",
                "note": "要保留",
                "amount": 25,
                "createdAt": "2026-08-07T00:03:00Z",
            }
        )
        client = TestClient(app.app)

        deleted = client.delete("/gpt-api/cost-additions/cost-delete")

        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.json()["deletedAmount"], 10)
        self.assertEqual(deleted.json()["state"]["storedState"]["cost"], 25)
        self.assertEqual([item["id"] for item in app.cost_additions()], ["cost-keep"])

        missing = client.delete("/gpt-api/cost-additions/cost-delete")
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(app.get_setting("stored_state", {})["cost"], 25)

    def test_cursor_pages_keep_old_history_available(self) -> None:
        app.init_db()
        app.insert_pool_snapshot(pool_snapshot("2024-01-01T00:00:00Z", 100))
        app.insert_pool_snapshot(pool_snapshot("2025-01-01T00:00:00Z", 99))
        app.insert_pool_snapshot(pool_snapshot("2026-01-01T00:00:00Z", 98))
        first = app.pool_history_page("PLUS共享号池", None, 2)
        second = app.pool_history_page("PLUS共享号池", first["nextCursor"], 2)
        self.assertTrue(first["hasMore"])
        self.assertEqual(len(first["items"]), 2)
        self.assertFalse(second["hasMore"])
        self.assertEqual([item["date"] for item in second["items"]], ["2024-01-01T00:00:00Z"])
        self.assertEqual(len(app.pool_history("PLUS共享号池")), 3)

    def test_dashboard_payload_keeps_precise_window_values(self) -> None:
        summary = {
            "group_name": "PLUS共享号池",
            "group_status": "active",
            "account_count": 100,
            "active_account_count": 90,
            "schedulable_account_count": 80,
            "usage_windows": [
                {"window": "5h", "account_count": 80, "remaining_capacity_percent": 7875, "average_utilization": 1.5625},
                {"window": "7d", "account_count": 80, "remaining_capacity_percent": 7550, "average_utilization": 5.625},
            ],
        }
        result = app.pool_snapshot_from_summary(summary)
        self.assertEqual(result["remaining5h"], 79)
        self.assertEqual(result["capacity5h"], 80)
        self.assertEqual(result["remainingCapacity5h"], 78.75)

    def test_secret_getters_return_metadata_without_stored_secrets(self) -> None:
        app.init_db()
        app.set_meta("initialized", "true")
        app.set_setting(
            "balance_accounts",
            [{"name": "Primary", "baseURL": "https://billing.example", "apiKey": "balance-secret"}],
        )
        app.set_setting("pool_credentials", {"email": "pool@example.com", "password": "pool-secret"})
        app.set_setting(
            "smtp_settings",
            {
                "host": "smtp.example.com",
                "port": 465,
                "username": "mailer@example.com",
                "password": "smtp-secret",
                "senderName": "91 通知",
                "recipient": "ops@example.com",
            },
        )

        client = TestClient(app.app)
        balance_request = client.get("/gpt-api/balance-accounts")
        pool_request = client.get("/gpt-api/pool-credentials")
        smtp_request = client.get("/gpt-api/smtp-settings")
        self.assertEqual(balance_request.status_code, 200)
        self.assertEqual(pool_request.status_code, 200)
        self.assertEqual(smtp_request.status_code, 200)
        balance_response = balance_request.json()
        pool_response = pool_request.json()
        smtp_response = smtp_request.json()
        encoded = json.dumps([balance_response, pool_response, smtp_response])

        self.assertNotIn("balance-secret", encoded)
        self.assertNotIn("pool@example.com", encoded)
        self.assertNotIn("pool-secret", encoded)
        self.assertNotIn("mailer@example.com", encoded)
        self.assertNotIn("smtp-secret", encoded)
        self.assertNotIn("ops@example.com", encoded)
        self.assertEqual(balance_response["accounts"][0]["name"], "Primary")
        self.assertEqual(balance_response["accounts"][0]["baseURL"], "https://billing.example")
        self.assertEqual(balance_response["accounts"][0]["apiKey"], "")
        self.assertTrue(balance_response["accounts"][0]["hasApiKey"])
        self.assertEqual(pool_response["credentials"]["email"], "")
        self.assertEqual(pool_response["credentials"]["password"], "")
        self.assertTrue(pool_response["credentials"]["hasEmail"])
        self.assertTrue(pool_response["credentials"]["hasPassword"])
        self.assertEqual(smtp_response["settings"]["host"], "smtp.example.com")
        self.assertEqual(smtp_response["settings"]["username"], "")
        self.assertEqual(smtp_response["settings"]["password"], "")
        self.assertEqual(smtp_response["settings"]["senderName"], "91 通知")
        self.assertEqual(smtp_response["settings"]["recipient"], "")
        self.assertTrue(smtp_response["settings"]["hasUsername"])
        self.assertTrue(smtp_response["settings"]["hasPassword"])
        self.assertTrue(smtp_response["settings"]["hasRecipient"])

    def test_balance_settings_reject_retired_account_restore(self) -> None:
        app.init_db()
        app.set_meta("initialized", "true")
        client = TestClient(app.app)

        response = client.put(
            "/gpt-api/balance-accounts",
            json={
                "accounts": [
                    {
                        "name": "1745627971@QQ.COM",
                        "baseURL": "https://billing.example",
                        "apiKey": "must-not-be-stored",
                    }
                ]
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(app.get_setting("balance_accounts", []), [])

    def test_blank_secret_updates_preserve_existing_values(self) -> None:
        app.init_db()
        app.set_meta("initialized", "true")
        app.set_setting(
            "balance_accounts",
            [{"name": "Primary", "baseURL": "https://billing.example", "apiKey": "balance-secret"}],
        )
        app.set_setting("pool_credentials", {"email": "old@example.com", "password": "pool-secret"})
        app.set_setting(
            "smtp_settings",
            {
                "host": "smtp.example.com",
                "port": 465,
                "username": "mailer@example.com",
                "password": "smtp-secret",
                "senderName": "Existing Sender",
                "recipient": "old-ops@example.com",
            },
        )

        asyncio.run(
            app.update_balance_accounts(
                {"accounts": [{"name": "Primary", "baseURL": "https://billing.example/", "apiKey": "  "}]}
            )
        )
        asyncio.run(
            app.update_pool_credentials(
                {"credentials": {"email": "", "password": ""}}
            )
        )
        asyncio.run(
            app.update_smtp_settings(
                {
                    "settings": {
                        "host": "smtp2.example.com",
                        "port": 587,
                        "username": "",
                        "password": "",
                        "senderName": "",
                        "recipient": "",
                    }
                }
            )
        )

        self.assertEqual(app.get_setting("balance_accounts", [])[0]["apiKey"], "balance-secret")
        self.assertEqual(app.get_setting("pool_credentials", {})["password"], "pool-secret")
        self.assertEqual(app.get_setting("pool_credentials", {})["email"], "old@example.com")
        self.assertEqual(app.get_setting("smtp_settings", {})["password"], "smtp-secret")
        self.assertEqual(app.get_setting("smtp_settings", {})["username"], "mailer@example.com")
        self.assertEqual(app.get_setting("smtp_settings", {})["senderName"], "Existing Sender")
        self.assertEqual(app.get_setting("smtp_settings", {})["recipient"], "old-ops@example.com")
        self.assertEqual(app.get_setting("smtp_settings", {})["host"], "smtp2.example.com")

    def test_pool_tokens_are_removed_from_compact_and_current_state(self) -> None:
        app.init_db()
        app.set_meta("initialized", "true")
        app.set_setting(
            "pool_state",
            {
                "selectedGroups": ["PLUS共享号池"],
                "accessToken": "top-access-secret",
                "refreshToken": "top-refresh-secret",
                "nested": {
                    "access_token": "nested-access-secret",
                    "refresh_token": "nested-refresh-secret",
                    "kept": True,
                },
            },
        )

        state = app.current_state()
        encoded = json.dumps(state, ensure_ascii=False)
        self.assertNotIn("top-access-secret", encoded)
        self.assertNotIn("top-refresh-secret", encoded)
        self.assertNotIn("nested-access-secret", encoded)
        self.assertNotIn("nested-refresh-secret", encoded)
        self.assertNotIn("accessToken", state["poolState"])
        self.assertNotIn("refreshToken", state["poolState"])
        self.assertEqual(state["poolState"]["nested"], {"kept": True})

    def test_cors_origins_are_explicit(self) -> None:
        required = {
            "tauri://localhost",
            "http://127.0.0.1:1420",
            "http://localhost:1420",
        }
        self.assertNotIn("*", app.ALLOWED_ORIGINS)
        self.assertTrue(required <= set(app.ALLOWED_ORIGINS))
        client = TestClient(app.app)
        allowed = client.options(
            "/gpt-api/health",
            headers={"Origin": "tauri://localhost", "Access-Control-Request-Method": "GET"},
        )
        blocked = client.options(
            "/gpt-api/health",
            headers={"Origin": "https://untrusted.example", "Access-Control-Request-Method": "GET"},
        )
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(allowed.headers.get("access-control-allow-origin"), "tauri://localhost")
        self.assertEqual(blocked.status_code, 400)
        self.assertIsNone(blocked.headers.get("access-control-allow-origin"))


if __name__ == "__main__":
    unittest.main()
