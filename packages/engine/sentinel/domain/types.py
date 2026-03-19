"""Domain enums, value objects, and type aliases."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import TypeAlias

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class AssetClass(str, Enum):
    EQUITY = "equity"
    CRYPTO = "crypto"
    ETF = "etf"


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class OrderStatus(str, Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"


class TimeInForce(str, Enum):
    DAY = "day"
    GTC = "gtc"
    IOC = "ioc"
    FOK = "fok"


class StrategyState(str, Enum):
    DRAFT = "draft"
    RESEARCH = "research"
    BACKTEST = "backtest"
    BACKTEST_APPROVED = "backtest_approved"
    PAPER = "paper"
    PAPER_APPROVED = "paper_approved"
    LIVE = "live"
    LIVE_APPROVED = "live_approved"
    SUSPENDED = "suspended"
    RETIRED = "retired"


class RegimeLabel(str, Enum):
    TRENDING_BULL = "trending_bull"
    TRENDING_BEAR = "trending_bear"
    MEAN_REVERTING = "mean_reverting"
    HIGH_VOL_UNSTABLE = "high_vol_unstable"
    LOW_LIQUIDITY = "low_liquidity"
    RISK_ON = "risk_on"
    RISK_OFF = "risk_off"
    OPENING_NOISE = "opening_noise"
    EVENT_DISTORTED = "event_distorted"
    UNKNOWN = "unknown"


class DecisionOutcome(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    REQUIRES_HUMAN_APPROVAL = "requires_human_approval"
    DEFERRED = "deferred"


class PositionSide(str, Enum):
    LONG = "long"
    SHORT = "short"


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Symbol:
    ticker: str
    asset_class: AssetClass = AssetClass.EQUITY

    def __post_init__(self) -> None:
        if not self.ticker or self.ticker != self.ticker.upper():
            raise ValueError(f"Invalid ticker: {self.ticker!r}. Must be non-empty uppercase.")

    def __str__(self) -> str:
        return self.ticker


@dataclass(frozen=True)
class Money:
    amount: Decimal
    currency: str = "USD"

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Decimal):
            object.__setattr__(self, "amount", Decimal(str(self.amount)))
        if len(self.currency) != 3:  # noqa: PLR2004
            raise ValueError(f"Currency must be a 3-letter ISO code, got: {self.currency!r}")

    def __add__(self, other: Money) -> Money:
        if self.currency != other.currency:
            raise ValueError(
                f"Cannot add Money with different currencies: {self.currency} vs {other.currency}"
            )
        return Money(amount=self.amount + other.amount, currency=self.currency)

    def __sub__(self, other: Money) -> Money:
        if self.currency != other.currency:
            raise ValueError(
                f"Cannot subtract Money with different currencies: "
                f"{self.currency} vs {other.currency}"
            )
        return Money(amount=self.amount - other.amount, currency=self.currency)

    def __mul__(self, factor: float | Decimal | int) -> Money:
        return Money(
            amount=(self.amount * Decimal(str(factor))).quantize(Decimal("0.0001")),
            currency=self.currency,
        )

    def __truediv__(self, divisor: float | Decimal | int) -> Money:
        return Money(
            amount=(self.amount / Decimal(str(divisor))).quantize(Decimal("0.0001")),
            currency=self.currency,
        )

    def __repr__(self) -> str:
        return f"Money({self.amount} {self.currency})"

    @classmethod
    def zero(cls, currency: str = "USD") -> Money:
        return cls(amount=Decimal("0"), currency=currency)


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

SymbolStr: TypeAlias = str          # raw ticker string
StrategyId: TypeAlias = str         # UUID string for strategies
AccountId: TypeAlias = str          # broker account identifier
BrokerOrderId: TypeAlias = str      # broker-assigned order ID
ClientOrderId: TypeAlias = str      # our internal order reference
