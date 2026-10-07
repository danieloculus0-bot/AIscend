from __future__ import annotations

import asyncio
import json
import os
import threading
import time
import uuid
import re
import urllib.parse
import urllib.request
from decimal import Decimal
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request

from src.advanced_live import AdvancedSpotEngine
from src.audit import AuditTrail
from src.bean import BeanMemory
from src.core import Decision, StateStore, app_data_dir
from src.live import LiveEngine
from src.opportunities import OpportunityUniverse
from src.portfolio import choose_execution_rail, combine_portfolio
from src.venues import capability_snapshot
from src.wallets.advanced_trade import AdvancedTradeSpot, AdvancedTradeVault
from src.wallets.cdp_wallet import CdpWallet, CredentialVault


app = Flask(__name__)

_loop_lock = threading.Lock()
_stop_event = threading.Event()
_loop_thread: threading.Thread | None = None
_capital_lock = threading.Lock()
_snapshot_cache_lock = threading.Lock()
_snapshot_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_loop_state: dict[str, Any] = {
    "running": False,
    "rail": "auto",
    "network": "base",
    "interval": 60.0,
    "active_rail": None,
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
    if rail not in {"auto", "base", "advanced"}:
        raise ValueError("rail must be auto, base or advanced")
    return rail


def _copy_game_meta(source: StateStore, target: StateStore) -> None:
    """Keep one experiment clock/baseline when capital moves between rails."""
    baseline = source.get_meta("live_starting_value")
    if baseline is not None and float(baseline) > 0:
        target.set_meta("advanced_starting_value", baseline)

    rows = source.conn.execute(
        """
        SELECT key,value FROM meta
        WHERE key='game_started_at'
           OR key LIKE 'milestone_%'
           OR key LIKE 'level_%'
        """
    ).fetchall()
    for row in rows:
        target.set_meta(str(row["key"]), str(row["value"]))


def _make_engine(network: str, rail: str = "base"):
    rail = _validate_rail(rail)
    network = _validate_network(network)
    if rail == "auto":
        raise ValueError("auto is a coordinator mode, not a direct execution adapter")
    store = StateStore(_db_path(network, rail))
    if rail == "advanced":
        # live-base.db is the canonical experiment memory. Advanced keeps its
        # own execution journal/state, but BEAN and the game clock do not reset
        # just because capital crossed an internal bridge.
        shared = StateStore(_db_path("base", "base"))
        _copy_game_meta(shared, store)
        engine = AdvancedSpotEngine(
            store=store,
            rail=AdvancedTradeSpot(),
            bean=BeanMemory(shared.conn),
        )
        setattr(engine, "_shared_memory_store", shared)
    else:
        engine = LiveEngine(store=store, wallet=CdpWallet(network=network))
    return store, engine


def _close_engine_store(store: StateStore, engine: Any) -> None:
    shared = getattr(engine, "_shared_memory_store", None)
    try:
        store.close()
    finally:
        if shared is not None and shared is not store:
            shared.close()


def _rows_to_dicts(rows) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def _decision_journal(network: str, limit: int = 60) -> list[dict[str, Any]]:
    sources = (
        ("advanced", _db_path(network, "advanced")),
        (network, _db_path(network, "base")),
    )
    rows: list[dict[str, Any]] = []
    for rail_name, path in sources:
        store = StateStore(path)
        try:
            for row in store.latest_decisions(limit):
                item = dict(row)
                item["rail"] = rail_name
                rows.append(item)
        finally:
            store.close()
    rows.sort(key=lambda item: float(item.get("ts") or 0.0), reverse=True)
    return rows[:limit]


def _cache_snapshot(network: str, rail: str, snapshot: dict[str, Any]) -> None:
    key = f"{network}:{rail}"
    with _snapshot_cache_lock:
        _snapshot_cache[key] = (time.time(), snapshot)


def _invalidate_snapshot_cache() -> None:
    with _snapshot_cache_lock:
        _snapshot_cache.clear()


def _sync_rail_snapshot(
    network: str,
    rail: str,
    *,
    force: bool = False,
    max_age: float = 12.0,
) -> dict[str, Any] | None:
    if rail == "base" and not CredentialVault().configured():
        return None
    if rail == "advanced" and not AdvancedTradeVault().configured():
        return None

    key = f"{network}:{rail}"
    if not force:
        with _snapshot_cache_lock:
            cached = _snapshot_cache.get(key)
        if cached is not None and time.time() - cached[0] <= max_age:
            return cached[1]

    store, engine = _make_engine(network, rail)
    try:
        snap = engine.sync()
        _cache_snapshot(network, rail, snap)
        return snap
    finally:
        _close_engine_store(store, engine)


def _portfolio_status(network: str, requested_rail: str) -> dict[str, Any]:
    requested_rail = _validate_rail(requested_rail)
    base = _sync_rail_snapshot(network, "base")
    advanced = _sync_rail_snapshot(network, "advanced")

    if requested_rail == "auto":
        active_rail = choose_execution_rail(base, advanced)
    else:
        active_rail = requested_rail

    if active_rail == "advanced" and advanced is None and base is not None:
        active_rail = "base"
    elif active_rail == "base" and base is None and advanced is not None:
        active_rail = "advanced"

    game_store = StateStore(_db_path("base", "base"))
    try:
        snapshot = combine_portfolio(
            base=base,
            advanced=advanced,
            active_rail=active_rail,
            game_store=game_store,
        )
    finally:
        game_store.close()

    active_store = StateStore(_db_path(network, active_rail))
    try:
        ledger = _rows_to_dicts(active_store.latest_ledger(30))
    finally:
        active_store.close()

    return {
        "ok": True,
        "snapshot": snapshot,
        "decisions": _decision_journal(network),
        "ledger": ledger,
        "runner": dict(_loop_state),
        "selected_rail": requested_rail,
        "active_rail": active_rail,
        "suggested_rail": active_rail,
    }


def _status_payload(network: str, rail: str = "auto") -> dict[str, Any]:
    return _portfolio_status(network, rail)


def _run_once(network: str, rail: str = "auto") -> dict[str, Any]:
    rail = _validate_rail(rail)

    with _capital_lock:
        if rail == "auto":
            base = _sync_rail_snapshot(network, "base", force=True)
            advanced = _sync_rail_snapshot(network, "advanced", force=True)
            chosen = choose_execution_rail(base, advanced)
        else:
            chosen = rail

        store, engine = _make_engine(network, chosen)
        try:
            try:
                snap = engine.step()
            except Exception as exc:
                store.add_decision(
                    Decision(
                        "HOLD",
                        None,
                        0.0,
                        f"{chosen} cycle failed before completion: {exc}",
                    ),
                    "CYCLE_ERROR",
                )
                raise
            decision_rows = store.latest_decisions(1)
            decision = dict(decision_rows[0]) if decision_rows else None
            if decision is not None:
                decision["rail"] = chosen
            _invalidate_snapshot_cache()
            return {
                "snapshot": snap,
                "decision": decision,
                "active_rail": chosen,
            }
        finally:
            _close_engine_store(store, engine)


def _runner(network: str, interval: float, rail: str) -> None:
    with _loop_lock:
        _loop_state.update(
            {
                "running": True,
                "rail": rail,
                "network": network,
                "interval": interval,
                "active_rail": None,
                "last_error": None,
            }
        )

    try:
        while not _stop_event.is_set():
            try:
                cycle = _run_once(network, rail)
                with _loop_lock:
                    _loop_state["active_rail"] = cycle.get("active_rail") or rail
                    _loop_state["last_error"] = None
            except Exception as exc:
                with _loop_lock:
                    _loop_state["last_error"] = str(exc)
            _stop_event.wait(interval)
    finally:
        with _loop_lock:
            _loop_state["running"] = False



def _monitor_token_ok() -> bool:
    configured = os.getenv("AISCEND_REMOTE_TOKEN", "").strip()
    if not configured:
        return True
    supplied = (
        request.args.get("token", "").strip()
        or request.headers.get("X-AIscend-Token", "").strip()
    )
    return supplied == configured


def _active_monitor_payload() -> dict[str, Any]:
    rail = str(_loop_state.get("rail") or "auto")
    network = str(_loop_state.get("network") or "base")
    data = _status_payload(network, rail)

    snapshot = data.get("snapshot") or {}
    # Explicitly omit anything credential-like. This endpoint is read-only.
    return {
        "ok": True,
        "runner": data.get("runner") or {},
        "snapshot": snapshot,
        "decisions": data.get("decisions") or [],
        "rail": rail,
        "network": network,
    }


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/monitor")
def monitor():
    if not _monitor_token_ok():
        return "Unauthorized", 401
    return render_template(
        "monitor.html",
        token=request.args.get("token", ""),
    )


@app.get("/api/monitor/status")
def monitor_status():
    if not _monitor_token_ok():
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    try:
        return jsonify(_active_monitor_payload())
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.get("/api/candles")
def candles():
    product = str(request.args.get("product") or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9-]{3,40}", product):
        return jsonify({"ok": False, "error": "Invalid Coinbase product."}), 400

    try:
        end_ts = int(time.time())
        start_ts = end_ts - (60 * 5 * 60)
        client = AdvancedTradeVault().client()
        # Use the authenticated Advanced Trade candle endpoint. Some products
        # available to the portfolio return 404 from Coinbase's public-market
        # candle route even though the authenticated product route works.
        payload = client.get_candles(
            product_id=product,
            start=str(start_ts),
            end=str(end_ts),
            granularity="FIVE_MINUTE",
            limit=60,
        )
        raw = payload.to_dict() if hasattr(payload, "to_dict") else payload
        rows = (raw or {}).get("candles") or []

        candles = []
        for row in reversed(rows):
            if not isinstance(row, dict):
                continue
            candles.append(
                {
                    "time": int(row.get("start") or 0),
                    "low": float(row.get("low") or 0),
                    "high": float(row.get("high") or 0),
                    "open": float(row.get("open") or 0),
                    "close": float(row.get("close") or 0),
                    "volume": float(row.get("volume") or 0),
                }
            )
        return jsonify({"ok": True, "product": product, "candles": candles})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.get("/api/runner")
def runner_status():
    return jsonify({"ok": True, "runner": dict(_loop_state)})


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


@app.get("/api/bridge-status")
def bridge_status():
    try:
        base_wallet = CdpWallet(network="base")
        base_status = asyncio.run(base_wallet.status())
        base_usdc = next(
            (str(item.amount) for item in base_status.balances if item.symbol == "USDC"),
            "0",
        )

        advanced = AdvancedTradeSpot()
        advanced_status = advanced.status()
        advanced_usdc = next(
            (
                str(item.available)
                for item in advanced_status.balances
                if item.currency == "USDC"
            ),
            "0",
        )

        receive_address = None
        receive_error = None
        try:
            receive_address = advanced.receive_address("USDC", "base")
        except Exception as exc:
            receive_error = str(exc)

        return jsonify(
            {
                "ok": True,
                "base_address": base_status.address,
                "base_usdc": base_usdc,
                "advanced_usdc": advanced_usdc,
                "advanced_receive_address": receive_address,
                "advanced_receive_error": receive_error,
                "can_transfer": advanced_status.can_transfer,
                "can_trade": advanced_status.can_trade,
                "can_view": advanced_status.can_view,
            }
        )
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.post("/api/bridge/base-to-advanced")
def bridge_base_to_advanced():
    payload = request.get_json(silent=True) or {}
    try:
        amount = Decimal(str(payload.get("amount") or "0"))
        if amount <= 0:
            raise ValueError("Amount must be greater than zero.")

        advanced = AdvancedTradeSpot()
        destination = advanced.receive_address("USDC", "base")
        with _capital_lock:
            tx_hash = asyncio.run(
                CdpWallet(network="base").send_usdc(destination, amount)
            )
        _invalidate_snapshot_cache()
        audit_warning = None
        try:
            AuditTrail().record_transfer(
                venue="coinbase",
                rail="wallet-bridge",
                network="base",
                asset="USDC",
                quantity=amount,
                direction="BASE_TO_ADVANCED",
                from_location="base-smart-wallet",
                to_location="coinbase-advanced",
                transaction_id=tx_hash,
                status="complete",
                source="bridge_api",
                raw_provider_response={"user_op_hash": tx_hash},
            )
        except Exception as exc:
            audit_warning = str(exc)
        return jsonify(
            {
                "ok": True,
                "direction": "base_to_advanced",
                "target_rail": "advanced",
                "amount": str(amount),
                "destination": destination,
                "transaction": tx_hash,
                "audit_warning": audit_warning,
            }
        )
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.post("/api/bridge/advanced-to-base")
def bridge_advanced_to_base():
    payload = request.get_json(silent=True) or {}
    try:
        amount = Decimal(str(payload.get("amount") or "0"))
        if amount <= 0:
            raise ValueError("Amount must be greater than zero.")

        base_address = asyncio.run(CdpWallet(network="base").ensure_account())
        with _capital_lock:
            result = AdvancedTradeSpot().send_usdc_to_address(
                base_address,
                amount,
                network="base",
                idem=f"aiscend-bridge-{uuid.uuid4()}",
            )
        _invalidate_snapshot_cache()
        audit_warning = None
        try:
            audit = AuditTrail()
            audit.record_transfer(
                venue="coinbase",
                rail="wallet-bridge",
                network="base",
                asset="USDC",
                quantity=amount,
                direction="ADVANCED_TO_BASE",
                from_location="coinbase-advanced",
                to_location=base_address,
                transaction_id=audit.provider_id(
                    result,
                    "transaction_id",
                    "transactionId",
                    "id",
                ),
                status="provider_response",
                source="bridge_api",
                raw_provider_response=result,
            )
        except Exception as exc:
            audit_warning = str(exc)
        return jsonify(
            {
                "ok": True,
                "direction": "advanced_to_base",
                "target_rail": "base",
                "amount": str(amount),
                "destination": base_address,
                "transaction": result,
                "audit_warning": audit_warning,
            }
        )
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
    rail = _validate_rail(request.args.get("rail", "auto"))
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
    rail = _validate_rail(str(payload.get("rail", "auto")))

    with _loop_lock:
        if _loop_state["running"]:
            active = str(_loop_state.get("rail") or "base")
            return jsonify(
                {
                    "ok": False,
                    "error": (
                        f"Background runner is already active on {active}. "
                        "Stop it before running a one-off cycle."
                    ),
                    "runner": dict(_loop_state),
                }
            ), 409

    try:
        result = _run_once(network, rail)
        return jsonify({"ok": True, "rail": rail, **result})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.post("/api/start")
def start():
    global _loop_thread

    payload = request.get_json(silent=True) or {}
    network = _validate_network(str(payload.get("network", "base")))
    rail = _validate_rail(str(payload.get("rail", "auto")))
    interval = max(5.0, float(payload.get("interval", 60.0)))

    with _loop_lock:
        if _loop_state["running"]:
            active_rail = str(_loop_state.get("rail") or "base")
            active_network = str(_loop_state.get("network") or "base")
            if active_rail == rail and active_network == network:
                return jsonify({"ok": True, "runner": dict(_loop_state)})
            return jsonify(
                {
                    "ok": False,
                    "error": (
                        f"Runner is already active on {active_rail}/{active_network}. "
                        f"Stop it before switching to {rail}/{network}."
                    ),
                    "runner": dict(_loop_state),
                }
            ), 409

        _stop_event.clear()
        _loop_state.update(
            {
                "running": True,
                "rail": rail,
                "network": network,
                "interval": interval,
                "last_error": None,
            }
        )
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
