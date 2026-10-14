import io
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from src.core import StateStore
from web_app import _loop_lock, _loop_state, _run_once, app


class WebAppTests(unittest.TestCase):
    def tearDown(self):
        with _loop_lock:
            _loop_state.update(
                {
                    "running": False,
                    "rail": "base",
                    "network": "base",
                    "interval": 60.0,
                    "last_error": None,
                }
            )

    def test_index_loads(self):
        client = app.test_client()
        response = client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"AIscend", response.data)

    @unittest.skipUnless(shutil.which("node"), "Node.js required for browser JS syntax validation")
    def test_dashboard_inline_javascript_compiles(self):
        """HTTP 200 is insufficient if an inline JS syntax error bricks the UI."""
        html = app.test_client().get("/").get_data(as_text=True)
        scripts = re.findall(r"<script(?:\\s[^>]*)?>(.*?)</script>", html, re.DOTALL)
        self.assertTrue(scripts, "Dashboard must ship a JavaScript entrypoint")
        for script in scripts:
            result = subprocess.run(
                [shutil.which("node"), "--check"], input=script,
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_execution_evidence_is_a_compact_help_popover_not_a_status_banner(self):
        html = app.test_client().get("/").get_data(as_text=True)
        self.assertIn('id="primary-policy"', html)
        self.assertIn('class="info-tip"', html)
        self.assertIn('id="primaryPolicyReason"', html)
        self.assertNotIn('id="traderRealityBanner"', html)
        self.assertNotIn('eligible for live buy confirmation', html.lower())

    def test_dashboard_inherits_official_bean_brand_without_losing_controls(self):
        html = app.test_client().get("/").get_data(as_text=True)
        self.assertIn("BEAN | AIscend", html)
        self.assertIn("BEAN INFRASTRUCTURE", html)
        self.assertIn("bean-site-header", html)
        self.assertIn("bean-brand-badge", html)
        self.assertIn("bean-official.webp", html)
        self.assertIn("danieloculus0-bot.github.io/BEAN/", html)
        self.assertIn("--bg:#101713", html)
        self.assertIn("--green:#91e274", html)
        for section in ("overview", "research", "strategy-school",
                        "live-engine", "decision-journal"):
            self.assertIn('href="#' + section + '"', html)
            self.assertIn('id="' + section + '"', html)
        for required in ("runnerPill", "pet", "beanLabRun", "beanResearchToggle",
                         "executionRail", "walletSecret", "advancedKeyfile",
                         "decisions", "beanLivePolicy"):
            self.assertIn('id="' + required + '"', html)
        self.assertIn("Autonomous capital research and execution", html)

    def test_bean_gate_is_displayed_in_live_engine(self):
        html = app.test_client().get("/").get_data(as_text=True)
        self.assertIn('id="beanLivePolicy"', html)
        self.assertIn("BUY_CONFIRMATION_GATE", html)

    def test_bean_sprite_is_not_the_old_trader_thing(self):
        html = app.test_client().get("/").get_data(as_text=True)
        self.assertIn("Autonomous capital research and execution", html)
        self.assertNotIn("Autonomous capital game - one point", html)
        self.assertIn('<h2 id="traderThingTitle">BEAN</h2>', html)
        self.assertIn('<div class="petname">BEAN</div>', html)
        self.assertIn('width="192" height="192"', html)
        self.assertIn("48x48 Game Boy-inspired pixel canvas", html)
        self.assertIn("const waving=", html)
        self.assertNotIn("TRADER THING", html)
        self.assertNotIn("AIS-0", html)

    @unittest.skipUnless(shutil.which("node"), "Node.js required for sprite pixel validation")
    def test_bean_sprite_eyes_arms_and_expression_pixels(self):
        """Render real canvas calls through a tiny pixel-grid test double."""
        html = app.test_client().get("/").get_data(as_text=True)
        snippet = html.split("function drawPet(){", 1)[1].split("  const moods={", 1)[0]
        script = "function drawPet(){" + snippet + "\n}\n"
        script += """
        let petState='idle', petBlink=false;
        const pixels=Array.from({length:48},()=>Array(48).fill(''));
        const context={
          fillStyle:'',
          fillRect(x,y,w,h){
            const left=Math.max(0,Math.floor(x/4)),top=Math.max(0,Math.floor(y/4));
            const right=Math.min(48,Math.ceil((x+w)/4));
            const bottom=Math.min(48,Math.ceil((y+h)/4));
            for(let j=top;j<bottom;j++)for(let i=left;i<right;i++)
              pixels[j][i]=this.fillStyle;
          }
        };
        const $=()=>({width:192,height:192,getContext:()=>context});
        function check(condition,message){if(!condition)throw Error(message);}
        drawPet();
        check(pixels[26][19]==='#081108','left eye must be dark on bean');
        check(pixels[26][30]==='#081108','right eye must be dark on bean');
        check(pixels[26][24]!=='#0b100b' && pixels[26][24]!=='#081108',
              'face between eyes must remain green, not blacked out');
        check(pixels[19][6]!=='#0b100b','left waving arm missing');
        check(pixels[34][43]!=='#0b100b','right arm/hand missing');
        const normal=pixels[33][24];
        petState='down';drawPet();
        check(pixels[33][24]!==normal,'facial expressions must respond to states');
        console.log('BEAN 48px sprite: eyes, green face, both arms, expressions OK');
        """
        result = subprocess.run(
            [shutil.which("node"), "-"], input=script,
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_unattended_research_status_and_local_pause_api(self):
        client = app.test_client()
        sample = {"enabled": True, "worker_running": True,
                  "policy": {"status": "NOT_EVALUATED"},
                  "next_experiment_at": 1791750000}
        with patch("web_app._bean_supervisor") as watch:
            watch.status.return_value = sample
            watch.set_enabled.return_value = {**sample, "enabled": False}
            result = client.get("/api/bean-lab").get_json()
            self.assertEqual(result["research_watch"], sample)
            self.assertEqual(result["live_strategy_activation"], "RESEARCH_ONLY")
            self.assertEqual(client.post("/api/bean-research/enable",
                                         json={"enabled": "false"}).status_code, 400)
            pause = client.post("/api/bean-research/enable",
                                json={"enabled": False})
            self.assertEqual(pause.status_code, 200)
            self.assertFalse(pause.get_json()["research_watch"]["enabled"])
            watch.set_enabled.assert_called_once_with(False)

            remote = {"REMOTE_ADDR": "100.101.102.103"}
            self.assertEqual(client.post("/api/bean-research/enable",
                                         json={"enabled": False},
                                         environ_overrides=remote).status_code, 403)

    def test_unattended_research_controls_and_real_gate_status_visible(self):
        page = app.test_client().get("/").get_data(as_text=True)
        self.assertIn('id="beanResearchWatch"', page)
        self.assertIn('id="beanResearchToggle"', page)
        self.assertIn('id="beanLabGateStatus"', page)
        self.assertIn("/api/bean-research/enable", page)
        self.assertIn("Independent BEAN gate", page)

    def test_dashboard_cdp_connect_persists_only_after_wallet_validation(self):
        key = {"id": "sample-id", "privateKey": "sample-key"}
        client = app.test_client()
        with patch("web_app.CdpWallet") as wallet_cls, patch("web_app.CredentialVault") as vault_cls, patch.dict("os.environ", {}, clear=False):
            wallet_cls.return_value.ensure_account = AsyncMock(return_value="0x123")
            response = client.post(
                "/api/configure",
                data={
                    "key_file": (io.BytesIO(json.dumps(key).encode()), "wallet.json"),
                    "wallet_secret": "sample-wallet-secret",
                    "network": "base",
                },
            )
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.get_json()["persisted"])
            vault_cls.return_value.save.assert_called_once_with(
                "sample-id", "sample-key", "sample-wallet-secret"
            )
            wallet_cls.return_value.ensure_account.assert_awaited_once()

    def test_failed_cdp_connect_does_not_persist_wallet(self):
        key = {"id": "sample-id", "privateKey": "sample-key"}
        client = app.test_client()
        with patch("web_app.CdpWallet") as wallet_cls, patch("web_app.CredentialVault") as vault_cls, patch.dict("os.environ", {}, clear=False):
            wallet_cls.return_value.ensure_account = AsyncMock(
                side_effect=RuntimeError("invalid credentials")
            )
            response = client.post(
                "/api/configure",
                data={
                    "key_file": (io.BytesIO(json.dumps(key).encode()), "wallet.json"),
                    "wallet_secret": "sample-wallet-secret",
                    "network": "base",
                },
            )
            self.assertEqual(response.status_code, 400)
            vault_cls.return_value.save.assert_not_called()

    def test_dashboard_advanced_connect_persists_only_after_validation(self):
        key = {"name": "sample-advanced-id", "privateKey": "sample-advanced-key"}
        client = app.test_client()
        with patch("web_app.AdvancedTradeVault") as vault_cls, patch("web_app.AdvancedTradeSpot") as spot_cls:
            spot_cls.return_value.status.return_value.as_dict.return_value = {
                "configured": True, "tradable_spot_products": 1
            }
            response = client.post(
                "/api/configure-advanced",
                data={"key_file": (io.BytesIO(json.dumps(key).encode()), "advanced.json")},
            )
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.get_json()["persisted"])
            vault_cls.return_value.configure.assert_called_once_with(
                "sample-advanced-id", "sample-advanced-key"
            )
            vault_cls.return_value.save.assert_called_once_with(
                "sample-advanced-id", "sample-advanced-key"
            )
            spot_cls.return_value.status.assert_called_once()

    def test_failed_advanced_connect_does_not_persist_key(self):
        key = {"name": "sample-advanced-id", "privateKey": "sample-advanced-key"}
        client = app.test_client()
        with patch("web_app.AdvancedTradeVault") as vault_cls, patch("web_app.AdvancedTradeSpot") as spot_cls:
            spot_cls.return_value.status.side_effect = RuntimeError("permission denied")
            response = client.post(
                "/api/configure-advanced",
                data={"key_file": (io.BytesIO(json.dumps(key).encode()), "advanced.json")},
            )
            self.assertEqual(response.status_code, 400)
            vault_cls.return_value.save.assert_not_called()

    def test_failed_cycle_is_written_to_decision_journal(self):
        class BrokenEngine:
            def step(self):
                raise RuntimeError("market discovery exploded")

        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "advanced.db"
            store = StateStore(db_path)
            with patch("web_app._make_engine", return_value=(store, BrokenEngine())):
                with self.assertRaisesRegex(RuntimeError, "market discovery exploded"):
                    _run_once("base", "advanced")

            check = StateStore(db_path)
            try:
                row = check.latest_decisions(1)[0]
                self.assertEqual(row["status"], "CYCLE_ERROR")
                self.assertIn("market discovery exploded", row["rationale"])
            finally:
                check.close()

    def test_remote_monitor_requires_token_and_is_read_only(self):
        client = app.test_client()
        remote = {"REMOTE_ADDR": "100.101.102.103"}
        with patch.dict("os.environ", {"AISCEND_REMOTE_TOKEN": "test-only-secret"}):
            self.assertEqual(
                client.get("/monitor", environ_overrides=remote).status_code, 401
            )
            self.assertEqual(
                client.get("/monitor?token=test-only-secret",
                           environ_overrides=remote).status_code, 200
            )
            self.assertEqual(
                client.get("/api/advanced-status",
                           environ_overrides=remote).status_code, 401
            )
            self.assertEqual(
                client.post("/api/start", json={"rail": "advanced"},
                            environ_overrides=remote).status_code, 403
            )

    def test_remote_monitor_fails_closed_without_configured_token(self):
        client = app.test_client()
        with patch.dict("os.environ", {"AISCEND_REMOTE_TOKEN": ""}):
            self.assertEqual(
                client.get("/monitor", environ_overrides={
                    "REMOTE_ADDR": "100.101.102.103"}).status_code,
                401,
            )

    def test_bean_lab_endpoint_requires_local_access_and_does_not_trade(self):
        class FakeLab:
            def status(self):
                return {"running": False, "latest": None, "history": [], "retained_events": 0}
            def start(self):
                return {"running": True, "latest": {"id": "test-1"}, "history": []}
        with patch("web_app._bean_lab", FakeLab()):
            client = app.test_client()
            self.assertEqual(client.get("/api/bean-lab").get_json()["retained_events"], 0)
            start = client.post("/api/bean-lab/start")
            self.assertEqual(start.status_code, 202)
            self.assertEqual(start.get_json()["latest"]["id"], "test-1")
            self.assertEqual(client.post("/api/bean-lab/approve").status_code, 404)
            remote = {"REMOTE_ADDR": "100.101.102.103"}
            self.assertEqual(client.post("/api/bean-lab/start",
                                         environ_overrides=remote).status_code, 403)

    def test_homepage_exposes_explicit_research_only_controls(self):
        body = app.test_client().get("/").data
        self.assertIn(b"BEAN STRATEGY SCHOOL", body)
        self.assertIn(b"beanLabActivation", body)
        self.assertIn(b"beanLabMemory", body)
        self.assertIn(b"Historical holdout", body)
        self.assertIn(b"TRY AGAIN", body)
        self.assertIn(b"No approval required", body)
        self.assertNotIn(b"APPROVE CANDIDATE FOR REVIEW", body)

    def test_start_refuses_silent_rail_switch_while_runner_is_active(self):
        with _loop_lock:
            _loop_state.update(
                {
                    "running": True,
                    "rail": "base",
                    "network": "base",
                    "interval": 60.0,
                    "last_error": None,
                }
            )

        client = app.test_client()
        response = client.post(
            "/api/start",
            json={"network": "base", "rail": "advanced", "interval": 60},
        )
        self.assertEqual(response.status_code, 409)
        data = response.get_json()
        self.assertFalse(data["ok"])
        self.assertIn("already active", data["error"])


if __name__ == "__main__":
    unittest.main()
