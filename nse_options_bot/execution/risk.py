"""Risk management module.

Risk gates, intraday loss limits, and kill switch functionality.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, date, time
from decimal import Decimal
from enum import Enum
from typing import Any

import pytz
import structlog

from nse_options_bot.market.regime import MarketRegime
from nse_options_bot.strategies.base_strategy import StrategyType

logger = structlog.get_logger(__name__)
IST = pytz.timezone("Asia/Kolkata")


class RiskLevel(str, Enum):
    """Risk level classification."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass
class RiskLimits:
    """Risk limit configuration."""

    # Capital limits
    max_capital_per_trade_pct: float = 5.0  # Max 5% of capital per trade
    max_open_trades: int = 5
    max_lots_per_underlying: int = 10

    # Loss limits
    max_loss_per_trade_pct: float = 2.0  # Max 2% of capital loss per trade
    max_daily_loss_pct: float = 5.0  # Max 5% daily loss
    kill_switch_loss_pct: float = 7.0  # Kill switch at 7% loss

    # Position limits
    max_delta_exposure: float = 500  # Net delta limit
    max_vega_exposure: float = 200  # Net vega limit
    max_margin_usage_pct: float = 80.0  # Max 80% margin usage

    # Time limits
    no_entry_after: time = time(15, 15)  # No new entries after 15:15
    forced_exit_before: time = time(15, 25)  # Force exit by 15:25

    # Event limits
    reduce_size_on_high_vol: bool = True
    vol_size_multiplier: float = 0.5  # Reduce size by 50% in high vol


@dataclass
class RiskState:
    """Current risk state."""

    # Daily tracking
    daily_pnl: Decimal = Decimal("0")
    daily_realized: Decimal = Decimal("0")
    daily_unrealized: Decimal = Decimal("0")
    trades_today: int = 0
    winning_trades: int = 0
    losing_trades: int = 0

    # Position tracking
    open_positions: int = 0
    margin_used: Decimal = Decimal("0")
    net_delta: float = 0.0
    net_gamma: float = 0.0
    net_vega: float = 0.0

    # Flags
    kill_switch_active: bool = False
    kill_switch_reason: str = ""
    entry_blocked: bool = False
    entry_block_reason: str = ""


class RiskManager:
    """Risk management and position gating.

    Enforces:
    - Position size limits
    - Daily loss limits
    - Kill switch functionality
    - Entry time restrictions
    - Regime-based adjustments
    """

    def __init__(
        self,
        capital: Decimal,
        limits: RiskLimits | None = None,
    ) -> None:
        """Initialize risk manager.

        Args:
            capital: Total trading capital
            limits: Risk limit configuration
        """
        self._capital = capital
        self._limits = limits or RiskLimits()
        self._state = RiskState()
        self._trade_history: list[dict[str, Any]] = []

    @property
    def state(self) -> RiskState:
        """Get current risk state."""
        return self._state

    @property
    def limits(self) -> RiskLimits:
        """Get risk limits."""
        return self._limits

    def check_entry_allowed(
        self,
        strategy_type: StrategyType,
        required_margin: Decimal,
        max_loss: Decimal,
        num_lots: int,
        underlying: str,
        regime: MarketRegime | None = None,
    ) -> tuple[bool, str]:
        """Check if new entry is allowed.

        Args:
            strategy_type: Strategy type
            required_margin: Required margin
            max_loss: Maximum potential loss
            num_lots: Number of lots
            underlying: Underlying symbol
            regime: Current market regime

        Returns:
            Tuple of (allowed, reason)
        """
        # Kill switch check
        if self._state.kill_switch_active:
            return False, f"Kill switch active: {self._state.kill_switch_reason}"

        # Time check
        now = datetime.now(IST).time()
        if now >= self._limits.no_entry_after:
            return False, f"No entries after {self._limits.no_entry_after}"

        # Open positions check
        if self._state.open_positions >= self._limits.max_open_trades:
            return False, f"Max open trades ({self._limits.max_open_trades}) reached"

        # Capital per trade check
        max_capital = self._capital * Decimal(str(self._limits.max_capital_per_trade_pct / 100))
        if required_margin > max_capital:
            return False, f"Margin ₹{required_margin} exceeds max ₹{max_capital}"

        # Loss per trade check
        max_allowed_loss = self._capital * Decimal(str(self._limits.max_loss_per_trade_pct / 100))
        if max_loss > max_allowed_loss:
            return False, f"Max loss ₹{max_loss} exceeds limit ₹{max_allowed_loss}"

        # Margin usage check
        total_margin = self._state.margin_used + required_margin
        margin_pct = float(total_margin / self._capital * 100)
        if margin_pct > self._limits.max_margin_usage_pct:
            return False, f"Margin usage {margin_pct:.0f}% exceeds {self._limits.max_margin_usage_pct}%"

        # Daily loss check - don't add to losing day
        daily_loss_pct = float(-self._state.daily_pnl / self._capital * 100) if self._state.daily_pnl < 0 else 0
        remaining_loss_budget = self._limits.max_daily_loss_pct - daily_loss_pct
        if float(max_loss / self._capital * 100) > remaining_loss_budget:
            return False, f"Trade max loss exceeds remaining daily budget"

        # Regime check
        if regime:
            if not self._is_strategy_allowed_for_regime(strategy_type, regime):
                return False, f"Strategy {strategy_type.value} not allowed in {regime.name}"

        return True, ""

    def _is_strategy_allowed_for_regime(
        self,
        strategy_type: StrategyType,
        regime: MarketRegime,
    ) -> bool:
        """Check if strategy is allowed for current regime.

        Args:
            strategy_type: Strategy type
            regime: Market regime

        Returns:
            True if allowed
        """
        # HIGH_VOLATILITY - only allow long straddle
        if regime == MarketRegime.HIGH_VOLATILITY:
            allowed = {StrategyType.LONG_STRADDLE}
            return strategy_type in allowed

        # RANGE_BOUND - allow short strategies
        if regime == MarketRegime.RANGE_BOUND:
            allowed = {
                StrategyType.SHORT_STRADDLE,
                StrategyType.IRON_CONDOR,
                StrategyType.STRANGLE,
            }
            return strategy_type in allowed

        # TRENDING - allow directional
        if regime in (MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN):
            allowed = {
                StrategyType.BULL_CALL_SPREAD,
                StrategyType.BEAR_PUT_SPREAD,
                StrategyType.BULL_PUT_SPREAD,
                StrategyType.BEAR_CALL_SPREAD,
            }
            return strategy_type in allowed

        return True

    def get_size_multiplier(
        self,
        regime: MarketRegime | None = None,
        vix: float = 0.0,
    ) -> float:
        """Get position size multiplier based on conditions.

        Args:
            regime: Current regime
            vix: Current VIX

        Returns:
            Size multiplier (0.0 - 1.0)
        """
        multiplier = 1.0

        # Regime-based multipliers
        if regime:
            regime_multipliers = {
                MarketRegime.RANGE_BOUND: 1.0,
                MarketRegime.TRENDING_UP: 0.7,
                MarketRegime.TRENDING_DOWN: 0.7,
                MarketRegime.HIGH_VOLATILITY: 0.4,
            }
            multiplier *= regime_multipliers.get(regime, 1.0)

        # VIX-based adjustment
        if vix > 0:
            if vix > 25:
                multiplier *= 0.5
            elif vix > 20:
                multiplier *= 0.7
            elif vix > 18:
                multiplier *= 0.85

        # Daily P&L adjustment
        daily_loss_pct = float(-self._state.daily_pnl / self._capital * 100) if self._state.daily_pnl < 0 else 0
        if daily_loss_pct > 3:
            multiplier *= 0.5  # Reduce size if down 3%+

        return max(0.1, min(1.0, multiplier))

    def record_trade_entry(
        self,
        trade_id: str,
        margin: Decimal,
        max_loss: Decimal,
    ) -> None:
        """Record trade entry.

        Args:
            trade_id: Trade ID
            margin: Margin used
            max_loss: Max loss
        """
        self._state.open_positions += 1
        self._state.margin_used += margin
        self._state.trades_today += 1

        logger.info(
            "trade_entry_recorded",
            trade_id=trade_id,
            margin=float(margin),
            open_positions=self._state.open_positions,
        )

    def record_trade_exit(
        self,
        trade_id: str,
        pnl: Decimal,
        margin: Decimal,
    ) -> None:
        """Record trade exit.

        Args:
            trade_id: Trade ID
            pnl: Realized P&L
            margin: Margin released
        """
        self._state.open_positions -= 1
        self._state.margin_used -= margin
        self._state.daily_realized += pnl
        self._state.daily_pnl = self._state.daily_realized + self._state.daily_unrealized

        if pnl >= 0:
            self._state.winning_trades += 1
        else:
            self._state.losing_trades += 1

        self._trade_history.append({
            "trade_id": trade_id,
            "pnl": float(pnl),
            "timestamp": datetime.now(IST).isoformat(),
        })

        # Check kill switch
        self._check_kill_switch()

        logger.info(
            "trade_exit_recorded",
            trade_id=trade_id,
            pnl=float(pnl),
            daily_pnl=float(self._state.daily_pnl),
        )

    def update_unrealized_pnl(self, unrealized: Decimal) -> None:
        """Update unrealized P&L.

        Args:
            unrealized: Current unrealized P&L
        """
        self._state.daily_unrealized = unrealized
        self._state.daily_pnl = self._state.daily_realized + self._state.daily_unrealized

        # Check kill switch
        self._check_kill_switch()

    def update_greeks(
        self,
        delta: float,
        gamma: float,
        vega: float,
    ) -> None:
        """Update aggregate Greeks.

        Args:
            delta: Net delta
            gamma: Net gamma
            vega: Net vega
        """
        self._state.net_delta = delta
        self._state.net_gamma = gamma
        self._state.net_vega = vega

        # Check Greek limits
        if abs(delta) > self._limits.max_delta_exposure:
            logger.warning(
                "delta_limit_breached",
                delta=delta,
                limit=self._limits.max_delta_exposure,
            )

        if abs(vega) > self._limits.max_vega_exposure:
            logger.warning(
                "vega_limit_breached",
                vega=vega,
                limit=self._limits.max_vega_exposure,
            )

    def _check_kill_switch(self) -> None:
        """Check and activate kill switch if needed."""
        if self._state.kill_switch_active:
            return

        loss_pct = float(-self._state.daily_pnl / self._capital * 100) if self._state.daily_pnl < 0 else 0

        if loss_pct >= self._limits.kill_switch_loss_pct:
            self.activate_kill_switch(
                f"Daily loss {loss_pct:.1f}% exceeded {self._limits.kill_switch_loss_pct}%"
            )

    def activate_kill_switch(self, reason: str) -> None:
        """Activate kill switch.

        Args:
            reason: Reason for activation
        """
        self._state.kill_switch_active = True
        self._state.kill_switch_reason = reason
        self._state.entry_blocked = True
        self._state.entry_block_reason = "Kill switch active"

        logger.warning("kill_switch_activated", reason=reason)

    def deactivate_kill_switch(self) -> None:
        """Deactivate kill switch."""
        self._state.kill_switch_active = False
        self._state.kill_switch_reason = ""
        self._state.entry_blocked = False
        self._state.entry_block_reason = ""

        logger.info("kill_switch_deactivated")

    def get_risk_level(self) -> RiskLevel:
        """Get current risk level.

        Returns:
            RiskLevel
        """
        if self._state.kill_switch_active:
            return RiskLevel.CRITICAL

        loss_pct = float(-self._state.daily_pnl / self._capital * 100) if self._state.daily_pnl < 0 else 0

        if loss_pct >= self._limits.max_daily_loss_pct * 0.8:
            return RiskLevel.HIGH
        elif loss_pct >= self._limits.max_daily_loss_pct * 0.5:
            return RiskLevel.MEDIUM

        return RiskLevel.LOW

    def get_summary(self) -> dict[str, Any]:
        """Get risk summary.

        Returns:
            Summary dict
        """
        return {
            "risk_level": self.get_risk_level().value,
            "daily_pnl": float(self._state.daily_pnl),
            "daily_pnl_pct": float(self._state.daily_pnl / self._capital * 100),
            "realized": float(self._state.daily_realized),
            "unrealized": float(self._state.daily_unrealized),
            "open_positions": self._state.open_positions,
            "trades_today": self._state.trades_today,
            "win_rate": (
                self._state.winning_trades / self._state.trades_today * 100
                if self._state.trades_today > 0
                else 0
            ),
            "margin_used": float(self._state.margin_used),
            "margin_pct": float(self._state.margin_used / self._capital * 100),
            "net_delta": self._state.net_delta,
            "net_vega": self._state.net_vega,
            "kill_switch": self._state.kill_switch_active,
        }

    def reset_daily(self) -> None:
        """Reset daily counters."""
        self._state = RiskState()
        self._trade_history.clear()
        logger.info("risk_manager_daily_reset")
