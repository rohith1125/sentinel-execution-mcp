"""PositionSizingEngine — fixed-fractional risk sizing with multi-constraint cap."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from sentinel.domain.types import RegimeLabel
from sentinel.regime.models import RegimeSnapshot

# Default parameters
_DEFAULT_RISK_PCT = 0.01            # 1% account risk per trade
_MAX_POSITION_PCT = 0.10            # 10% of account per position
_MAX_GROSS_EXPOSURE_PCT = 0.80      # 80% total gross exposure
_LIQUIDITY_ADV_PCT = 0.01           # max 1% of average daily volume
_MIN_SHARES = 1

# Volatility scalar mapping
_HIGH_VOL_REGIMES = frozenset([
    RegimeLabel.HIGH_VOL_UNSTABLE,
    RegimeLabel.RISK_OFF,
    RegimeLabel.EVENT_DISTORTED,
])
_HIGH_VOL_SCALAR = 0.50
_NORMAL_VOL_SCALAR = 1.00

# Confidence scalar: maps signal confidence [0.5, 1.0] → size scalar [0.7, 1.0]
_CONFIDENCE_MIN_SCALAR = 0.70
_CONFIDENCE_MIN_INPUT = 0.50        # below this = minimum scalar
_CONFIDENCE_MAX_INPUT = 1.00


class PositionSizingEngine:
    """Determines appropriate position size using multiple constraints.

    Method:
    1. Compute base shares via fixed-fractional risk:
       shares = (account_value * risk_pct) / stop_distance_per_share

    2. Apply all constraints (take minimum):
       - max_position_pct: notional <= account_value * max_position_pct
       - max_gross_exposure: total gross stays under cap
       - liquidity_constraint: shares < avg_daily_volume * adv_pct
       - volatility_scaling: 0.5x in high-vol regimes
       - confidence_scaling: 0.7x–1.0x based on signal confidence
    """

    def __init__(
        self,
        risk_pct: float = _DEFAULT_RISK_PCT,
        max_position_pct: float = _MAX_POSITION_PCT,
        max_gross_exposure_pct: float = _MAX_GROSS_EXPOSURE_PCT,
        liquidity_adv_pct: float = _LIQUIDITY_ADV_PCT,
    ) -> None:
        self.risk_pct = risk_pct
        self.max_position_pct = max_position_pct
        self.max_gross_exposure_pct = max_gross_exposure_pct
        self.liquidity_adv_pct = liquidity_adv_pct

    @dataclass
    class SizingResult:
        """Full output of a sizing computation with audit trail."""

        shares: int
        notional_value: Decimal
        risk_amount: Decimal            # $ at risk if stop is hit
        risk_pct_of_account: float      # actual risk as % of account
        binding_constraint: str         # which constraint was smallest
        details: dict[str, float] = field(default_factory=dict)

    def compute_size(
        self,
        account_value: Decimal,
        entry_price: Decimal,
        stop_price: Decimal,
        signal_confidence: float,
        regime: RegimeSnapshot,
        current_positions_value: Decimal,
        avg_daily_volume: int,
        risk_pct: float | None = None,
    ) -> PositionSizingEngine.SizingResult:
        """Compute position size applying all constraints.

        Args:
            account_value: Total account equity.
            entry_price: Expected entry price per share.
            stop_price: Stop-loss price per share.
            signal_confidence: Strategy signal confidence (0-1).
            regime: Current market regime (for volatility scalar).
            current_positions_value: Sum of notional in open positions.
            avg_daily_volume: Average daily volume for the symbol (shares).
            risk_pct: Override the default risk_pct (optional).

        Returns:
            SizingResult with shares, notional, risk, and binding constraint.
        """
        effective_risk_pct = risk_pct if risk_pct is not None else self.risk_pct
        account_f = float(account_value)
        entry_f = float(entry_price)
        stop_f = float(stop_price)

        if entry_f <= 0:
            return self._zero_result(account_value, entry_price, "zero_entry_price")

        stop_distance = abs(entry_f - stop_f)
        if stop_distance < 0.0001:
            return self._zero_result(account_value, entry_price, "stop_too_close_to_entry")

        # --- Step 1: Base risk-based sizing ---
        risk_dollars = account_f * effective_risk_pct
        base_shares = risk_dollars / stop_distance
        base_shares_int = max(_MIN_SHARES, int(base_shares))

        # --- Step 2: Apply scalars before constraints ---
        vol_scalar = self._apply_volatility_scalar(regime)
        conf_scalar = self._apply_confidence_scalar(signal_confidence)
        combined_scalar = vol_scalar * conf_scalar

        scaled_shares = max(_MIN_SHARES, int(base_shares * combined_scalar))

        # --- Step 3: Apply hard constraints ---
        constraints: dict[str, int] = {}

        # 3a. Max position % of account
        max_notional = account_f * self.max_position_pct
        position_limit_shares = max(_MIN_SHARES, int(max_notional / entry_f))
        constraints["max_position_pct"] = position_limit_shares

        # 3b. Gross exposure cap
        available_exposure = max(0.0, account_f * self.max_gross_exposure_pct - float(current_positions_value))
        exposure_limit_shares = max(_MIN_SHARES, int(available_exposure / entry_f))
        constraints["gross_exposure_cap"] = exposure_limit_shares

        # 3c. Liquidity: max X% of ADV
        adv_limit_shares = max(_MIN_SHARES, int(avg_daily_volume * self.liquidity_adv_pct))
        constraints["liquidity_adv"] = adv_limit_shares

        # 3d. Scaled risk sizing
        constraints["risk_scaled"] = scaled_shares

        # Take minimum
        final_shares = min(constraints.values())
        binding = min(constraints, key=lambda k: constraints[k])

        final_notional = Decimal(str(round(final_shares * entry_f, 2)))
        risk_amount = Decimal(str(round(final_shares * stop_distance, 2)))
        risk_pct_actual = float(risk_amount) / account_f if account_f > 0 else 0.0

        details: dict[str, float] = {
            "base_shares": float(base_shares_int),
            "vol_scalar": vol_scalar,
            "conf_scalar": conf_scalar,
            "combined_scalar": combined_scalar,
            "scaled_shares": float(scaled_shares),
            "risk_dollars": risk_dollars,
            "stop_distance": stop_distance,
            "account_f": account_f,
            **{f"constraint_{k}": float(v) for k, v in constraints.items()},
        }

        return PositionSizingEngine.SizingResult(
            shares=final_shares,
            notional_value=final_notional,
            risk_amount=risk_amount,
            risk_pct_of_account=round(risk_pct_actual, 6),
            binding_constraint=binding,
            details=details,
        )

    def _apply_volatility_scalar(self, regime: RegimeSnapshot) -> float:
        """Return 0.5x in high-vol/risk-off regimes, 1.0x otherwise."""
        if regime.label in _HIGH_VOL_REGIMES:
            return _HIGH_VOL_SCALAR
        # Also scale by tradeability for intermediate regimes
        if regime.tradeability_score < 0.5:
            return max(_HIGH_VOL_SCALAR, regime.tradeability_score)
        return _NORMAL_VOL_SCALAR

    def _apply_confidence_scalar(self, confidence: float) -> float:
        """Scale position size from 0.7x (low confidence) to 1.0x (full confidence).

        Linear interpolation between _CONFIDENCE_MIN_INPUT and _CONFIDENCE_MAX_INPUT.
        """
        if confidence <= _CONFIDENCE_MIN_INPUT:
            return _CONFIDENCE_MIN_SCALAR
        if confidence >= _CONFIDENCE_MAX_INPUT:
            return 1.0
        slope = (1.0 - _CONFIDENCE_MIN_SCALAR) / (_CONFIDENCE_MAX_INPUT - _CONFIDENCE_MIN_INPUT)
        return _CONFIDENCE_MIN_SCALAR + slope * (confidence - _CONFIDENCE_MIN_INPUT)

    def _zero_result(
        self,
        account_value: Decimal,
        entry_price: Decimal,
        reason: str,
    ) -> PositionSizingEngine.SizingResult:
        return PositionSizingEngine.SizingResult(
            shares=0,
            notional_value=Decimal("0"),
            risk_amount=Decimal("0"),
            risk_pct_of_account=0.0,
            binding_constraint=reason,
            details={"account_value": float(account_value), "entry_price": float(entry_price)},
        )
