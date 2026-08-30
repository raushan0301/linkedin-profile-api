"""
auth.py — Reverse-Engineered LinkedIn Authentication

This module implements LinkedIn's login flow using pure HTTP requests —
no browser, no Selenium, no Playwright.

## How LinkedIn's Login Works (Reverse Engineered)

By inspecting the network traffic during a normal browser login, the flow is:

Step 1 — GET /login
  LinkedIn's login page contains a hidden form field `loginCsrfParam`.
  This is a one-time anti-CSRF token that must be submitted with credentials.
  The server also sets initial session cookies at this point.

Step 2 — POST /checkpoint/lg/login-submit
  Submit credentials as a standard HTML form (application/x-www-form-urlencoded).
  On success, LinkedIn sets the `li_at` session cookie and refreshes `JSESSIONID`.

Step 3 — (Sometimes) Challenge checkpoint
  If LinkedIn detects an unusual login (new IP, unusual pattern), it redirects
  to a verification step. We detect this and raise a clear error rather than
  silently returning bad credentials.

## Security Note
Credentials are stored only as environment variables and never logged or
included in API responses. The resulting session tokens are held in memory.
"""

import logging
import re
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# LinkedIn login endpoints (reverse engineered from browser network traffic)
_LOGIN_PAGE_URL = "https://www.linkedin.com/login"
_LOGIN_SUBMIT_URL = "https://www.linkedin.com/checkpoint/lg/login-submit"

# Headers that make the request look like a real browser login form submission
_LOGIN_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
}


class LinkedInLoginError(Exception):
    """Raised when LinkedIn rejects the login credentials."""


class LinkedInChallengeError(Exception):
    """
    Raised when LinkedIn requires additional verification (2FA, CAPTCHA, etc.).
    This happens when it detects an unusual login pattern (new IP, high frequency).
    """


async def login(email: str, password: str) -> dict:
    """
    Authenticate with LinkedIn using email and password.
    Returns a dict with 'li_at' and 'jsessionid' session tokens.

    This function reverse-engineers LinkedIn's standard login form flow:
    no browser is opened at any point.

    Args:
        email: LinkedIn account email
        password: LinkedIn account password

    Returns:
        {"li_at": "...", "jsessionid": "ajax:..."}

    Raises:
        LinkedInLoginError: Credentials rejected by LinkedIn
        LinkedInChallengeError: LinkedIn requires CAPTCHA/2FA verification
        ConnectionError: Network-level failure reaching LinkedIn
    """
    # A single client with follow_redirects=True handles the redirect
    # chain that LinkedIn does after successful login automatically.
    # Cookie storage is automatic — the client's cookie jar accumulates
    # all Set-Cookie headers across the redirect chain.
    async with httpx.AsyncClient(
        follow_redirects=True,
        headers=_LOGIN_HEADERS,
        timeout=httpx.Timeout(30.0),
    ) as client:

        # ── Step 1: Fetch the login page ───────────────────────────────────
        logger.info("Fetching LinkedIn login page to extract CSRF token...")
        try:
            login_page = await client.get(_LOGIN_PAGE_URL)
        except httpx.RequestError as exc:
            raise ConnectionError(f"Could not reach LinkedIn login page: {exc}") from exc

        if login_page.status_code != 200:
            raise ConnectionError(
                f"LinkedIn login page returned unexpected status: {login_page.status_code}"
            )

        # Extract the anti-CSRF token from the hidden form field.
        # The HTML contains: <input name="loginCsrfParam" value="<token>" ...>
        csrf_match = re.search(
            r'name="loginCsrfParam"\s+value="([^"]+)"',
            login_page.text,
        )
        if not csrf_match:
            # Fallback: try alternate attribute ordering in the HTML
            csrf_match = re.search(
                r'loginCsrfParam["\s]+value["\s=]+([a-zA-Z0-9_\-]+)',
                login_page.text,
            )

        if not csrf_match:
            raise LinkedInLoginError(
                "Could not extract CSRF token from LinkedIn login page. "
                "LinkedIn may have changed its login page structure."
            )

        csrf_token = csrf_match.group(1)
        logger.debug("Extracted CSRF token: %s...", csrf_token[:8])

        # ── Step 2: Submit credentials ─────────────────────────────────────
        logger.info("Submitting credentials to LinkedIn...")

        # The form data mirrors exactly what a browser sends on login.
        # Fields identified by inspecting the login form's HTML + network tab.
        form_data = {
            "session_key": email,
            "session_password": password,
            "loginCsrfParam": csrf_token,
            "isJsEnabled": "false",
            "trk": "guest_homepage-basic_nav-header-signin",
        }

        try:
            submit_response = await client.post(
                _LOGIN_SUBMIT_URL,
                data=form_data,
                headers={
                    **_LOGIN_HEADERS,
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Referer": _LOGIN_PAGE_URL,
                    "Origin": "https://www.linkedin.com",
                },
            )
        except httpx.RequestError as exc:
            raise ConnectionError(f"Network error during login submission: {exc}") from exc

        # ── Step 3: Validate the session ───────────────────────────────────
        final_url = str(submit_response.url)
        logger.debug("Post-login redirect destination: %s", final_url)

        # Detect challenge page — LinkedIn redirects here when it suspects
        # automated access or requires 2FA/email verification
        if "checkpoint" in final_url or "challenge" in final_url:
            raise LinkedInChallengeError(
                "LinkedIn requires additional verification for this login. "
                "This typically happens with a new IP address or if 2FA is enabled. "
                "To resolve: log in manually once from this server's IP, "
                "or disable 2FA, then retry. "
                f"Challenge URL: {final_url}"
            )

        # Extract session cookies from the client's accumulated cookie jar
        li_at = _get_cookie(client, "li_at")
        jsessionid = _get_cookie(client, "JSESSIONID")

        if not li_at:
            raise LinkedInLoginError(
                "Login appeared to succeed but no session token was returned. "
                "Credentials may be incorrect, or LinkedIn blocked the login. "
                "Try logging in via browser first to check if the account is locked."
            )

        logger.info("LinkedIn authentication successful.")
        return {
            "li_at": li_at,
            "jsessionid": jsessionid or "",
        }


def _get_cookie(client: httpx.AsyncClient, name: str) -> Optional[str]:
    """Extract a named cookie value from the httpx client's cookie jar."""
    cookie = client.cookies.get(name)
    return cookie if cookie else None
