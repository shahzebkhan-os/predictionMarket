"""Exit conditions and logic for the watcher.

All exit condition checks: stop loss, target, trailing, time, Greeks, etc.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from decimal import Decimal
from enum import Enum
from typing import Any

import pytz
import structlog

from nse_options_bot.watcher.state import ExitReason, OptionsTradeState, TradeStatus

logger = structlog.get_logger(__name__)
IST = pytz.timezone("Asia/Kolkata")


class ExitPriority(int, Enum):
    """Exit priority levels."""

    CRITICAL = 1  # Kill switch, max loss
    HIGH = 2  # Stop loss, time stop
    MEDIUM = 3  # Target, trailing
    LOW = 4  # Greeks, regime


@dataclass
class ExitSignal:
    """Exit signal from checker."""

    should_exit: bool
    reason: ExitReason
    priority: ExitPriority
    description: str
    urgency: str = "normal"  # "normal", "urgent", "immediate"

    def __lt__(self, other: "ExitSignal") -> bool:
        """Compare by priority."""
        return self.priority.value < other.priority.value


class ExitConditionChecker:
    """Checks all exit conditions for a trade.

    Exit conditions:
    1. Stop loss hit (% of max loss)
    2. Target profit hit
    3. Trailing stop triggered
    4. Time stop (max time in trade)
    5. EOD square-off (before 15:25)
    6. Expiry exit (DTE <= threshold)
    7. Delta breach (net delta too high)
    8. Theta target (collected enough theta)
    9. IV crush (for long vega positions)
    10. Regime change (unfavorable regime)
    11. Kill switch activated
    12. Max daily loss hit
    """

    # Time-based exits
    EOD_SQUARE_OFF_TIME = time(15, 25)  # 15:25 IST
    LAST_ENTRY_TIME = time(15, 15)  # No entries after 15:15

    # Default thresholds
    DEFAULT_SL_PCT = 50.0  # 50% of max loss
    DEFAULT_TARGET_PCT = 80.0  # 80% of max profit
    DEFAULT_TRAILING_ACTIVATION = 50.0  # Activate trailing at 50% profit
    DEFAULT_TRAILING_PCT = 30.0  # Trail by 30%

    # Greeks thresholds
    MAX_DELTA_BREACH = 100  # Net delta > 100 = too directional
    MIN_THETA_RATIO = 0.8  # Collected 80% of expected theta = target

    # IV thresholds
    IV_CRUSH_PCT = -20  # IV dropped 20%

    # DTE thresholds
    EXPIRY_EXIT_DTE = 0.5  # Exit when DTE < 0.5 (same day)

    def __init__(
        self,
        kill_switch_active: bool = False,
        max_daily_loss: Decimal = Decimal("0"),
        current_daily_pnl: Decimal = Decimal("0"),
    ) -> None:
        """Initialize checker.

        Args:
            kill_switch_active: Whether kill switch is active
            max_daily_loss: Maximum daily loss limit
            current_daily_pnl: Current daily P&L
        """
        self.kill_switch_active = kill_switch_active
        self.max_daily_loss = max_daily_loss
        self.current_daily_pnl = current_daily_pnl

    def check_all_conditions(
        self,
        trade: OptionsTradeState,
        current_iv: float = 0.0,
        entry_iv: float = 0.0,
        dte: float = 7.0,
        current_regime: str = "",
    ) -> list[ExitSignal]:
        """Check all exit conditions.

        Args:
            trade: Trade state
            current_iv: Current IV
            entry_iv: IV at entry
            dte: Days to expiry
            current_regime: Current market regime

        Returns:
            List of triggered exit signals, sorted by priority
        """
        signals: list[ExitSignal] = []

        # 1. Kill switch (highest priority)
        if self.kill_switch_active:
            signals.append(
                ExitSignal(
                    should_exit=True,
                    reason=ExitReason.KILL_SWITCH,
                    priority=ExitPriority.CRITICAL,
                    description="Kill switch activated - exit all positions",
                    urgency="immediate",
                )
            )

        # 2. Max daily loss
        if self._check_max_daily_loss(trade):
            signals.append(
                ExitSignal(
                    should_exit=True,
                    reason=ExitReason.MAX_LOSS_HIT,
                    priority=ExitPriority.CRITICAL,
                    description=f"Daily loss limit hit: {float(self.current_daily_pnl):.0f}",
                    urgency="immediate",
                )
            )

        # 3. Stop loss
        sl_signal = self._check_stop_loss(trade)
        if sl_signal:
            signals.append(sl_signal)

        # 4. Target profit
        target_signal = self._check_target(trade)
        if target_signal:
            signals.append(target_signal)

        # 5. Trailing stop
        trailing_signal = self._check_trailing_stop(trade)
        if trailing_signal:
            signals.append(trailing_signal)

        # 6. Time stop
        time_signal = self._check_time_stop(trade)
        if time_signal:
            signals.append(time_signal)

        # 7. EOD square-off
        eod_signal = self._check_eod_exit()
        if eod_signal:
            signals.append(eod_signal)

        # 8. Expiry exit
        expiry_signal = self._check_expiry_exit(dte)
        if expiry_signal:
            signals.append(expiry_signal)

        # 9. Delta breach
        delta_signal = self._check_delta_breach(trade)
        if delta_signal:
            signals.append(delta_signal)

        # 10. Theta target
        theta_signal = self._check_theta_target(trade)
        if theta_signal:
            signals.append(theta_signal)

        # 11. IV crush
        iv_signal = self._check_iv_crush(current_iv, entry_iv, trade)
        if iv_signal:
            signals.append(iv_signal)

        # 12. Regime change
        regime_signal = self._check_regime_change(trade, current_regime)
        if regime_signal:
            signals.append(regime_signal)

        # Sort by priority
        return sorted(signals)

    def _check_max_daily_loss(self, trade: OptionsTradeState) -> bool:
        """Check if max daily loss hit.

        Args:
            trade: Trade state

        Returns:
            True if max daily loss hit
        """
        if self.max_daily_loss == 0:
            return False

        # Include this trade's P&L
        total_daily = self.current_daily_pnl + trade.total_pnl
        return total_daily <= -self.max_daily_loss

    def _check_stop_loss(self, trade: OptionsTradeState) -> ExitSignal | None:
        """Check stop loss condition.

        Args:
            trade: Trade state

        Returns:
            ExitSignal or None
        """
        if trade.max_loss_amount == 0:
            return None

        current_loss = -trade.total_pnl if trade.total_pnl < 0 else Decimal("0")
        loss_pct = float(current_loss / trade.max_loss_amount * 100)

        if loss_pct >= trade.stop_loss_pct:
            return ExitSignal(
                should_exit=True,
                reason=ExitReason.STOP_LOSS,
                priority=ExitPriority.HIGH,
                description=f"Stop loss hit: {loss_pct:.0f}% of max loss",
                urgency="urgent",
            )

        return None

    def _check_target(self, trade: OptionsTradeState) -> ExitSignal | None:
        """Check target profit condition.

        Args:
            trade: Trade state

        Returns:
            ExitSignal or None
        """
        if trade.target_profit_amount == 0:
            return None

        if trade.total_pnl >= trade.target_profit_amount:
            profit_pct = float(trade.total_pnl / trade.target_profit_amount * 100)
            return ExitSignal(
                should_exit=True,
                reason=ExitReason.TARGET_HIT,
                priority=ExitPriority.MEDIUM,
                description=f"Target hit: {profit_pct:.0f}% of target profit",
                urgency="normal",
            )

        return None

    def _check_trailing_stop(self, trade: OptionsTradeState) -> ExitSignal | None:
        """Check trailing stop condition.

        Args:
            trade: Trade state

        Returns:
            ExitSignal or None
        """
        if trade.trailing_stop_pct == 0:
            return None

        if trade.peak_profit <= 0:
            return None

        # Calculate drawdown from peak
        drawdown = trade.peak_profit - trade.total_pnl
        drawdown_pct = float(drawdown / trade.peak_profit * 100)

        if drawdown_pct >= trade.trailing_stop_pct:
            return ExitSignal(
                should_exit=True,
                reason=ExitReason.TRAILING_STOP,
                priority=ExitPriority.MEDIUM,
                description=f"Trailing stop: {drawdown_pct:.0f}% drawdown from peak",
                urgency="normal",
            )

        return None

    def _check_time_stop(self, trade: OptionsTradeState) -> ExitSignal | None:
        """Check time stop condition.

        Args:
            trade: Trade state

        Returns:
            ExitSignal or None
        """
        if trade.time_stop_minutes == 0:
            return None

        if trade.time_in_trade_minutes >= trade.time_stop_minutes:
            return ExitSignal(
                should_exit=True,
                reason=ExitReason.TIME_STOP,
                priority=ExitPriority.HIGH,
                description=f"Time stop: {trade.time_in_trade_minutes:.0f} min in trade",
                urgency="normal",
            )

        return None

    def _check_eod_exit(self) -> ExitSignal | None:
        """Check EOD square-off condition.

        Returns:
            ExitSignal or None
        """
        now = datetime.now(IST).time()

        if now >= self.EOD_SQUARE_OFF_TIME:
            return ExitSignal(
                should_exit=True,
                reason=ExitReason.EOD_SQUARE_OFF,
                priority=ExitPriority.HIGH,
                description=f"EOD square-off time: {now.strftime('%H:%M')}",
                urgency="urgent",
            )

        return None

    def _check_expiry_exit(self, dte: float) -> ExitSignal | None:
        """Check expiry exit condition.

        Args:
            dte: Days to expiry

        Returns:
            ExitSignal or None
        """
        if dte <= self.EXPIRY_EXIT_DTE:
            return ExitSignal(
                should_exit=True,
                reason=ExitReason.EXPIRY_EXIT,
                priority=ExitPriority.HIGH,
                description=f"Expiry exit: DTE={dte:.2f}",
                urgency="urgent",
            )

        return None

    def _check_delta_breach(self, trade: OptionsTradeState) -> ExitSignal | None:
        """Check delta breach condition.

        Args:
            trade: Trade state

        Returns:
            ExitSignal or None
        """
        if abs(trade.net_delta) >= self.MAX_DELTA_BREACH:
            return ExitSignal(
                should_exit=True,
                reason=ExitReason.DELTA_BREACH,
                priority=ExitPriority.LOW,
                description=f"Delta breach: net delta={trade.net_delta:.0f}",
                urgency="normal",
            )

        return None

    def _check_theta_target(self, trade: OptionsTradeState) -> ExitSignal | None:
        """Check theta target condition.

        Only applies to short vega strategies (positive theta).

        Args:
            trade: Trade state

        Returns:
            ExitSignal or None
        """
        if trade.net_theta <= 0:
            return None  # Not a theta-positive strategy

        if trade.expected_theta_today == 0:
            return None

        collected_ratio = float(
            trade.actual_theta_today / trade.expected_theta_today
        )

        if collected_ratio >= self.MIN_THETA_RATIO:
            return ExitSignal(
                should_exit=True,
                reason=ExitReason.THETA_TARGET,
                priority=ExitPriority.LOW,
                description=f"Theta target: collected {collected_ratio*100:.0f}% of expected",
                urgency="normal",
            )

        return None

    def _check_iv_crush(
        self,
        current_iv: float,
        entry_iv: float,
        trade: OptionsTradeState,
    ) -> ExitSignal | None:
        """Check IV crush condition.

        Applies to long vega positions (negative theta).

        Args:
            current_iv: Current IV
            entry_iv: IV at entry
            trade: Trade state

        Returns:
            ExitSignal or None
        """
        if trade.net_vega <= 0:
            return None  # Not long vega

        if entry_iv == 0:
            return None

        iv_change_pct = (current_iv - entry_iv) / entry_iv * 100

        if iv_change_pct <= self.IV_CRUSH_PCT:
            return ExitSignal(
                should_exit=True,
                reason=ExitReason.IV_CRUSH,
                priority=ExitPriority.LOW,
                description=f"IV crush: {iv_change_pct:.0f}% IV drop",
                urgency="normal",
            )

        return None

    def _check_regime_change(
        self,
        trade: OptionsTradeState,
        current_regime: str,
    ) -> ExitSignal | None:
        """Check regime change condition.

        Args:
            trade: Trade state
            current_regime: Current market regime

        Returns:
            ExitSignal or None
        """
        if not trade.entry_regime or not current_regime:
            return None

        # Define unfavorable regime changes
        unfavorable = {
            # Short vol strategies should exit on high vol regime
            "SHORT_STRADDLE": ["HIGH_VOLATILITY"],
            "IRON_CONDOR": ["HIGH_VOLATILITY", "TRENDING_UP", "TRENDING_DOWN"],
            # Long vol strategies should exit on range-bound
            "LONG_STRADDLE": ["RANGE_BOUND"],
            # Directional strategies should exit on opposite trend
            "BULL_CALL_SPREAD": ["TRENDING_DOWN"],
            "BEAR_PUT_SPREAD": ["TRENDING_UP"],
        }

        bad_regimes = unfavorable.get(trade.strategy_type, [])

        if current_regime in bad_regimes and current_regime != trade.entry_regime:
            return ExitSignal(
                should_exit=True,
                reason=ExitReason.REGIME_CHANGE,
                priority=ExitPriority.LOW,
                description=f"Regime changed: {trade.entry_regime} → {current_regime}",
                urgency="normal",
            )

        return None

    def get_exit_action(
        self,
        signals: list[ExitSignal],
    ) -> tuple[bool, ExitReason | None, str]:
        """Get the primary exit action from signals.

        Args:
            signals: List of exit signals

        Returns:
            Tuple of (should_exit, reason, description)
        """
        if not signals:
            return False, None, ""

        # Get highest priority signal that should exit
        exit_signals = [s for s in signals if s.should_exit]
        if not exit_signals:
            return False, None, ""

        primary = exit_signals[0]  # Already sorted by priority
        return True, primary.reason, primary.description


def check_should_exit(
    trade: OptionsTradeState,
    kill_switch: bool = False,
    max_daily_loss: Decimal = Decimal("0"),
    daily_pnl: Decimal = Decimal("0"),
    current_iv: float = 0.0,
    entry_iv: float = 0.0,
    dte: float = 7.0,
    current_regime: str = "",
) -> tuple[bool, ExitReason | None, str]:
    """Convenience function to check if trade should exit.

    Args:
        trade: Trade state
        kill_switch: Kill switch active
        max_daily_loss: Max daily loss
        daily_pnl: Current daily P&L
        current_iv: Current IV
        entry_iv: Entry IV
        dte: Days to expiry
        current_regime: Current regime

    Returns:
        Tuple of (should_exit, reason, description)
    """
    checker = ExitConditionChecker(
        kill_switch_active=kill_switch,
        max_daily_loss=max_daily_loss,
        current_daily_pnl=daily_pnl,
    )

    signals = checker.check_all_conditions(
        trade=trade,
        current_iv=current_iv,
        entry_iv=entry_iv,
        dte=dte,
        current_regime=current_regime,
    )

    return checker.get_exit_action(signals)
