"""BEAN launch lab: measure Coinbase new-listing outcomes, not assumptions.

Pure analytics over observed, timestamped market prices. No orders are placed.
"""
from __future__ import annotations
import math
from statistics import median


def evaluate_launches(events, price_series, *, horizons=(300, 900, 3600, 21600),
                      roundtrip_cost=0.012):
    """Assess post-listing forward outcomes with missing data marked unknown.

    price_series maps product to sorted (UTC epoch second, executable price) pairs.
    The first quote at or AFTER the event is the entry, never a pre-listing quote.
    For each horizon require a quote at or after the deadline; do not substitute
    an earlier observation or invent a return. Does not imply realizable fills.
    """
    output = []
    for event in events:
        if event.get("stage") != "FULL_TRADING":
            continue
        product = str(event.get("product") or "").upper()
        ts = float(event["ts"])
        observations = sorted(
            (float(t), float(p)) for t, p in price_series.get(product, ())
            if math.isfinite(float(t)) and math.isfinite(float(p)) and float(p) > 0
        )
        entry = next(((t, p) for t, p in observations if t >= ts), None)
        if entry is None:
            output.append({"product": product, "status": "unknown_no_entry"})
            continue
        results = {}
        for horizon in horizons:
            observation = next(
                ((t, p) for t, p in observations if t >= entry[0] + horizon), None)
            results[str(horizon)] = (
                None if observation is None else
                round(observation[1] / entry[1] - 1.0 - roundtrip_cost, 8))
        output.append({"product": product, "status": "observed",
                       "entry_timestamp": entry[0],
                       "net_forward_returns": results})
    stats = {}
    for horizon in horizons:
        observations = [item["net_forward_returns"][str(horizon)] for item in output
                        if item["status"] == "observed"
                        and item["net_forward_returns"][str(horizon)] is not None]
        stats[str(horizon)] = {
            "count": len(observations),
            "median_net_return": median(observations) if observations else None,
            "win_rate": (sum(x > 0 for x in observations) / len(observations))
                        if observations else None,
        }
    return {"schema": "bean.launch-analysis.v1", "orders_enabled": False,
            "roundtrip_cost": roundtrip_cost,
            "events": output, "by_horizon": stats}
