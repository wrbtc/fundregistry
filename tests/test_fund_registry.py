import importlib.util
import base64
import json
import os
import re
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient


REPO_ROOT = Path(__file__).resolve().parents[1]
APP_PATH = REPO_ROOT / "app.py"


def load_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class FundRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        previous = os.environ.get("FUND_REGISTRY_DISABLE_AUTO_APP")
        os.environ["FUND_REGISTRY_DISABLE_AUTO_APP"] = "1"
        try:
            cls.module = load_module(APP_PATH, "fund_registry_module")
        finally:
            if previous is None:
                os.environ.pop("FUND_REGISTRY_DISABLE_AUTO_APP", None)
            else:
                os.environ["FUND_REGISTRY_DISABLE_AUTO_APP"] = previous

    def make_client(self, root: Path, **setting_overrides):
        defaults = {
            "db_path": root / "fundregistry.db",
            "static_dir": REPO_ROOT / "static",
            "messages_path": root / "messages.jsonl",
            "public_base_url": "https://fundregistry.test",
            "allow_dev_actions": True,
            "payment_mode": "mock",
            "proof_mode": "mock",
            "anchor_mode": "mock",
            "sats_per_usd": 1000,
            "fetch_json_fn": self.fake_fetch_json,
        }
        defaults.update(setting_overrides)
        settings = self.module.FundRegistrySettings(**defaults)
        app = self.module.create_app(settings)
        return TestClient(app), settings

    def make_locked_client(self, root: Path, **setting_overrides):
        defaults = {
            "db_path": root / "fundregistry.db",
            "static_dir": REPO_ROOT / "static",
            "messages_path": root / "messages.jsonl",
            "public_base_url": "https://fundregistry.test",
            "allow_dev_actions": False,
            "payment_mode": "disabled",
            "proof_mode": "disabled",
            "anchor_mode": "disabled",
            "sats_per_usd": 1000,
            "fetch_json_fn": self.fake_fetch_json,
        }
        defaults.update(setting_overrides)
        settings = self.module.FundRegistrySettings(**defaults)
        app = self.module.create_app(settings)
        return TestClient(app), settings

    def fake_fetch_json(self, url: str):
        marker = "/address/"
        if marker not in url:
            raise AssertionError(f"Unexpected mempool lookup: {url}")

        address = url.split(marker, 1)[1].split("/", 1)[0]
        if url.endswith(f"/address/{address}"):
            return {
                "chain_stats": {
                    "funded_txo_sum": 65000000,
                    "tx_count": 6,
                },
                "mempool_stats": {
                    "funded_txo_sum": 0,
                    "tx_count": 0,
                },
            }
        if url.endswith(f"/address/{address}/txs"):
            return [
                {
                    "txid": f"txid-{index}",
                    "vout": [{"scriptpubkey_address": address, "value": 10_000_000 + index}],
                    "status": {"confirmed": True, "block_time": 1_710_000_000 + index, "block_height": 900_000 + index},
                }
                for index in range(6)
            ]
        if f"/address/{address}/txs/chain/" in url:
            return []
        raise AssertionError(f"Unexpected mempool lookup: {url}")

    def create_page(self, client: TestClient, **overrides):
        payload = {
            "title": "Open Source Relay Infrastructure Fund",
            "description": "Fund relay hosting and maintenance.",
            "btc_address": "bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh",
            "tier": "free",
        }
        payload.update(overrides)
        response = client.post("/v1/pages", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_create_page_rejects_unknown_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client, _settings = self.make_client(Path(tmpdir))
            payload = {
                "title": "Agent Spec Drift Test",
                "description": "Unknown request fields should fail loudly.",
                "btc_address": "bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh",
                "tier": "free",
                "mode": "agent",
            }

            response = client.post("/v1/pages", json=payload)

            self.assertEqual(response.status_code, 422, response.text)

    def activate_payment(self, client: TestClient, payment_id: str):
        paid = client.post(f"/v1/dev/payments/{payment_id}/mark-paid")
        self.assertEqual(paid.status_code, 200, paid.text)
        challenge = paid.json()["challenge"]
        verified = client.post(f"/v1/proofs/{challenge['id']}/verify", json={"proof": "mock-valid"})
        self.assertEqual(verified.status_code, 200, verified.text)
        return verified.json()

    def test_contact_message_is_stored_as_jsonl(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            messages_path = root / "messages.jsonl"
            client, _settings = self.make_client(root, messages_path=messages_path)

            response = client.post(
                "/v1/messages",
                json={
                    "message": "Please add a clearer proof explainer on this page.",
                    "email": "reader@example.com",
                    "page_url": "https://fundregistry.test/fund/example",
                    "website": "",
                },
                headers={"X-Forwarded-For": "198.51.100.22"},
            )

            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json(), {"status": "ok"})
            stored_lines = messages_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(stored_lines), 1)
            stored = json.loads(stored_lines[0])
            self.assertEqual(stored["message"], "Please add a clearer proof explainer on this page.")
            self.assertEqual(stored["email"], "reader@example.com")
            self.assertEqual(stored["page_url"], "https://fundregistry.test/fund/example")
            self.assertEqual(stored["read"], False)
            self.assertEqual("198.51.100.0/24", stored["ip_prefix"])
            self.assertEqual(stored["source_host"], "testserver")
            self.assertNotIn("ip", stored)

    def test_contact_message_stores_source_host_header(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            messages_path = root / "messages.jsonl"
            client, _settings = self.make_client(root, messages_path=messages_path)

            response = client.post(
                "/v1/messages",
                json={
                    "message": "Charts are missing on the wallet view.",
                    "page_url": "/contact",
                    "website": "",
                },
                headers={
                    "Host": "fundregistry.org",
                    "X-Forwarded-Host": "example.org",
                    "X-Fund-Registry-Source-Host": "example.org",
                },
            )

            self.assertEqual(response.status_code, 200, response.text)
            stored = json.loads(messages_path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(stored["source_host"], "example.org")

    def test_contact_message_honeypot_is_silently_dropped(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            messages_path = root / "messages.jsonl"
            client, _settings = self.make_client(root, messages_path=messages_path)

            response = client.post(
                "/v1/messages",
                json={
                    "message": "Spam payload",
                    "email": "bot@example.com",
                    "page_url": "https://fundregistry.test/fund/example",
                    "website": "https://spam.example/bot",
                },
            )

            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json(), {"status": "ok"})
            self.assertFalse(messages_path.exists())

    def test_contact_message_route_is_rate_limited(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client, _settings = self.make_client(Path(tmpdir))

            responses = [
                client.post("/v1/messages", json={"message": f"Message {index}", "website": ""})
                for index in range(4)
            ]

            for response in responses[:3]:
                self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(responses[3].status_code, 429, responses[3].text)
            self.assertIn("Retry-After", responses[3].headers)

    def test_contact_message_rejects_missing_or_empty_message(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client, _settings = self.make_client(Path(tmpdir))

            missing = client.post("/v1/messages", json={"website": ""})
            blank = client.post("/v1/messages", json={"message": "   ", "website": ""})

            self.assertEqual(missing.status_code, 400, missing.text)
            self.assertEqual(blank.status_code, 400, blank.text)
            self.assertEqual(missing.json()["detail"], "Message is required.")
            self.assertEqual(blank.json()["detail"], "Message is required.")

    def test_contact_message_rejects_oversized_message(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client, _settings = self.make_client(Path(tmpdir))

            response = client.post("/v1/messages", json={"message": "x" * 2001, "website": ""})

            self.assertEqual(response.status_code, 400, response.text)
            self.assertEqual(response.json()["detail"], "Message must be 2000 characters or fewer.")

    def test_contact_message_returns_503_when_storage_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            messages_path = root / "messages-store"
            messages_path.mkdir()
            client, _settings = self.make_client(root, messages_path=messages_path)

            response = client.post("/v1/messages", json={"message": "Please get back to me.", "website": ""})

            self.assertEqual(response.status_code, 503, response.text)
            self.assertEqual(response.json()["detail"], "Unable to accept message right now.")

    def test_message_read_routes_require_admin_token(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            messages_path = root / "messages.jsonl"
            client, _settings = self.make_client(
                root,
                messages_path=messages_path,
                messages_admin_token="messages-admin-token",
            )

            first = client.post("/v1/messages", json={"message": "First message", "website": ""})
            self.assertEqual(first.status_code, 200, first.text)
            first_id = json.loads(messages_path.read_text(encoding="utf-8").splitlines()[0])["id"]

            missing = client.put(f"/v1/messages/{first_id}/read")
            self.assertEqual(missing.status_code, 401, missing.text)
            self.assertEqual(missing.json()["detail"], "Admin token is required.")

            invalid = client.put(
                f"/v1/messages/{first_id}/read",
                headers={"X-Fund-Registry-Admin-Token": "wrong-token"},
            )
            self.assertEqual(invalid.status_code, 403, invalid.text)
            self.assertEqual(invalid.json()["detail"], "Invalid admin token.")

            read_single = client.put(
                f"/v1/messages/{first_id}/read",
                headers={"X-Fund-Registry-Admin-Token": "messages-admin-token"},
            )
            self.assertEqual(read_single.status_code, 200, read_single.text)
            self.assertTrue(read_single.json()["read"])

            second = client.post("/v1/messages", json={"message": "Second message", "website": ""})
            self.assertEqual(second.status_code, 200, second.text)
            read_all = client.put(
                "/v1/messages/read-all",
                headers={"X-Fund-Registry-Admin-Token": "messages-admin-token"},
            )
            self.assertEqual(read_all.status_code, 200, read_all.text)
            self.assertEqual(read_all.json()["status"], "ok")
            self.assertEqual(read_all.json()["marked"], 1)

    def test_message_read_routes_return_503_when_admin_controls_disabled(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            messages_path = root / "messages.jsonl"
            client, _settings = self.make_client(root, messages_path=messages_path)

            created = client.post("/v1/messages", json={"message": "Needs triage", "website": ""})
            self.assertEqual(created.status_code, 200, created.text)
            message_id = json.loads(messages_path.read_text(encoding="utf-8").splitlines()[0])["id"]

            read_single = client.put(
                f"/v1/messages/{message_id}/read",
                headers={"X-Fund-Registry-Admin-Token": "any-token"},
            )
            self.assertEqual(read_single.status_code, 503, read_single.text)
            self.assertEqual(read_single.json()["detail"], "Message admin controls are disabled.")

            read_all = client.put(
                "/v1/messages/read-all",
                headers={"X-Fund-Registry-Admin-Token": "any-token"},
            )
            self.assertEqual(read_all.status_code, 503, read_all.text)
            self.assertEqual(read_all.json()["detail"], "Message admin controls are disabled.")

    def test_message_read_routes_advertise_admin_security_in_openapi(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            client, _settings = self.make_client(root, messages_admin_token="messages-admin-token")

            response = client.get("/openapi.json")
            self.assertEqual(response.status_code, 200, response.text)
            payload = response.json()

            read_all_security = payload["paths"]["/v1/messages/read-all"]["put"]["security"]
            read_one_security = payload["paths"]["/v1/messages/{message_id}/read"]["put"]["security"]
            self.assertEqual(read_all_security, [{"FundRegistryMessagesAdminToken": []}])
            self.assertEqual(read_one_security, [{"FundRegistryMessagesAdminToken": []}])

    def test_create_free_page_and_manage(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client, _settings = self.make_client(Path(tmpdir))
            created = self.create_page(client, links=[{"link_type": "github", "url": "https://github.com/example/project"}])
            page = created["page"]
            campaign_key = created["campaign_key"]

            self.assertEqual(page["tier"], "free")
            self.assertEqual(page["requested_tier"], "free")
            self.assertEqual(len(page["verification_code"]), 6)
            self.assertTrue(campaign_key["secret"].startswith("frk_"))
            self.assertTrue(page["canonical_url"].endswith(f"/fund/{page['page_ref']}"))

            managed = client.post("/v1/pages/manage", json={"campaign_key": campaign_key})
            self.assertEqual(managed.status_code, 200, managed.text)
            self.assertEqual(managed.json()["id"], page["id"])

            links_update = client.post(
                f"/v1/pages/{page['id']}/links",
                json={
                    "campaign_key": campaign_key,
                    "links": [{"platform": "website", "url": "https://example.org/fund"}],
                },
            )
            self.assertEqual(links_update.status_code, 200, links_update.text)
            self.assertEqual(links_update.json()["page"]["links"][0]["url"], "https://example.org/fund")

            update = client.post(
                f"/v1/pages/{page['id']}/updates",
                json={"campaign_key": campaign_key, "body": "First deployment complete."},
            )
            self.assertEqual(update.status_code, 403, update.text)

            page_view = client.get(f"/fund/{page['page_ref']}")
            self.assertEqual(page_view.status_code, 200)
            self.assertIn("Open Source Relay Infrastructure Fund", page_view.text)
            self.assertIn("Transactions", page_view.text)

            search = client.get(f"/v1/search?q={page['verification_code']}")
            self.assertEqual(search.status_code, 200, search.text)
            self.assertEqual(search.json()["resolved_by"], "verification_code")

            button = client.get(f"/v1/pages/{page['id']}/button")
            self.assertEqual(button.status_code, 200, button.text)
            self.assertEqual(button.json()["current_state"], "unverified")
            self.assertIn(page["verification_code"], button.json()["html_snippet"])

            transactions = client.get(f"/v1/pages/{page['id']}/transactions")
            self.assertEqual(transactions.status_code, 200, transactions.text)
            self.assertEqual(transactions.json()["history_mode"], "recent")
            self.assertEqual(transactions.json()["visible_count"], 5)

    def test_stats_endpoint_counts_created_pages_excluding_deleted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client, _settings = self.make_client(Path(tmpdir))

            first = self.create_page(client)["page"]
            second = self.create_page(client, btc_address="1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa")["page"]
            third = self.create_page(client, btc_address="3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy")["page"]

            store = client.app.state.store
            with store.connection() as connection:
                connection.execute("UPDATE pages SET public_state = 'expired' WHERE id = ?", (second["id"],))
                connection.execute("UPDATE pages SET public_state = 'tombstoned' WHERE id = ?", (third["id"],))
                connection.execute("UPDATE pages SET public_state = 'deleted' WHERE id = ?", (first["id"],))
                connection.commit()
            store._invalidate_stats_cache()

            response = client.get("/v1/stats")

            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.headers["cache-control"], "no-store, max-age=0")
            self.assertEqual(response.json()["total_pages"], 2)
            self.assertIn("generated_at", response.json())

            home = client.get("/")
            self.assertEqual(home.status_code, 200, home.text)
            self.assertIn('id="pageCounter"', home.text)
            self.assertIn("Registered pages", home.text)
            self.assertRegex(home.text, re.compile(r'/assets/index\.js\?v=[^"]+'))
            self.assertRegex(home.text, re.compile(r'styles\.css\?v=[^"]+'))

    def test_deleted_pages_404_on_public_and_json_routes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client, settings = self.make_client(Path(tmpdir))
            created = self.create_page(client)
            page = created["page"]

            active_paths = [
                f"/v1/pages/{page['id']}",
                f"/v1/pages/{page['id']}/share",
                f"/v1/pages/{page['id']}/button",
                f"/v1/pages/ref/{page['page_ref']}",
                f"/fund/{page['page_ref']}",
                f"/verify/{page['page_ref']}",
            ]
            for path in active_paths:
                response = client.get(path)
                self.assertEqual(response.status_code, 200, path)

            store = self.module.FundRegistryStore(settings)
            with store.connection() as connection:
                connection.execute("UPDATE pages SET public_state = 'deleted' WHERE id = ?", (page["id"],))
                connection.commit()

            for path in active_paths:
                response = client.get(path)
                self.assertEqual(response.status_code, 404, path)

    def test_lookup_redirects_to_address_reader_and_verify_page(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client, _settings = self.make_client(Path(tmpdir))
            created = self.create_page(client, tier="tier2")
            payment = created["payment_intent"]
            page = self.activate_payment(client, payment["id"])["page"]

            address_lookup = client.get("/lookup", params={"q": page["btc_address"]}, follow_redirects=False)
            self.assertEqual(address_lookup.status_code, 302, address_lookup.text)
            self.assertTrue(address_lookup.headers["location"].endswith(f"/address/{page['btc_address']}"))

            code_lookup = client.get("/lookup", params={"q": page["verification_code"]}, follow_redirects=False)
            self.assertEqual(code_lookup.status_code, 302, code_lookup.text)
            self.assertTrue(code_lookup.headers["location"].endswith(f"/verify/{page['page_ref']}"))

    def test_public_and_verify_routes_reuse_loaded_page_payload(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client, _settings = self.make_client(Path(tmpdir))
            created = self.create_page(client)
            page = created["page"]

            original_get_page_by_id = self.module.FundRegistryStore.get_page_by_id

            def fail_get_page_by_id(_store, _page_id: str):
                raise AssertionError("public HTML routes should not reload pages by id")

            self.module.FundRegistryStore.get_page_by_id = fail_get_page_by_id
            try:
                public_page = client.get(f"/fund/{page['page_ref']}")
                self.assertEqual(public_page.status_code, 200, public_page.text)
                self.assertIn(page["verification_code"], public_page.text)

                verify_page = client.get(f"/verify/{page['page_ref']}")
                self.assertEqual(verify_page.status_code, 200, verify_page.text)
                self.assertIn("Verification record", verify_page.text)
            finally:
                self.module.FundRegistryStore.get_page_by_id = original_get_page_by_id

    def test_bitcoin_cli_uses_ssh_multiplexing_for_remote_wallet_calls(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = self.module.FundRegistrySettings(
                db_path=Path(tmpdir) / "fundregistry.db",
                static_dir=REPO_ROOT / "services" / "fund-registry" / "static",
                bitcoin_cli_path="/opt/bitcoin/bin/bitcoin-cli",
                bitcoin_conf_path="/etc/bitcoin/bitcoin.conf",
                bitcoin_backend="ssh",
                bitcoin_backend_source="explicit",
                bitcoin_ssh_host="remote-bitcoin",
            )
            store = self.module.FundRegistryStore(settings)
            captured = {}
            original_run = self.module.subprocess.run

            class Result:
                returncode = 0
                stdout = "{}"
                stderr = ""

            def fake_run(command, **kwargs):
                captured["command"] = command
                captured["kwargs"] = kwargs
                return Result()

            self.module.subprocess.run = fake_run
            try:
                payload = store._bitcoin_cli_call(["gettransaction", "ab" * 32, "true"], wallet="fund-registry-anchor")
            finally:
                self.module.subprocess.run = original_run

            self.assertEqual(payload, {})
            command = captured["command"]
            self.assertEqual(command[0], "ssh")
            self.assertIn("BatchMode=yes", command)
            self.assertIn("ControlMaster=auto", command)
            self.assertIn("ControlPersist=60", command)
            self.assertIn("ControlPath=/tmp/fund-registry-bitcoin-ssh-%C", command)
            self.assertEqual(command[-2], "remote-bitcoin")
            self.assertIn("-conf=/etc/bitcoin/bitcoin.conf", command[-1])
            self.assertIn("-rpcwallet=fund-registry-anchor", command[-1])
            self.assertIn("gettransaction", command[-1])

    def test_address_reader_returns_current_and_historical_records(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client, _settings = self.make_client(Path(tmpdir))
            created_free = self.create_page(client, title="Neighborhood Mutual Aid")
            free_page = created_free["page"]

            created_tier3 = self.create_page(client, title="Relay Expansion Fund", tier="tier3", vanity_slug="relay-fund")
            payment = created_tier3["payment_intent"]
            tier3_page = self.activate_payment(client, payment["id"])["page"]

            aborted = client.post(
                f"/v1/pages/{free_page['id']}/abort",
                json={"campaign_key": created_free["campaign_key"]},
            )
            self.assertEqual(aborted.status_code, 200, aborted.text)

            records = client.get(f"/v1/addresses/{free_page['btc_address']}/records")
            self.assertEqual(records.status_code, 200, records.text)
            payload = records.json()
            self.assertEqual(payload["record_count"], 2)
            self.assertEqual(payload["records"][0]["title"], "Relay Expansion Fund")
            self.assertEqual(payload["records"][0]["current_funding_state"], "active")
            self.assertEqual(payload["records"][0]["proof_status"], "anchored")
            self.assertEqual(payload["records"][1]["title"], "Neighborhood Mutual Aid")
            self.assertEqual(payload["records"][1]["current_funding_state"], "aborted")
            self.assertEqual(payload["records"][1]["proof_status"], "aborted")
            self.assertIn("does not verify campaign truth", payload["disclosure"])

            reader_page = client.get(f"/address/{free_page['btc_address']}")
            self.assertEqual(reader_page.status_code, 200, reader_page.text)
            self.assertIn("Address reader", reader_page.text)
            self.assertIn("Relay Expansion Fund", reader_page.text)
            self.assertIn("Neighborhood Mutual Aid", reader_page.text)
            self.assertIn("does not verify campaign truth", reader_page.text)
            self.assertIn(tier3_page["verification_code"], reader_page.text)

    def test_lookup_renders_html_not_found_page(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client, _settings = self.make_client(Path(tmpdir))
            response = client.get("/lookup", params={"q": "not-a-real-page"}, follow_redirects=False)
            self.assertEqual(response.status_code, 404, response.text)
            self.assertIn("Lookup not found", response.text)
            self.assertIn("Fund Registry could not resolve this lookup.", response.text)

    def test_create_page_rejects_removed_lightning_destination_field(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "fundregistry.db"
            client, _settings = self.make_client(Path(tmpdir))
            response = client.post(
                "/v1/pages",
                json={
                    "title": "Emergency Housing Fund",
                    "description": "Support a temporary housing bridge while we stabilize after an abrupt family crisis.",
                    "btc_address": "bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh",
                    "lightning_destination": "creator@lightning.example",
                },
            )
            self.assertEqual(response.status_code, 422, response.text)
            with sqlite3.connect(db_path) as connection:
                columns = {row[1] for row in connection.execute("PRAGMA table_info(pages)").fetchall()}
            self.assertNotIn("lightning_destination", columns)

    def test_tier2_activation_and_share_payload(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client, _settings = self.make_client(Path(tmpdir))
            created = self.create_page(client, tier="tier2")
            page = created["page"]
            payment = created["payment_intent"]
            campaign_key = created["campaign_key"]

            self.assertEqual(page["tier"], "free")
            self.assertEqual(page["requested_tier"], "tier2")
            self.assertEqual(payment["target_tier"], "tier2")

            paid = client.post(f"/v1/dev/payments/{payment['id']}/mark-paid")
            self.assertEqual(paid.status_code, 200, paid.text)
            prepare = client.post(
                f"/v1/pages/{page['id']}/proof/prepare",
                json={"campaign_key": campaign_key},
            )
            self.assertEqual(prepare.status_code, 200, prepare.text)
            prepare_payload = prepare.json()
            self.assertEqual(prepare_payload["payload"]["tier"], "tier2")
            challenge = prepare_payload["challenge"]
            self.assertEqual(challenge["payload"]["challenge_id"], challenge["id"])
            self.assertEqual(challenge["payload"]["canonical_proof_payload_hash"], prepare_payload["payload_hash"])
            self.assertEqual(challenge["hash"], self.module.sha256_hex(challenge["text"]))
            self.assertNotEqual(challenge["text"], prepare_payload["payload_json"])

            verified = client.post(
                f"/v1/pages/{page['id']}/proof/verify",
                json={"campaign_key": campaign_key, "challenge_id": challenge["id"], "proof": "mock-valid"},
            )
            self.assertEqual(verified.status_code, 200, verified.text)
            verified = verified.json()
            activated_page = verified["page"]
            self.assertEqual(activated_page["tier"], "tier2")
            self.assertEqual(activated_page["page_ref"], activated_page["btc_address"])

            share = client.get(f"/v1/pages/{activated_page['id']}/share")
            self.assertEqual(share.status_code, 200, share.text)
            share_payload = share.json()
            self.assertIn("/badge/", share_payload["badge_svg_url"])
            self.assertIn("Wallet Verified", share_payload["markdown_snippet"])

            badge = client.get(f"/badge/{activated_page['page_ref']}.svg")
            self.assertEqual(badge.status_code, 200, badge.text)
            self.assertIn("Wallet Verified", badge.text)

            managed = client.post("/v1/pages/manage", json={"campaign_key": campaign_key})
            self.assertEqual(managed.status_code, 200, managed.text)
            self.assertEqual(managed.json()["tier"], "tier2")
            self.assertEqual(managed.json()["proof_status"], "verified")

            button = client.get(f"/v1/pages/{activated_page['id']}/button")
            self.assertEqual(button.status_code, 200, button.text)
            self.assertEqual(button.json()["current_state"], "verified")

            transactions = client.get(f"/v1/pages/{activated_page['id']}/transactions")
            self.assertEqual(transactions.status_code, 200, transactions.text)
            self.assertEqual(transactions.json()["history_mode"], "full")
            self.assertEqual(transactions.json()["visible_count"], 6)

    def test_payment_intent_honors_temporary_sats_overrides(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client, settings = self.make_client(
                Path(tmpdir),
                payment_mode="mock",
                tier2_amount_sats_override=1000,
                tier3_amount_sats_override=2000,
                sats_per_usd=1200,
            )

            tier2_created = self.create_page(client, tier="tier2", btc_address="17YBEokEMaweQn9VatKEZTRVVHTeiKT4dq")
            tier2_payment = tier2_created["payment_intent"]
            self.assertEqual(tier2_payment["amount_sats"], 1000)
            self.assertEqual(tier2_payment["amount_btc"], "0.00001000")
            self.assertEqual(tier2_payment["amount_usd_cents"], 83)

            tier3_created = self.create_page(
                client,
                tier="tier3",
                btc_address="1JeGb9TxU4iXBSXYqUKxwDhPW2PjBtph52",
                vanity_slug="temporary-smoke",
            )
            tier3_payment = tier3_created["payment_intent"]
            self.assertEqual(tier3_payment["amount_sats"], 2000)
            self.assertEqual(tier3_payment["amount_btc"], "0.00002000")
            self.assertEqual(tier3_payment["amount_usd_cents"], 167)

            health = client.get("/v1/health")
            self.assertEqual(health.status_code, 200, health.text)
            amounts = health.json()["amounts"]
            self.assertEqual(amounts["tier2_amount_sats_override"], 1000)
            self.assertEqual(amounts["tier3_amount_sats_override"], 2000)
            self.assertEqual(amounts["sats_per_usd"], settings.sats_per_usd)

    def test_payment_polling_fields_and_manage_verify_reuses_pending_challenge(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client, _settings = self.make_client(Path(tmpdir))
            created = self.create_page(client, tier="tier2")
            page = created["page"]
            payment = created["payment_intent"]
            campaign_key = created["campaign_key"]

            self.assertEqual(payment["payment_status"], "pending")
            self.assertIn("invoice", payment)
            self.assertTrue(payment["invoice"].startswith("mock-bitcoin-payment:frpay_"))
            self.assertNotIn("lnbc", payment["invoice"].lower())
            self.assertNotIn("bolt11", payment)
            self.assertNotIn("payment_request", payment)
            self.assertNotIn("qr_value", payment)

            payment_view = client.get(f"/v1/payments/{payment['id']}")
            self.assertEqual(payment_view.status_code, 200, payment_view.text)
            self.assertEqual(payment_view.json()["payment_status"], "pending")

            before_paid_verify = client.post(
                f"/v1/pages/{page['id']}/verify",
                json={"campaign_key": campaign_key},
            )
            self.assertEqual(before_paid_verify.status_code, 409, before_paid_verify.text)

            paid = client.post(f"/v1/dev/payments/{payment['id']}/mark-paid")
            self.assertEqual(paid.status_code, 200, paid.text)
            payment_after_paid = client.get(f"/v1/payments/{payment['id']}")
            self.assertEqual(payment_after_paid.status_code, 200, payment_after_paid.text)
            self.assertEqual(payment_after_paid.json()["payment_status"], "paid_pending_proof")
            self.assertIn("challenge", payment_after_paid.json())

            verify_prepare = client.post(
                f"/v1/pages/{page['id']}/verify",
                json={"campaign_key": campaign_key},
            )
            self.assertEqual(verify_prepare.status_code, 200, verify_prepare.text)
            prepared = verify_prepare.json()
            self.assertEqual(prepared["challenge_id"], payment_after_paid.json()["challenge"]["id"])
            self.assertEqual(prepared["payment_intent"]["id"], payment["id"])

    def test_bitcoin_core_payment_intent_progression_and_proof_completion(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state = {
                "address": "bc1qpaymentwallettest0000000000000000000000000",
                "unconfirmed_sats": 0,
                "confirmed_sats": 0,
                "confirmations": 0,
                "txids": [],
                "transactions": {},
            }

            def receive_tx(txid, amount_sats, *, confirmations=0, timereceived=0, walletconflicts=None):
                return {
                    "txid": txid,
                    "confirmations": confirmations,
                    "timereceived": timereceived,
                    "walletconflicts": walletconflicts or [],
                    "details": [
                        {
                            "address": state["address"],
                            "category": "receive",
                            "amount": self.module.sats_to_btc_string(amount_sats),
                        }
                    ],
                }

            def fake_bitcoin_cli(args, wallet):
                command = args[0]
                if command == "getblockchaininfo":
                    return {"chain": "main", "initialblockdownload": False, "blocks": 941700, "headers": 941700}
                if command == "listwallets":
                    return ["fund-registry-payments"]
                if command == "listwalletdir":
                    return {"wallets": [{"name": "fund-registry-payments"}]}
                if command == "getbalances":
                    return {"mine": {"trusted": 0.0, "untrusted_pending": 0.0, "immature": 0.0}}
                if command == "getnewaddress":
                    self.assertEqual(wallet, "fund-registry-payments")
                    return state["address"]
                if command == "listreceivedbyaddress":
                    self.assertEqual(wallet, "fund-registry-payments")
                    if state["unconfirmed_sats"] <= 0:
                        return []
                    return [
                        {
                            "address": state["address"],
                            "amount": self.module.sats_to_btc_string(state["unconfirmed_sats"]),
                            "confirmations": state["confirmations"],
                            "txids": state["txids"],
                        }
                    ]
                if command == "getreceivedbyaddress":
                    self.assertEqual(wallet, "fund-registry-payments")
                    return self.module.sats_to_btc_string(state["confirmed_sats"])
                if command == "gettransaction":
                    self.assertEqual(wallet, "fund-registry-payments")
                    return state["transactions"][args[1]]
                raise AssertionError(f"Unexpected bitcoin-cli call: {args}")

            client, _settings = self.make_client(
                Path(tmpdir),
                allow_dev_actions=True,
                payment_mode="bitcoin-core",
                payment_wallet_name="fund-registry-payments",
                proof_mode="mock",
                anchor_mode="disabled",
                tier2_amount_sats_override=12_000,
                bitcoin_cli_fn=fake_bitcoin_cli,
            )
            created = self.create_page(client, tier="tier2")
            payment = created["payment_intent"]

            self.assertEqual(payment["payment_method"], "btc_onchain")
            self.assertEqual(payment["payment_status"], "pending")
            self.assertEqual(payment["payment_address"], state["address"])
            self.assertTrue(payment["payment_uri"].startswith(f"bitcoin:{state['address']}"))
            self.assertEqual(payment["confirmation_target"], 1)
            self.assertIn("qr_value", payment)
            self.assertNotIn("challenge", payment)

            state["unconfirmed_sats"] = 10_000
            state["confirmed_sats"] = 0
            partial = client.get(f"/v1/payments/{payment['id']}")
            self.assertEqual(partial.status_code, 200, partial.text)
            self.assertEqual(partial.json()["payment_status"], "pending")
            self.assertEqual(partial.json()["received_sats"], 10_000)
            self.assertEqual(partial.json()["underpaid_sats"], 2_000)

            state["unconfirmed_sats"] = 13_000
            state["confirmed_sats"] = 0
            state["confirmations"] = 0
            state["txids"] = ["tx-overpay-1"]
            state["transactions"] = {
                "tx-overpay-1": receive_tx("tx-overpay-1", 13_000, confirmations=0, timereceived=10)
            }
            confirming = client.get(f"/v1/payments/{payment['id']}")
            self.assertEqual(confirming.status_code, 200, confirming.text)
            self.assertEqual(confirming.json()["payment_status"], "confirming")
            self.assertEqual(confirming.json()["overpaid_sats"], 1_000)
            self.assertEqual(confirming.json()["txids"], ["tx-overpay-1"])

            state["confirmed_sats"] = 13_000
            state["confirmations"] = 1
            state["transactions"] = {
                "tx-overpay-1": receive_tx("tx-overpay-1", 13_000, confirmations=1, timereceived=10)
            }
            ready = client.get(f"/v1/payments/{payment['id']}")
            self.assertEqual(ready.status_code, 200, ready.text)
            self.assertEqual(ready.json()["payment_status"], "paid_pending_proof")
            self.assertEqual(ready.json()["status"], "paid_pending_proof")
            self.assertEqual(ready.json()["challenge"]["payment_intent_id"], payment["id"])

            verified = client.post(
                f"/v1/proofs/{ready.json()['challenge']['id']}/verify",
                json={"proof": "mock-valid"},
            )
            self.assertEqual(verified.status_code, 200, verified.text)
            self.assertEqual(verified.json()["payment_intent"]["payment_status"], "paid")
            self.assertEqual(verified.json()["payment_intent"]["status"], "paid")
            self.assertEqual(verified.json()["page"]["tier"], "tier2")

    def test_bitcoin_core_payment_polling_dedupes_rbf_replacements(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state = {
                "address": "bc1qrbfcheckout0000000000000000000000000000",
                "unconfirmed_sats": 0,
                "confirmed_sats": 0,
                "confirmations": 0,
                "txids": [],
                "transactions": {},
            }

            def receive_tx(txid, amount_sats, *, confirmations=0, timereceived=0, walletconflicts=None):
                return {
                    "txid": txid,
                    "confirmations": confirmations,
                    "timereceived": timereceived,
                    "walletconflicts": walletconflicts or [],
                    "details": [
                        {
                            "address": state["address"],
                            "category": "receive",
                            "amount": self.module.sats_to_btc_string(amount_sats),
                        }
                    ],
                }

            def fake_bitcoin_cli(args, wallet):
                command = args[0]
                if command == "getblockchaininfo":
                    return {"chain": "main", "initialblockdownload": False, "blocks": 941700, "headers": 941700}
                if command == "listwallets":
                    return ["fund-registry-payments"]
                if command == "listwalletdir":
                    return {"wallets": [{"name": "fund-registry-payments"}]}
                if command == "getbalances":
                    return {"mine": {"trusted": 0.0, "untrusted_pending": 0.0, "immature": 0.0}}
                if command == "getnewaddress":
                    self.assertEqual(wallet, "fund-registry-payments")
                    return state["address"]
                if command == "listreceivedbyaddress":
                    if state["unconfirmed_sats"] <= 0:
                        return []
                    return [
                        {
                            "address": state["address"],
                            "amount": self.module.sats_to_btc_string(state["unconfirmed_sats"]),
                            "confirmations": state["confirmations"],
                            "txids": state["txids"],
                        }
                    ]
                if command == "getreceivedbyaddress":
                    self.assertEqual(wallet, "fund-registry-payments")
                    return self.module.sats_to_btc_string(state["confirmed_sats"])
                if command == "gettransaction":
                    self.assertEqual(wallet, "fund-registry-payments")
                    return state["transactions"][args[1]]
                raise AssertionError(f"Unexpected bitcoin-cli call: {args}")

            client, _settings = self.make_client(
                Path(tmpdir),
                allow_dev_actions=True,
                payment_mode="bitcoin-core",
                payment_wallet_name="fund-registry-payments",
                proof_mode="mock",
                anchor_mode="disabled",
                tier2_amount_sats_override=2000,
                bitcoin_cli_fn=fake_bitcoin_cli,
            )
            created = self.create_page(client, tier="tier2")
            payment = created["payment_intent"]

            state["unconfirmed_sats"] = 4_000
            state["confirmed_sats"] = 0
            state["confirmations"] = 0
            state["txids"] = ["tx-rbf-old", "tx-rbf-new"]
            state["transactions"] = {
                "tx-rbf-old": receive_tx(
                    "tx-rbf-old",
                    2_000,
                    confirmations=0,
                    timereceived=10,
                    walletconflicts=["tx-rbf-new"],
                ),
                "tx-rbf-new": receive_tx(
                    "tx-rbf-new",
                    2_000,
                    confirmations=0,
                    timereceived=20,
                    walletconflicts=["tx-rbf-old"],
                ),
            }
            confirming = client.get(f"/v1/payments/{payment['id']}")
            self.assertEqual(confirming.status_code, 200, confirming.text)
            self.assertEqual(confirming.json()["payment_status"], "confirming")
            self.assertEqual(confirming.json()["received_sats"], 2_000)
            self.assertEqual(confirming.json()["unconfirmed_received_sats"], 2_000)
            self.assertEqual(confirming.json()["overpaid_sats"], 0)
            self.assertEqual(confirming.json()["txids"], ["tx-rbf-new"])

            state["confirmed_sats"] = 2_000
            state["confirmations"] = 1
            state["transactions"]["tx-rbf-new"]["confirmations"] = 1
            ready = client.get(f"/v1/payments/{payment['id']}")
            self.assertEqual(ready.status_code, 200, ready.text)
            self.assertEqual(ready.json()["payment_status"], "paid_pending_proof")
            self.assertEqual(ready.json()["received_sats"], 2_000)
            self.assertEqual(ready.json()["txids"], ["tx-rbf-new"])

    def test_bitcoin_core_payment_polling_keeps_distinct_unconfirmed_payments(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state = {
                "address": "bc1qsplitcheckout000000000000000000000000000",
                "unconfirmed_sats": 0,
                "confirmed_sats": 0,
                "confirmations": 0,
                "txids": [],
                "transactions": {},
            }

            def receive_tx(txid, amount_sats, *, confirmations=0, timereceived=0):
                return {
                    "txid": txid,
                    "confirmations": confirmations,
                    "timereceived": timereceived,
                    "walletconflicts": [],
                    "details": [
                        {
                            "address": state["address"],
                            "category": "receive",
                            "amount": self.module.sats_to_btc_string(amount_sats),
                        }
                    ],
                }

            def fake_bitcoin_cli(args, wallet):
                command = args[0]
                if command == "getblockchaininfo":
                    return {"chain": "main", "initialblockdownload": False, "blocks": 941700, "headers": 941700}
                if command == "listwallets":
                    return ["fund-registry-payments"]
                if command == "listwalletdir":
                    return {"wallets": [{"name": "fund-registry-payments"}]}
                if command == "getbalances":
                    return {"mine": {"trusted": 0.0, "untrusted_pending": 0.0, "immature": 0.0}}
                if command == "getnewaddress":
                    self.assertEqual(wallet, "fund-registry-payments")
                    return state["address"]
                if command == "listreceivedbyaddress":
                    if state["unconfirmed_sats"] <= 0:
                        return []
                    return [
                        {
                            "address": state["address"],
                            "amount": self.module.sats_to_btc_string(state["unconfirmed_sats"]),
                            "confirmations": state["confirmations"],
                            "txids": state["txids"],
                        }
                    ]
                if command == "getreceivedbyaddress":
                    self.assertEqual(wallet, "fund-registry-payments")
                    return self.module.sats_to_btc_string(state["confirmed_sats"])
                if command == "gettransaction":
                    self.assertEqual(wallet, "fund-registry-payments")
                    return state["transactions"][args[1]]
                raise AssertionError(f"Unexpected bitcoin-cli call: {args}")

            client, _settings = self.make_client(
                Path(tmpdir),
                allow_dev_actions=True,
                payment_mode="bitcoin-core",
                payment_wallet_name="fund-registry-payments",
                proof_mode="mock",
                anchor_mode="disabled",
                tier2_amount_sats_override=12_000,
                bitcoin_cli_fn=fake_bitcoin_cli,
            )
            created = self.create_page(client, tier="tier2")
            payment = created["payment_intent"]

            state["unconfirmed_sats"] = 13_000
            state["confirmed_sats"] = 0
            state["confirmations"] = 0
            state["txids"] = ["tx-part-1", "tx-part-2"]
            state["transactions"] = {
                "tx-part-1": receive_tx("tx-part-1", 6_000, confirmations=0, timereceived=10),
                "tx-part-2": receive_tx("tx-part-2", 7_000, confirmations=0, timereceived=20),
            }
            confirming = client.get(f"/v1/payments/{payment['id']}")
            self.assertEqual(confirming.status_code, 200, confirming.text)
            self.assertEqual(confirming.json()["payment_status"], "confirming")
            self.assertEqual(confirming.json()["received_sats"], 13_000)
            self.assertEqual(confirming.json()["overpaid_sats"], 1_000)
            self.assertEqual(confirming.json()["txids"], ["tx-part-1", "tx-part-2"])

    def test_manage_page_resumes_expired_bitcoin_core_checkout_and_flags_late_payment(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            now_state = {"value": self.module.dt.datetime(2026, 3, 21, 18, 0, tzinfo=self.module.dt.timezone.utc)}
            state = {
                "address": "bc1qcheckoutexpire000000000000000000000000000",
                "unconfirmed_sats": 0,
                "confirmed_sats": 0,
                "confirmations": 0,
                "txids": [],
                "transactions": {},
            }

            def receive_tx(txid, amount_sats, *, confirmations=0, timereceived=0):
                return {
                    "txid": txid,
                    "confirmations": confirmations,
                    "timereceived": timereceived,
                    "walletconflicts": [],
                    "details": [
                        {
                            "address": state["address"],
                            "category": "receive",
                            "amount": self.module.sats_to_btc_string(amount_sats),
                        }
                    ],
                }

            def fake_bitcoin_cli(args, wallet):
                command = args[0]
                if command == "getblockchaininfo":
                    return {"chain": "main", "initialblockdownload": False, "blocks": 941700, "headers": 941700}
                if command == "listwallets":
                    return ["fund-registry-payments"]
                if command == "listwalletdir":
                    return {"wallets": [{"name": "fund-registry-payments"}]}
                if command == "getbalances":
                    return {"mine": {"trusted": 0.0, "untrusted_pending": 0.0, "immature": 0.0}}
                if command == "getnewaddress":
                    return state["address"]
                if command == "listreceivedbyaddress":
                    if state["unconfirmed_sats"] <= 0:
                        return []
                    return [
                        {
                            "address": state["address"],
                            "amount": self.module.sats_to_btc_string(state["unconfirmed_sats"]),
                            "confirmations": state["confirmations"],
                            "txids": state["txids"],
                        }
                    ]
                if command == "getreceivedbyaddress":
                    return self.module.sats_to_btc_string(state["confirmed_sats"])
                if command == "gettransaction":
                    return state["transactions"][args[1]]
                raise AssertionError(f"Unexpected bitcoin-cli call: {args}")

            settings = self.module.FundRegistrySettings(
                db_path=Path(tmpdir) / "fundregistry.db",
                static_dir=REPO_ROOT / "services" / "fund-registry" / "static",
                public_base_url="https://fundregistry.test",
                allow_dev_actions=True,
                payment_mode="bitcoin-core",
                payment_wallet_name="fund-registry-payments",
                proof_mode="mock",
                anchor_mode="disabled",
                sats_per_usd=1000,
                tier2_amount_sats_override=12_000,
                fetch_json_fn=self.fake_fetch_json,
                bitcoin_cli_fn=fake_bitcoin_cli,
                now_fn=lambda: now_state["value"],
            )
            app = self.module.create_app(settings)
            client = TestClient(app)

            created = self.create_page(client, tier="tier2")
            payment = created["payment_intent"]
            self.assertEqual(payment["payment_status"], "pending")

            now_state["value"] = now_state["value"] + self.module.dt.timedelta(minutes=16)
            expired = client.get(f"/v1/payments/{payment['id']}")
            self.assertEqual(expired.status_code, 200, expired.text)
            self.assertEqual(expired.json()["payment_status"], "expired")

            resumed = client.post("/v1/pages/manage", json={"campaign_key": created["campaign_key"]})
            self.assertEqual(resumed.status_code, 200, resumed.text)
            self.assertEqual(resumed.json()["payment_intent"]["id"], payment["id"])
            self.assertEqual(resumed.json()["payment_intent"]["payment_status"], "expired")

            state["unconfirmed_sats"] = 12_000
            state["confirmed_sats"] = 12_000
            state["confirmations"] = 1
            state["txids"] = ["tx-late-1"]
            state["transactions"] = {"tx-late-1": receive_tx("tx-late-1", 12_000, confirmations=1, timereceived=10)}
            late = client.get(f"/v1/payments/{payment['id']}")
            self.assertEqual(late.status_code, 200, late.text)
            self.assertEqual(late.json()["payment_status"], "expired")
            self.assertTrue(late.json()["late_payment_detected"])
            self.assertEqual(late.json()["txids"], ["tx-late-1"])

    def test_payments_paused_redacts_bitcoin_checkout_details(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state = {
                "address": "bc1qpausedcheckout0000000000000000000000000",
                "unconfirmed_sats": 0,
                "confirmed_sats": 0,
                "confirmations": 0,
                "txids": [],
            }

            def fake_bitcoin_cli(args, wallet):
                command = args[0]
                if command == "getblockchaininfo":
                    return {"chain": "main", "initialblockdownload": False, "blocks": 941700, "headers": 941700}
                if command == "listwallets":
                    return ["fund-registry-payments"]
                if command == "listwalletdir":
                    return {"wallets": [{"name": "fund-registry-payments"}]}
                if command == "getbalances":
                    return {"mine": {"trusted": 0.0, "untrusted_pending": 0.0, "immature": 0.0}}
                if command == "getnewaddress":
                    self.assertEqual(wallet, "fund-registry-payments")
                    return state["address"]
                if command == "listreceivedbyaddress":
                    return []
                if command == "getreceivedbyaddress":
                    return self.module.sats_to_btc_string(0)
                raise AssertionError(f"Unexpected bitcoin-cli call: {args}")

            client, _settings = self.make_client(
                Path(tmpdir),
                allow_dev_actions=True,
                payment_mode="bitcoin-core",
                payment_wallet_name="fund-registry-payments",
                payments_paused=True,
                proof_mode="mock",
                anchor_mode="disabled",
                bitcoin_cli_fn=fake_bitcoin_cli,
            )
            created = self.create_page(client, tier="tier2")
            payment = created["payment_intent"]

            self.assertTrue(payment["payment_ui_paused"])
            self.assertEqual(payment["payment_ui_message"], self.module.PAYMENTS_PAUSED_MESSAGE)
            self.assertEqual(payment["payment_method"], "btc_onchain")
            self.assertEqual(payment["payment_status"], "pending")
            self.assertIsNone(payment["payment_address"])
            self.assertIsNone(payment["payment_uri"])
            self.assertNotIn("qr_value", payment)
            self.assertNotIn("qr_image_uri", payment)

            state["unconfirmed_sats"] = 25_000
            state["confirmed_sats"] = 25_000
            state["confirmations"] = 1
            state["txids"] = ["tx-paused-1"]
            still_paused = client.get(f"/v1/payments/{payment['id']}")
            self.assertEqual(still_paused.status_code, 200, still_paused.text)
            self.assertEqual(still_paused.json()["payment_status"], "pending")
            self.assertEqual(still_paused.json()["received_sats"], 0)

    def test_payment_details_redacted_keeps_checkout_live_but_hides_btc_details(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state = {
                "address": "bc1qredactedcheckout00000000000000000000000",
                "unconfirmed_sats": 0,
                "confirmed_sats": 0,
                "confirmations": 0,
                "txids": [],
                "transactions": {},
            }

            def receive_tx(txid, amount_sats, *, confirmations=0, timereceived=0):
                return {
                    "txid": txid,
                    "confirmations": confirmations,
                    "timereceived": timereceived,
                    "walletconflicts": [],
                    "details": [
                        {
                            "address": state["address"],
                            "category": "receive",
                            "amount": self.module.sats_to_btc_string(amount_sats),
                        }
                    ],
                }

            def fake_bitcoin_cli(args, wallet):
                command = args[0]
                if command == "getblockchaininfo":
                    return {"chain": "main", "initialblockdownload": False, "blocks": 941700, "headers": 941700}
                if command == "listwallets":
                    return ["fund-registry-payments"]
                if command == "listwalletdir":
                    return {"wallets": [{"name": "fund-registry-payments"}]}
                if command == "getbalances":
                    return {"mine": {"trusted": 0.0, "untrusted_pending": 0.0, "immature": 0.0}}
                if command == "getnewaddress":
                    self.assertEqual(wallet, "fund-registry-payments")
                    return state["address"]
                if command == "listreceivedbyaddress":
                    if state["unconfirmed_sats"] <= 0:
                        return []
                    return [
                        {
                            "address": state["address"],
                            "amount": self.module.sats_to_btc_string(state["unconfirmed_sats"]),
                            "confirmations": state["confirmations"],
                            "txids": state["txids"],
                        }
                    ]
                if command == "getreceivedbyaddress":
                    return self.module.sats_to_btc_string(state["confirmed_sats"])
                if command == "gettransaction":
                    self.assertEqual(wallet, "fund-registry-payments")
                    return state["transactions"][args[1]]
                raise AssertionError(f"Unexpected bitcoin-cli call: {args}")

            client, _settings = self.make_locked_client(
                Path(tmpdir),
                payment_mode="bitcoin-core",
                proof_mode="bitcoin-message",
                payment_wallet_name="fund-registry-payments",
                payment_details_redacted=True,
                tier2_amount_sats_override=12_000,
                bitcoin_cli_fn=fake_bitcoin_cli,
            )
            created = self.create_page(client, tier="tier2", btc_address="17YBEokEMaweQn9VatKEZTRVVHTeiKT4dq")
            payment = created["payment_intent"]

            self.assertTrue(payment["payment_ui_redacted"])
            self.assertEqual(payment["payment_ui_message"], self.module.PAYMENT_DETAILS_REDACTED_MESSAGE)
            self.assertFalse(payment.get("payment_ui_paused", False))
            self.assertEqual(payment["payment_method"], "btc_onchain")
            self.assertEqual(payment["payment_status"], "pending")
            self.assertIsNone(payment["payment_address"])
            self.assertIsNone(payment["payment_uri"])
            self.assertNotIn("qr_value", payment)
            self.assertNotIn("qr_image_uri", payment)

            state["unconfirmed_sats"] = 12_000
            state["confirmed_sats"] = 12_000
            state["confirmations"] = 1
            state["txids"] = ["tx-redacted-1"]
            state["transactions"] = {"tx-redacted-1": receive_tx("tx-redacted-1", 12_000, confirmations=1, timereceived=10)}
            ready = client.get(f"/v1/payments/{payment['id']}")
            self.assertEqual(ready.status_code, 200, ready.text)
            self.assertEqual(ready.json()["payment_status"], "paid_pending_proof")
            self.assertEqual(ready.json()["received_sats"], 12_000)
            self.assertTrue(ready.json()["payment_ui_redacted"])
            self.assertIsNotNone(ready.json()["challenge"])

    def test_locked_checkout_pause_blocks_new_paid_activation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client, _settings = self.make_locked_client(
                Path(tmpdir),
                payment_mode="bitcoin-core",
                proof_mode="bitcoin-message",
                payments_paused=True,
            )
            response = client.post(
                "/v1/pages",
                json={
                    "title": "Paused Paid Activation",
                    "description": "Testing checkout pause behavior.",
                    "btc_address": "17YBEokEMaweQn9VatKEZTRVVHTeiKT4dq",
                    "tier": "tier2",
                },
            )
            self.assertEqual(response.status_code, 503, response.text)
            self.assertIn("temporarily paused", response.text)

    def test_payment_preflight_reports_receive_ready_without_funds(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            def fake_bitcoin_cli(args, wallet):
                command = args[0]
                if command == "getblockchaininfo":
                    return {"chain": "main", "initialblockdownload": False, "blocks": 941700, "headers": 941700}
                if command == "listwallets":
                    return ["fund-registry-payments"]
                if command == "listwalletdir":
                    return {"wallets": [{"name": "fund-registry-payments"}]}
                if command == "getbalances":
                    return {"mine": {"trusted": 0.0, "untrusted_pending": 0.0, "immature": 0.0}}
                raise AssertionError(f"Unexpected bitcoin-cli call: {args}")

            _client, settings = self.make_client(
                Path(tmpdir),
                payment_mode="bitcoin-core",
                payment_wallet_name="fund-registry-payments",
                proof_mode="mock",
                anchor_mode="disabled",
                bitcoin_cli_fn=fake_bitcoin_cli,
            )
            store = self.module.FundRegistryStore(settings)
            payload = store.payment_preflight_payload(require_funds=False)

            self.assertTrue(payload["wiring_ready"])
            self.assertTrue(payload["receive_ready"])
            self.assertTrue(payload["ready"])
            self.assertFalse(payload["checks"]["wallet_has_confirmed_funds"])
            self.assertEqual(payload["backend"]["wallet_name"], "fund-registry-payments")

    def test_public_page_renders_btc_qr_only_for_active_pages(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            client, settings = self.make_client(root)
            created = self.create_page(client)
            page = created["page"]

            active_page = client.get(f"/fund/{page['page_ref']}")
            self.assertEqual(active_page.status_code, 200, active_page.text)
            self.assertIn('src="data:image/png;base64,', active_page.text)
            self.assertIn('alt="Bitcoin QR code for Open Source Relay Infrastructure Fund"', active_page.text)
            self.assertNotIn("qr-placeholder", active_page.text)

            store = self.module.FundRegistryStore(settings)
            with store.connection() as connection:
                connection.execute("UPDATE pages SET public_state = 'expired' WHERE id = ?", (page["id"],))
                connection.commit()

            expired_page = client.get(f"/fund/{page['page_ref']}")
            self.assertEqual(expired_page.status_code, 200, expired_page.text)
            self.assertNotIn('src="data:image/png;base64,', expired_page.text)
            self.assertNotIn("Fund this campaign", expired_page.text)

    def test_public_page_hides_funding_details_when_payments_paused(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            client, _settings = self.make_client(root, payments_paused=True)
            created = self.create_page(client)
            page = created["page"]

            active_page = client.get(f"/fund/{page['page_ref']}")
            self.assertEqual(active_page.status_code, 200, active_page.text)
            self.assertIn("Bitcoin payments are temporarily paused", active_page.text)
            self.assertIn("QR codes and payment addresses are hidden", active_page.text)
            self.assertNotIn(page["btc_address"], active_page.text)
            self.assertNotIn('src="data:image/png;base64,', active_page.text)

    def test_public_page_hides_funding_details_when_payment_details_redacted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            client, _settings = self.make_client(root, payment_details_redacted=True)
            created = self.create_page(client)
            page = created["page"]

            active_page = client.get(f"/fund/{page['page_ref']}")
            self.assertEqual(active_page.status_code, 200, active_page.text)
            self.assertIn("payment details are intentionally hidden during invite-code testing", active_page.text)
            self.assertIn("QR codes and payment addresses are hidden", active_page.text)
            self.assertNotIn(page["btc_address"], active_page.text)
            self.assertNotIn('src="data:image/png;base64,', active_page.text)

    def test_public_page_shows_compromised_warning(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            client, settings = self.make_client(root)
            created = self.create_page(client)
            page = created["page"]

            store = self.module.FundRegistryStore(settings)
            with store.connection() as connection:
                connection.execute("UPDATE pages SET public_state = 'compromised' WHERE id = ?", (page["id"],))
                connection.commit()

            compromised_page = client.get(f"/fund/{page['page_ref']}")
            self.assertEqual(compromised_page.status_code, 200, compromised_page.text)
            self.assertIn("marked compromised", compromised_page.text)
            self.assertNotIn("Fund this campaign", compromised_page.text)

    def test_redacted_tier2_activation_keeps_opaque_slug_and_public_page_hides_address(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            client, settings = self.make_client(
                root,
                payment_mode="mock",
                proof_mode="mock",
                anchor_mode="disabled",
                payment_details_redacted=True,
            )
            store = self.module.FundRegistryStore(settings)
            store.create_promo_code(code="TESTBADGE", valid_for_badge=True, valid_for_vanity=False, max_uses=1)

            created = self.create_page(client)
            page = created["page"]
            campaign_key = created["campaign_key"]

            apply = client.post(
                f"/v1/pages/{page['id']}/promo/apply",
                json={"campaign_key": campaign_key, "code": "TESTBADGE", "target_tier": "tier2"},
            )
            self.assertEqual(apply.status_code, 200, apply.text)
            activated_page = apply.json()["page"]
            self.assertEqual(activated_page["tier"], "tier2")
            self.assertEqual(activated_page["slug_kind"], "random")
            self.assertNotEqual(activated_page["page_ref"], activated_page["btc_address"])
            self.assertNotIn(activated_page["btc_address"], activated_page["verify_url"])

            prepare = client.post(
                f"/v1/pages/{page['id']}/proof/prepare",
                json={"campaign_key": campaign_key},
            )
            self.assertEqual(prepare.status_code, 200, prepare.text)
            verify = client.post(
                f"/v1/pages/{page['id']}/proof/verify",
                json={
                    "campaign_key": campaign_key,
                    "challenge_id": prepare.json()["challenge"]["id"],
                    "proof": "mock-valid",
                },
            )
            self.assertEqual(verify.status_code, 200, verify.text)
            verified_page = verify.json()["page"]
            self.assertEqual(verified_page["page_ref"], activated_page["page_ref"])

            active_page = client.get(f"/fund/{verified_page['page_ref']}")
            self.assertEqual(active_page.status_code, 200, active_page.text)
            self.assertIn("payment details are intentionally hidden during invite-code testing", active_page.text)
            self.assertNotIn(verified_page["btc_address"], active_page.text)

    def test_manage_verify_creates_fresh_challenge_for_paid_page(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client, _settings = self.make_client(Path(tmpdir))
            created = self.create_page(client, tier="tier2")
            payment = created["payment_intent"]
            page = self.activate_payment(client, payment["id"])["page"]
            campaign_key = created["campaign_key"]

            verify_prepare = client.post(
                f"/v1/pages/{page['id']}/verify",
                json={"campaign_key": campaign_key},
            )
            self.assertEqual(verify_prepare.status_code, 200, verify_prepare.text)
            prepared = verify_prepare.json()
            self.assertIsNone(prepared["payment_intent"])
            self.assertEqual(prepared["tier"], "tier2")
            self.assertTrue(prepared["challenge_text"].startswith("{"))
            self.assertEqual(prepared["challenge_payload"]["challenge_id"], prepared["challenge_id"])
            self.assertEqual(prepared["challenge_payload"]["canonical_proof_payload_hash"], prepared["payload_hash"])
            self.assertEqual(prepared["challenge_hash"], self.module.sha256_hex(prepared["challenge_text"]))
            self.assertNotEqual(prepared["challenge_text"], prepared["payload_json"])

            verified = client.post(
                f"/v1/proofs/{prepared['challenge_id']}/verify",
                json={"proof": "mock-valid"},
            )
            self.assertEqual(verified.status_code, 200, verified.text)
            self.assertEqual(verified.json()["page"]["id"], page["id"])
            self.assertEqual(verified.json()["proof_record"]["purpose"], "verify")

    def test_bitcoin_message_verification_uses_one_time_challenge_payload(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            settings = self.module.FundRegistrySettings(
                db_path=root / "fundregistry.db",
                static_dir=REPO_ROOT / "services" / "fund-registry" / "static",
                photo_dir=root / "story-photos",
                transaction_cache_dir=root / "tx-cache",
                public_base_url="https://fundregistry.test",
                allow_dev_actions=False,
                payment_mode="mock",
                proof_mode="bitcoin-message",
                anchor_mode="disabled",
                sats_per_usd=1000,
                fetch_json_fn=self.fake_fetch_json,
            )
            store = self.module.FundRegistryStore(settings)
            created = store.create_page(
                self.module.CreatePageRequest(
                    title="Wallet Proof Boundary Test",
                    description="Exercise the Bitcoin-message verifier path with a one-time challenge.",
                    btc_address="17YBEokEMaweQn9VatKEZTRVVHTeiKT4dq",
                    tier="tier2",
                )
            )
            payment = created["payment_intent"]
            self.assertIsNotNone(payment)
            store.mark_payment_paid(payment["id"])
            challenge = store.get_challenge_for_payment(payment["id"])
            self.assertIsNotNone(challenge)

            captured = {}

            def fake_verify(address: str, signature: str, message: str) -> bool:
                captured["address"] = address
                captured["signature"] = signature
                captured["message"] = message
                return True

            store._verify_bitcoin_message = fake_verify  # type: ignore[method-assign]
            verified = store.verify_challenge(challenge["id"], "signed-proof")

            self.assertEqual(captured["address"], created["page"]["btc_address"])
            self.assertEqual(captured["signature"], "signed-proof")
            self.assertEqual(captured["message"], challenge["challenge_text"])
            self.assertEqual(challenge["challenge_payload"]["challenge_id"], challenge["id"])
            self.assertEqual(
                challenge["challenge_payload"]["canonical_proof_payload_hash"],
                challenge["payload_hash"],
            )
            self.assertIn("nonce", challenge["challenge_payload"])
            self.assertNotEqual(challenge["challenge_text"], challenge["payload_json"])
            self.assertEqual(
                verified["proof_record"]["challenge"]["challenge_hash"],
                self.module.sha256_hex(challenge["challenge_text"]),
            )

    def test_bip322_simple_vector_verifies_for_bc1q_address(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            settings = self.module.FundRegistrySettings(
                db_path=root / "fundregistry.db",
                static_dir=REPO_ROOT / "services" / "fund-registry" / "static",
                photo_dir=root / "story-photos",
                transaction_cache_dir=root / "tx-cache",
                public_base_url="https://fundregistry.test",
                allow_dev_actions=False,
                payment_mode="mock",
                proof_mode="mixed",
                anchor_mode="disabled",
                sats_per_usd=1000,
                fetch_json_fn=self.fake_fetch_json,
            )
            store = self.module.FundRegistryStore(settings)
            address = "bc1q9vza2e8x573nczrlzms0wvx3gsqjx7vavgkx0l"
            signature = (
                "AkcwRAIgZRfIY3p7/DoVTty6YZbWS71bc5Vct9p9Fia83eRmw2QCICK/ENGfwLtptFluMGs2KsqoNSk89pO7F29zJLUx9a/sASECx/EgAxlkQpQ9hYjgGu6EBCPMVPwVIVJqO4XCsMvViHI="
            )
            self.assertTrue(store._verify_bip322_simple(address, signature, "Hello World"))
            self.assertFalse(store._verify_bip322_simple(address, signature, "Hello World!"))

    def test_bip322_verification_uses_one_time_challenge_payload(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            settings = self.module.FundRegistrySettings(
                db_path=root / "fundregistry.db",
                static_dir=REPO_ROOT / "services" / "fund-registry" / "static",
                photo_dir=root / "story-photos",
                transaction_cache_dir=root / "tx-cache",
                public_base_url="https://fundregistry.test",
                allow_dev_actions=False,
                payment_mode="mock",
                proof_mode="mixed",
                anchor_mode="disabled",
                sats_per_usd=1000,
                fetch_json_fn=self.fake_fetch_json,
            )
            store = self.module.FundRegistryStore(settings)
            created = store.create_page(
                self.module.CreatePageRequest(
                    title="Modern Wallet Proof Boundary Test",
                    description="Exercise the BIP-322 verifier path with a one-time challenge.",
                    btc_address="bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh",
                    tier="tier2",
                )
            )
            payment = created["payment_intent"]
            self.assertIsNotNone(payment)
            store.mark_payment_paid(payment["id"])
            challenge = store.get_challenge_for_payment(payment["id"])
            self.assertIsNotNone(challenge)
            self.assertEqual(challenge["proof_method"], "bip322-simple")

            captured = {}

            def fake_verify(address: str, signature: str, message: str) -> bool:
                captured["address"] = address
                captured["signature"] = signature
                captured["message"] = message
                return True

            store._verify_bip322_simple = fake_verify  # type: ignore[method-assign]
            verified = store.verify_challenge(challenge["id"], "bip322-proof")

            self.assertEqual(captured["address"], created["page"]["btc_address"])
            self.assertEqual(captured["signature"], "bip322-proof")
            self.assertEqual(captured["message"], challenge["challenge_text"])
            self.assertEqual(verified["proof_record"]["signature_method"], "bip322-simple")
            self.assertEqual(
                verified["proof_record"]["challenge"]["challenge_hash"],
                self.module.sha256_hex(challenge["challenge_text"]),
            )

    def test_recovery_challenge_uses_bip322_for_supported_bc1q_page(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            settings = self.module.FundRegistrySettings(
                db_path=root / "fundregistry.db",
                static_dir=REPO_ROOT / "services" / "fund-registry" / "static",
                photo_dir=root / "story-photos",
                transaction_cache_dir=root / "tx-cache",
                public_base_url="https://fundregistry.test",
                allow_dev_actions=False,
                payment_mode="mock",
                proof_mode="mixed",
                anchor_mode="disabled",
                sats_per_usd=1000,
                fetch_json_fn=self.fake_fetch_json,
            )
            store = self.module.FundRegistryStore(settings)
            created = store.create_page(
                self.module.CreatePageRequest(
                    title="Recovery Proof Method Test",
                    description="Ensure recovery stays on the modern proof path.",
                    btc_address="bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh",
                    tier="tier2",
                )
            )
            payment = created["payment_intent"]
            self.assertIsNotNone(payment)
            store.mark_payment_paid(payment["id"])
            challenge = store.get_challenge_for_payment(payment["id"])
            self.assertIsNotNone(challenge)

            def fake_verify(_address: str, _signature: str, _message: str) -> bool:
                return True

            store._verify_bip322_simple = fake_verify  # type: ignore[method-assign]
            verify_result = store.verify_challenge(challenge["id"], "bip322-proof")
            self.assertIsNotNone(verify_result["page"]["wallet_proof_verified_at"])

            recovery = store.create_recovery_challenge(verify_result["page"]["page_ref"])
            self.assertEqual(recovery["proof_method"], "bip322-simple")
            self.assertEqual(recovery["payload"]["btc_address"], created["page"]["btc_address"])

    def test_promo_code_validate_and_apply_flow(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            client, settings = self.make_locked_client(root)
            store = self.module.FundRegistryStore(settings)
            store.create_promo_code(code="TESTBADGE", valid_for_badge=True, valid_for_vanity=False, max_uses=1)
            store.create_promo_code(code="TESTVIP", valid_for_badge=False, valid_for_vanity=True, max_uses=1)
            store.create_promo_code(
                code="EXPIRED",
                valid_for_badge=True,
                valid_for_vanity=False,
                max_uses=1,
                expires_at=settings.now_fn() - self.module.dt.timedelta(days=1),
            )

            created = self.create_page(client)
            page = created["page"]
            campaign_key = created["campaign_key"]

            badge_validate = client.post(
                "/v1/promo/validate",
                json={"campaign_key": campaign_key, "code": "TESTBADGE"},
            )
            self.assertEqual(badge_validate.status_code, 200, badge_validate.text)
            self.assertTrue(badge_validate.json()["valid"])
            self.assertEqual(badge_validate.json()["eligible_tiers"], ["tier2"])

            badge_apply = client.post(
                f"/v1/pages/{page['id']}/promo/apply",
                json={"campaign_key": campaign_key, "code": "TESTBADGE", "target_tier": "tier2"},
            )
            self.assertEqual(badge_apply.status_code, 200, badge_apply.text)
            badge_page = badge_apply.json()["page"]
            self.assertEqual(badge_page["tier"], "tier2")
            self.assertEqual(badge_page["page_ref"], badge_page["btc_address"])

            exhausted = client.post(
                "/v1/promo/validate",
                json={"campaign_key": campaign_key, "code": "TESTBADGE"},
            )
            self.assertEqual(exhausted.status_code, 200, exhausted.text)
            self.assertFalse(exhausted.json()["valid"])
            self.assertEqual(exhausted.json()["reason"], "exhausted")

            exhausted_apply = client.post(
                f"/v1/pages/{page['id']}/promo/apply",
                json={"campaign_key": campaign_key, "code": "TESTBADGE", "target_tier": "tier2"},
            )
            self.assertEqual(exhausted_apply.status_code, 409, exhausted_apply.text)
            self.assertIn("no longer available", exhausted_apply.text)

            expired = client.post(
                "/v1/promo/validate",
                json={"campaign_key": campaign_key, "code": "EXPIRED"},
            )
            self.assertEqual(expired.status_code, 200, expired.text)
            self.assertFalse(expired.json()["valid"])
            self.assertEqual(expired.json()["reason"], "expired")

            vanity_validate = client.post(
                "/v1/promo/validate",
                json={"campaign_key": campaign_key, "code": "TESTVIP", "target_tier": "tier3"},
            )
            self.assertEqual(vanity_validate.status_code, 200, vanity_validate.text)
            self.assertFalse(vanity_validate.json()["valid"])
            self.assertTrue(vanity_validate.json()["requires_vanity_slug"])

            vanity_apply_missing_slug = client.post(
                f"/v1/pages/{page['id']}/promo/apply",
                json={"campaign_key": campaign_key, "code": "TESTVIP", "target_tier": "tier3"},
            )
            self.assertEqual(vanity_apply_missing_slug.status_code, 400)
            self.assertIn("Tier3 slug", vanity_apply_missing_slug.text)

            vanity_apply = client.post(
                f"/v1/pages/{page['id']}/promo/apply",
                json={
                    "campaign_key": campaign_key,
                    "code": "TESTVIP",
                    "target_tier": "tier3",
                    "vanity_slug": "relay-fund",
                },
            )
            self.assertEqual(vanity_apply.status_code, 200, vanity_apply.text)
            vanity_page = vanity_apply.json()["page"]
            self.assertEqual(vanity_page["tier"], "tier3")
            self.assertEqual(vanity_page["page_ref"], "relay-fund")

    def test_invite_code_apply_closes_pending_payment_intent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            client, settings = self.make_locked_client(
                root,
                payment_mode="mock",
                proof_mode="mock",
                anchor_mode="disabled",
            )
            store = self.module.FundRegistryStore(settings)
            store.create_promo_code(code="TESTBADGE", valid_for_badge=True, valid_for_vanity=False, max_uses=1)

            created = self.create_page(client, tier="tier2")
            page = created["page"]
            campaign_key = created["campaign_key"]
            payment = created["payment_intent"]
            self.assertEqual(payment["payment_status"], "pending")

            badge_apply = client.post(
                f"/v1/pages/{page['id']}/promo/apply",
                json={"campaign_key": campaign_key, "code": "TESTBADGE", "target_tier": "tier2"},
            )
            self.assertEqual(badge_apply.status_code, 200, badge_apply.text)
            self.assertEqual(badge_apply.json()["page"]["tier"], "tier2")

            managed = client.post("/v1/pages/manage", json={"campaign_key": campaign_key})
            self.assertEqual(managed.status_code, 200, managed.text)
            self.assertIsNone(managed.json()["payment_intent"])

            stored_payment = store.get_payment_intent(payment["id"])
            self.assertIsNotNone(stored_payment)
            self.assertEqual(stored_payment["status"], "expired")

    def test_invite_code_activation_still_requires_wallet_proof(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            client, settings = self.make_client(
                root,
                payment_mode="mock",
                proof_mode="mock",
                anchor_mode="disabled",
            )
            store = self.module.FundRegistryStore(settings)
            store.create_promo_code(code="TESTBADGE", valid_for_badge=True, valid_for_vanity=False, max_uses=1)

            created = self.create_page(client, tier="tier2")
            page = created["page"]
            campaign_key = created["campaign_key"]

            badge_apply = client.post(
                f"/v1/pages/{page['id']}/promo/apply",
                json={"campaign_key": campaign_key, "code": "TESTBADGE", "target_tier": "tier2"},
            )
            self.assertEqual(badge_apply.status_code, 200, badge_apply.text)
            self.assertEqual(badge_apply.json()["page"]["tier"], "tier2")
            self.assertIsNone(badge_apply.json()["page"]["wallet_proof_verified_at"])

            prepare = client.post(
                f"/v1/pages/{page['id']}/proof/prepare",
                json={"campaign_key": campaign_key},
            )
            self.assertEqual(prepare.status_code, 200, prepare.text)
            self.assertIsNone(prepare.json()["payment_intent"])

            challenge_id = prepare.json()["challenge"]["id"]
            verify = client.post(
                f"/v1/pages/{page['id']}/proof/verify",
                json={"campaign_key": campaign_key, "challenge_id": challenge_id, "proof": "mock-valid"},
            )
            self.assertEqual(verify.status_code, 200, verify.text)
            self.assertEqual(verify.json()["page"]["proof_status"], "verified")
            self.assertIsNotNone(verify.json()["page"]["wallet_proof_verified_at"])

    def test_paid_create_rejects_unsupported_bitcoin_message_address(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client, _settings = self.make_locked_client(
                Path(tmpdir),
                payment_mode="mock",
                proof_mode="bitcoin-message",
                anchor_mode="disabled",
            )

            created = client.post(
                "/v1/pages",
                json={
                    "title": "Proof-incompatible paid page",
                    "description": "This should fail before creating a paid checkout.",
                    "btc_address": "bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh",
                    "tier": "tier2",
                },
            )
            self.assertEqual(created.status_code, 400, created.text)
            self.assertIn("legacy Bitcoin address", created.text)

    def test_paid_create_allows_bc1q_address_in_mixed_mode(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client, _settings = self.make_locked_client(
                Path(tmpdir),
                payment_mode="mock",
                proof_mode="mixed",
                anchor_mode="disabled",
            )

            created = client.post(
                "/v1/pages",
                json={
                    "title": "Modern proof page",
                    "description": "This should create a paid checkout for a supported bc1q address.",
                    "btc_address": "bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh",
                    "tier": "tier2",
                },
            )
            self.assertEqual(created.status_code, 200, created.text)
            self.assertEqual(created.json()["payment_intent"]["payment_method"], "mock")

    def test_paid_create_rejects_unsupported_taproot_address_in_mixed_mode(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client, _settings = self.make_locked_client(
                Path(tmpdir),
                payment_mode="mock",
                proof_mode="mixed",
                anchor_mode="disabled",
            )

            created = client.post(
                "/v1/pages",
                json={
                    "title": "Unsupported taproot page",
                    "description": "Taproot support should stay honestly gated for now.",
                    "btc_address": "bc1p5cyxnuxmeuwuvkwfem96lxyepd7k4y8x8h7z7v2mga9y2j5h6xqs0v7v6t",
                    "tier": "tier2",
                },
            )
            self.assertEqual(created.status_code, 400, created.text)
            self.assertIn("Taproot addresses", created.text)

    def test_promo_apply_rejects_unsupported_bitcoin_message_address(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            client, settings = self.make_locked_client(
                root,
                payment_mode="mock",
                proof_mode="bitcoin-message",
                anchor_mode="disabled",
            )
            store = self.module.FundRegistryStore(settings)
            store.create_promo_code(code="TESTBADGE", valid_for_badge=True, valid_for_vanity=False, max_uses=1)

            created = self.create_page(client)
            page = created["page"]
            campaign_key = created["campaign_key"]

            validate = client.post(
                "/v1/promo/validate",
                json={"campaign_key": campaign_key, "code": "TESTBADGE", "target_tier": "tier2"},
            )
            self.assertEqual(validate.status_code, 200, validate.text)
            self.assertFalse(validate.json()["valid"])
            self.assertEqual(validate.json()["reason"], "unsupported_address")

            apply_code = client.post(
                f"/v1/pages/{page['id']}/promo/apply",
                json={"campaign_key": campaign_key, "code": "TESTBADGE", "target_tier": "tier2"},
            )
            self.assertEqual(apply_code.status_code, 400, apply_code.text)
            self.assertIn("legacy Bitcoin address", apply_code.text)

    def test_upgrade_free_to_tier3_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client, _settings = self.make_client(Path(tmpdir))
            created = self.create_page(client)
            page = created["page"]
            campaign_key = created["campaign_key"]

            response = client.post(
                f"/v1/pages/{page['id']}/upgrade",
                json={
                    "campaign_key": campaign_key,
                    "target_tier": "tier3",
                    "vanity_slug": "my-relay-fund",
                },
            )
            self.assertEqual(response.status_code, 400)
            self.assertIn("tier2 before tier3", response.text)

    def test_expired_tier2_and_dead_tier3_remain_historical(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client, settings = self.make_client(Path(tmpdir))

            badge_created = self.create_page(client, tier="tier2")
            badge_payment = badge_created["payment_intent"]
            badge_verified = self.activate_payment(client, badge_payment["id"])
            badge_page = badge_verified["page"]

            store = self.module.FundRegistryStore(settings)
            with store.connection() as connection:
                connection.execute(
                    """
                    UPDATE pages
                    SET public_state = 'active',
                        active_until = '2026-03-01T00:00:00Z',
                        grace_until = '2026-04-20T00:00:00Z'
                    WHERE id = ?
                    """,
                    (badge_page["id"],),
                )
                connection.commit()

            expired_badge = client.get(f"/badge/{badge_page['page_ref']}.svg")
            self.assertEqual(expired_badge.status_code, 200)
            self.assertIn("Inactive", expired_badge.text)

            vanity_created = self.create_page(client, tier="tier3", vanity_slug="relay-fund")
            vanity_payment = vanity_created["payment_intent"]
            vanity_verified = self.activate_payment(client, vanity_payment["id"])
            vanity_page = vanity_verified["page"]

            with store.connection() as connection:
                connection.execute(
                    """
                    UPDATE pages
                    SET public_state = 'expired',
                        active_until = '2026-03-01T00:00:00Z',
                        grace_until = '2026-03-02T00:00:00Z'
                    WHERE id = ?
                    """,
                    (vanity_page["id"],),
                )
                connection.commit()

            store.sweep_pages(self.module.parse_timestamp("2026-03-03T00:00:00Z"))

            tombstone_page = client.get(f"/fund/{vanity_page['page_ref']}")
            self.assertEqual(tombstone_page.status_code, 200)
            self.assertIn("Event ledger", tombstone_page.text)
            self.assertIn('href="/manage"', tombstone_page.text)

            tombstone_badge = client.get(f"/badge/{vanity_page['page_ref']}.svg")
            self.assertEqual(tombstone_badge.status_code, 200)

            dead_button = client.get(f"/v1/pages/{vanity_page['id']}/button")
            self.assertEqual(dead_button.status_code, 200, dead_button.text)
            self.assertEqual(dead_button.json()["current_state"], "dead")
            self.assertEqual(dead_button.json()["verification_code"], vanity_page["verification_code"])

            dead_search = client.get(f"/v1/search?q={vanity_page['verification_code']}")
            self.assertEqual(dead_search.status_code, 200, dead_search.text)
            self.assertEqual(dead_search.json()["page"]["public_state"], "dead")

    def test_paid_activation_is_disabled_without_live_payment_and_proof(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client, _settings = self.make_locked_client(Path(tmpdir))

            badge_create = client.post(
                "/v1/pages",
                json={
                    "title": "Legal Defense Support",
                    "description": "Wallet-verified support page.",
                    "btc_address": "bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh",
                    "tier": "tier2",
                },
            )
            self.assertEqual(badge_create.status_code, 503, badge_create.text)
            self.assertIn("not enabled yet", badge_create.text)

            free_create = self.create_page(client)
            recovery = client.post("/v1/recover", json={"page_ref": free_create["page"]["page_ref"]})
            self.assertEqual(recovery.status_code, 503, recovery.text)

    def test_story_photo_upload_and_public_route(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client, _settings = self.make_client(Path(tmpdir))
            created = self.create_page(client)
            page = created["page"]
            campaign_key = created["campaign_key"]
            png_payload = (
                b"\x89PNG\r\n\x1a\n"
                b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
                b"\x00\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x04\x00\x01\xf6\x177\xd2"
                b"\x00\x00\x00\x00IEND\xaeB`\x82"
            )

            response = client.post(
                f"/v1/pages/{page['id']}/photo",
                json={
                    "campaign_key": campaign_key,
                    "content_type": "image/png",
                    "image_base64": base64.b64encode(png_payload).decode("ascii"),
                },
            )
            self.assertEqual(response.status_code, 200, response.text)
            uploaded_page = response.json()["page"]
            self.assertIsNotNone(uploaded_page["story_photo_url"])

            public_photo = client.get(f"/story-photo/{page['id']}")
            self.assertEqual(public_photo.status_code, 200, public_photo.text)
            self.assertEqual(public_photo.headers["content-type"], "image/png")
            self.assertEqual(public_photo.headers["cross-origin-resource-policy"], "cross-origin")

    def test_tier3_progress_photo_abort_and_proof_bundle(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client, _settings = self.make_client(Path(tmpdir))
            created = self.create_page(client, tier="tier3", vanity_slug="relay-fund")
            payment = created["payment_intent"]
            page = self.activate_payment(client, payment["id"])["page"]
            campaign_key = created["campaign_key"]
            self.assertEqual(page["proof_status"], "anchored")

            update = client.post(
                f"/v1/pages/{page['id']}/updates",
                json={"campaign_key": campaign_key, "body": "Infrastructure shipped."},
            )
            self.assertEqual(update.status_code, 200, update.text)

            png_payload = (
                b"\x89PNG\r\n\x1a\n"
                b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
                b"\x00\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x04\x00\x01\xf6\x177\xd2"
                b"\x00\x00\x00\x00IEND\xaeB`\x82"
            )
            progress = client.post(
                f"/v1/pages/{page['id']}/progress-photo",
                json={
                    "campaign_key": campaign_key,
                    "content_type": "image/png",
                    "image_base64": base64.b64encode(png_payload).decode("ascii"),
                },
            )
            self.assertEqual(progress.status_code, 200, progress.text)
            self.assertIsNotNone(progress.json()["page"]["progress_photo_url"])

            public_progress = client.get(f"/progress-photo/{page['id']}")
            self.assertEqual(public_progress.status_code, 200, public_progress.text)
            self.assertEqual(public_progress.headers["content-type"], "image/png")
            self.assertEqual(public_progress.headers["cross-origin-resource-policy"], "cross-origin")

            verify = client.get(f"/v1/pages/{page['id']}/verify")
            self.assertEqual(verify.status_code, 200, verify.text)
            self.assertEqual(verify.json()["proof_status"], "anchored")
            self.assertTrue(any(event["event_type"] == "activated" for event in verify.json()["events"]))
            self.assertEqual(
                verify.json()["proof_record"]["challenge"]["challenge_payload"]["canonical_proof_payload_hash"],
                verify.json()["proof_record"]["payload_hash"],
            )

            bundle = client.get(f"/v1/pages/{page['id']}/proof-bundle")
            self.assertEqual(bundle.status_code, 200, bundle.text)
            self.assertEqual(bundle.headers["content-type"].split(";")[0], "application/json")
            self.assertIn("attachment;", bundle.headers["content-disposition"])
            bundle_payload = bundle.json()
            self.assertEqual(bundle_payload["latest_anchor_event"]["anchor_receipt"]["format"], "FRG1")
            self.assertEqual(bundle_payload["latest_anchor_event"]["anchor_receipt"]["event_type"], "activated")
            self.assertTrue(bundle_payload["latest_anchor_event"]["anchor_receipt"]["op_return_hex"].startswith("465247310101"))
            self.assertEqual(
                bundle_payload["latest_anchor_event"]["anchor_receipt"]["digest_hex"],
                bundle_payload["proof_record"]["payload_hash"],
            )

            verify_page = client.get(f"/verify/{page['page_ref']}")
            self.assertEqual(verify_page.status_code, 200, verify_page.text)
            self.assertIn("Verification record", verify_page.text)
            self.assertIn("Signed one-time challenge", verify_page.text)
            self.assertIn("FRG1 receipt", verify_page.text)
            self.assertIn("465247310101", verify_page.text)

            abort = client.post(
                f"/v1/pages/{page['id']}/abort",
                json={"campaign_key": campaign_key},
            )
            self.assertEqual(abort.status_code, 200, abort.text)
            self.assertEqual(abort.json()["public_state"], "aborted")

            compromised = client.post(
                f"/v1/pages/{page['id']}/compromise",
                json={"campaign_key": campaign_key},
            )
            self.assertEqual(compromised.status_code, 409, compromised.text)

    def test_tier3_bitcoin_core_anchor_broadcasts_and_confirms(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            anchor_state = {
                "confirmations": 0,
                "txid": "ab" * 32,
                "op_return_hex": None,
                "calls": [],
            }

            def fake_bitcoin_cli(args, wallet):
                anchor_state["calls"].append((list(args), wallet))
                command = args[0]
                if command == "getblockchaininfo":
                    return {"initialblockdownload": False, "blocks": 941591, "headers": 941591}
                if command == "listwallets":
                    return ["fund-registry-anchor"]
                if command == "listwalletdir":
                    return {"wallets": [{"name": "fund-registry-anchor"}]}
                if command == "getbalances":
                    return {"mine": {"trusted": 0.01234567, "untrusted_pending": 0.0, "immature": 0.0}}
                if command == "createrawtransaction":
                    outputs = json.loads(args[2])
                    anchor_state["op_return_hex"] = outputs["data"]
                    return "raw-anchor"
                if command == "fundrawtransaction":
                    return {"hex": "funded-anchor"}
                if command == "signrawtransactionwithwallet":
                    return {"hex": "signed-anchor", "complete": True}
                if command == "sendrawtransaction":
                    return anchor_state["txid"]
                if command == "gettransaction":
                    payload = {
                        "txid": anchor_state["txid"],
                        "time": 1710000000,
                        "timereceived": 1710000000,
                        "confirmations": anchor_state["confirmations"],
                    }
                    if anchor_state["confirmations"] > 0:
                        payload.update(
                            {
                                "blockhash": "cd" * 32,
                                "blockheight": 900001,
                                "blocktime": 1710000600,
                            }
                        )
                    return payload
                raise AssertionError(f"Unexpected bitcoin-cli call: {args}")

            client, _settings = self.make_client(
                Path(tmpdir),
                anchor_mode="bitcoin-core",
                bitcoin_cli_fn=fake_bitcoin_cli,
            )
            created = self.create_page(client, tier="tier3", vanity_slug="relay-fund")
            page = created["page"]
            campaign_key = created["campaign_key"]
            payment = created["payment_intent"]

            paid = client.post(f"/v1/dev/payments/{payment['id']}/mark-paid")
            self.assertEqual(paid.status_code, 200, paid.text)
            challenge = paid.json()["challenge"]

            verified = client.post(
                f"/v1/pages/{page['id']}/proof/verify",
                json={
                    "campaign_key": campaign_key,
                    "challenge_id": challenge["id"],
                    "proof": "mock-valid",
                },
            )
            self.assertEqual(verified.status_code, 200, verified.text)
            verified_payload = verified.json()
            self.assertEqual(verified_payload["page"]["proof_status"], "anchor_pending")
            self.assertEqual(verified_payload["page"]["latest_anchor_event"]["anchor_status"], "broadcast")
            self.assertEqual(verified_payload["page"]["latest_anchor_event"]["anchor_txid"], anchor_state["txid"])
            self.assertEqual(
                anchor_state["op_return_hex"],
                verified_payload["page"]["latest_anchor_event"]["anchor_receipt"]["op_return_hex"],
            )
            self.assertTrue(anchor_state["op_return_hex"].startswith("465247310101"))
            self.assertEqual(
                verified_payload["page"]["latest_anchor_event"]["anchor_receipt"]["digest_hex"],
                verified_payload["proof_record"]["payload_hash"],
            )

            pending_verify = client.get(f"/v1/pages/{page['id']}/verify")
            self.assertEqual(pending_verify.status_code, 200, pending_verify.text)
            self.assertEqual(pending_verify.json()["proof_status"], "anchor_pending")

            anchor_state["confirmations"] = 1
            confirmed_verify = client.get(f"/v1/pages/{page['id']}/verify")
            self.assertEqual(confirmed_verify.status_code, 200, confirmed_verify.text)
            confirmed_payload = confirmed_verify.json()
            self.assertEqual(confirmed_payload["proof_status"], "anchored")
            self.assertEqual(confirmed_payload["latest_anchor_event"]["anchor_status"], "confirmed")
            self.assertEqual(confirmed_payload["latest_anchor_event"]["anchor_block_height"], 900001)
            self.assertEqual(confirmed_payload["latest_anchor_event"]["anchor_block_hash"], "cd" * 32)

            commands = [call[0][0] for call in anchor_state["calls"]]
            self.assertIn("sendrawtransaction", commands)
            self.assertIn("gettransaction", commands)

    def test_anchor_preflight_reports_ready_wiring_before_enablement(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            anchor_state = {"loaded_wallets": [], "loadwallet_calls": 0}

            def fake_bitcoin_cli(args, wallet):
                command = args[0]
                if command == "getblockchaininfo":
                    return {"chain": "main", "initialblockdownload": False, "blocks": 941591, "headers": 941591}
                if command == "listwallets":
                    return list(anchor_state["loaded_wallets"])
                if command == "listwalletdir":
                    return {"wallets": [{"name": "fund-registry-anchor"}]}
                if command == "loadwallet":
                    anchor_state["loadwallet_calls"] += 1
                    anchor_state["loaded_wallets"] = ["fund-registry-anchor"]
                    return {"name": "fund-registry-anchor", "warning": ""}
                if command == "getbalances":
                    return {"mine": {"trusted": 0.01234567, "untrusted_pending": 0.0, "immature": 0.0}}
                raise AssertionError(f"Unexpected bitcoin-cli call: {args}")

            _client, settings = self.make_client(
                Path(tmpdir),
                anchor_mode="disabled",
                bitcoin_cli_fn=fake_bitcoin_cli,
            )
            store = self.module.FundRegistryStore(settings)
            payload = store.anchor_preflight_payload(require_funds=True)

            self.assertEqual(payload["current_anchor_mode"], "disabled")
            self.assertTrue(payload["checks"]["bitcoin_cli_reachable"])
            self.assertTrue(payload["checks"]["chain_ready"])
            self.assertTrue(payload["checks"]["wallet_present"])
            self.assertTrue(payload["checks"]["wallet_loaded"])
            self.assertTrue(payload["checks"]["wallet_rpc_ready"])
            self.assertTrue(payload["checks"]["wallet_has_confirmed_funds"])
            self.assertTrue(payload["wallet"]["auto_load_attempted"])
            self.assertTrue(payload["wallet"]["auto_load_succeeded"])
            self.assertTrue(payload["wiring_ready"])
            self.assertTrue(payload["broadcast_ready"])
            self.assertTrue(payload["ready"])
            self.assertEqual(payload["wallet"]["balances_btc"]["trusted"], 0.01234567)
            self.assertIn("Set FUND_REGISTRY_ANCHOR_MODE=bitcoin-core", payload["next_step"])
            self.assertEqual(anchor_state["loadwallet_calls"], 1)

    def test_health_reports_explicit_anchor_backend_selection(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client, _settings = self.make_client(
                Path(tmpdir),
                anchor_mode="bitcoin-core",
                bitcoin_backend="ssh",
                bitcoin_backend_source="explicit",
                bitcoin_ssh_host="remote-bitcoin",
            )

            payload = client.get("/v1/health").json()

            self.assertEqual("bitcoind-ssh:remote-bitcoin", payload["anchor_backend"]["backend"])
            self.assertEqual("ssh", payload["anchor_backend"]["transport"])
            self.assertEqual("explicit", payload["anchor_backend"]["selection_source"])
            self.assertEqual("remote-bitcoin", payload["anchor_backend"]["ssh_host"])
            self.assertEqual(False, payload["anchor_backend"]["auto_failover"])
            self.assertTrue(payload["anchor_backend"]["operator_controlled"])

    def test_health_aliases_do_not_sweep_pages(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client, _settings = self.make_client(Path(tmpdir))
            store = client.app.state.store

            def fail_sweep(*_args, **_kwargs):
                raise AssertionError("health endpoints must not mutate page lifecycle state")

            store.sweep_pages = fail_sweep

            for path in ("/api/health", "/v1/health"):
                response = client.get(path)
                self.assertEqual(response.status_code, 200, path)
                self.assertEqual(response.json()["status"], "ok")

    def test_anchor_preflight_blocks_backend_that_is_behind_headers(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            def fake_bitcoin_cli(args, wallet):
                command = args[0]
                if command == "getblockchaininfo":
                    return {"chain": "main", "initialblockdownload": False, "blocks": 941580, "headers": 941591}
                if command == "listwallets":
                    return ["fund-registry-anchor"]
                if command == "listwalletdir":
                    return {"wallets": [{"name": "fund-registry-anchor"}]}
                if command == "getbalances":
                    return {"mine": {"trusted": 0.01, "untrusted_pending": 0.0, "immature": 0.0}}
                raise AssertionError(f"Unexpected bitcoin-cli call: {args}")

            _client, settings = self.make_client(
                Path(tmpdir),
                anchor_mode="disabled",
                bitcoin_cli_fn=fake_bitcoin_cli,
            )
            store = self.module.FundRegistryStore(settings)
            payload = store.anchor_preflight_payload(require_funds=False)

            self.assertFalse(payload["checks"]["chain_ready"])
            self.assertEqual(11, payload["chain"]["block_lag"])
            self.assertIn("behind headers", payload["blocking_reasons"][0])
            self.assertFalse(payload["ready"])

    def test_tier3_bitcoin_core_anchor_failure_does_not_activate_page(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            def fake_bitcoin_cli(args, wallet):
                command = args[0]
                if command == "getblockchaininfo":
                    return {"initialblockdownload": False, "blocks": 941591, "headers": 941591}
                if command == "listwallets":
                    return ["fund-registry-anchor"]
                if command == "listwalletdir":
                    return {"wallets": [{"name": "fund-registry-anchor"}]}
                if command == "getbalances":
                    return {"mine": {"trusted": 0.0, "untrusted_pending": 0.0, "immature": 0.0}}
                if command == "createrawtransaction":
                    return "raw-anchor"
                if command == "fundrawtransaction":
                    raise self.module.HTTPException(status_code=502, detail="Insufficient funds")
                raise AssertionError(f"Unexpected bitcoin-cli call: {args}")

            client, _settings = self.make_client(
                Path(tmpdir),
                anchor_mode="bitcoin-core",
                bitcoin_cli_fn=fake_bitcoin_cli,
            )
            created = self.create_page(client, tier="tier3", vanity_slug="relay-fund")
            page = created["page"]
            payment = created["payment_intent"]
            paid = client.post(f"/v1/dev/payments/{payment['id']}/mark-paid")
            self.assertEqual(paid.status_code, 200, paid.text)
            challenge = paid.json()["challenge"]

            verified = client.post(f"/v1/proofs/{challenge['id']}/verify", json={"proof": "mock-valid"})
            self.assertEqual(verified.status_code, 503, verified.text)
            self.assertIn("anchor wallet is not funded yet", verified.text)

            payment_view = client.get(f"/v1/payments/{payment['id']}")
            self.assertEqual(payment_view.status_code, 200, payment_view.text)
            self.assertEqual(payment_view.json()["status"], "paid_pending_proof")
            self.assertEqual(payment_view.json()["challenge"]["status"], "pending")

            page_view = client.post("/v1/pages/manage", json={"campaign_key": created["campaign_key"]})
            self.assertEqual(page_view.status_code, 200, page_view.text)
            self.assertEqual(page_view.json()["tier"], "free")
            self.assertEqual(page_view.json()["requested_tier"], "tier3")

            proof_view = client.get(f"/v1/pages/{page['id']}/proof")
            self.assertEqual(proof_view.status_code, 200, proof_view.text)
            self.assertIsNone(proof_view.json()["proof_record"])

    def test_anchor_preflight_reports_unfunded_wallet(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            def fake_bitcoin_cli(args, wallet):
                command = args[0]
                if command == "getblockchaininfo":
                    return {"chain": "main", "initialblockdownload": False, "blocks": 941591, "headers": 941591}
                if command == "listwallets":
                    return ["fund-registry-anchor"]
                if command == "listwalletdir":
                    return {"wallets": [{"name": "fund-registry-anchor"}]}
                if command == "getbalances":
                    return {"mine": {"trusted": 0.0, "untrusted_pending": 0.0, "immature": 0.0}}
                raise AssertionError(f"Unexpected bitcoin-cli call: {args}")

            _client, settings = self.make_client(
                Path(tmpdir),
                anchor_mode="disabled",
                bitcoin_cli_fn=fake_bitcoin_cli,
            )
            store = self.module.FundRegistryStore(settings)
            payload = store.anchor_preflight_payload(require_funds=True)

            self.assertTrue(payload["wiring_ready"])
            self.assertFalse(payload["broadcast_ready"])
            self.assertFalse(payload["ready"])
            self.assertFalse(payload["checks"]["wallet_has_confirmed_funds"])
            self.assertIn("Bitcoin anchor wallet has no confirmed funds.", payload["blocking_reasons"])
            self.assertIn("Resolve the blocking reasons", payload["next_step"])

    def test_rejects_unsafe_public_link_schemes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client, _settings = self.make_client(Path(tmpdir))
            response = client.post(
                "/v1/pages",
                json={
                    "title": "Unsafe Link Test",
                    "description": "This page exists to verify Fund Registry rejects unsafe public link schemes.",
                    "btc_address": "bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh",
                    "tier": "free",
                    "links": [{"platform": "website", "url": "javascript:alert(1)"}],
                },
            )
            self.assertEqual(response.status_code, 400, response.text)
            self.assertIn("https://", response.text)

    def test_legacy_unsafe_links_are_dropped_on_read(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            client, settings = self.make_client(root)
            created = self.create_page(client)
            page = created["page"]
            store = self.module.FundRegistryStore(settings)
            with store.connection() as connection:
                connection.execute(
                    "UPDATE pages SET links_json = ? WHERE id = ?",
                    (json.dumps([{"platform": "site", "url": "javascript:alert(1)"}]), page["id"]),
                )
                connection.commit()

            managed = client.post("/v1/pages/manage", json={"campaign_key": created["campaign_key"]})
            self.assertEqual(managed.status_code, 200, managed.text)
            self.assertEqual(managed.json()["links"], [])

            public_page = client.get(f"/fund/{page['page_ref']}")
            self.assertEqual(public_page.status_code, 200, public_page.text)
            self.assertNotIn('href="javascript:alert(1)"', public_page.text)

    def test_html_responses_send_security_headers_and_untrusted_hosts_are_blocked(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client, _settings = self.make_client(Path(tmpdir))
            response = client.get("/", headers={"x-forwarded-proto": "https"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn("script-src 'self'", response.headers["content-security-policy"])
            self.assertIn("style-src-attr", response.headers["content-security-policy"])
            self.assertNotIn("'unsafe-inline'", response.headers["content-security-policy"])
            self.assertEqual(response.headers["x-content-type-options"], "nosniff")
            self.assertEqual(response.headers["x-frame-options"], "DENY")
            self.assertEqual(response.headers["cross-origin-resource-policy"], "same-origin")
            self.assertEqual(
                response.headers["strict-transport-security"],
                "max-age=31536000; includeSubDomains; preload",
            )

            manage = client.get("/manage", headers={"x-forwarded-proto": "https"})
            self.assertEqual(manage.status_code, 200, manage.text)
            self.assertIn("style-src-attr 'unsafe-hashes'", manage.headers["content-security-policy"])
            self.assertNotIn("'unsafe-inline'", manage.headers["content-security-policy"])

            blocked = client.get("/", headers={"host": "evil.example"})
            self.assertEqual(blocked.status_code, 400, blocked.text)

    def test_robots_and_sitemap_routes_serve_static_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client, _settings = self.make_client(Path(tmpdir))

            robots = client.get("/robots.txt")
            self.assertEqual(robots.status_code, 200, robots.text)
            self.assertIn("text/plain", robots.headers.get("content-type", ""))
            self.assertIn("Sitemap: https://fundregistry.org/sitemap.xml", robots.text)

            sitemap = client.get("/sitemap.xml")
            self.assertEqual(sitemap.status_code, 200, sitemap.text)
            self.assertIn("application/xml", sitemap.headers.get("content-type", ""))
            self.assertTrue(sitemap.text.startswith('<?xml version="1.0" encoding="UTF-8"?>\n'))
            self.assertIn("<loc>https://fundregistry.org/</loc>", sitemap.text)
            self.assertNotIn("<!doctype html", sitemap.text.lower())

    def test_badge_route_allows_cross_origin_embedding(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client, _settings = self.make_client(Path(tmpdir))
            created = self.create_page(client, tier="tier2")
            payment = created["payment_intent"]
            page = self.activate_payment(client, payment["id"])["page"]

            badge = client.get(f"/badge/{page['page_ref']}.svg")
            self.assertEqual(badge.status_code, 200, badge.text)
            self.assertEqual(badge.headers["cross-origin-resource-policy"], "cross-origin")
            self.assertEqual(
                badge.headers["cache-control"],
                "no-store, no-cache, max-age=0, must-revalidate",
            )

    def test_manage_auth_is_logged_and_rate_limited(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client, _settings = self.make_client(Path(tmpdir))
            created = self.create_page(client)
            campaign_key = created["campaign_key"]

            invalid_key = dict(campaign_key)
            invalid_key["secret"] = "frk_invalid"
            with self.assertLogs("fund_registry.security", level="INFO") as logs:
                invalid = client.post("/v1/pages/manage", json={"campaign_key": invalid_key})
                valid = client.post("/v1/pages/manage", json={"campaign_key": campaign_key})

            self.assertEqual(invalid.status_code, 403, invalid.text)
            self.assertEqual(valid.status_code, 200, valid.text)
            parsed = [json.loads(record.getMessage()) for record in logs.records]
            self.assertEqual(parsed[0]["event"], "campaign_key_auth")
            self.assertEqual(parsed[0]["outcome"], "failure")
            self.assertEqual(parsed[0]["action"], "manage")
            self.assertEqual(parsed[1]["event"], "campaign_key_auth")
            self.assertEqual(parsed[1]["outcome"], "success")
            self.assertEqual(parsed[1]["action"], "manage")

            for _ in range(8):
                response = client.post("/v1/pages/manage", json={"campaign_key": campaign_key})
                self.assertEqual(response.status_code, 200, response.text)

            limited = client.post("/v1/pages/manage", json={"campaign_key": campaign_key})
            self.assertEqual(limited.status_code, 429, limited.text)
            self.assertIn("Retry-After", limited.headers)

    def test_recovery_challenge_is_reused_then_refreshed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client, _settings = self.make_client(Path(tmpdir))
            created = self.create_page(client)
            page = created["page"]
            store = client.app.state.store

            first = client.post("/v1/recover", json={"page_ref": page["page_ref"]})
            self.assertEqual(first.status_code, 200, first.text)
            first_payload = first.json()

            second = client.post("/v1/recover", json={"page_ref": page["page_ref"]})
            self.assertEqual(second.status_code, 200, second.text)
            self.assertEqual(second.json()["challenge_id"], first_payload["challenge_id"])

            with store.connection() as connection:
                pending_count = connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM wallet_proof_challenges
                    WHERE page_id = ? AND purpose = 'recover' AND status = 'pending'
                    """,
                    (page["id"],),
                ).fetchone()[0]
                self.assertEqual(pending_count, 1)
                connection.execute(
                    "UPDATE wallet_proof_challenges SET expires_at = ? WHERE id = ?",
                    ("2020-01-01T00:00:00Z", first_payload["challenge_id"]),
                )
                connection.commit()

            refreshed = client.post("/v1/recover", json={"page_ref": page["page_ref"]})
            self.assertEqual(refreshed.status_code, 200, refreshed.text)
            refreshed_payload = refreshed.json()
            self.assertNotEqual(refreshed_payload["challenge_id"], first_payload["challenge_id"])

            with store.connection() as connection:
                rows = connection.execute(
                    """
                    SELECT id, status
                    FROM wallet_proof_challenges
                    WHERE page_id = ? AND purpose = 'recover'
                    ORDER BY created_at
                    """,
                    (page["id"],),
                ).fetchall()
            statuses = {row["id"]: row["status"] for row in rows}
            self.assertEqual(statuses[first_payload["challenge_id"]], "superseded")
            self.assertEqual(statuses[refreshed_payload["challenge_id"]], "pending")

    def test_recovery_route_is_logged_and_rate_limited(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client, _settings = self.make_client(Path(tmpdir))
            created = self.create_page(client)
            page_ref = created["page"]["page_ref"]

            with self.assertLogs("fund_registry.security", level="INFO") as logs:
                responses = [client.post("/v1/recover", json={"page_ref": page_ref}) for _ in range(6)]

            for response in responses[:5]:
                self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(responses[5].status_code, 429, responses[5].text)
            self.assertIn("Retry-After", responses[5].headers)

            parsed = [json.loads(record.getMessage()) for record in logs.records]
            self.assertTrue(
                any(entry["event"] == "recovery_challenge" and entry["outcome"] == "success" for entry in parsed)
            )
            limited = next(entry for entry in parsed if entry["event"] == "rate_limit_exceeded")
            self.assertEqual(limited["rule"], "recovery_challenge")

    def test_revoked_campaign_key_stays_generic_but_logs_reason_code(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client, _settings = self.make_client(Path(tmpdir))
            created = self.create_page(client)
            page = created["page"]
            campaign_key = created["campaign_key"]
            client.app.state.store.rotate_campaign_key(page["id"])

            with self.assertLogs("fund_registry.security", level="INFO") as logs:
                response = client.post("/v1/pages/manage", json={"campaign_key": campaign_key})

            self.assertEqual(response.status_code, 403, response.text)
            self.assertEqual(response.json()["detail"], "Campaign Key is invalid.")
            parsed = [json.loads(record.getMessage()) for record in logs.records]
            self.assertEqual(parsed[0]["event"], "campaign_key_auth")
            self.assertEqual(parsed[0]["outcome"], "failure")
            self.assertEqual(parsed[0]["reason_code"], "revoked")


if __name__ == "__main__":
    unittest.main()
