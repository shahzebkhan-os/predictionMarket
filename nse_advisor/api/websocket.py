"""
WebSocket Connection Manager.

Manages WebSocket connections for real-time updates.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from fastapi import WebSocket
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


class ConnectionManager:
    """
    WebSocket connection manager.
    
    Features:
    - Track active connections
    - Broadcast to all connected clients
    - Auto-cleanup on disconnect
    """
    
    def __init__(self) -> None:
        """Initialize connection manager."""
        self.active_connections: list[WebSocket] = []
        self._lock = asyncio.Lock()
    
    async def connect(self, websocket: WebSocket) -> None:
        """Accept and track a new WebSocket connection."""
        await websocket.accept()
        async with self._lock:
            self.active_connections.append(websocket)
        logger.info(f"WebSocket connected. Active connections: {len(self.active_connections)}")
    
    def disconnect(self, websocket: WebSocket) -> None:
        """Remove a WebSocket connection."""
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        logger.info(f"WebSocket disconnected. Active connections: {len(self.active_connections)}")
    
    async def send_json(self, websocket: WebSocket, data: dict[str, Any]) -> None:
        """Send JSON data to a specific connection."""
        try:
            await websocket.send_json(data)
        except Exception as e:
            logger.warning(f"Failed to send to WebSocket: {e}")
            self.disconnect(websocket)
    
    async def broadcast(self, data: dict[str, Any]) -> None:
        """Broadcast JSON data to all connected clients."""
        disconnected = []
        
        for connection in self.active_connections:
            try:
                await connection.send_json(data)
            except Exception as e:
                logger.warning(f"Broadcast failed for connection: {e}")
                disconnected.append(connection)
        
        # Cleanup disconnected
        for conn in disconnected:
            self.disconnect(conn)
    
    @property
    def connection_count(self) -> int:
        """Get number of active connections."""
        return len(self.active_connections)
