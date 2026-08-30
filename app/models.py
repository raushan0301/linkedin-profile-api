"""
models.py — Pydantic data models

Defines the request and response schemas for the API.

Design decisions:
- All fields Optional: LinkedIn profiles vary wildly. A field missing from
  a profile should not cause a 500; it returns null instead.
- Nested models rather than flat dicts: forces explicit shape contracts,
  makes the API self-documenting, and generates clean OpenAPI schemas.
- No snake_case ↔ camelCase aliasing at model level — we keep Python
  conventions internally and only serialize with snake_case (standard for
  REST APIs).
"""

from typing import List, Optional
from pydantic import BaseModel, Field, HttpUrl, field_validator
import re


# ── Request ────────────────────────────────────────────────────────────────────

class ProfileRequest(BaseModel):
    url: str = Field(
        ...,
        description="Full LinkedIn profile URL",
        examples=["https://www.linkedin.com/in/williamhgates"],
    )

    @field_validator("url")
    @classmethod
    def validate_linkedin_url(cls, v: str) -> str:
        """
        Validate that the URL is a recognisable LinkedIn profile URL.
        We intentionally keep this loose (no strict regex) to handle
        edge cases like locale-prefixed URLs (/in/ vs /pub/).
        """
        v = v.strip()
        if "linkedin.com" not in v:
            raise ValueError("URL must be a LinkedIn URL (linkedin.com)")
        if "/in/" not in v and "/pub/" not in v:
            raise ValueError(
                "URL must point to a personal profile (/in/ or /pub/)"
            )
        return v

    def extract_username(self) -> str:
        """
        Pull the vanity name out of the URL.
        Handles trailing slashes, query params, and locale prefixes.

        Examples:
          https://www.linkedin.com/in/williamhgates      → williamhgates
          https://www.linkedin.com/in/williamhgates/     → williamhgates
          https://linkedin.com/in/williamhgates?trk=foo  → williamhgates
        """
        match = re.search(r"linkedin\.com/in/([^/?#]+)", self.url)
        if not match:
            raise ValueError(f"Cannot extract username from URL: {self.url}")
        return match.group(1).strip("/")


# ── Sub-models ─────────────────────────────────────────────────────────────────

class DateRange(BaseModel):
    start: Optional[str] = None   # e.g. "Jan 2020"
    end: Optional[str] = None     # e.g. "Present" or "Dec 2023"


class Experience(BaseModel):
    title: Optional[str] = None
    company: Optional[str] = None
    company_linkedin_url: Optional[str] = None
    location: Optional[str] = None
    duration: Optional[DateRange] = None
    description: Optional[str] = None
    employment_type: Optional[str] = None   # Full-time, Contract, etc.


class Education(BaseModel):
    school: Optional[str] = None
    school_linkedin_url: Optional[str] = None
    degree: Optional[str] = None
    field_of_study: Optional[str] = None
    duration: Optional[DateRange] = None
    description: Optional[str] = None
    grade: Optional[str] = None


class Skill(BaseModel):
    name: str
    endorsement_count: Optional[int] = None


class Certification(BaseModel):
    name: Optional[str] = None
    issuing_organization: Optional[str] = None
    issued_date: Optional[str] = None
    expiry_date: Optional[str] = None
    credential_id: Optional[str] = None
    credential_url: Optional[str] = None


class Language(BaseModel):
    name: Optional[str] = None
    proficiency: Optional[str] = None  # e.g. "NATIVE_OR_BILINGUAL"


class ProfileImage(BaseModel):
    """
    LinkedIn provides images at multiple resolutions.
    We expose the highest-resolution URL plus a convenience thumbnail.
    """
    url: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None


# ── Main response ──────────────────────────────────────────────────────────────

class ProfileResponse(BaseModel):
    # Identity
    username: str
    profile_url: str
    name: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None

    # Header info
    headline: Optional[str] = None
    location: Optional[str] = None
    country: Optional[str] = None

    # Summary / About
    about: Optional[str] = None

    # Counts
    connection_count: Optional[str] = None   # LinkedIn returns "500+" strings
    follower_count: Optional[int] = None

    # Media
    profile_image: Optional[ProfileImage] = None
    background_image: Optional[ProfileImage] = None

    # Detailed sections
    experience: List[Experience] = []
    education: List[Education] = []
    skills: List[Skill] = []
    certifications: List[Certification] = []
    languages: List[Language] = []

    # Metadata
    open_to_work: Optional[bool] = None
    premium: Optional[bool] = None


class ErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None
    status_code: int
