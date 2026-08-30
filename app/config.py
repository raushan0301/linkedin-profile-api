"""
config.py — Application Configuration

Two credential modes are supported:

Mode A — Email/Password (recommended, fully browser-free):
  Set LINKEDIN_EMAIL and LINKEDIN_PASSWORD.
  The app authenticates programmatically at startup and refreshes the
  session automatically when it expires. No browser ever needed.

Mode B — Raw cookies (manual fallback):
  Set LI_AT and JSESSIONID directly.
  Useful if programmatic login is blocked by 2FA or a CAPTCHA challenge.
  Cookies must be manually refreshed from a browser when they expire.

The app prefers Mode A if LINKEDIN_EMAIL is present.
"""

import os
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


class Settings:
    """
    Central settings object. Validated at startup so we fail fast
    if required credentials are missing, rather than at request time.
    """

    def __init__(self):
        # Mode A: email + password (programmatic auth — fully browser-free)
        self.linkedin_email: str = os.getenv("LINKEDIN_EMAIL", "")
        self.linkedin_password: str = os.getenv("LINKEDIN_PASSWORD", "")

        # Mode B: raw session cookies (manual fallback)
        self.li_at: str = os.getenv("LI_AT", "")
        self.jsessionid: str = os.getenv("JSESSIONID", "")

        # HTTP client settings
        self.user_agent: str = os.getenv(
            "USER_AGENT",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36",
        )
        # Seconds between LinkedIn API calls — keeps cadence human-like
        self.request_delay: float = float(os.getenv("REQUEST_DELAY", "0.5"))

    @property
    def use_programmatic_auth(self) -> bool:
        """True if we should log in programmatically (Mode A)."""
        return bool(self.linkedin_email and self.linkedin_password)

    @property
    def use_cookie_auth(self) -> bool:
        """True if raw cookies are configured (Mode B)."""
        return bool(self.li_at and self.jsessionid)

    @property
    def csrf_token(self) -> str:
        """
        LinkedIn's CSRF token is derived from the JSESSIONID cookie.
        The cookie is stored with surrounding quotes (e.g. "ajax:12345"),
        so we strip them to get the raw token value.
        """
        return self.jsessionid.strip('"')

    def set_session(self, li_at: str, jsessionid: str) -> None:
        """
        Update the in-memory session after a successful programmatic login.
        Called by the lifespan handler after auth.login() completes.
        """
        self.li_at = li_at
        self.jsessionid = jsessionid

    def validate(self) -> None:
        """Raise at startup if no usable credential combination is configured."""
        if not self.use_programmatic_auth and not self.use_cookie_auth:
            raise EnvironmentError(
                "No LinkedIn credentials configured.\n\n"
                "Option A (recommended — fully browser-free):\n"
                "  Set LINKEDIN_EMAIL and LINKEDIN_PASSWORD\n\n"
                "Option B (manual cookie fallback):\n"
                "  Set LI_AT and JSESSIONID\n\n"
                "See .env.example for details."
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached singleton Settings instance."""
    settings = Settings()
    settings.validate()
    return settings
