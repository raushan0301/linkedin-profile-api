"""
main.py — FastAPI Application Entry Point

This module wires together the HTTP API layer, request validation,
error handling, and the LinkedIn client/parser pipeline.

Design decisions:
- A single POST endpoint rather than GET: Profile URLs contain special
  characters that are cumbersome to URL-encode; a JSON body is cleaner.
- Lifespan context manager (not deprecated on_startup): recommended
  FastAPI pattern for startup/shutdown tasks.
- Typed exception handlers: each custom exception maps to a specific
  HTTP status code, giving callers meaningful error semantics.
- CORS enabled for all origins: this is a public API, so we allow any
  frontend to call it directly.
- Programmatic login at startup: if LINKEDIN_EMAIL + LINKEDIN_PASSWORD are
  set, the app authenticates itself via the reverse-engineered login flow —
  no browser is ever opened.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.auth import login as linkedin_login, LinkedInLoginError, LinkedInChallengeError
from app.linkedin_client import (
    LinkedInAuthError,
    LinkedInClient,
    LinkedInNotFoundError,
    LinkedInRateLimitError,
)
from app.models import ErrorResponse, ProfileRequest, ProfileResponse
from app.parser import parse_profile

# ── Logging ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


# ── App lifecycle ────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    On startup:
    - If LINKEDIN_EMAIL + LINKEDIN_PASSWORD are set: authenticate
      programmatically using the reverse-engineered login flow.
      No browser is opened — this is pure HTTP.
    - If LI_AT + JSESSIONID are set instead: use those directly (manual
      cookie fallback mode).
    Either way, validate that usable credentials are present before
    accepting any traffic.
    """
    settings = get_settings()

    if settings.use_programmatic_auth:
        logger.info(
            "Programmatic auth mode — logging into LinkedIn as %s (no browser)...",
            settings.linkedin_email,
        )
        try:
            session = await linkedin_login(
                settings.linkedin_email,
                settings.linkedin_password,
            )
            settings.set_session(session["li_at"], session["jsessionid"])
            logger.info("LinkedIn login successful — session established.")
        except LinkedInChallengeError as exc:
            logger.error("LinkedIn challenge required: %s", exc)
            raise RuntimeError(str(exc)) from exc
        except LinkedInLoginError as exc:
            logger.error("LinkedIn login failed: %s", exc)
            raise RuntimeError(str(exc)) from exc
    else:
        logger.info(
            "Cookie auth mode — using provided li_at token (%s...).",
            settings.li_at[:8],
        )

    yield
    logger.info("LinkedIn Profile API shutting down")


# ── Application factory ──────────────────────────────────────────────────────

app = FastAPI(
    title="LinkedIn Profile API",
    description=(
        "A reverse-engineered API that fetches structured data from LinkedIn "
        "profiles using LinkedIn's internal Voyager endpoints.\n\n"
        "**Authentication**: The API uses server-side LinkedIn session cookies "
        "(configured as environment variables). Callers do not need their own "
        "LinkedIn credentials.\n\n"
        "**Rate limits**: LinkedIn imposes request limits on the underlying "
        "account. High-volume use may trigger temporary restrictions."
    ),
    version="1.0.0",
    contact={"name": "Raushan Raj"},
    lifespan=lifespan,
)

# Allow any frontend to call this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ── Exception handlers ───────────────────────────────────────────────────────
# Mapping our domain exceptions to HTTP status codes here (rather than in
# the route) keeps the route handler clean and centralises error policy.

@app.exception_handler(LinkedInAuthError)
async def auth_error_handler(request: Request, exc: LinkedInAuthError):
    return JSONResponse(
        status_code=401,
        content=ErrorResponse(
            error="authentication_failed",
            detail=str(exc),
            status_code=401,
        ).model_dump(),
    )


@app.exception_handler(LinkedInNotFoundError)
async def not_found_handler(request: Request, exc: LinkedInNotFoundError):
    return JSONResponse(
        status_code=404,
        content=ErrorResponse(
            error="profile_not_found",
            detail=str(exc),
            status_code=404,
        ).model_dump(),
    )


@app.exception_handler(LinkedInRateLimitError)
async def rate_limit_handler(request: Request, exc: LinkedInRateLimitError):
    return JSONResponse(
        status_code=429,
        content=ErrorResponse(
            error="rate_limited",
            detail=str(exc),
            status_code=429,
        ).model_dump(),
    )


# ── Routes ───────────────────────────────────────────────────────────────────

@app.get("/health", tags=["Meta"])
async def health_check():
    """
    Liveness probe endpoint.
    Returns 200 if the server is running and credentials are configured.
    Also reports which authentication mode is active.
    """
    settings = get_settings()
    return {
        "status": "ok",
        "auth_mode": "programmatic" if settings.use_programmatic_auth else "cookie",
        "session_active": bool(settings.li_at),
    }


@app.post(
    "/api/profile",
    response_model=ProfileResponse,
    tags=["Profile"],
    summary="Fetch LinkedIn Profile",
    responses={
        200: {"description": "Profile data returned successfully"},
        400: {"model": ErrorResponse, "description": "Invalid LinkedIn URL"},
        401: {"model": ErrorResponse, "description": "LinkedIn credentials expired"},
        404: {"model": ErrorResponse, "description": "Profile not found or private"},
        429: {"model": ErrorResponse, "description": "Rate limited by LinkedIn"},
    },
)
async def get_profile(request: ProfileRequest) -> ProfileResponse:
    """
    Fetch structured data from a LinkedIn profile.

    Accepts a full LinkedIn profile URL and returns all publicly visible
    profile information as structured JSON, including experience, education,
    skills, certifications, and languages.

    **Example request body:**
    ```json
    { "url": "https://www.linkedin.com/in/williamhgates" }
    ```
    """
    # Extract username from URL (validated by Pydantic model)
    try:
        username = request.extract_username()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    logger.info("Fetching profile for username: %s", username)

    settings = get_settings()
    async with LinkedInClient(settings) as client:
        # Fetch raw data — typed exceptions propagate to handlers above
        raw_data = await client.get_full_profile(username)

    # Parse raw Voyager JSON into our clean response model
    profile = parse_profile(raw_data, username)
    logger.info("Successfully returned profile for @%s", username)
    return profile
