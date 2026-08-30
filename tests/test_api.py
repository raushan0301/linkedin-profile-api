"""
test_api.py — Unit tests for the LinkedIn Profile API

Testing strategy:
- Unit tests for URL parsing and validation (no network calls)
- Unit tests for the auth module with mocked HTTP responses (no network calls)
- Unit tests for the parser with fixture data (no network calls)
- Integration tests for API endpoints with mocked LinkedIn client (no network calls)

All tests run without real LinkedIn credentials.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from fastapi.testclient import TestClient

from app.main import app
from app.models import ProfileRequest
from app.parser import parse_profile, _format_date, _parse_date_range, _safe_get


# ── Fixtures ─────────────────────────────────────────────────────────────────

MOCK_PROFILE_VIEW = {
    "profile": {
        "firstName": "Bill",
        "lastName": "Gates",
        "headline": "Co-chair, Bill & Melinda Gates Foundation",
        "locationName": "Seattle, Washington, United States",
        "geoCountryName": "United States",
        "summary": "Entrepreneur and co-founder of Microsoft.",
        "miniProfile": {
            "firstName": "Bill",
            "lastName": "Gates",
            "occupation": "Co-chair, Bill & Melinda Gates Foundation",
            "picture": {
                "rootUrl": "https://media.licdn.com/dms/image/",
                "artifacts": [
                    {
                        "fileIdentifyingUrlPathSegment": "100_100/photo.jpg",
                        "width": 100,
                        "height": 100,
                    },
                    {
                        "fileIdentifyingUrlPathSegment": "400_400/photo.jpg",
                        "width": 400,
                        "height": 400,
                    },
                ],
            },
            "premium": True,
        },
        "positionView": None,
        "educationView": None,
    },
    "positionView": {
        "elements": [
            {
                "title": "Co-chair",
                "company": {
                    "miniCompany": {
                        "name": "Bill & Melinda Gates Foundation",
                        "universalName": "bill-melinda-gates-foundation",
                    }
                },
                "locationName": "Seattle, WA",
                "timePeriod": {
                    "startDate": {"year": 2000, "month": 1},
                    "endDate": None,
                },
                "description": "Working on global health initiatives.",
            }
        ]
    },
    "educationView": {
        "elements": [
            {
                "schoolName": "Harvard University",
                "degreeName": "Bachelor of Science",
                "fieldOfStudy": "Computer Science",
                "timePeriod": {
                    "startDate": {"year": 1973},
                    "endDate": {"year": 1975},
                },
            }
        ]
    },
    "networkInfo": {
        "connections": {"value": 500},
        "followersCount": 35000000,
    },
}

MOCK_SKILLS = {
    "elements": [
        {"name": "Software Engineering", "endorsementCount": 99},
        {"name": "Philanthropy", "endorsementCount": 150},
        {"name": "Strategic Planning", "endorsementCount": 75},
    ]
}

MOCK_CERTIFICATIONS = {
    "elements": [
        {
            "name": "Microsoft Certified Professional",
            "company": {"miniCompany": {"name": "Microsoft"}},
            "startedOn": {"year": 1990, "month": 6},
        }
    ]
}

MOCK_LANGUAGES = {
    "elements": [
        {"name": "English", "proficiency": "NATIVE_OR_BILINGUAL"},
    ]
}

MOCK_RAW = {
    "profileView": MOCK_PROFILE_VIEW,
    "skills": MOCK_SKILLS,
    "certifications": MOCK_CERTIFICATIONS,
    "languages": MOCK_LANGUAGES,
}


# ── URL extraction tests ───────────────────────────────────────────────────

class TestProfileRequestParsing:

    def test_standard_url(self):
        req = ProfileRequest(url="https://www.linkedin.com/in/williamhgates")
        assert req.extract_username() == "williamhgates"

    def test_trailing_slash(self):
        req = ProfileRequest(url="https://www.linkedin.com/in/williamhgates/")
        assert req.extract_username() == "williamhgates"

    def test_url_with_query_params(self):
        req = ProfileRequest(url="https://www.linkedin.com/in/williamhgates?trk=public_profile")
        assert req.extract_username() == "williamhgates"

    def test_url_without_www(self):
        req = ProfileRequest(url="https://linkedin.com/in/williamhgates")
        assert req.extract_username() == "williamhgates"

    def test_rejects_non_linkedin_url(self):
        with pytest.raises(Exception):
            ProfileRequest(url="https://twitter.com/BillGates")

    def test_rejects_company_page(self):
        with pytest.raises(Exception):
            ProfileRequest(url="https://www.linkedin.com/company/microsoft")


# ── Utility function tests ─────────────────────────────────────────────────

class TestUtilityFunctions:

    def test_safe_get_nested(self):
        d = {"a": {"b": {"c": 42}}}
        assert _safe_get(d, "a", "b", "c") == 42

    def test_safe_get_missing_key(self):
        d = {"a": {"b": {}}}
        assert _safe_get(d, "a", "b", "c", default="fallback") == "fallback"

    def test_safe_get_none_intermediate(self):
        d = {"a": None}
        assert _safe_get(d, "a", "b") is None

    def test_format_date_year_and_month(self):
        assert _format_date({"year": 2020, "month": 3}) == "Mar 2020"

    def test_format_date_year_only(self):
        assert _format_date({"year": 2020}) == "2020"

    def test_format_date_none(self):
        assert _format_date(None) is None

    def test_parse_date_range_with_end(self):
        dr = _parse_date_range({
            "startDate": {"year": 2018, "month": 6},
            "endDate": {"year": 2021, "month": 12},
        })
        assert dr.start == "Jun 2018"
        assert dr.end == "Dec 2021"

    def test_parse_date_range_present(self):
        dr = _parse_date_range({
            "startDate": {"year": 2022, "month": 1},
        })
        assert dr.start == "Jan 2022"
        assert dr.end == "Present"


# ── Parser tests ───────────────────────────────────────────────────────────

class TestParser:

    def test_parses_name(self):
        profile = parse_profile(MOCK_RAW, "williamhgates")
        assert profile.name == "Bill Gates"
        assert profile.first_name == "Bill"
        assert profile.last_name == "Gates"

    def test_parses_headline(self):
        profile = parse_profile(MOCK_RAW, "williamhgates")
        assert profile.headline == "Co-chair, Bill & Melinda Gates Foundation"

    def test_parses_location(self):
        profile = parse_profile(MOCK_RAW, "williamhgates")
        assert profile.location == "Seattle, Washington, United States"

    def test_parses_about(self):
        profile = parse_profile(MOCK_RAW, "williamhgates")
        assert "Microsoft" in profile.about

    def test_parses_experience(self):
        profile = parse_profile(MOCK_RAW, "williamhgates")
        assert len(profile.experience) == 1
        exp = profile.experience[0]
        assert exp.title == "Co-chair"
        assert exp.company == "Bill & Melinda Gates Foundation"
        assert exp.duration.end == "Present"

    def test_parses_education(self):
        profile = parse_profile(MOCK_RAW, "williamhgates")
        assert len(profile.education) == 1
        edu = profile.education[0]
        assert edu.school == "Harvard University"
        assert edu.degree == "Bachelor of Science"

    def test_parses_skills_sorted_by_endorsements(self):
        profile = parse_profile(MOCK_RAW, "williamhgates")
        assert len(profile.skills) == 3
        # Should be sorted descending by endorsement count
        counts = [s.endorsement_count for s in profile.skills]
        assert counts == sorted(counts, reverse=True)
        assert profile.skills[0].name == "Philanthropy"

    def test_parses_certifications(self):
        profile = parse_profile(MOCK_RAW, "williamhgates")
        assert len(profile.certifications) == 1
        cert = profile.certifications[0]
        assert cert.name == "Microsoft Certified Professional"
        assert cert.issuing_organization == "Microsoft"

    def test_parses_languages(self):
        profile = parse_profile(MOCK_RAW, "williamhgates")
        assert len(profile.languages) == 1
        assert profile.languages[0].name == "English"
        assert profile.languages[0].proficiency == "NATIVE_OR_BILINGUAL"

    def test_picks_largest_profile_image(self):
        profile = parse_profile(MOCK_RAW, "williamhgates")
        assert profile.profile_image is not None
        assert profile.profile_image.width == 400
        assert "400_400" in profile.profile_image.url

    def test_correct_profile_url(self):
        profile = parse_profile(MOCK_RAW, "williamhgates")
        assert profile.profile_url == "https://www.linkedin.com/in/williamhgates"

    def test_handles_empty_sections_gracefully(self):
        """Parser should not crash when sections are missing."""
        minimal_raw = {
            "profileView": {"profile": {"firstName": "Jane", "miniProfile": {}}},
            "skills": {},
            "certifications": {},
            "languages": {},
        }
        profile = parse_profile(minimal_raw, "janedoe")
        assert profile.name == "Jane"
        assert profile.experience == []
        assert profile.skills == []


# ── API endpoint tests ─────────────────────────────────────────────────────

class TestAPIEndpoints:
    """
    Integration tests for the FastAPI endpoints.
    The LinkedIn client is mocked so these tests run without credentials.
    """

    @pytest.fixture(autouse=True)
    def mock_settings(self, monkeypatch):
        """Inject fake credentials so the app starts successfully."""
        monkeypatch.setenv("LI_AT", "fake_li_at_token_for_testing")
        monkeypatch.setenv("JSESSIONID", '"ajax:fake_jsessionid"')
        # Reset the cached settings singleton
        from app.config import get_settings
        get_settings.cache_clear()

    def test_health_endpoint(self):
        with TestClient(app) as client:
            response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_profile_endpoint_success(self):
        with patch(
            "app.main.LinkedInClient.get_full_profile",
            new_callable=AsyncMock,
            return_value=MOCK_RAW,
        ):
            with TestClient(app) as client:
                response = client.post(
                    "/api/profile",
                    json={"url": "https://www.linkedin.com/in/williamhgates"},
                )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Bill Gates"
        assert data["username"] == "williamhgates"

    def test_profile_endpoint_invalid_url(self):
        with TestClient(app) as client:
            response = client.post(
                "/api/profile",
                json={"url": "https://twitter.com/someone"},
            )
        assert response.status_code == 422  # Pydantic validation error

    def test_profile_endpoint_auth_error(self):
        from app.linkedin_client import LinkedInAuthError
        with patch(
            "app.main.LinkedInClient.get_full_profile",
            new_callable=AsyncMock,
            side_effect=LinkedInAuthError("Credentials expired"),
        ):
            with TestClient(app) as client:
                response = client.post(
                    "/api/profile",
                    json={"url": "https://www.linkedin.com/in/williamhgates"},
                )
        assert response.status_code == 401
        assert response.json()["error"] == "authentication_failed"

    def test_profile_endpoint_not_found(self):
        from app.linkedin_client import LinkedInNotFoundError
        with patch(
            "app.main.LinkedInClient.get_full_profile",
            new_callable=AsyncMock,
            side_effect=LinkedInNotFoundError("Profile not found"),
        ):
            with TestClient(app) as client:
                response = client.post(
                    "/api/profile",
                    json={"url": "https://www.linkedin.com/in/nonexistentuser999"},
                )
        assert response.status_code == 404

    def test_profile_endpoint_rate_limited(self):
        from app.linkedin_client import LinkedInRateLimitError
        with patch(
            "app.main.LinkedInClient.get_full_profile",
            new_callable=AsyncMock,
            side_effect=LinkedInRateLimitError("Too many requests"),
        ):
            with TestClient(app) as client:
                response = client.post(
                    "/api/profile",
                    json={"url": "https://www.linkedin.com/in/williamhgates"},
                )
        assert response.status_code == 429

    def test_health_reports_auth_mode_cookie(self):
        with TestClient(app) as client:
            response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["auth_mode"] == "cookie"


# ── Auth module tests ──────────────────────────────────────────────────────

class TestLinkedInAuth:
    """
    Tests for the reverse-engineered LinkedIn login flow.
    All HTTP calls are mocked — no real network traffic.
    """

    # HTML snippet that mimics what LinkedIn's login page actually returns
    MOCK_LOGIN_HTML = """
    <html><body>
    <form>
      <input type="hidden" name="loginCsrfParam" value="abc123-csrf-token">
      <input type="text" name="session_key">
      <input type="password" name="session_password">
    </form>
    </body></html>
    """

    @pytest.mark.asyncio
    async def test_extracts_csrf_from_login_page(self):
        """Verify we can extract the CSRF token from a real-looking login page."""
        import re
        match = re.search(
            r'name="loginCsrfParam"\s+value="([^"]+)"',
            self.MOCK_LOGIN_HTML,
        )
        assert match is not None
        assert match.group(1) == "abc123-csrf-token"

    @pytest.mark.asyncio
    async def test_successful_login_returns_tokens(self):
        """
        Simulate a successful login: login page returns CSRF token,
        submit returns li_at cookie.
        """
        from app.auth import login

        mock_login_response = MagicMock()
        mock_login_response.status_code = 200
        mock_login_response.text = self.MOCK_LOGIN_HTML

        mock_submit_response = MagicMock()
        mock_submit_response.status_code = 200
        mock_submit_response.url = httpx.URL("https://www.linkedin.com/feed/")

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_login_response)
        mock_client.post = AsyncMock(return_value=mock_submit_response)
        mock_client.cookies = {"li_at": "mock_li_at_token", "JSESSIONID": "ajax:mocksession"}
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("app.auth.httpx.AsyncClient", return_value=mock_client):
            result = await login("user@example.com", "password123")

        assert result["li_at"] == "mock_li_at_token"

    @pytest.mark.asyncio
    async def test_challenge_response_raises_challenge_error(self):
        """
        Mobile API returns HTTP 401 with 'challenge' in the body when
        LinkedIn requires 2FA or CAPTCHA. We should raise LinkedInChallengeError.
        """
        from app.auth import login, LinkedInChallengeError

        # Step 1: session init succeeds, returns JSESSIONID
        mock_init_response = MagicMock()
        mock_init_response.status_code = 200

        # Step 2: credential submission returns 401 with challenge body
        mock_auth_response = MagicMock()
        mock_auth_response.status_code = 401
        mock_auth_response.text = '{"status": 401, "message": "challenge required", "serviceErrorCode": 65}"}'

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_init_response)
        mock_client.post = AsyncMock(return_value=mock_auth_response)
        mock_client.cookies = MagicMock()
        mock_client.cookies.get = MagicMock(side_effect=lambda k: "ajax:test" if k == "JSESSIONID" else None)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("app.auth.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(LinkedInChallengeError):
                await login("user@example.com", "correctpassword")

    @pytest.mark.asyncio
    async def test_missing_csrf_raises_login_error(self):
        """
        If the login page HTML doesn't contain the expected CSRF field,
        raise a clear error rather than an obscure AttributeError.
        """
        from app.auth import login, LinkedInLoginError

        mock_login_response = MagicMock()
        mock_login_response.status_code = 200
        mock_login_response.text = "<html><body>Unexpected page content</body></html>"

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_login_response)
        mock_client.cookies = {}
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("app.auth.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(LinkedInLoginError):
                await login("user@example.com", "password123")


# ── Config tests ───────────────────────────────────────────────────────────

class TestConfig:
    """Tests for the dual-mode credential configuration."""

    def test_programmatic_mode_detected(self, monkeypatch):
        monkeypatch.setenv("LINKEDIN_EMAIL", "user@example.com")
        monkeypatch.setenv("LINKEDIN_PASSWORD", "secret")
        monkeypatch.delenv("LI_AT", raising=False)
        monkeypatch.delenv("JSESSIONID", raising=False)
        from app.config import get_settings, Settings
        s = Settings()
        assert s.use_programmatic_auth is True
        assert s.use_cookie_auth is False

    def test_cookie_mode_detected(self, monkeypatch):
        monkeypatch.delenv("LINKEDIN_EMAIL", raising=False)
        monkeypatch.delenv("LINKEDIN_PASSWORD", raising=False)
        monkeypatch.setenv("LI_AT", "some_token")
        monkeypatch.setenv("JSESSIONID", '"ajax:123"')
        from app.config import Settings
        s = Settings()
        assert s.use_programmatic_auth is False
        assert s.use_cookie_auth is True

    def test_csrf_token_strips_quotes(self, monkeypatch):
        monkeypatch.setenv("JSESSIONID", '"ajax:1234567890"')
        from app.config import Settings
        s = Settings()
        s.jsessionid = '"ajax:1234567890"'
        assert s.csrf_token == "ajax:1234567890"

    def test_validation_fails_with_no_credentials(self, monkeypatch):
        monkeypatch.delenv("LINKEDIN_EMAIL", raising=False)
        monkeypatch.delenv("LINKEDIN_PASSWORD", raising=False)
        monkeypatch.delenv("LI_AT", raising=False)
        monkeypatch.delenv("JSESSIONID", raising=False)
        from app.config import Settings
        s = Settings()
        with pytest.raises(EnvironmentError):
            s.validate()

