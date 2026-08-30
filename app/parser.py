"""
parser.py — LinkedIn Voyager JSON → Clean Python Models

The LinkedIn Voyager API returns deeply nested, denormalized JSON.
This module is responsible for translating that raw payload into the
clean, typed models defined in models.py.

## Why a Separate Parser Module?

Keeping parsing logic isolated from both the HTTP client and the API
layer means:
1. We can unit-test parsing logic with fixture data (no HTTP calls needed)
2. If LinkedIn changes its response structure, we only touch this file
3. The client stays focused on transport concerns, models on schema

## Key Parsing Challenges

- **Nested structures**: Profile data is buried 3-4 levels deep
- **Optional everything**: Missing keys are the norm, not the exception
- **Date handling**: Dates are split into {year, month} sub-objects
- **Image URLs**: Profile pictures need to be assembled from root + artifact path
- **Type discriminators**: The `included` array mixes entity types;
  we filter by `$type` fields to extract what we need
"""

import logging
from typing import Any, Dict, List, Optional

from app.models import (
    Certification,
    DateRange,
    Education,
    Experience,
    Language,
    ProfileImage,
    ProfileResponse,
    Skill,
)

logger = logging.getLogger(__name__)

MONTH_MAP = {
    1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr",
    5: "May", 6: "Jun", 7: "Jul", 8: "Aug",
    9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec",
}


# ── Utility helpers ─────────────────────────────────────────────────────────

def _safe_get(d: Any, *keys, default=None) -> Any:
    """
    Safely traverse a nested dict/list structure.
    Returns `default` if any key is missing or the value is None.

    Examples:
        _safe_get(data, "profile", "firstName")
        _safe_get(data, "timePeriod", "startDate", "year")
    """
    try:
        for key in keys:
            if d is None:
                return default
            if isinstance(d, dict):
                d = d.get(key)
            elif isinstance(d, list) and isinstance(key, int):
                d = d[key]
            else:
                return default
        return d if d is not None else default
    except (KeyError, IndexError, TypeError):
        return default


def _format_date(date_obj: Optional[Dict]) -> Optional[str]:
    """
    Convert LinkedIn's {year: int, month: int} date object to a readable string.

    LinkedIn months are 1-indexed. Month is optional (year-only dates exist).
    """
    if not date_obj:
        return None
    year = date_obj.get("year")
    month = date_obj.get("month")
    if not year:
        return None
    if month:
        return f"{MONTH_MAP.get(month, str(month))} {year}"
    return str(year)


def _parse_date_range(time_period: Optional[Dict]) -> Optional[DateRange]:
    """Parse a LinkedIn timePeriod object into a DateRange model."""
    if not time_period:
        return None
    start = _format_date(time_period.get("startDate"))
    end_raw = time_period.get("endDate")
    end = _format_date(end_raw) if end_raw else "Present"
    return DateRange(start=start, end=end)


def _extract_best_image_url(picture_obj: Optional[Dict]) -> Optional[ProfileImage]:
    """
    LinkedIn profile pictures are stored as arrays of artifact candidates
    at different resolutions. We pick the largest one.

    Structure (simplified):
    {
      "rootUrl": "https://media.licdn.com/dms/image/...",
      "artifacts": [
        { "fileIdentifyingUrlPathSegment": "100_100/...", "width": 100, "height": 100 },
        { "fileIdentifyingUrlPathSegment": "200_200/...", "width": 200, "height": 200 },
        ...
      ]
    }
    """
    if not picture_obj:
        return None

    root_url = picture_obj.get("rootUrl", "")
    artifacts = picture_obj.get("artifacts", [])

    if not artifacts or not root_url:
        return None

    # Sort by area (width * height) descending, pick the largest
    sorted_artifacts = sorted(
        artifacts,
        key=lambda a: (a.get("width", 0) or 0) * (a.get("height", 0) or 0),
        reverse=True,
    )
    best = sorted_artifacts[0]
    path_segment = best.get("fileIdentifyingUrlPathSegment", "")

    return ProfileImage(
        url=f"{root_url}{path_segment}",
        width=best.get("width"),
        height=best.get("height"),
    )


# ── Section parsers ─────────────────────────────────────────────────────────

def _parse_experience(position_view: Optional[Dict]) -> List[Experience]:
    """
    Parse the positionView section from profileView.

    Each element represents one job. Company info is nested under
    'company' → 'miniCompany'. The universalName field gives us the
    company's LinkedIn slug for building its URL.
    """
    if not position_view:
        return []

    results = []
    for elem in _safe_get(position_view, "elements", default=[]):
        company = elem.get("company") or {}
        mini_company = company.get("miniCompany") or {}
        company_slug = mini_company.get("universalName")

        results.append(
            Experience(
                title=elem.get("title"),
                company=mini_company.get("name") or company.get("name"),
                company_linkedin_url=(
                    f"https://www.linkedin.com/company/{company_slug}"
                    if company_slug else None
                ),
                location=elem.get("locationName"),
                duration=_parse_date_range(elem.get("timePeriod")),
                description=elem.get("description"),
                employment_type=elem.get("employmentType"),
            )
        )
    return results


def _parse_education(education_view: Optional[Dict]) -> List[Education]:
    """
    Parse the educationView section from profileView.

    School info is under 'school' → 'schoolName' and 'miniSchool'.
    Field of study, degree, and date range are top-level on the element.
    """
    if not education_view:
        return []

    results = []
    for elem in _safe_get(education_view, "elements", default=[]):
        school = elem.get("school") or {}
        mini_school = school.get("miniSchool") or {}
        school_slug = mini_school.get("universalName")

        results.append(
            Education(
                school=elem.get("schoolName") or mini_school.get("name"),
                school_linkedin_url=(
                    f"https://www.linkedin.com/school/{school_slug}"
                    if school_slug else None
                ),
                degree=elem.get("degreeName"),
                field_of_study=elem.get("fieldOfStudy"),
                duration=_parse_date_range(elem.get("timePeriod")),
                description=elem.get("description"),
                grade=elem.get("grade"),
            )
        )
    return results


def _parse_skills(skills_data: Dict) -> List[Skill]:
    """
    Parse the skills endpoint response.

    Skills come back as a flat list of { name, endorsementCount } objects.
    We sort by endorsement count descending so the most-validated skills
    appear first — this mirrors the ordering on the actual LinkedIn page.
    """
    elements = _safe_get(skills_data, "elements", default=[])
    skills = []
    for elem in elements:
        name = elem.get("name")
        if name:
            skills.append(
                Skill(
                    name=name,
                    endorsement_count=elem.get("endorsementCount"),
                )
            )

    return sorted(
        skills,
        key=lambda s: s.endorsement_count or 0,
        reverse=True,
    )


def _parse_certifications(certs_data: Dict) -> List[Certification]:
    """
    Parse the certifications endpoint response.

    Dates come as {year, month} objects (same pattern as experience/education).
    Credential URLs are optional — not all issuers provide them.
    """
    elements = _safe_get(certs_data, "elements", default=[])
    results = []
    for elem in elements:
        company = elem.get("company") or {}
        mini_company = company.get("miniCompany") or {}

        results.append(
            Certification(
                name=elem.get("name"),
                issuing_organization=mini_company.get("name") or company.get("name"),
                issued_date=_format_date(elem.get("startedOn")),
                expiry_date=_format_date(elem.get("endedOn")),
                credential_id=elem.get("licenseNumber"),
                credential_url=elem.get("url"),
            )
        )
    return results


def _parse_languages(langs_data: Dict) -> List[Language]:
    """
    Parse the languages endpoint response.

    LinkedIn stores proficiency as an enum string, e.g.:
    ELEMENTARY_PROFICIENCY, LIMITED_WORKING, PROFESSIONAL_WORKING,
    FULL_PROFESSIONAL, NATIVE_OR_BILINGUAL.

    We expose the raw enum value so callers can apply their own
    formatting, but could easily humanize it here if needed.
    """
    elements = _safe_get(langs_data, "elements", default=[])
    return [
        Language(
            name=elem.get("name"),
            proficiency=elem.get("proficiency"),
        )
        for elem in elements
    ]


# ── Main entry point ────────────────────────────────────────────────────────

def parse_profile(raw: Dict, username: str) -> ProfileResponse:
    """
    Transform the raw combined API response into a clean ProfileResponse.

    This is the top-level parsing function called by the API layer.
    All individual section parsers are called from here.
    """
    profile_view = raw.get("profileView", {})
    profile = profile_view.get("profile") or {}
    mini_profile = profile.get("miniProfile") or {}

    # ── Name ──────────────────────────────────────────────────────────────
    first_name = profile.get("firstName") or mini_profile.get("firstName")
    last_name = profile.get("lastName") or mini_profile.get("lastName")
    full_name = " ".join(filter(None, [first_name, last_name])) or None

    # ── Location ──────────────────────────────────────────────────────────
    # LinkedIn splits location across several fields; we prefer the
    # full locationName string, falling back to geoLocationName.
    location = (
        profile.get("locationName")
        or profile.get("geoLocationName")
        or profile.get("geoCountryName")
    )

    # ── Connection / follower counts ───────────────────────────────────────
    network_info = profile_view.get("networkInfo") or {}
    connections_value = network_info.get("connections") or {}
    # LinkedIn returns the count as {"value": 500} where 500 means "500+"
    raw_count = connections_value.get("value") if isinstance(connections_value, dict) else None
    connection_count = f"{raw_count}+" if raw_count else None

    # ── Profile image ──────────────────────────────────────────────────────
    picture = (
        mini_profile.get("picture")
        or _safe_get(profile, "miniProfile", "picture")
    )
    profile_image = _extract_best_image_url(picture)

    # ── Background image ───────────────────────────────────────────────────
    background_picture = mini_profile.get("backgroundImage")
    background_image = _extract_best_image_url(background_picture)

    # ── Open to work / Premium flags ───────────────────────────────────────
    # These are in the miniProfile as boolean flags
    open_to_work = mini_profile.get("showCreatorCard") or None  # proxy signal
    premium = mini_profile.get("premium")

    logger.info("Parsed profile for @%s: %s", username, full_name)

    return ProfileResponse(
        username=username,
        profile_url=f"https://www.linkedin.com/in/{username}",
        name=full_name,
        first_name=first_name,
        last_name=last_name,
        headline=profile.get("headline") or mini_profile.get("occupation"),
        location=location,
        country=profile.get("geoCountryName"),
        about=profile.get("summary"),
        connection_count=connection_count,
        follower_count=network_info.get("followersCount"),
        profile_image=profile_image,
        background_image=background_image,
        experience=_parse_experience(profile_view.get("positionView")),
        education=_parse_education(profile_view.get("educationView")),
        skills=_parse_skills(raw.get("skills", {})),
        certifications=_parse_certifications(raw.get("certifications", {})),
        languages=_parse_languages(raw.get("languages", {})),
        open_to_work=open_to_work,
        premium=premium,
    )
