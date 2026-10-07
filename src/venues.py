from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class VenueCapability:
    key: str
    name: str
    asset_class: str
    research_ready: bool
    execution_ready: bool
    account_requirement: str
    execution_note: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


VENUES: tuple[VenueCapability, ...] = (
    VenueCapability(
        key="crypto",
        name="Crypto",
        asset_class="spot crypto",
        research_ready=True,
        execution_ready=True,
        account_requirement="CDP smart account",
        execution_note=(
            "Research rotates across active Coinbase USD/USDC products. "
            "Live Base execution is currently wired for USDC/WETH only."
        ),
    ),
    VenueCapability(
        key="predictions",
        name="Predictions",
        asset_class="event contracts",
        research_ready=True,
        execution_ready=False,
        account_requirement="Coinbase Financial Markets predictions account",
        execution_note=(
            "Visible from first launch. Research lane is available, but autonomous "
            "execution stays locked until a supported programmatic trading interface "
            "is connected."
        ),
    ),
    VenueCapability(
        key="stocks_etfs",
        name="Stocks & ETFs",
        asset_class="US-listed equities and funds",
        research_ready=True,
        execution_ready=False,
        account_requirement="Coinbase Capital Markets brokerage account",
        execution_note=(
            "Visible from first launch. Research lane is available, but autonomous "
            "execution stays locked until a supported brokerage API is connected."
        ),
    ),
    VenueCapability(
        key="options",
        name="Options",
        asset_class="listed equity options",
        research_ready=True,
        execution_ready=False,
        account_requirement="CCM brokerage account plus options approval",
        execution_note=(
            "Visible from first launch. Research lane is available, but autonomous "
            "execution stays locked until a supported options API is connected."
        ),
    ),
)


def capability_snapshot() -> dict[str, Any]:
    venues = [venue.as_dict() for venue in VENUES]
    return {
        "venues": venues,
        "research_ready": sum(1 for venue in VENUES if venue.research_ready),
        "execution_ready": sum(1 for venue in VENUES if venue.execution_ready),
    }
