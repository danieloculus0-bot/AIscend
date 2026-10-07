from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class VenueCapability:
    key: str
    name: str
    asset_class: str
    enabled: bool
    research_ready: bool
    execution_ready: bool
    account_requirement: str
    execution_note: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


VENUES: tuple[VenueCapability, ...] = (
    VenueCapability(
        key="crypto",
        name="Crypto Spot",
        asset_class="spot crypto",
        enabled=True,
        research_ready=True,
        execution_ready=True,
        account_requirement="Linked Base smart wallet + Coinbase Advanced",
        execution_note=(
            "Live execution is enabled across the funded linked crypto rails. "
            "Coinbase Advanced scans all account-tradable spot quote currencies, "
            "including newly listed and small-cap assets."
        ),
    ),
    VenueCapability(
        key="predictions",
        name="Predictions",
        asset_class="event contracts",
        enabled=True,
        research_ready=True,
        execution_ready=False,
        account_requirement="Coinbase Financial Markets predictions account",
        execution_note=(
            "Live public discovery now paginates and ranks open Coinbase/Kalshi "
            "prediction markets, including sports. Autonomous Coinbase execution "
            "is not claimed until a supported CFM programmatic order path is connected."
        ),
    ),
    VenueCapability(
        key="stocks_etfs",
        name="Stocks & ETFs",
        asset_class="US-listed equities and funds",
        enabled=True,
        research_ready=True,
        execution_ready=False,
        account_requirement="Coinbase Capital Markets brokerage account",
        execution_note=(
            "Lane is reserved in the common opportunity model. Live Coinbase stock/ETF "
            "discovery and execution adapters are not connected yet."
        ),
    ),
    VenueCapability(
        key="futures",
        name="Futures",
        asset_class="regulated crypto futures",
        enabled=True,
        research_ready=True,
        execution_ready=False,
        account_requirement="eligible Coinbase Financial Markets account",
        execution_note=(
            "Enabled as an opportunity lane. Account eligibility and a supported "
            "autonomous execution connector are still required."
        ),
    ),
    VenueCapability(
        key="perpetuals",
        name="Perpetuals",
        asset_class="crypto perpetual futures",
        enabled=True,
        research_ready=True,
        execution_ready=False,
        account_requirement="eligible Coinbase derivatives account",
        execution_note=(
            "Enabled as an opportunity lane. Availability depends on account and region."
        ),
    ),
    VenueCapability(
        key="options",
        name="Options",
        asset_class="listed / crypto options",
        enabled=True,
        research_ready=True,
        execution_ready=False,
        account_requirement="eligible Coinbase options account",
        execution_note=(
            "Enabled as an opportunity lane. Research can use the lane before "
            "programmatic execution is connected."
        ),
    ),
)


def capability_snapshot() -> dict[str, Any]:
    venues = [venue.as_dict() for venue in VENUES]
    return {
        "venues": venues,
        "enabled": sum(1 for venue in VENUES if venue.enabled),
        "research_ready": sum(
            1 for venue in VENUES if venue.enabled and venue.research_ready
        ),
        "execution_ready": sum(
            1 for venue in VENUES if venue.enabled and venue.execution_ready
        ),
    }
