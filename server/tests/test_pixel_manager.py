import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx
from fastapi.testclient import TestClient

from server import app as server_app
from server.pixel_manager import (
    ExportBundle,
    ImportDefaults,
    PixelManager,
    PixelManagerConfig,
    PixelExportJobs,
    PixelImportJobs,
    PixelTarget,
    PixelManagerError,
    PixelValidationError,
    _safe_text,
    build_target_credential_bundle,
    load_config,
    merge_export_payloads,
    parse_credential_bundle,
)


MANAGER_KEY = "test-manager-key-1234567890"


def target(target_id: str = "pixel-1", base_url: str = "https://pixel-one.example") -> PixelTarget:
    return PixelTarget(
        id=target_id,
        email=f"{target_id}@example.com",
        password="fake-password",
        base_url=base_url,
        login_agreement_revision="test-revision",
        import_defaults=ImportDefaults(),
    )


def manager_with_transport(
    handler,
    *targets: PixelTarget,
    delay_seconds: float = 0,
) -> PixelManager:
    configured = targets or (target(),)
    transport = httpx.MockTransport(handler)
    return PixelManager(
        PixelManagerConfig(manager_key=MANAGER_KEY, targets={item.id: item for item in configured}),
        client_factory=lambda: httpx.AsyncClient(transport=transport),
        inter_target_delay_seconds=delay_seconds,
    )


def login_response(access_token: str = "access-one", refresh_token: str = "refresh-one") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "data": {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "expires_in": 86400,
                "token_type": "Bearer",
            }
        },
    )


class PixelConfigAndTransformTests(unittest.TestCase):
    def test_safe_text_redacts_json_and_bearer_tokens(self) -> None:
        samples = (
            r'{\"access_token\":\"json-secret\"}',
            '{"refresh_token":"refresh-secret"}',
            "Authorization: Bearer eyJhbGciOi.secret.signature",
            "password=plain-secret",
        )
        redacted = " ".join(_safe_text(sample) for sample in samples)
        for secret in (
            "json-secret",
            "refresh-secret",
            "eyJhbGciOi.secret.signature",
            "plain-secret",
        ):
            self.assertNotIn(secret, redacted)
        self.assertIn("[redacted]", redacted)

    def test_config_loads_only_from_supplied_secret_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pixel-secret.json"
            path.write_text(
                json.dumps(
                    {
                        "managerKey": MANAGER_KEY,
                        "targets": [
                            {
                                "id": "first",
                                "email": "first@example.com",
                                "password": "fake-secret",
                                "baseUrl": "https://first.example",
                                "loginAgreementRevision": "revision",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            config = load_config(path)

        self.assertEqual(config.targets["first"].email, "first@example.com")
        self.assertEqual(config.targets["first"].password, "fake-secret")
        self.assertTrue(config.allow_open_access)
        self.assertNotIn("fake-secret", repr(config.targets["first"]))
        self.assertNotIn(MANAGER_KEY, repr(config))

    def test_config_can_disable_open_access(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pixel-secret.json"
            path.write_text(
                json.dumps(
                    {
                        "managerKey": MANAGER_KEY,
                        "allowOpenAccess": False,
                        "targets": [
                            {
                                "id": "first",
                                "email": "first@example.com",
                                "password": "fake-secret",
                                "baseUrl": "https://first.example",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            config = load_config(path)

        self.assertFalse(config.allow_open_access)

    def test_json_array_is_parsed_without_mutating_source(self) -> None:
        bundle = parse_credential_bundle(
            "../batch.json",
            json.dumps(
                [
                    {
                        "name": "first@sample.test",
                        "platform": "OpenAI",
                        "type": "OAuth",
                        "chatgpt_field_source": "top-level",
                        "credentials": {
                            "email": "first@sample.test",
                            "plan_type": "free",
                            "nested": {"chatgpt_field_source": "nested"},
                        },
                    },
                    {
                        "name": "second@sample.test",
                        "platform": "openai",
                        "type": "oauth",
                    },
                ]
            ).encode(),
        )
        original = json.loads(json.dumps(bundle.source_payload))
        used = {"first@sample.test", "second@sample.test"}
        first = build_target_credential_bundle(bundle, used)
        second = build_target_credential_bundle(bundle, used)
        first_payload = json.loads(first.contents[0])
        second_payload = json.loads(second.contents[0])

        self.assertEqual(bundle.source_file_name, "batch.json")
        self.assertEqual(bundle.source_count, 2)
        self.assertEqual(bundle.source_payload, original)
        first_names = {item["name"] for item in first_payload["accounts"]}
        second_names = {item["name"] for item in second_payload["accounts"]}
        self.assertFalse(first_names & second_names)
        self.assertTrue(all(name.endswith("@sample.test") for name in first_names | second_names))
        self.assertTrue(all(item["credentials"]["plan_type"] == "plus" for item in first_payload["accounts"]))
        self.assertEqual(first_payload["accounts"][0]["credentials"]["email"], first_payload["accounts"][0]["name"])
        self.assertNotIn("email", first_payload["accounts"][1]["credentials"])
        self.assertNotIn("chatgpt_field_source", json.dumps(first_payload))
        self.assertNotEqual(first.generated_file_name, second.generated_file_name)

    def test_target_bundle_splits_every_one_hundred_accounts(self) -> None:
        bundle = parse_credential_bundle(
            "batch.json",
            json.dumps({"accounts": [{"credentials": {"email": f"user-{index}@example.com"}} for index in range(205)]}).encode(),
        )
        prepared = build_target_credential_bundle(bundle, set())
        self.assertEqual(prepared.chunk_sizes, (100, 100, 5))
        self.assertEqual([len(json.loads(item)["accounts"]) for item in prepared.contents], [100, 100, 5])

    def test_target_bundle_removes_chatgpt_field_source_recursively(self) -> None:
        bundle = parse_credential_bundle(
            "batch.json",
            json.dumps(
                {
                    "chatgpt_field_source": "root-source",
                    "accounts": [
                        {
                            "name": "oauth@example.com",
                            "platform": "openai",
                            "type": "oauth",
                            "credentials": {
                                "email": "oauth@example.com",
                                "chatgpt_field_source": "credential-source",
                                "nested": {
                                    "chatgpt_field_source": "nested-source",
                                    "kept": "yes",
                                },
                            },
                            "extra": {
                                "values": [
                                    {"chatgpt_field_source": "list-source", "kept": 1}
                                ]
                            },
                        }
                    ],
                }
            ).encode(),
        )
        source_before = json.loads(json.dumps(bundle.source_payload))

        prepared = build_target_credential_bundle(bundle, set())
        payload = json.loads(prepared.contents[0])
        encoded = json.dumps(payload)
        account = payload["accounts"][0]

        self.assertNotIn("chatgpt_field_source", encoded)
        self.assertEqual(account["credentials"]["plan_type"], "plus")
        self.assertEqual(account["credentials"]["nested"]["kept"], "yes")
        self.assertEqual(account["extra"]["values"][0]["kept"], 1)
        self.assertEqual(bundle.source_payload, source_before)

    def test_export_merge_deduplicates_by_any_jwt_signature(self) -> None:
        first = {
            "proxies": [],
            "accounts": [
                {"name": "one", "credentials": {"access_token": "a.b.same-signature"}},
                {"name": "unsigned-one", "credentials": {"refresh_token": "opaque"}},
            ],
        }
        second = {
            "proxies": [],
            "accounts": [
                {"name": "duplicate", "credentials": {"id_token": "x.y.same-signature"}},
                {"name": "unsigned-two", "credentials": {"refresh_token": "opaque"}},
            ],
        }
        merged, source_count, duplicate_count = merge_export_payloads([first, second])
        self.assertEqual(source_count, 4)
        self.assertEqual(duplicate_count, 1)
        self.assertEqual([item["name"] for item in merged["accounts"]], ["one", "unsigned-one", "unsigned-two"])

    def test_invalid_bundle_is_rejected(self) -> None:
        with self.assertRaises(PixelValidationError):
            parse_credential_bundle("batch.txt", b"{}")
        with self.assertRaises(PixelValidationError):
            parse_credential_bundle("batch.json", b"not-json")


class PixelManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_accounts_are_paginated_sanitized_and_use_cached_token(self) -> None:
        calls = {"login": 0, "accounts": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v1/auth/login":
                calls["login"] += 1
                return login_response()
            if request.url.path == "/api/v1/accounts":
                calls["accounts"] += 1
                self.assertEqual(request.headers["authorization"], "Bearer access-one")
                self.assertEqual(request.url.params["page_size"], "20")
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "items": [
                                {
                                    "id": 12,
                                    "name": "Account 12",
                                    "platform": "openai",
                                    "account_level": "plus",
                                    "type": "oauth",
                                    "share_mode": "public",
                                    "share_status": "approved",
                                    "concurrency": 3,
                                    "current_concurrency": 1,
                                    "priority": 1,
                                    "status": "active",
                                    "schedulable": True,
                                    "credentials_status": "valid",
                                    "error_since": "2026-07-29T05:00:00Z",
                                    "codex_quota_protection_reason": "5h quota exhausted",
                                    "codex_quota_protection_reset_at": "2026-07-29T10:00:00Z",
                                    "credentials": {"access_token": "must-not-leak"},
                                    "access_token": "must-not-leak-either",
                                    "codex_5h_limit_percent": 75.5,
                                    "groups": [{"id": 2, "name": "PLUS"}],
                                }
                            ],
                            "page": 1,
                            "page_size": 20,
                            "pages": 1,
                            "total": 1,
                        }
                    },
                )
            raise AssertionError(f"unexpected request: {request.method} {request.url}")

        manager = manager_with_transport(handler)
        first = await manager.list_accounts("pixel-1", 1, 20)
        second = await manager.list_accounts("pixel-1", 1, 20)

        self.assertEqual(calls, {"login": 1, "accounts": 2})
        self.assertEqual(first["items"][0]["accountLevel"], "plus")
        self.assertEqual(first["items"][0]["codex5hLimitPercent"], 75.5)
        self.assertEqual(first["items"][0]["errorSince"], "2026-07-29T05:00:00Z")
        self.assertEqual(
            first["items"][0]["codexQuotaProtectionReason"],
            "5h quota exhausted",
        )
        self.assertEqual(
            first["items"][0]["codexQuotaProtectionResetAt"],
            "2026-07-29T10:00:00Z",
        )
        self.assertEqual(first["target"]["accountCount"], 1)
        encoded = json.dumps([first, second])
        self.assertNotIn("must-not-leak", encoded)
        self.assertNotIn("access_token", encoded)

    async def test_account_search_and_status_are_normalized_and_forwarded(self) -> None:
        account_queries: list[dict[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v1/auth/login":
                return login_response()
            if request.url.path == "/api/v1/accounts":
                account_queries.append(dict(request.url.params))
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "items": [],
                            "page": 2,
                            "page_size": 40,
                            "pages": 0,
                            "total": 0,
                        }
                    },
                )
            raise AssertionError(f"unexpected request: {request.method} {request.url}")

        manager = manager_with_transport(handler)
        result = await manager.list_accounts(
            "pixel-1",
            2,
            40,
            search="  account@example.com  ",
            status=" RATE_LIMITED ",
        )

        self.assertEqual(result["page"], 2)
        self.assertIsNone(result["target"]["accountCount"])
        self.assertEqual(
            account_queries,
            [
                {
                    "page": "2",
                    "page_size": "40",
                    "sort_by": "created_at",
                    "sort_order": "desc",
                    "timezone": "Asia/Shanghai",
                    "search": "account@example.com",
                    "status": "rate_limited",
                }
            ],
        )

        with self.assertRaises(PixelValidationError):
            await manager.list_accounts("pixel-1", 1, 20, status="disabled")
        self.assertEqual(len(account_queries), 1)

    async def test_401_uses_refresh_token_then_retries(self) -> None:
        calls = {"login": 0, "refresh": 0, "old": 0, "new": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v1/auth/login":
                calls["login"] += 1
                return login_response("old-access", "refresh-one")
            if request.url.path == "/api/v1/auth/refresh":
                calls["refresh"] += 1
                self.assertEqual(json.loads(request.content), {"refresh_token": "refresh-one"})
                return login_response("new-access", "refresh-two")
            if request.url.path == "/api/v1/accounts":
                if request.headers["authorization"] == "Bearer old-access":
                    calls["old"] += 1
                    return httpx.Response(401)
                calls["new"] += 1
                return httpx.Response(
                    200,
                    json={"data": {"items": [], "page": 1, "page_size": 20, "pages": 0, "total": 0}},
                )
            raise AssertionError(f"unexpected request: {request.url}")

        manager = manager_with_transport(handler)
        result = await manager.list_accounts("pixel-1", 1, 20)
        self.assertEqual(result["total"], 0)
        self.assertEqual(calls, {"login": 1, "refresh": 1, "old": 1, "new": 1})

    async def test_failed_refresh_falls_back_to_relogin(self) -> None:
        login_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal login_count
            if request.url.path == "/api/v1/auth/login":
                login_count += 1
                return login_response(f"access-{login_count}", "refresh-one")
            if request.url.path == "/api/v1/auth/refresh":
                return httpx.Response(404)
            if request.url.path == "/api/v1/accounts":
                if request.headers["authorization"] == "Bearer access-1":
                    return httpx.Response(401)
                return httpx.Response(
                    200,
                    json={"data": {"items": [], "page": 1, "page_size": 20, "pages": 0, "total": 0}},
                )
            raise AssertionError(f"unexpected request: {request.url}")

        manager = manager_with_transport(handler)
        await manager.list_accounts("pixel-1", 1, 20)
        self.assertEqual(login_count, 2)

    async def test_tokens_are_cached_independently_per_target(self) -> None:
        logins: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            host = request.url.host
            if request.url.path == "/api/v1/auth/login":
                logins.append(host)
                return login_response(f"access-{host}", f"refresh-{host}")
            if request.url.path == "/api/v1/accounts":
                self.assertEqual(request.headers["authorization"], f"Bearer access-{host}")
                return httpx.Response(
                    200,
                    json={"data": {"items": [], "page": 1, "page_size": 20, "pages": 0, "total": 0}},
                )
            raise AssertionError(f"unexpected request: {request.url}")

        manager = manager_with_transport(
            handler,
            target("one", "https://one.example"),
            target("two", "https://two.example"),
        )
        await manager.list_accounts("one", 1, 20)
        await manager.list_accounts("two", 1, 20)
        await manager.list_accounts("one", 1, 20)
        self.assertEqual(logins, ["one.example", "two.example"])

    async def test_import_converts_only_new_ids_to_public_pool(self) -> None:
        account_reads = 0
        captured_import: dict = {}
        captured_share: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal account_reads, captured_import, captured_share
            if request.url.path == "/api/v1/auth/login":
                return login_response()
            if request.url.path == "/api/v1/accounts" and request.method == "GET":
                account_reads += 1
                items = [{"id": 1, "name": "existing@example.com"}]
                if account_reads > 1:
                    imported_accounts = json.loads(captured_import["contents"][0])["accounts"]
                    items.extend(
                        {"id": index + 2, "name": account["name"]}
                        for index, account in enumerate(imported_accounts)
                    )
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "items": items,
                            "page": 1,
                            "page_size": 100,
                            "pages": 1,
                            "total": len(items),
                        }
                    },
                )
            if request.url.path == "/api/v1/accounts/import-credentials":
                captured_import = json.loads(request.content)
                return httpx.Response(
                    200,
                    json={"data": {"total": 2, "created": 2, "updated": 0, "failed": 0, "errors": []}},
                )
            if request.url.path == "/api/v1/accounts/external-placement:convert-batch":
                captured_share = json.loads(request.content)
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "success": 1,
                            "failed": 1,
                            "success_ids": [2],
                            "failed_ids": [3],
                            "results": [],
                        }
                    },
                )
            raise AssertionError(f"unexpected request: {request.method} {request.url}")

        manager = manager_with_transport(handler)
        bundle = parse_credential_bundle(
            "source.json",
            json.dumps(
                [
                    {"credentials": {"access_token": "fake-one", "plan_type": "free"}},
                    {"credentials": {"access_token": "fake-two", "plan_type": "team"}},
                ]
            ).encode(),
        )
        response = await manager.import_bundle(bundle, ["pixel-1"])
        result = response["results"][0]

        self.assertEqual(captured_import["platform"], "openai")
        self.assertEqual(captured_import["share_mode"], "private")
        self.assertEqual(captured_import["account_level"], "plus")
        self.assertEqual(captured_import["concurrency"], 3)
        imported_payload = json.loads(captured_import["contents"][0])
        self.assertEqual(len(imported_payload["accounts"]), 2)
        self.assertTrue(all(item["credentials"]["plan_type"] == "plus" for item in imported_payload["accounts"]))
        self.assertTrue(all(item["name"].startswith("acct-") for item in imported_payload["accounts"]))
        self.assertEqual(captured_share["account_ids"], [2, 3])
        self.assertEqual(captured_share["target"], "public_pool")
        self.assertTrue(captured_share["idempotency_key"])
        self.assertEqual(result["shared"], 1)
        self.assertEqual(result["shareFailed"], 1)
        self.assertEqual(result["failedShareIds"], [3])
        self.assertEqual(result["status"], "partial")
        self.assertNotIn("fake-one", json.dumps(response))

    async def test_share_rejects_empty_ids(self) -> None:
        manager = manager_with_transport(lambda _: login_response())
        with self.assertRaises(PixelValidationError):
            await manager.share_accounts("pixel-1", [])

    async def test_bulk_delete_deduplicates_ids_and_filters_platform_results(self) -> None:
        captured_payloads: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v1/auth/login":
                return login_response()
            if request.url.path == "/api/v1/accounts/bulk-delete":
                captured_payloads.append(json.loads(request.content))
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "success": 2,
                            "failed": 2,
                            "success_ids": [11, 999],
                            "failed_ids": [12, 998],
                            "results": [
                                {"account_id": 11, "success": True},
                                {
                                    "account_id": 12,
                                    "success": False,
                                    "error": "password=platform-delete-secret",
                                },
                                {"account_id": 999, "success": True},
                            ],
                            "credentials": {"access_token": "delete-response-secret"},
                        }
                    },
                )
            raise AssertionError(f"unexpected request: {request.method} {request.url}")

        manager = manager_with_transport(handler)
        result = await manager.bulk_delete_accounts("pixel-1", [11, 11, 0, -1, 12])

        self.assertEqual(captured_payloads, [{"account_ids": [11, 12]}])
        self.assertEqual(result["successIds"], [11])
        self.assertEqual(result["failedIds"], [12])
        self.assertEqual(result["success"], 1)
        self.assertEqual(result["failed"], 1)
        encoded = json.dumps(result)
        self.assertNotIn("platform-delete-secret", encoded)
        self.assertNotIn("delete-response-secret", encoded)
        self.assertNotIn("credentials", encoded)
        self.assertNotIn("999", encoded)
        self.assertNotIn("998", encoded)

        with self.assertRaises(PixelValidationError):
            await manager.bulk_delete_accounts("pixel-1", [0, -1])
        with self.assertRaises(PixelValidationError):
            await manager.bulk_delete_accounts("pixel-1", range(1, 102))

    async def test_bulk_test_calls_each_requested_id_once_and_sanitizes_results(self) -> None:
        tested_paths: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v1/auth/login":
                return login_response()
            if request.url.path in {"/api/v1/accounts/21/test", "/api/v1/accounts/22/test"}:
                tested_paths.append(request.url.path)
                account_id = int(request.url.path.split("/")[-2])
                if account_id == 21:
                    return httpx.Response(
                        200,
                        json={
                            "data": {
                                "success": True,
                                "message": "Connection successful",
                                "account_id": 999,
                                "access_token": "test-success-secret",
                            }
                        },
                    )
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "success": False,
                            "message": "authorization=platform-test-secret",
                            "account_id": 998,
                            "credentials": {"refresh_token": "test-failure-secret"},
                        }
                    },
                )
            raise AssertionError(f"unexpected request: {request.method} {request.url}")

        manager = manager_with_transport(handler)
        result = await manager.bulk_test_accounts("pixel-1", [21, 21, 22])

        self.assertEqual(tested_paths, ["/api/v1/accounts/21/test", "/api/v1/accounts/22/test"])
        self.assertEqual([item["accountId"] for item in result["results"]], [21, 22])
        self.assertEqual([item["success"] for item in result["results"]], [True, False])
        self.assertEqual(result["success"], 1)
        self.assertEqual(result["failed"], 1)
        encoded = json.dumps(result)
        self.assertNotIn("platform-test-secret", encoded)
        self.assertNotIn("test-success-secret", encoded)
        self.assertNotIn("test-failure-secret", encoded)
        self.assertNotIn("credentials", encoded)
        self.assertNotIn("999", encoded)
        self.assertNotIn("998", encoded)

    async def test_bulk_update_sends_only_public_mode_and_concurrency(self) -> None:
        captured_payloads: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v1/auth/login":
                return login_response()
            if request.url.path == "/api/v1/accounts/bulk-update":
                captured_payloads.append(json.loads(request.content))
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "success": 2,
                            "failed": 0,
                            "success_ids": [31, 32, 999],
                            "failed_ids": [998],
                            "results": [
                                {"account_id": 31, "success": True},
                                {"account_id": 32, "success": True},
                                {
                                    "account_id": 999,
                                    "success": True,
                                    "credentials": {"access_token": "update-result-secret"},
                                },
                            ],
                        }
                    },
                )
            raise AssertionError(f"unexpected request: {request.method} {request.url}")

        manager = manager_with_transport(handler)
        result = await manager.bulk_update_accounts(
            "pixel-1",
            [31, 31, 32],
            share_mode="public",
            concurrency=4,
        )

        self.assertEqual(
            captured_payloads,
            [{"account_ids": [31, 32], "share_mode": "public", "concurrency": 4}],
        )
        self.assertEqual(result["successIds"], [31, 32])
        self.assertEqual(result["failedIds"], [])
        self.assertEqual(result["success"], 2)
        self.assertEqual(result["failed"], 0)
        encoded = json.dumps(result)
        self.assertNotIn("update-result-secret", encoded)
        self.assertNotIn("999", encoded)
        self.assertNotIn("998", encoded)

        with self.assertRaises(PixelValidationError):
            await manager.bulk_update_accounts("pixel-1", [31])
        with self.assertRaises(PixelValidationError):
            await manager.bulk_update_accounts("pixel-1", [31], share_mode="private")
        with self.assertRaises(PixelValidationError):
            await manager.bulk_update_accounts("pixel-1", [31], concurrency=0)

    async def test_targets_are_imported_sequentially_with_configured_delay(self) -> None:
        delays: list[float] = []

        async def sleeper(seconds: float) -> None:
            delays.append(seconds)

        manager = PixelManager(
            PixelManagerConfig(
                manager_key=MANAGER_KEY,
                targets={"one": target("one"), "two": target("two")},
            ),
            inter_target_delay_seconds=30,
            sleeper=sleeper,
        )
        call_order: list[str] = []

        async def fake_import(item: PixelTarget, _bundle) -> dict:
            call_order.append(item.id)
            return {
                "targetId": item.id,
                "email": item.email,
                "generatedFileName": f"{item.id}.json",
                "sourceCount": 1,
                "created": 1,
                "updated": 0,
                "failed": 0,
                "shared": 1,
                "shareFailed": 0,
                "failedShareIds": [],
                "status": "success",
                "message": "ok",
            }

        manager._import_target = fake_import
        progress: list[dict] = []
        bundle = parse_credential_bundle("source.json", json.dumps([{"credentials": {"email": "a@example.com"}}]).encode())
        result = await manager.import_bundle(bundle, ["one", "two"], progress.append)
        self.assertEqual(call_order, ["one", "two"])
        self.assertEqual(delays, [30])
        self.assertEqual([item["phase"] for item in progress], ["processing", "waiting", "processing"])
        self.assertEqual(len(result["results"]), 2)

    async def test_export_all_combines_target_payloads(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v1/auth/login":
                return login_response(f"access-{request.url.host}", f"refresh-{request.url.host}")
            if request.url.path == "/api/v1/accounts/data":
                suffix = "same" if request.url.host == "one.example" else "same"
                accounts = [{"name": request.url.host, "credentials": {"access_token": f"a.b.{suffix}"}}]
                if request.url.host == "two.example":
                    accounts.append({"name": "unique", "credentials": {"access_token": "a.b.unique"}})
                return httpx.Response(200, json={"data": {"exported_at": "x", "proxies": [], "accounts": accounts}})
            raise AssertionError(f"unexpected request: {request.url}")

        manager = manager_with_transport(
            handler,
            target("one", "https://one.example"),
            target("two", "https://two.example"),
        )
        exported = await manager.export_all()
        payload = json.loads(exported.content)
        self.assertEqual(exported.source_count, 3)
        self.assertEqual(exported.deduplicated_count, 2)
        self.assertEqual(exported.duplicate_count, 1)
        self.assertEqual(exported.batch_count, 1)
        self.assertEqual(len(payload["accounts"]), 2)
        self.assertEqual([batch["count"] for batch in payload["account_batches"]], [2])

    async def test_export_all_groups_accounts_in_batches_of_100(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v1/auth/login":
                return login_response("access", "refresh")
            if request.url.path == "/api/v1/accounts/data":
                accounts = [
                    {"name": f"account-{index}", "credentials": {"access_token": f"a.b.sig{index}"}}
                    for index in range(205)
                ]
                return httpx.Response(200, json={"data": {"proxies": [], "accounts": accounts}})
            raise AssertionError(f"unexpected request: {request.url}")

        manager = manager_with_transport(handler, target("one", "https://one.example"))
        exported = await manager.export_all()
        payload = json.loads(exported.content)
        self.assertEqual(exported.deduplicated_count, 205)
        self.assertEqual(exported.batch_count, 3)
        self.assertEqual([batch["count"] for batch in payload["account_batches"]], [100, 100, 5])
        self.assertTrue(all(len(batch["accounts"]) <= 100 for batch in payload["account_batches"]))

    async def test_background_job_reports_completion(self) -> None:
        manager = PixelManager(
            PixelManagerConfig(manager_key=MANAGER_KEY, targets={"one": target("one")}),
            inter_target_delay_seconds=0,
        )

        async def fake_import(_bundle, target_ids, progress_callback=None):
            if progress_callback:
                await progress_callback({"phase": "processing", "currentTargetId": target_ids[0], "completedTargets": 0, "totalTargets": 1, "results": []})
            return {"ok": True, "sourceFileName": "source.json", "sourceCount": 1, "results": []}

        manager.import_bundle = AsyncMock(side_effect=fake_import)
        jobs = PixelImportJobs(manager)
        bundle = parse_credential_bundle("source.json", json.dumps([{"credentials": {"email": "a@example.com"}}]).encode())
        created = await jobs.create(bundle, ["one"])
        await jobs._tasks[created["jobId"]]
        finished = jobs.get(created["jobId"])
        self.assertEqual(finished["status"], "completed")
        self.assertEqual(finished["completedTargets"], 1)

    async def test_export_rebuild_stops_before_delete_when_export_fails(self) -> None:
        manager = PixelManager(
            PixelManagerConfig(manager_key=MANAGER_KEY, targets={"one": target("one")}),
            inter_target_delay_seconds=0,
        )
        manager.export_all = AsyncMock(side_effect=PixelManagerError("导出失败"))
        manager.delete_all_target_accounts = AsyncMock()
        manager.import_bundle = AsyncMock()
        with tempfile.TemporaryDirectory() as directory:
            jobs = PixelExportJobs(manager, Path(directory))
            created = await jobs.create_rebuild(["one"])
            await jobs._tasks[created["jobId"]]
            finished = jobs.get(created["jobId"])

        self.assertEqual(finished["status"], "failed")
        self.assertEqual(finished["error"], "导出失败")
        manager.delete_all_target_accounts.assert_not_awaited()
        manager.import_bundle.assert_not_awaited()

    async def test_export_rebuild_saves_backup_before_delete_and_import(self) -> None:
        manager = PixelManager(
            PixelManagerConfig(manager_key=MANAGER_KEY, targets={"one": target("one")}),
            inter_target_delay_seconds=0,
        )
        content = json.dumps(
            {"accounts": [{"name": "a@example.com", "credentials": {"access_token": "a.b.c"}}]}
        ).encode()
        manager.export_all = AsyncMock(
            return_value=ExportBundle(
                content=content,
                source_count=1,
                deduplicated_count=1,
                duplicate_count=0,
                batch_count=1,
            )
        )
        manager.delete_all_target_accounts = AsyncMock(
            return_value=[
                {
                    "targetId": "one",
                    "email": "one@example.com",
                    "total": 1,
                    "deleted": 1,
                    "failed": 0,
                    "failedIds": [],
                    "status": "success",
                    "message": "已清空平台账号",
                }
            ]
        )
        manager.import_bundle = AsyncMock(
            return_value={"ok": True, "sourceFileName": "backup.json", "sourceCount": 1, "results": []}
        )
        with tempfile.TemporaryDirectory() as directory:
            jobs = PixelExportJobs(manager, Path(directory))
            created = await jobs.create_rebuild(["one"])
            await jobs._tasks[created["jobId"]]
            finished = jobs.get(created["jobId"])
            backup_path = Path(directory) / finished["backupFileName"]
            self.assertTrue(backup_path.exists())
            self.assertEqual(backup_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(backup_path.read_bytes(), content)

        self.assertEqual(finished["status"], "completed")
        manager.delete_all_target_accounts.assert_awaited_once()
        manager.import_bundle.assert_awaited_once()

    async def test_background_jobs_retain_only_the_latest_fifty(self) -> None:
        manager = PixelManager(
            PixelManagerConfig(manager_key=MANAGER_KEY, targets={"one": target("one")}),
            inter_target_delay_seconds=0,
        )

        async def fake_import(_bundle, _target_ids, _progress_callback=None):
            return {
                "ok": True,
                "sourceFileName": "source.json",
                "sourceCount": 1,
                "results": [],
            }

        manager.import_bundle = AsyncMock(side_effect=fake_import)
        jobs = PixelImportJobs(manager)
        bundle = parse_credential_bundle(
            "source.json",
            json.dumps([{"credentials": {"email": "a@example.com"}}]).encode(),
        )
        created_ids: list[str] = []
        for _ in range(52):
            created = await jobs.create(bundle, ["one"])
            created_ids.append(created["jobId"])
            await jobs._tasks[created["jobId"]]

        self.assertEqual(len(jobs._jobs), 50)
        self.assertEqual(len(jobs._tasks), 50)
        self.assertNotIn(created_ids[0], jobs._jobs)
        self.assertNotIn(created_ids[1], jobs._jobs)
        self.assertIn(created_ids[-1], jobs._jobs)


class PixelManagerEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_manager = server_app.pixel_manager
        self.previous_jobs = server_app.pixel_import_jobs
        self.manager = manager_with_transport(self._upstream_handler)
        server_app.pixel_manager = self.manager
        server_app.pixel_import_jobs = None
        self.client = TestClient(server_app.app)

    def tearDown(self) -> None:
        self.client.close()
        server_app.pixel_manager = self.previous_manager
        server_app.pixel_import_jobs = self.previous_jobs

    @staticmethod
    def _upstream_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/auth/login":
            return login_response("endpoint-access-secret", "endpoint-refresh-secret")
        if request.url.path == "/api/v1/accounts":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "items": [
                            {
                                "id": 7,
                                "name": "Visible account",
                                "platform": "openai",
                                "account_level": "plus",
                                "status": "active",
                                "schedulable": True,
                                "credentials": {
                                    "access_token": "upstream-account-secret",
                                    "password": "upstream-password-secret",
                                },
                                "extra": {"refresh_token": "upstream-extra-secret"},
                            }
                        ],
                        "page": 1,
                        "page_size": 20,
                        "pages": 1,
                        "total": 1,
                    }
                },
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    @staticmethod
    def _headers(key: str = MANAGER_KEY) -> dict[str, str]:
        return {"X-91-Manager-Key": key}

    def test_manager_endpoints_allow_missing_key_by_default(self) -> None:
        response = self.client.get("/gpt-api/pixel-manager/targets")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["targets"]), 1)

    def test_manager_endpoints_reject_missing_and_wrong_keys_when_open_access_disabled(self) -> None:
        self.manager.config = PixelManagerConfig(
            manager_key=MANAGER_KEY,
            targets=self.manager.config.targets,
            allow_open_access=False,
        )
        paths = [
            "/gpt-api/pixel-manager/targets",
            "/gpt-api/pixel-manager/targets/pixel-1/accounts",
            "/gpt-api/pixel-manager/export",
            "/gpt-api/pixel-manager/import-jobs/job-1",
        ]
        for path in paths:
            with self.subTest(path=path, key="missing"):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json(), {"detail": "账号池管理认证失败"})
            with self.subTest(path=path, key="wrong"):
                response = self.client.get(path, headers=self._headers("wrong-manager-key"))
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json(), {"detail": "账号池管理认证失败"})

    def test_bulk_endpoints_forward_validated_options_without_key_by_default(self) -> None:
        operations = [
            ("bulk-delete", {"accountIds": [41, 41, 42]}),
            ("bulk-test", {"accountIds": [41, 41, 42]}),
            (
                "bulk-update",
                {"accountIds": [41, 41, 42], "shareMode": "public", "concurrency": 6},
            ),
        ]
        self.manager.bulk_delete_accounts = AsyncMock(
            return_value={
                "ok": True,
                "success": 2,
                "failed": 0,
                "successIds": [41, 42],
                "failedIds": [],
            }
        )
        self.manager.bulk_test_accounts = AsyncMock(
            return_value={
                "ok": True,
                "success": 2,
                "failed": 0,
                "results": [
                    {"accountId": 41, "success": True, "message": "ok"},
                    {"accountId": 42, "success": True, "message": "ok"},
                ],
            }
        )
        self.manager.bulk_update_accounts = AsyncMock(
            return_value={
                "ok": True,
                "success": 2,
                "failed": 0,
                "successIds": [41, 42],
                "failedIds": [],
            }
        )

        delete_response = self.client.post(
            "/gpt-api/pixel-manager/targets/pixel-1/accounts/bulk-delete",
            json=operations[0][1],
        )
        test_response = self.client.post(
            "/gpt-api/pixel-manager/targets/pixel-1/accounts/bulk-test",
            json=operations[1][1],
        )
        update_response = self.client.post(
            "/gpt-api/pixel-manager/targets/pixel-1/accounts/bulk-update",
            json=operations[2][1],
        )

        self.assertEqual(delete_response.status_code, 200)
        self.assertEqual(test_response.status_code, 200)
        self.assertEqual(update_response.status_code, 200)
        self.manager.bulk_delete_accounts.assert_awaited_once_with(
            "pixel-1", [41, 41, 42]
        )
        self.manager.bulk_test_accounts.assert_awaited_once_with(
            "pixel-1", [41, 41, 42]
        )
        self.manager.bulk_update_accounts.assert_awaited_once_with(
            "pixel-1",
            [41, 41, 42],
            share_mode="public",
            concurrency=6,
        )

    def test_target_and_account_responses_expose_only_public_fields(self) -> None:
        targets_response = self.client.get(
            "/gpt-api/pixel-manager/targets",
            headers=self._headers(),
        )
        self.assertEqual(targets_response.status_code, 200)
        target_payload = targets_response.json()["targets"][0]
        self.assertEqual(
            set(target_payload),
            {"id", "email", "connected", "accountCount", "lastCheckedAt", "error"},
        )

        accounts_response = self.client.get(
            "/gpt-api/pixel-manager/targets/pixel-1/accounts?page=1&pageSize=20",
            headers=self._headers(),
        )
        self.assertEqual(accounts_response.status_code, 200)
        payload = accounts_response.json()
        account = payload["items"][0]
        self.assertEqual(account["name"], "Visible account")
        self.assertEqual(account["accountLevel"], "plus")
        self.assertNotIn("credentials", account)
        self.assertNotIn("extra", account)

        encoded = json.dumps([targets_response.json(), payload])
        for secret in (
            MANAGER_KEY,
            "fake-password",
            "https://pixel-one.example",
            "test-revision",
            "endpoint-access-secret",
            "endpoint-refresh-secret",
            "upstream-account-secret",
            "upstream-password-secret",
            "upstream-extra-secret",
        ):
            self.assertNotIn(secret, encoded)

    def test_account_route_validates_and_forwards_search_and_status(self) -> None:
        invalid_status = self.client.get(
            "/gpt-api/pixel-manager/targets/pixel-1/accounts",
            headers=self._headers(),
            params={"status": "disabled"},
        )
        self.assertEqual(invalid_status.status_code, 400)
        self.assertEqual(invalid_status.json()["detail"], "账号状态筛选无效")

        overlong_search = self.client.get(
            "/gpt-api/pixel-manager/targets/pixel-1/accounts",
            headers=self._headers(),
            params={"search": "x" * 121},
        )
        self.assertEqual(overlong_search.status_code, 422)

        self.manager.list_accounts = AsyncMock(
            return_value={
                "items": [],
                "total": 0,
                "page": 3,
                "pageSize": 40,
                "pages": 0,
                "target": self.manager.targets()[0],
            }
        )
        response = self.client.get(
            "/gpt-api/pixel-manager/targets/pixel-1/accounts",
            headers=self._headers(),
            params={
                "page": 3,
                "pageSize": 40,
                "search": "  account@example.com  ",
                "status": "error",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.manager.list_accounts.assert_awaited_once_with(
            "pixel-1",
            3,
            40,
            search="account@example.com",
            status="error",
        )

    def test_import_endpoint_validates_target_json_file_type_and_size(self) -> None:
        invalid_targets = self.client.post(
            "/gpt-api/pixel-manager/import",
            headers=self._headers(),
            params={"targetIds": "not-json", "fileName": "accounts.json"},
            content=b'{"accounts":[{}]}',
        )
        self.assertEqual(invalid_targets.status_code, 400)
        self.assertEqual(invalid_targets.json()["detail"], "上传账号选择无效")

        invalid_type = self.client.post(
            "/gpt-api/pixel-manager/import",
            headers=self._headers(),
            params={"targetIds": '["pixel-1"]', "fileName": "accounts.txt"},
            content=b'{"accounts":[{}]}',
        )
        self.assertEqual(invalid_type.status_code, 400)
        self.assertEqual(invalid_type.json()["detail"], "只支持 JSON 文件")

        empty_accounts = self.client.post(
            "/gpt-api/pixel-manager/import",
            headers=self._headers(),
            params={"targetIds": '["pixel-1"]', "fileName": "accounts.json"},
            content=b'{"accounts":[]}',
        )
        self.assertEqual(empty_accounts.status_code, 400)

        oversized_contents = b'{"accounts":[' + (b" " * 256)
        with (
            patch.object(server_app, "MAX_UPLOAD_BYTES", 128),
            patch("server.pixel_manager.MAX_UPLOAD_BYTES", 128),
        ):
            oversized = self.client.post(
                "/gpt-api/pixel-manager/import",
                headers=self._headers(),
                params={"targetIds": '["pixel-1"]', "fileName": "accounts.json"},
                content=oversized_contents,
            )
        self.assertEqual(oversized.status_code, 413)
        self.assertEqual(oversized.json()["detail"], "JSON 文件不能超过 50 MB")

        def chunked_payload():
            yield b'{"accounts":['
            yield b" " * 256

        with (
            patch.object(server_app, "MAX_UPLOAD_BYTES", 128),
            patch("server.pixel_manager.MAX_UPLOAD_BYTES", 128),
        ):
            chunked = self.client.post(
                "/gpt-api/pixel-manager/import",
                headers={**self._headers(), "Transfer-Encoding": "chunked"},
                params={"targetIds": '["pixel-1"]', "fileName": "accounts.json"},
                content=chunked_payload(),
            )
        self.assertEqual(chunked.status_code, 413)

    def test_import_creation_and_job_polling_return_no_uploaded_secrets(self) -> None:
        created_job = {
            "jobId": "job-123",
            "status": "queued",
            "phase": "queued",
            "sourceFileName": "accounts.json",
            "sourceCount": 1,
            "totalTargets": 1,
            "completedTargets": 0,
            "currentTargetId": None,
            "results": [],
        }
        completed_job = {
            **created_job,
            "status": "completed",
            "phase": "completed",
            "completedTargets": 1,
        }
        jobs = SimpleNamespace(
            manager=self.manager,
            create=AsyncMock(return_value=created_job),
            get=Mock(return_value=completed_job),
        )
        server_app.pixel_import_jobs = jobs

        created = self.client.post(
            "/gpt-api/pixel-manager/import",
            headers=self._headers(),
            params={"targetIds": '["pixel-1"]', "fileName": "accounts.json"},
            content=b'{"accounts":[{"credentials":{"access_token":"uploaded-secret"}}]}',
        )
        self.assertEqual(created.status_code, 202)
        self.assertEqual(created.json()["job"]["jobId"], "job-123")
        self.assertNotIn("uploaded-secret", created.text)
        jobs.create.assert_awaited_once()
        self.assertEqual(jobs.create.await_args.args[1], ["pixel-1"])

        polled = self.client.get(
            "/gpt-api/pixel-manager/import-jobs/job-123",
            headers=self._headers(),
        )
        self.assertEqual(polled.status_code, 200)
        self.assertEqual(polled.json()["job"]["status"], "completed")
        self.assertNotIn("uploaded-secret", polled.text)
        jobs.get.assert_called_once_with("job-123")

    def test_export_returns_download_metadata_without_key_by_default(self) -> None:
        self.manager.export_all = AsyncMock(
            return_value=ExportBundle(
                content=b'{"accounts":[]}',
                source_count=9,
                deduplicated_count=7,
                duplicate_count=2,
                batch_count=1,
            )
        )
        response = self.client.get("/gpt-api/pixel-manager/export")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"accounts": []})
        self.assertEqual(response.headers["content-type"], "application/json")
        self.assertRegex(
            response.headers["content-disposition"],
            r'^attachment; filename="pixel-accounts-\d{8}-\d{6}\.json"$',
        )
        self.assertEqual(response.headers["x-pixel-source-count"], "9")
        self.assertEqual(response.headers["x-pixel-deduplicated-count"], "7")
        self.assertEqual(response.headers["x-pixel-duplicate-count"], "2")
        self.assertEqual(response.headers["x-pixel-batch-count"], "1")
        self.manager.export_all.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
