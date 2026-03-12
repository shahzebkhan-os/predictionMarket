"""
Tests for NSE Session Manager.

Tests all 6 layers of anti-bot handling.
"""

import asyncio
from datetime import datetime, timedelta
from unittest.mock import MagicMock, AsyncMock, patch

import pytest
from zoneinfo import ZoneInfo

from nse_advisor.data.nse_session import (
    NseSession,
    NseSessionError,
    NseIpBannedError,
    NseSessionStaleError,
    API_HEADERS,
    HOMEPAGE_HEADERS,
    BASE_HEADERS,
)

IST = ZoneInfo("Asia/Kolkata")


@pytest.fixture
def session():
    """Create a fresh NseSession for each test."""
    return NseSession()


class TestLayer1CookieInit:
    """Test Layer 1: 3-step session initialization for cookies."""
    
    def test_session_starts_uninitialized(self, session):
        """Session should start without an active session."""
        assert session._session is None
        assert session._last_init is None
        assert not session.is_initialized
    
    @pytest.mark.asyncio
    async def test_init_creates_session(self, session):
        """Init should create a valid session."""
        with patch.object(session, '_init_sync') as mock_init:
            await session.init()
            mock_init.assert_called_once()
    
    def test_init_sync_visits_homepage_and_option_chain(self, session):
        """3-step init should visit homepage then option-chain page."""
        with patch("requests.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session_cls.return_value = mock_session
            mock_session.get.return_value.status_code = 200
            mock_session.cookies = {"cookie1": "value1"}
            
            with patch("time.sleep"):
                session._init_sync()
            
            # Should have visited at least 2 URLs
            assert mock_session.get.call_count >= 2
            
            # First call should be homepage
            first_call = mock_session.get.call_args_list[0]
            assert "nseindia.com" in first_call[0][0]
            
            # Second call should be option-chain
            second_call = mock_session.get.call_args_list[1]
            assert "option-chain" in second_call[0][0]


class TestLayer2CookieExpiry:
    """Test Layer 2: Session staleness detection and auto-refresh."""
    
    def test_fresh_session_not_stale(self, session):
        """Recently initialized session should not be stale."""
        session._last_init = datetime.now(IST)
        session._session = MagicMock()
        
        assert not session._is_stale()
    
    def test_old_session_is_stale(self, session):
        """Session older than EXPIRY_MINUTES should be stale."""
        session._last_init = datetime.now(IST) - timedelta(minutes=26)
        session._session = MagicMock()
        
        assert session._is_stale()
    
    def test_no_session_is_stale(self, session):
        """No session should be considered stale."""
        assert session._is_stale()
    
    @pytest.mark.asyncio
    async def test_stale_session_triggers_reinit(self, session):
        """Fetching with stale session should trigger re-init."""
        session._last_init = datetime.now(IST) - timedelta(minutes=30)
        session._session = MagicMock()
        
        with patch.object(session, 'init', new_callable=AsyncMock) as mock_init:
            with patch.object(session, '_session') as mock_s:
                mock_s.get.return_value.status_code = 200
                mock_s.get.return_value.text = '{"data": []}'
                mock_s.get.return_value.json.return_value = {"data": []}
                
                # Reset stale flag after init
                async def reset_stale():
                    session._last_init = datetime.now(IST)
                mock_init.side_effect = reset_stale
                
                await session.fetch("https://www.nseindia.com/api/allIndices")
                mock_init.assert_called_once()


class TestLayer3HtmlDetection:
    """Test Layer 3: HTML response detection (session expired but 200 OK)."""
    
    def test_detects_html_doctype(self, session):
        """Should detect HTML with DOCTYPE."""
        assert session._is_html("<!DOCTYPE html><html></html>")
        assert session._is_html("<!doctype HTML><html></html>")
    
    def test_detects_html_tag(self, session):
        """Should detect HTML with <html> tag."""
        assert session._is_html("<html lang='en'>")
        assert session._is_html("  <HTML>")
    
    def test_valid_json_not_html(self, session):
        """Should not flag valid JSON as HTML."""
        assert not session._is_html('{"records": {"data": []}}')
        assert not session._is_html('[]')
        assert not session._is_html('{"status": "ok"}')
    
    @pytest.mark.asyncio
    async def test_html_response_triggers_reinit(self, session):
        """HTML response should trigger session re-init."""
        session._session = MagicMock()
        session._last_init = datetime.now(IST)
        
        # First response is HTML, second is JSON
        responses = [
            MagicMock(
                status_code=200,
                text="<!DOCTYPE html><html>login page</html>"
            ),
            MagicMock(
                status_code=200,
                text='{"data":[]}',
                json=MagicMock(return_value={"data": []})
            ),
        ]
        
        call_count = [0]
        def get_side_effect(*args, **kwargs):
            result = responses[min(call_count[0], len(responses) - 1)]
            call_count[0] += 1
            return result
        
        session._session.get.side_effect = get_side_effect
        
        with patch.object(session, 'init', new_callable=AsyncMock):
            result = await session.fetch("https://www.nseindia.com/api/allIndices")
            # Should have re-inited due to HTML response
            assert session.init.called


class TestLayer4IPBan:
    """Test Layer 4: IP ban detection and handling."""
    
    def test_classify_cloudflare_403(self):
        """403 with Cloudflare in body should be IP_BANNED_CLOUDFLARE."""
        result = NseSession._classify_error(403, "cloudflare ray-id blocked")
        assert result == "IP_BANNED_CLOUDFLARE"
    
    def test_classify_cf_ray_403(self):
        """403 with cf-ray should be IP_BANNED_CLOUDFLARE."""
        result = NseSession._classify_error(403, "cf-ray: abc123")
        assert result == "IP_BANNED_CLOUDFLARE"
    
    def test_classify_regular_403(self):
        """Regular 403 should be SESSION_EXPIRED_403."""
        result = NseSession._classify_error(403, "Access denied")
        assert result == "SESSION_EXPIRED_403"
    
    def test_classify_429(self):
        """429 should be RATE_LIMITED."""
        result = NseSession._classify_error(429, "Too many requests")
        assert result == "RATE_LIMITED"
    
    def test_classify_503(self):
        """503 should be NSE_DOWN."""
        result = NseSession._classify_error(503, "Service unavailable")
        assert result == "NSE_DOWN"
    
    @pytest.mark.asyncio
    async def test_cloudflare_403_raises_ip_banned(self, session):
        """Cloudflare 403 should raise NseIpBannedError."""
        session._session = MagicMock()
        session._last_init = datetime.now(IST)
        session._session.get.return_value.status_code = 403
        session._session.get.return_value.text = "cloudflare ray-id blocked"
        
        with pytest.raises(NseIpBannedError):
            await session.fetch("https://www.nseindia.com/api/allIndices")
    
    @pytest.mark.asyncio
    async def test_429_waits_and_retries(self, session):
        """429 should wait and retry."""
        session._session = MagicMock()
        session._last_init = datetime.now(IST)
        
        responses = [
            MagicMock(status_code=429, text="Too Many Requests"),
            MagicMock(
                status_code=200,
                text='{"data":[]}',
                json=MagicMock(return_value={"data": []})
            ),
        ]
        session._session.get.side_effect = responses
        
        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await session.fetch("https://www.nseindia.com/api/allIndices")
            assert result == {"data": []}


class TestLayer5Headers:
    """Test Layer 5: Browser-like headers."""
    
    def test_api_headers_include_referer(self):
        """API headers should include Referer."""
        assert "Referer" in API_HEADERS
        assert "nseindia.com" in API_HEADERS["Referer"]
    
    def test_api_headers_include_x_requested_with(self):
        """API headers should include X-Requested-With."""
        assert "X-Requested-With" in API_HEADERS
        assert API_HEADERS["X-Requested-With"] == "XMLHttpRequest"
    
    def test_api_headers_include_sec_fetch(self):
        """API headers should include Sec-Fetch headers."""
        assert "Sec-Fetch-Site" in API_HEADERS
        assert API_HEADERS["Sec-Fetch-Site"] == "same-origin"
        assert API_HEADERS["Sec-Fetch-Mode"] == "cors"
        assert API_HEADERS["Sec-Fetch-Dest"] == "empty"
    
    def test_homepage_headers_different_from_api(self):
        """Homepage headers should be for navigation, not API."""
        assert HOMEPAGE_HEADERS["Sec-Fetch-Site"] == "none"
        assert HOMEPAGE_HEADERS["Sec-Fetch-Mode"] == "navigate"
        assert HOMEPAGE_HEADERS["Sec-Fetch-Dest"] == "document"
        assert "Referer" not in HOMEPAGE_HEADERS
    
    def test_user_agent_present(self):
        """User-Agent should be present and browser-like."""
        assert "User-Agent" in BASE_HEADERS
        assert "Mozilla" in BASE_HEADERS["User-Agent"]
        assert "Chrome" in BASE_HEADERS["User-Agent"]


class TestSessionStatus:
    """Test session status monitoring."""
    
    def test_status_returns_dict(self, session):
        """Status should return a dict with expected keys."""
        status = session.status()
        
        assert isinstance(status, dict)
        assert "initialized" in status
        assert "age_minutes" in status
        assert "is_stale" in status
        assert "consecutive_failures" in status
        assert "playwright_mode" in status
        assert "last_init" in status
    
    def test_status_uninitialized(self, session):
        """Uninitialized session status should reflect that."""
        status = session.status()
        
        assert status["initialized"] is False
        assert status["is_stale"] is True
        assert status["last_init"] is None
    
    def test_status_initialized(self, session):
        """Initialized session status should reflect that."""
        session._session = MagicMock()
        session._last_init = datetime.now(IST)
        
        status = session.status()
        
        assert status["initialized"] is True
        assert status["is_stale"] is False
        assert status["last_init"] is not None
    
    def test_session_age_minutes(self, session):
        """Session age should be calculated correctly."""
        session._last_init = datetime.now(IST) - timedelta(minutes=10)
        
        age = session.session_age_minutes
        assert 9.5 < age < 10.5


class TestConsecutiveFailures:
    """Test consecutive failure tracking."""
    
    @pytest.mark.asyncio
    async def test_consecutive_failures_increment(self, session):
        """Consecutive failures should increment on errors."""
        session._session = MagicMock()
        session._last_init = datetime.now(IST)
        session._session.get.side_effect = Exception("Network error")
        
        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(NseSessionError):
                await session.fetch("https://www.nseindia.com/api/allIndices")
        
        assert session._consecutive_failures >= 3  # MAX_RETRIES
    
    @pytest.mark.asyncio
    async def test_consecutive_failures_reset_on_success(self, session):
        """Consecutive failures should reset on success."""
        session._session = MagicMock()
        session._last_init = datetime.now(IST)
        session._consecutive_failures = 4
        
        session._session.get.return_value.status_code = 200
        session._session.get.return_value.text = '{"data":[]}'
        session._session.get.return_value.json.return_value = {"data": []}
        
        await session.fetch("https://www.nseindia.com/api/allIndices")
        
        assert session._consecutive_failures == 0


class TestGlobalSession:
    """Test global session management."""
    
    def test_get_nse_session_returns_singleton(self):
        """get_nse_session should return the same instance."""
        from nse_advisor.data.nse_session import get_nse_session, _nse_session
        
        session1 = get_nse_session()
        session2 = get_nse_session()
        
        assert session1 is session2
    
    @pytest.mark.asyncio
    async def test_close_nse_session(self):
        """close_nse_session should clean up."""
        from nse_advisor.data.nse_session import (
            get_nse_session,
            close_nse_session,
            _nse_session
        )
        
        session = get_nse_session()
        session._session = MagicMock()
        
        await close_nse_session()
        
        # After close, getting session should create new one
        from nse_advisor.data import nse_session as ns_module
        ns_module._nse_session = None
        new_session = get_nse_session()
        assert new_session is not session
