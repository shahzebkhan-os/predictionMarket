"""
Tests for NSE Session Manager.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from nse_advisor.data.nse_session import NseSession


@pytest.fixture
def nse_session():
    """Create NSE session fixture."""
    return NseSession()


class TestNseSession:
    """Tests for NseSession class."""
    
    @pytest.mark.asyncio
    async def test_session_cookie_refresh(self, nse_session):
        """Test that session cookies are properly initialized."""
        with patch.object(nse_session, '_get_session') as mock_get:
            mock_session = MagicMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_session.get.return_value = mock_response
            mock_get.return_value = mock_session
            
            await nse_session.init_session()
            
            # Verify NSE homepage was fetched to seed cookies
            mock_session.get.assert_called()
    
    @pytest.mark.asyncio
    async def test_retry_on_403(self, nse_session):
        """Test retry logic on HTTP 403 errors."""
        with patch.object(nse_session, '_get_session') as mock_get:
            mock_session = MagicMock()
            
            # First call returns 403, second succeeds
            responses = [
                MagicMock(status_code=403),
                MagicMock(status_code=200, json=lambda: {"data": "test"}),
            ]
            mock_session.get.side_effect = responses
            mock_get.return_value = mock_session
            
            await nse_session.init_session()
            result = await nse_session.fetch("https://www.nseindia.com/api/test")
            
            # Should have retried
            assert mock_session.get.call_count >= 2
    
    @pytest.mark.asyncio
    async def test_stale_data_flag(self, nse_session):
        """Test stale data detection."""
        # Simulate old timestamp
        nse_session._last_successful_fetch = datetime(2020, 1, 1)
        
        assert nse_session.is_data_stale(max_age_seconds=10)
    
    def test_headers_set_correctly(self, nse_session):
        """Test that browser-like headers are set."""
        with patch.object(nse_session, '_get_session') as mock_get:
            mock_session = MagicMock()
            mock_get.return_value = mock_session
            
            nse_session._setup_session()
            
            # Headers should include User-Agent
            assert 'User-Agent' in mock_session.headers


class TestBanListParse:
    """Tests for ban list parsing."""
    
    def test_ban_list_parse(self):
        """Test parsing of ban list response."""
        from nse_advisor.market.ban_list import BanListChecker
        
        checker = BanListChecker()
        
        # Mock NSE response format
        mock_data = {
            "data": [
                {"symbol": "DELTACORP"},
                {"symbol": "INDIABULLS"},
            ]
        }
        
        checker._parse_ban_list(mock_data)
        
        assert checker.is_banned("DELTACORP")
        assert checker.is_banned("INDIABULLS")
        assert not checker.is_banned("RELIANCE")
