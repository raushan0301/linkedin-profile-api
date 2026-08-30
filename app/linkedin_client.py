"""
linkedin_client.py — LinkedIn Voyager API Client

This module is the heart of the reverse-engineering work.

## How LinkedIn's Internal API Works

LinkedIn's web frontend communicates with its own backend through an internal
service called "Voyager". By inspecting browser network traffic while logged in,
we can identify:

1. The exact endpoints being called
2. The required authentication headers
3. The structure of request and response payloads

## Authentication Mechanism

LinkedIn uses two layers of authentication:

1. **Session token** (`li_at` cookie): A long-lived JWT that identifies the
   logged-in user. This is the primary credential.

2. **CSRF protection** (`JSESSIONID` cookie + `csrf-token` header): LinkedIn
   mirrors the JSESSIONID cookie value in a `csrf-token` request header. The
   server verifies these match — this is a double-submit cookie pattern, a
   standard CSRF mitigation technique. The header value is the cookie value
   with surrounding quotes stripped.

## Endpoints Used

| Data                | Endpoint                                                        |
|---------------------|-----------------------------------------------------------------|
| Full profile        | /voyager/api/identity/profiles/{username}/profileView           |
| Skills              | /voyager/api/identity/profiles/{username}/skills?count=100      |
| Certifications      | /voyager/api/identity/profiles/{username}/certifications        |
| Languages           | /voyager/api/identity/profiles/{username}/languages             |

## Rate Limiting Strategy

LinkedIn employs sophisticated bot detection. We mitigate this by:
- Sending realistic User-Agent headers
- Adding a configurable delay between consecutive requests (default 0.5s)
- Using exponential backoff on 429/999 responses
- Reusing a single httpx.AsyncClient (keeps connection pool alive, looks
  more like a real browser session)
"""

import asyncio
import logging
from typing import Any, Dict, Optional

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

# LinkedIn's base URL. All Voyager endpoints are under /voyager/api/
BASE_URL = "https://www.linkedin.com"

# Voyager expects these headers on every request.
# x-restli-protocol-version tells LinkedIn which response format to use.
# x-li-lang sets the display language for returned strings.
VOYAGER_HEADERS = {
    "x-restli-protocol-version": "2.0.0",
    "x-li-lang": "en_US",
    "x-li-track": (
        '{"clientVersion":"1.13.1799","mpVersion":"1.13.1799",'
        '"osName":"web","timezoneOffset":5.5,"timezone":"Asia/Calcutta",'
        '"deviceFormFactor":"DESKTOP","mpName":"voyager-web",'
        '"displayDensity":1,"displayWidth":1920,"displayHeight":1080}'
    ),
    "accept": "application/json",
}


class LinkedInAuthError(Exception):
    """Raised when LinkedIn returns a 401/403 — credentials are stale."""


class LinkedInNotFoundError(Exception):
    """Raised when the requested profile does not exist or is private."""


class LinkedInRateLimitError(Exception):
    """Raised when LinkedIn throttles our requests."""


class LinkedInClient:
    """
    Async HTTP client for LinkedIn's internal Voyager API.

    Usage:
        async with LinkedInClient(settings) as client:
            data = await client.get_profile("williamhgates")
    """

    def __init__(self, settings: Settings):
        self._settings = settings
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "LinkedInClient":
        self._client = httpx.AsyncClient(
            base_url=BASE_URL,
            headers=self._build_base_headers(),
            cookies=self._build_cookies(),
            follow_redirects=True,
            timeout=httpx.Timeout(30.0),  # 30s total timeout
        )
        return self

    async def __aexit__(self, *_) -> None:
        if self._client:
            await self._client.aclose()

    # ── Header / cookie construction ────────────────────────────────────────

    def _build_cookies(self) -> Dict[str, str]:
        """
        Build the cookie jar LinkedIn expects.
        Both li_at and JSESSIONID must be present for authentication to work.
        """
        return {
            "li_at": self._settings.li_at,
            "JSESSIONID": self._settings.jsessionid,
        }

    def _build_base_headers(self) -> Dict[str, str]:
        """
        Combine Voyager-specific headers with auth headers.
        The User-Agent should ideally match the browser that generated the
        li_at cookie — LinkedIn can compare these fingerprints.
        """
        return {
            **VOYAGER_HEADERS,
            "user-agent": self._settings.user_agent,
            "csrf-token": self._settings.csrf_token,
        }

    # ── Core request logic ──────────────────────────────────────────────────

    async def _get(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        retries: int = 2,
    ) -> Dict[str, Any]:
        """
        Make a GET request to a Voyager endpoint with retry/backoff logic.

        LinkedIn occasionally returns 999 (a custom anti-bot status) or 429
        on transient throttling. We retry with exponential backoff before
        giving up. Persistent failures raise typed exceptions that the API
        layer can translate into proper HTTP error codes.
        """
        assert self._client is not None, "Client not initialised — use async with"

        for attempt in range(retries + 1):
            try:
                response = await self._client.get(path, params=params)
            except httpx.RequestError as exc:
                raise ConnectionError(f"Network error reaching LinkedIn: {exc}") from exc

            status = response.status_code
            logger.debug("GET %s → %d", path, status)

            if status == 200:
                return response.json()

            if status in (401, 403):
                raise LinkedInAuthError(
                    "LinkedIn rejected the credentials. "
                    "The li_at or JSESSIONID cookies may have expired. "
                    "Please refresh them from your browser."
                )

            if status == 404:
                raise LinkedInNotFoundError(
                    "Profile not found. It may be private, deleted, or "
                    "the username is incorrect."
                )

            if status in (429, 999):
                # 999 is LinkedIn's non-standard "too many requests" code
                if attempt < retries:
                    wait = 2 ** attempt  # 1s, 2s, ...
                    logger.warning(
                        "Rate limited (HTTP %d). Retrying in %ds (attempt %d/%d)",
                        status, wait, attempt + 1, retries,
                    )
                    await asyncio.sleep(wait)
                    continue
                raise LinkedInRateLimitError(
                    "LinkedIn is rate-limiting requests from this account. "
                    "Wait a few minutes before retrying."
                )

            # Unexpected status: log and raise generically
            raise RuntimeError(
                f"Unexpected LinkedIn response: HTTP {status}\n{response.text[:500]}"
            )

        raise RuntimeError("Exceeded retry attempts")  # should not be reached

    # ── Public data-fetching methods ────────────────────────────────────────

    async def get_profile_view(self, username: str) -> Dict[str, Any]:
        """
        Fetch the primary profile payload.

        The /profileView endpoint returns a rich composite object containing:
        - Basic info (name, headline, summary, location)
        - positionView (work experience)
        - educationView (education history)
        - volunteerExperienceView
        - patentView, publicationView, courseView
        - miniProfile (avatar images, background images)

        This is the single most useful endpoint — it contains the majority
        of what's visible on a LinkedIn profile page.
        """
        await asyncio.sleep(self._settings.request_delay)
        return await self._get(
            f"/voyager/api/identity/profiles/{username}/profileView"
        )

    async def get_skills(self, username: str) -> Dict[str, Any]:
        """
        Fetch the skills section separately.

        Skills don't come back in profileView (they're on a separate tab
        on the LinkedIn UI), so we need a dedicated call.
        count=100 fetches up to 100 skills — the default is much lower.
        """
        await asyncio.sleep(self._settings.request_delay)
        return await self._get(
            f"/voyager/api/identity/profiles/{username}/skills",
            params={"count": 100},
        )

    async def get_certifications(self, username: str) -> Dict[str, Any]:
        """Fetch the certifications & licenses section."""
        await asyncio.sleep(self._settings.request_delay)
        return await self._get(
            f"/voyager/api/identity/profiles/{username}/certifications"
        )

    async def get_languages(self, username: str) -> Dict[str, Any]:
        """Fetch the languages section."""
        await asyncio.sleep(self._settings.request_delay)
        return await self._get(
            f"/voyager/api/identity/profiles/{username}/languages"
        )

    async def get_full_profile(self, username: str) -> Dict[str, Any]:
        """
        Orchestrate all required API calls and return a combined raw dict.

        We gather the secondary calls (skills, certifications, languages)
        concurrently — they are independent and the parallel fetching halves
        the wall-clock time compared to sequential calls. The profileView
        call runs first because its result is used to detect 404/auth errors
        early, before we fire the secondary calls.
        """
        # Primary call — fail fast if profile doesn't exist or auth is broken
        profile_view = await self.get_profile_view(username)

        # Secondary calls — run concurrently
        skills_data, certs_data, langs_data = await asyncio.gather(
            self.get_skills(username),
            self.get_certifications(username),
            self.get_languages(username),
            return_exceptions=True,  # Don't let one failure kill the rest
        )

        return {
            "profileView": profile_view,
            "skills": skills_data if not isinstance(skills_data, Exception) else {},
            "certifications": certs_data if not isinstance(certs_data, Exception) else {},
            "languages": langs_data if not isinstance(langs_data, Exception) else {},
        }
