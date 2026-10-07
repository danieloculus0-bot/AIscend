from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request

from src.advanced_live import AdvancedSpotEngine
from src.core import StateStore, app_data_dir
from src.live import LiveEngine
from src.opportunities import OpportunityUniverse
from src.venues import capability_snapshot
from src.wallets.advanced_trade import AdvancedTradeSpot, AdvancedTradeVault
from src.wallets.cdp_wallet import CdpWallet, CredentialVault


app = Flask(__name__)

_loop_lock = threading.Lock()
_stop_event = threading.Event()
_loop_thread: threading.Thread | None = None
_loop_state: dict[str, Any] = {
    "running": False,
    "rail": "base",
    "network": "base",
    "interval": 60.0,
    "last_error": None,
}


def _db_path(network: str, rail: str = "base") -> Path:
    if rail == "advanced":
        return app_data_dir() / "live-advanced.db"
    return app_data_dir() / f"live-{network}.db"


def _validate_network(network: str) -> str:
    if network not in {"base", "base-sepolia"}:
        raise ValueError("network must be base or base-sepolia")
    return network


def _validate_rail(rail: str) -> str:
    if rail not in {"base", "advanced"}:
        raise ValueError("rail must be base or advanced")
    return rail


def _make_engine(network: str, rail: str = "base"):
    rail = _validate_rail(rail)
    network = _validate_network(network)
    store = StateStore(_db_path(network, rail))
    if rail == "advanced":
        engine = AdvancedSpotEngine(store=store, rail=AdvancedTradeSpot())
    else:
        engine = LiveEngine(store=store, wallet=CdpWallet(network=network))
    return store, engine


def _rows_to_dicts(rows) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def _status_payload(network: str, rail: str = "base") -> dict[str, Any]:
    store, engine = _make_engine(network, rail)
    try:
        snap = engine.sync()
        return {
            "ok": True,
            "snapshot": snap,
            "decisions": _rows_to_dicts(store.latest_decisions(30)),
            "ledger": _rows_to_dicts(store.latest_ledger(30)),
            "runner": dict(_loop_state),
        }
    finally:
        store.close()


def _run_once(network: str, rail: str = "base") -> dict[str, Any]:
    store, engine = _make_engine(network, rail)
    try:
        snap = engine.step()
        decision_rows = store.latest_decisions(1)
        decision = dict(decision_rows[0]) if decision_rows else None
        return {
            "snapshot": snap,
            "decision": decision,
        }
    finally:
        store.close()


def _runner(network: str, interval: float, rail: str) -> None:
    with _loop_lock:
        _loop_state.update(
            {
                "running": True,
                "rail": rail,
                "network": network,
                "interval": interval,
                "last_error": None,
            }
        )

    try:
        while not _stop_event.is_set():
            try:
                _run_once(network, rail)
                with _loop_lock:
                    _loop_state["last_error"] = None
            except Exception as exc:
                with _loop_lock:
                    _loop_state["last_error"] = str(exc)
            _stop_event.wait(interval)
    finally:
        with _loop_lock:
            _loop_state["running"] = False


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/credentials")
def credentials():
    return jsonify({"configured": CredentialVault().configured()})


@app.get("/api/capabilities")
def capabilities():
    data = capability_snapshot()
    data["advanced_trade_configured"] = AdvancedTradeVault().configured()
    return jsonify({"ok": True, **data})


@app.get("/api/advanced-status")
def advanced_status():
    vault = AdvancedTradeVault()
    if not vault.configured():
        return jsonify({"ok": True, "configured": False})
    try:
        return jsonify({"ok": True, **AdvancedTradeSpot(vault).status().as_dict()})
    except Exception as exc:
        return jsonify({"ok": False, "configured": True, "error": str(exc)}), 400


@app.post("/api/configure-advanced")
def configure_advanced():
    key_file = request.files.get("key_file")
    if key_file is None:
        return jsonify({"ok": False, "error": "Advanced Trade API key JSON is required."}), 400

    try:
        data = json.load(key_file)
        api_key_id = str(data.get("id") or data.get("name") or "").strip()
        api_key_secret = str(
            data.get("privateKey") or data.get("private_key") or ""
        ).strip()
        if not api_key_id or not api_key_secret:
            raise ValueError("Advanced Trade key JSON is missing id/name or privateKey.")

        vault = AdvancedTradeVault()
        vault.configure(api_key_id, api_key_secret)
        status = AdvancedTradeSpot(vault).status()
        return jsonify({"ok": True, **status.as_dict()})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.get("/api/opportunities")
def opportunities():
    try:
        return jsonify({"ok": True, **OpportunityUniverse().collect()})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.post("/api/configure")
def configure():
    key_file = request.files.get("key_file")
    wallet_secret = request.form.get("wallet_secret", "").strip()
    network = _validate_network(request.form.get("network", "base"))

    if key_file is None:
        return jsonify({"ok": False, "error": "CDP API key JSON is required."}), 400
    if not wallet_secret:
        return jsonify({"ok": False, "error": "Wallet Secret is required."}), 400

    try:
        data = json.load(key_file)
        api_key_id = str(data.get("id") or data.get("name") or "").strip()
        api_key_secret = str(
            data.get("privateKey") or data.get("private_key") or ""
        ).strip()
        if not api_key_id or not api_key_secret:
            raise ValueError("CDP key JSON is missing id or privateKey.")

        os.environ["CDP_API_KEY_ID"] = api_key_id
        os.environ["CDP_API_KEY_SECRET"] = api_key_secret
        os.environ["CDP_WALLET_SECRET"] = wallet_secret

        import asyncio

        address = asyncio.run(CdpWallet(network=network).ensure_account())
        return jsonify({"ok": True, "address": address, "network": network})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.get("/api/status")
def status():
    network = _validate_network(request.args.get("network", "base"))
    rail = _validate_rail(request.args.get("rail", "base"))
    try:
        return jsonify(_status_payload(network, rail))
    except Exception as exc:
        return jsonify(
            {
                "ok": False,
                "error": str(exc),
                "runner": dict(_loop_state),
            }
        ), 400


@app.post("/api/run-once")
def run_once():
    payload = request.get_json(silent=True) or {}
    network = _validate_network(str(payload.get("network", "base")))
    rail = _validate_rail(str(payload.get("rail", "base")))
    try:
        result = _run_once(network, rail)
        return jsonify({"ok": True, **result})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.post("/api/start")
def start():
    global _loop_thread

    payload = request.get_json(silent=True) or {}
    network = _validate_network(str(payload.get("network", "base")))
    rail = _validate_rail(str(payload.get("rail", "base")))
    interval = max(5.0, float(payload.get("interval", 60.0)))

    with _loop_lock:
        if _loop_state["running"]:
            return jsonify({"ok": True, "runner": dict(_loop_state)})

        _stop_event.clear()
        _loop_thread = threading.Thread(
            target=_runner,
            args=(network, interval, rail),
            name="aiscend-live-runner",
            daemon=True,
        )
        _loop_thread.start()

    return jsonify({"ok": True, "runner": dict(_loop_state)})


@app.post("/api/stop")
def stop():
    _stop_event.set()
    return jsonify({"ok": True, "runner": dict(_loop_state)})


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
