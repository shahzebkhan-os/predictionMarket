"""
Position Tracker.

Monitors manually logged trades and computes live P&L.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, date

from zoneinfo import ZoneInfo

from nse_advisor.config import get_settings
from nse_advisor.market.option_chain import OptionChainSnapshot
from nse_advisor.tracker.state import ManualTrade, TradeLeg

logger = logging.getLogger(__name__)


class PositionTracker:
    """
    Tracks manual trades and computes live P&L.
    
    Two loops:
    - fast_loop (5s): Update prices, recalculate P&L
    - slow_loop (60s): Recalculate Greeks, check exit conditions
    """
    
    def __init__(self) -> None:
        """Initialize position tracker."""
        self._ist = ZoneInfo("Asia/Kolkata")
        self._settings = get_settings()
        
        # State
        self._trades: dict[str, ManualTrade] = {}
        self._running = False
    
    def add_trade(self, trade: ManualTrade) -> None:
        """Add a trade to track."""
        self._trades[trade.trade_id] = trade
        logger.info(f"Tracking trade: {trade.trade_id}")
    
    def remove_trade(self, trade_id: str) -> ManualTrade | None:
        """Remove a trade from tracking."""
        return self._trades.pop(trade_id, None)
    
    def get_trade(self, trade_id: str) -> ManualTrade | None:
        """Get a tracked trade by ID."""
        return self._trades.get(trade_id)
    
    def get_all_trades(self) -> list[ManualTrade]:
        """Get all tracked trades."""
        return list(self._trades.values())
    
    def get_open_trades(self) -> list[ManualTrade]:
        """Get all open trades."""
        return [t for t in self._trades.values() if t.is_open]
    
    def update_prices(
        self,
        chain: OptionChainSnapshot,
    ) -> None:
        """
        Update current prices for all tracked positions.
        
        Args:
            chain: Option chain snapshot with current prices
        """
        for trade in self._trades.values():
            if not trade.is_open:
                continue
            
            for leg in trade.legs:
                strike_data = chain.get_strike(leg.strike)
                if strike_data:
                    if leg.option_type == "CE":
                        leg.current_price = strike_data.ce_ltp
                    else:
                        leg.current_price = strike_data.pe_ltp
    
    def update_greeks(
        self,
        chain: OptionChainSnapshot,
    ) -> None:
        """
        Update current Greeks for all tracked positions.
        
        Args:
            chain: Option chain snapshot with Greeks
        """
        for trade in self._trades.values():
            if not trade.is_open:
                continue
            
            for leg in trade.legs:
                strike_data = chain.get_strike(leg.strike)
                if strike_data:
                    if leg.option_type == "CE":
                        leg.current_delta = strike_data.ce_delta
                        leg.current_gamma = strike_data.ce_gamma
                        leg.current_theta = strike_data.ce_theta
                        leg.current_vega = strike_data.ce_vega
                    else:
                        leg.current_delta = strike_data.pe_delta
                        leg.current_gamma = strike_data.pe_gamma
                        leg.current_theta = strike_data.pe_theta
                        leg.current_vega = strike_data.pe_vega
    
    def get_portfolio_greeks(self) -> dict[str, float]:
        """Get aggregated Greeks across all open positions."""
        delta = 0.0
        gamma = 0.0
        theta = 0.0
        vega = 0.0
        
        for trade in self.get_open_trades():
            delta += trade.net_delta
            gamma += trade.net_gamma
            theta += trade.net_theta
            vega += trade.net_vega
        
        return {
            "delta": delta,
            "gamma": gamma,
            "theta": theta,
            "vega": vega,
        }
    
    def get_total_pnl(self) -> dict[str, float]:
        """Get total P&L across all positions."""
        unrealized = sum(
            t.unrealized_pnl
            for t in self._trades.values()
            if t.is_open
        )
        realized = sum(
            t.realized_pnl or 0
            for t in self._trades.values()
            if not t.is_open
        )
        
        return {
            "unrealized": unrealized,
            "realized": realized,
            "total": unrealized + realized,
        }
    
    def close_trade(
        self,
        trade_id: str,
        exit_prices: dict[str, float] | None = None,
        reason: str = "Manual close",
    ) -> ManualTrade | None:
        """
        Close a trade.
        
        Args:
            trade_id: ID of trade to close
            exit_prices: Map of symbol -> exit price
            reason: Reason for closing
            
        Returns:
            Closed trade or None if not found
        """
        trade = self._trades.get(trade_id)
        if not trade:
            return None
        
        now = datetime.now(self._ist)
        trade.status = "CLOSED"
        trade.exit_time = now
        trade.exit_reason = reason
        
        # Set exit prices
        for leg in trade.legs:
            if exit_prices and leg.tradingsymbol in exit_prices:
                leg.exit_price = exit_prices[leg.tradingsymbol]
            else:
                leg.exit_price = leg.current_price
            leg.exit_time = now
        
        logger.info(
            f"Trade closed: {trade_id} "
            f"P&L: ₹{trade.realized_pnl:.0f} ({reason})"
        )
        
        return trade
    
    async def fast_loop(
        self,
        chain_provider,  # Callable that returns OptionChainSnapshot
        interval: float = 5.0,
    ) -> None:
        """
        Fast update loop - price updates every 5 seconds.
        
        Args:
            chain_provider: Async function that returns latest chain
            interval: Update interval in seconds
        """
        self._running = True
        
        while self._running:
            try:
                chain = await chain_provider()
                if chain and chain.is_valid:
                    self.update_prices(chain)
                    logger.debug("Fast loop: prices updated")
            except Exception as e:
                logger.error(f"Fast loop error: {e}")
            
            await asyncio.sleep(interval)
    
    async def slow_loop(
        self,
        chain_provider,  # Callable that returns OptionChainSnapshot
        interval: float = 60.0,
    ) -> None:
        """
        Slow update loop - Greeks updates every 60 seconds.
        
        Args:
            chain_provider: Async function that returns latest chain
            interval: Update interval in seconds
        """
        self._running = True
        
        while self._running:
            try:
                chain = await chain_provider()
                if chain and chain.is_valid:
                    self.update_greeks(chain)
                    logger.debug("Slow loop: Greeks updated")
            except Exception as e:
                logger.error(f"Slow loop error: {e}")
            
            await asyncio.sleep(interval)
    
    def stop(self) -> None:
        """Stop tracker loops."""
        self._running = False


# Global instance
_position_tracker: PositionTracker | None = None


def get_position_tracker() -> PositionTracker:
    """Get or create global position tracker."""
    global _position_tracker
    if _position_tracker is None:
        _position_tracker = PositionTracker()
    return _position_tracker
