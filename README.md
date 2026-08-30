# LinkedIn Profile API

A reverse-engineered REST API that fetches structured profile data from LinkedIn using LinkedIn's internal Voyager API — **no browser, no Selenium, no Playwright, no third-party scraping services**.

Both the authentication and the data fetching are pure HTTP: the server logs into LinkedIn programmatically and queries profile data directly, all within the same Python process.

## Live API

> **Base URL**: `https://<your-railway-url>.railway.app`  
> **Swagger docs**: `https://<your-railway-url>.railway.app/docs`

---

## Quick Start

```bash
# Clone
git clone https://github.com/your-username/linkedin-profile-api
cd linkedin-profile-api

# Install dependencies
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Configure credentials (see "Authentication" below)
cp .env.example .env
# Edit .env

# Run locally
uvicorn app.main:app --reload

# Test
pytest tests/ -v
```

---

## Authentication

Two modes are supported. **Mode A is recommended** — it is fully browser-free.

### Mode A — Email + Password (recommended)

Set `LINKEDIN_EMAIL` and `LINKEDIN_PASSWORD` in your environment.

The server authenticates with LinkedIn programmatically at startup using the **reverse-engineered login flow**:
1. `GET /login` → extract the `loginCsrfParam` anti-CSRF token from the HTML form
2. `POST /checkpoint/lg/login-submit` → submit credentials as a form
3. Extract `li_at` and `JSESSIONID` session cookies from the response

No browser is opened at any point. The session is held in memory and the server is ready to serve requests.

```env
LINKEDIN_EMAIL=your.email@example.com
LINKEDIN_PASSWORD=your_linkedin_password
```

### Mode B — Raw session cookies (manual fallback)

If Mode A is blocked by 2FA or a CAPTCHA challenge, you can supply cookies directly:

```env
LI_AT=your_li_at_cookie_value
JSESSIONID="ajax:your_jsessionid_value"
```

To get these: Chrome → linkedin.com → DevTools → Application → Cookies.  
Note: cookies expire after a few weeks and require manual refresh.


## API Reference

### `POST /api/profile`

Fetch structured data from a LinkedIn profile.

**Request**
```json
{
  "url": "https://www.linkedin.com/in/williamhgates"
}
```

**Response (200 OK)**
```json
{
  "username": "williamhgates",
  "profile_url": "https://www.linkedin.com/in/williamhgates",
  "name": "Bill Gates",
  "first_name": "Bill",
  "last_name": "Gates",
  "headline": "Co-chair, Bill & Melinda Gates Foundation",
  "location": "Seattle, Washington, United States",
  "country": "United States",
  "about": "Co-founder of Microsoft...",
  "connection_count": "500+",
  "follower_count": 35000000,
  "profile_image": {
    "url": "https://media.licdn.com/dms/image/.../400_400/photo.jpg",
    "width": 400,
    "height": 400
  },
  "background_image": null,
  "experience": [
    {
      "title": "Co-chair",
      "company": "Bill & Melinda Gates Foundation",
      "company_linkedin_url": "https://www.linkedin.com/company/bill-melinda-gates-foundation",
      "location": "Seattle, WA",
      "duration": { "start": "Jan 2000", "end": "Present" },
      "description": "Working on global health initiatives.",
      "employment_type": null
    }
  ],
  "education": [
    {
      "school": "Harvard University",
      "school_linkedin_url": "https://www.linkedin.com/school/harvard-university",
      "degree": "Bachelor of Science",
      "field_of_study": "Computer Science",
      "duration": { "start": "1973", "end": "1975" },
      "description": null,
      "grade": null
    }
  ],
  "skills": [
    { "name": "Software Engineering", "endorsement_count": 99 }
  ],
  "certifications": [],
  "languages": [
    { "name": "English", "proficiency": "NATIVE_OR_BILINGUAL" }
  ],
  "open_to_work": null,
  "premium": true
}
```

**Error Responses**

| Status | Error key | Meaning |
|--------|-----------|---------|
| `400` | `validation_error` | URL is not a valid LinkedIn profile URL |
| `401` | `authentication_failed` | LinkedIn cookies have expired |
| `404` | `profile_not_found` | Profile is private, deleted, or username is wrong |
| `429` | `rate_limited` | LinkedIn is throttling requests from this account |

### `GET /health`

Liveness probe. Returns `200 OK` if the server is running and credentials are present.

```json
{ "status": "ok", "credentials_configured": true }
```

---

## How It Works

### The Approach: Reverse Engineering LinkedIn's Voyager API

LinkedIn's public developer API is heavily restricted — it's primarily designed for enterprise OAuth integrations, not profile data access. However, LinkedIn's own web frontend communicates with its backend through an internal service called **"Voyager"**, discoverable via browser DevTools.

By inspecting the network requests made by the LinkedIn web app, I identified:

1. **The endpoints** serving each profile section
2. **The authentication headers** required for each request
3. **The response structure** for parsing

### Authentication

LinkedIn uses a two-layer session authentication:

| Layer | Implementation |
|-------|---------------|
| **Identity** | `li_at` cookie — a long-lived JWT that identifies the logged-in user |
| **CSRF protection** | Double-submit cookie pattern: `JSESSIONID` cookie value must match the `csrf-token` request header |

The CSRF token is derived by stripping surrounding quotes from the `JSESSIONID` cookie value:
```
JSESSIONID cookie: "ajax:1234567890"
csrf-token header:  ajax:1234567890
```

### Endpoints Used

| Data | Endpoint |
|------|----------|
| Full profile (name, headline, location, about, experience, education) | `GET /voyager/api/identity/profiles/{username}/profileView` |
| Skills | `GET /voyager/api/identity/profiles/{username}/skills?count=100` |
| Certifications | `GET /voyager/api/identity/profiles/{username}/certifications` |
| Languages | `GET /voyager/api/identity/profiles/{username}/languages` |

### Request Orchestration

The `profileView` endpoint is called first (it's the richest source of data and also the failure point for private/nonexistent profiles). The three secondary endpoints (skills, certifications, languages) are then fetched **concurrently** with `asyncio.gather`, cutting total fetch time roughly in half.

### Rate Limit Strategy

LinkedIn uses sophisticated bot detection. We mitigate with:
- A configurable inter-request delay (default 0.5s)
- Exponential backoff on `429` and LinkedIn's non-standard `999` responses
- Realistic `User-Agent` headers matching the session browser
- Reuse of a single `httpx.AsyncClient` across the request lifecycle

---

## Project Structure

```
.
├── app/
│   ├── config.py           # Environment variable loading & validation
│   ├── linkedin_client.py  # HTTP client for Voyager API endpoints
│   ├── models.py           # Pydantic request/response models
│   ├── parser.py           # Transform raw Voyager JSON → clean models
│   └── main.py             # FastAPI app, routes, exception handlers
├── tests/
│   └── test_api.py         # Unit + integration tests (mock LinkedIn)
├── .env.example            # Credential template
├── Dockerfile              # Container image
├── railway.toml            # Railway deployment config
└── requirements.txt
```

**Key design decisions:**

- **Separate `parser.py`**: Parsing logic is isolated from the HTTP client. This means parser unit tests don't need network access, and if LinkedIn's response schema changes, only one file needs updating.
- **Typed exceptions**: `LinkedInAuthError`, `LinkedInNotFoundError`, `LinkedInRateLimitError` propagate cleanly from the client to FastAPI exception handlers, mapping to proper HTTP status codes.
- **All fields `Optional`**: LinkedIn profiles vary enormously. A missing field returns `null` rather than crashing.
- **Credentials validated at startup**: The app fails immediately if `LI_AT` or `JSESSIONID` are absent — no silent failures on first real request.

---

## Getting LinkedIn Credentials

1. Log into [linkedin.com](https://linkedin.com) in Chrome or Firefox
2. Open DevTools → **Application** tab → **Cookies** → `www.linkedin.com`
3. Copy the values for:
   - `li_at`
   - `JSESSIONID` (include the surrounding quotes, e.g. `"ajax:1234..."`)
4. Paste into your `.env` file or Railway environment variables

**⚠️ Keep these private.** They authenticate as you on LinkedIn. Never commit them to a repository.

Session cookies expire periodically (typically every few weeks). When the API returns `401`, refresh the cookies from your browser.

---

## Deployment (Railway)

1. Push the repository to GitHub
2. Create a new project at [railway.app](https://railway.app) → **Deploy from GitHub repo**
3. Set environment variables in the Railway dashboard:
   - `LI_AT` = your `li_at` cookie value
   - `JSESSIONID` = your JSESSIONID cookie value (with quotes)
4. Railway detects the `Dockerfile` and builds automatically
5. Your API is live at `https://<project>.railway.app`

---

## Running Tests

```bash
pytest tests/ -v
```

Tests use fixture data and mock the LinkedIn client — no real LinkedIn account needed to run the test suite.

```
tests/test_api.py::TestProfileRequestParsing::test_standard_url PASSED
tests/test_api.py::TestProfileRequestParsing::test_trailing_slash PASSED
tests/test_api.py::TestProfileRequestParsing::test_url_with_query_params PASSED
tests/test_api.py::TestUtilityFunctions::test_safe_get_nested PASSED
...
tests/test_api.py::TestParser::test_parses_skills_sorted_by_endorsements PASSED
tests/test_api.py::TestAPIEndpoints::test_profile_endpoint_success PASSED
```

---

## Known Limitations

| Limitation | Detail |
|------------|--------|
| **Private profiles** | If the target user's profile is set to private or not visible to your LinkedIn account, the API returns `404` |
| **Cookie expiry** | `li_at` sessions expire (typically 1–4 weeks). Requires manual refresh from the browser |
| **Rate limiting** | LinkedIn monitors request frequency. High-volume use from a single account risks temporary restriction. Use `REQUEST_DELAY` env var to throttle |
| **Schema changes** | LinkedIn can change internal API response structures without notice. Parser may need updates if field names shift |
| **No login automation** | Re-authentication must be done manually (grab cookies from browser). Automated login would require browser automation, which is excluded by the challenge requirements |
| **LinkedIn ToS** | Accessing internal APIs violates LinkedIn's Terms of Service. Use responsibly and only with your own account's credentials |

---

## License

MIT
