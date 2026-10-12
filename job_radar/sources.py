from __future__ import annotations

import html
import json
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any

from .models import Job


USER_AGENT = "SoftwareJobRadar/0.1 (+local fresh-job monitor)"

try:
    import certifi

    SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    SSL_CONTEXT = ssl.create_default_context()


class SourceError(RuntimeError):
    pass


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def text(self) -> str:
        return re.sub(r"\s+", " ", html.unescape(" ".join(self.parts))).strip()


def strip_html(value: Any) -> str:
    if value is None:
        return ""
    parser = TextExtractor()
    parser.feed(str(value))
    return parser.text()


def parse_date(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 10_000_000_000:
            number /= 1000
        return datetime.fromtimestamp(number, tz=timezone.utc)
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except ValueError:
        pass
    for pattern in ("%B %d, %Y", "%b %d, %Y", "%b %d %Y"):
        try:
            return datetime.strptime(text, pattern).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    relative = re.search(r"(?:posted\s+)?(\d+)\+?\s+days?\s+ago", text.lower())
    if relative:
        return datetime.now(timezone.utc) - timedelta(days=int(relative.group(1)))
    if text.lower() in {"today", "posted today"}:
        return datetime.now(timezone.utc)
    return None


def get_json(url: str) -> Any:
    for attempt in range(4):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=30, context=SSL_CONTEXT) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < 3:
                retry_after = exc.headers.get("Retry-After", "")
                try:
                    delay = max(1.0, min(float(retry_after), 15.0))
                except ValueError:
                    delay = 2.0 * (attempt + 1)
                time.sleep(delay)
                continue
            raise SourceError(f"{url}: {exc}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise SourceError(f"{url}: {exc}") from exc


def post_json(url: str, payload: dict[str, Any]) -> Any:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"User-Agent": USER_AGENT, "Accept": "application/json", "Content-Type": "application/json"},
    )
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=30, context=SSL_CONTEXT) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < 3:
                retry_after = exc.headers.get("Retry-After", "")
                try:
                    delay = max(1.0, min(float(retry_after), 15.0))
                except ValueError:
                    delay = 2.0 * (attempt + 1)
                time.sleep(delay)
                continue
            raise SourceError(f"{url}: {exc}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise SourceError(f"{url}: {exc}") from exc


def get_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html"})
    try:
        with urllib.request.urlopen(request, timeout=30, context=SSL_CONTEXT) as response:
            return response.read().decode("utf-8", "ignore")
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
        raise SourceError(f"{url}: {exc}") from exc


def greenhouse(company: str, board: str) -> list[Job]:
    payload = get_json(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true")
    jobs: list[Job] = []
    for item in payload.get("jobs", []):
        location = (item.get("location") or {}).get("name", "")
        jobs.append(
            Job(
                company=company,
                source_type="greenhouse",
                source_id=str(item.get("id", item.get("absolute_url", ""))),
                title=item.get("title", "").strip(),
                location=location,
                url=item.get("absolute_url", ""),
                apply_url=item.get("absolute_url", ""),
                description=strip_html(item.get("content", "")),
                published_at=parse_date(item.get("first_published")),
                updated_at=parse_date(item.get("updated_at")),
                department=", ".join((d.get("name", "") for d in item.get("departments", []))),
            )
        )
    return jobs


def lever(company: str, board: str) -> list[Job]:
    payload = get_json(f"https://api.lever.co/v0/postings/{board}?mode=json")
    jobs: list[Job] = []
    for item in payload if isinstance(payload, list) else []:
        categories = item.get("categories") or {}
        location = categories.get("location", "")
        if not location and categories.get("allLocations"):
            location = ", ".join(categories["allLocations"])
        jobs.append(
            Job(
                company=company,
                source_type="lever",
                source_id=str(item.get("id", item.get("hostedUrl", ""))),
                title=item.get("text", "").strip(),
                location=location,
                url=item.get("hostedUrl", ""),
                apply_url=item.get("applyUrl", item.get("hostedUrl", "")),
                description=strip_html(item.get("descriptionPlain") or item.get("description", "")),
                published_at=parse_date(item.get("createdAt")),
                updated_at=parse_date(item.get("updatedAt")),
                workplace_type=str(categories.get("commitment", "")),
                department=str(categories.get("team", "")),
            )
        )
    return jobs


def ashby(company: str, board: str) -> list[Job]:
    payload = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{board}")
    jobs: list[Job] = []
    for item in payload.get("jobs", []) if isinstance(payload, dict) else []:
        source_id = str(item.get("id") or "").strip()
        raw_job_url = str(item.get("jobUrl") or "").strip()
        if source_id:
            # Ashby's API also returns jobUrl/applyUrl, but the UUID is the
            # stable identity and lets us rebuild a canonical public URL if a
            # board returns a stale or malformed URL field.
            job_url = f"https://jobs.ashbyhq.com/{urllib.parse.quote(board.strip('/'), safe='')}/{urllib.parse.quote(source_id, safe='')}"
            apply_url = f"{job_url}/application"
        else:
            job_url = raw_job_url or str(item.get("applyUrl") or "").strip()
            apply_url = str(item.get("applyUrl") or "").strip() or f"{job_url}/application"
        location = item.get("location", "")
        if isinstance(location, list):
            location = ", ".join(str(value) for value in location)
        description = strip_html(item.get("descriptionHtml") or item.get("description", ""))
        jobs.append(
            Job(
                company=company,
                source_type="ashby",
                source_id=source_id or job_url,
                title=item.get("title", "").strip(),
                location=str(location),
                url=job_url,
                apply_url=apply_url,
                description=description,
                published_at=parse_date(item.get("publishedAt")),
                updated_at=parse_date(item.get("updatedAt")),
                workplace_type=str(item.get("workplaceType", "")),
                department=str(item.get("department", "")),
            )
        )
    return jobs


def smartrecruiters(company: str, identifier: str) -> list[Job]:
    jobs: list[Job] = []
    offset = 0
    while True:
        payload = get_json(f"https://api.smartrecruiters.com/v1/companies/{identifier}/postings?limit=100&offset={offset}")
        content = payload.get("content", []) if isinstance(payload, dict) else []
        if not content:
            break
        for item in content:
            title = str(item.get("name", "")).strip()
            # Fetch full descriptions only for likely technical roles. This
            # keeps a large company board within a reasonable request budget.
            detail = item
            if re.search(r"engineer|developer|software|backend|frontend|full.?stack|machine learning|\bml\b|\bai\b|data|platform|devops|infrastructure|computer vision|nlp|llm", title, re.I):
                try:
                    detail = get_json(item.get("ref", ""))
                except SourceError:
                    detail = item
            location = item.get("location") or {}
            location_text = location.get("fullLocation", "") if isinstance(location, dict) else str(location)
            sections = ((detail.get("jobAd") or {}).get("sections") or {}) if isinstance(detail, dict) else {}
            description = " ".join(str(section.get("text", "")) for section in sections.values() if isinstance(section, dict))
            jobs.append(
                Job(
                    company=company,
                    source_type="smartrecruiters",
                    source_id=str(item.get("id", item.get("ref", ""))),
                    title=title,
                    location=location_text,
                    url=item.get("postingUrl", item.get("ref", "")),
                    apply_url=item.get("applyUrl", item.get("postingUrl", item.get("ref", ""))),
                    description=strip_html(description),
                    published_at=parse_date(item.get("releasedDate")),
                    updated_at=parse_date(item.get("releasedDate")),
                    workplace_type=("Remote" if isinstance(location, dict) and location.get("remote") else ""),
                    department=str((item.get("function") or {}).get("label", "")) if isinstance(item.get("function"), dict) else "",
                )
            )
        if len(content) < 100:
            break
        offset += len(content)
    return jobs


def amazon(company: str, queries: list[str]) -> list[Job]:
    jobs: list[Job] = []
    seen: set[str] = set()
    for query in queries:
        url = "https://www.amazon.jobs/en/search.json?" + urllib.parse.urlencode(
            {"base_query": query, "country": "USA", "sort": "recent", "offset": 0, "result_limit": 100}
        )
        payload = get_json(url)
        for item in payload.get("jobs", []) if isinstance(payload, dict) else []:
            source_id = str(item.get("id_icims") or item.get("id") or item.get("job_path"))
            if source_id in seen:
                continue
            seen.add(source_id)
            location = item.get("normalized_location") or item.get("location", "")
            description = " ".join(
                str(item.get(key, "")) for key in ("basic_qualifications", "description", "preferred_qualifications")
            )
            job_path = item.get("job_path", "")
            jobs.append(
                Job(
                    company=company,
                    source_type="amazon",
                    source_id=source_id,
                    title=str(item.get("title", "")).strip(),
                    location=str(location),
                    url="https://www.amazon.jobs" + job_path,
                    apply_url="https://www.amazon.jobs" + job_path,
                    description=strip_html(description),
                    published_at=parse_date(item.get("posted_date")),
                    updated_at=parse_date(item.get("posted_date")),
                    department=str(item.get("job_category", "")),
                )
            )
    return jobs


def apple(company: str, queries: list[str], pages: int = 3) -> list[Job]:
    jobs: list[Job] = []
    seen: set[str] = set()
    for query in queries:
        for page in range(pages):
            payload = post_json(
                "https://jobs.apple.com/api/v1/search",
                {
                    "query": query,
                    "filters": {},
                    "page": page,
                    "locale": "en-us",
                    "sort": "newest",
                    "format": {"longDate": "MMMM D, YYYY", "mediumDate": "MMM D, YYYY"},
                },
            )
            results = (payload.get("res") or {}).get("searchResults", [])
            if not results:
                break
            for item in results:
                source_id = str(item.get("positionId") or item.get("reqId") or "")
                if not source_id or source_id in seen:
                    continue
                seen.add(source_id)
                detail = item
                if re.search(r"engineer|developer|software|backend|frontend|machine learning|\bml\b|\bai\b|data|platform|devops|infrastructure|computer vision|nlp|llm", str(item.get("postingTitle", "")), re.I):
                    try:
                        detail = (get_json(f"https://jobs.apple.com/api/v1/jobDetails/{source_id}") or {}).get("res", item)
                    except SourceError:
                        detail = item
                locations = item.get("locations") or []
                location_text = "; ".join(str(location.get("name", "")) for location in locations if isinstance(location, dict))
                title = str(item.get("postingTitle", detail.get("postingTitle", ""))).strip()
                job_number = detail.get("jobNumber") or item.get("positionId", "")
                slug = detail.get("transformedPostingTitle", "")
                url = f"https://jobs.apple.com/en-us/details/{job_number}/{slug}".rstrip("/")
                description = " ".join(str(detail.get(key, "")) for key in ("jobSummary", "description", "responsibilities", "requirements"))
                jobs.append(
                    Job(
                        company=company,
                        source_type="apple",
                        source_id=source_id,
                        title=title,
                        location=location_text,
                        url=url,
                        apply_url=url,
                        description=strip_html(description),
                        published_at=parse_date(item.get("postDateInGMT") or item.get("postingDate")),
                        updated_at=parse_date(item.get("postDateInGMT") or item.get("postingDate")),
                        department=str((item.get("team") or {}).get("teamName", "")) if isinstance(item.get("team"), dict) else "",
                    )
                )
    return jobs


def google(company: str, queries: list[str], pages: int = 2) -> list[Job]:
    jobs: list[Job] = []
    seen: set[str] = set()
    for query in queries:
        for page in range(1, pages + 1):
            params = {"location": "United States", "q": query, "sort_by": "date"}
            if page > 1:
                params["page"] = str(page)
            url = "https://www.google.com/about/careers/applications/jobs/results/?" + urllib.parse.urlencode(params)
            body = get_text(url)
            headings = list(re.finditer(r'<h3 class="QJPWVe">(.*?)</h3>', body, re.I | re.S))
            for index, heading in enumerate(headings):
                end = headings[index + 1].start() if index + 1 < len(headings) else len(body)
                segment = body[heading.start():end]
                title = strip_html(heading.group(1))
                info_end = segment.find('class="Xsxa1e"')
                info = segment if info_end < 0 else segment[:info_end]
                locations = [strip_html(value) for value in re.findall(r'class="r0wTof[^\"]*">(.*?)</span>', info, re.I | re.S)]
                links = re.findall(r'href="([^"]*jobs/results/[^" ]+)"', segment, re.I)
                if not title or not links:
                    continue
                job_url = links[0].replace("&amp;", "&")
                if job_url.startswith("/"):
                    job_url = "https://www.google.com" + job_url
                elif not job_url.startswith("http"):
                    job_url = "https://www.google.com/about/careers/applications/" + job_url
                source_id = job_url.split("/jobs/results/")[-1].split("?")[0]
                if source_id in seen:
                    continue
                seen.add(source_id)
                jobs.append(
                    Job(
                        company=company,
                        source_type="google",
                        source_id=source_id,
                        title=title,
                        location="; ".join(locations),
                        url=job_url,
                        apply_url=job_url,
                        description=strip_html(segment),
                    )
                )
    return jobs


def microsoft(company: str, queries: list[str], pages: int = 3) -> list[Job]:
    jobs: list[Job] = []
    seen: set[str] = set()
    detail_cutoff = datetime.now(timezone.utc) - timedelta(hours=96)
    for query in queries:
        for start in range(0, pages * 10, 10):
            # Eightfold throttles this endpoint fairly aggressively. A small
            # pause also keeps a 20-minute monitor from looking like a burst.
            if jobs:
                time.sleep(0.4)
            params = urllib.parse.urlencode(
                {"domain": "microsoft.com", "query": query, "location": "United States", "start": start, "sort_by": "date"}
            )
            payload = get_json(f"https://apply.careers.microsoft.com/api/pcsx/search?{params}")
            data = payload.get("data") or {}
            positions = data.get("positions") or []
            if not positions:
                break
            for item in positions:
                source_id = str(item.get("id") or item.get("atsJobId") or "")
                if not source_id or source_id in seen:
                    continue
                seen.add(source_id)
                posted = parse_date(item.get("postedTs"))
                detail = item
                # Search results carry a reliable timestamp. Avoid hundreds
                # of detail requests for stale listings; those are discarded
                # by the freshness filter anyway.
                if posted is None or posted >= detail_cutoff:
                    try:
                        detail = get_json(
                            "https://apply.careers.microsoft.com/api/pcsx/position_details?" + urllib.parse.urlencode(
                                {"position_id": source_id, "domain": "microsoft.com", "hl": "en"}
                            )
                        ).get("data", item)
                    except SourceError:
                        pass
                location = "; ".join(str(value) for value in item.get("locations", []))
                path = detail.get("publicUrl") or detail.get("positionUrl") or item.get("positionUrl", "")
                if path.startswith("/"):
                    path = "https://apply.careers.microsoft.com" + path
                jobs.append(
                    Job(
                        company=company,
                        source_type="microsoft",
                        source_id=source_id,
                        title=str(detail.get("name", item.get("name", ""))).strip(),
                        location=location,
                        url=path,
                        apply_url=path,
                        description=strip_html(str(detail.get("jobDescription", ""))),
                        published_at=posted,
                        updated_at=posted,
                        department=str(detail.get("department", item.get("department", ""))),
                    )
                )
    return jobs


def eightfold(company: str, host: str, domain: str, queries: list[str], pages: int = 2) -> list[Job]:
    """Read an Eightfold-powered company board through its public search API."""
    jobs: list[Job] = []
    seen: set[str] = set()
    detail_cutoff = datetime.now(timezone.utc) - timedelta(hours=96)
    for query in queries:
        for start in range(0, pages * 10, 10):
            if jobs:
                time.sleep(0.4)
            params = urllib.parse.urlencode(
                {
                    "domain": domain,
                    "query": query,
                    "location": "United States",
                    "start": start,
                    "sort_by": "date",
                }
            )
            payload = get_json(f"https://{host}/api/pcsx/search?{params}")
            positions = ((payload.get("data") or {}).get("positions") or []) if isinstance(payload, dict) else []
            if not positions:
                break
            for item in positions:
                source_id = str(item.get("id") or item.get("atsJobId") or "")
                if not source_id or source_id in seen:
                    continue
                seen.add(source_id)
                posted = parse_date(item.get("postedTs"))
                detail = item
                if posted is None or posted >= detail_cutoff:
                    try:
                        detail = (
                            get_json(
                                f"https://{host}/api/pcsx/position_details?"
                                + urllib.parse.urlencode(
                                    {"position_id": source_id, "domain": domain, "hl": "en"}
                                )
                            ).get("data", item)
                        )
                    except SourceError:
                        pass
                locations = item.get("locations") or detail.get("locations") or []
                location = "; ".join(str(value) for value in locations)
                path = detail.get("publicUrl") or detail.get("positionUrl") or item.get("positionUrl", "")
                if path.startswith("/"):
                    path = f"https://{host}{path}"
                jobs.append(
                    Job(
                        company=company,
                        source_type="eightfold",
                        source_id=source_id,
                        title=str(detail.get("name", item.get("name", ""))).strip(),
                        location=location,
                        url=path,
                        apply_url=path,
                        description=strip_html(str(detail.get("jobDescription", ""))),
                        published_at=posted,
                        updated_at=posted,
                        workplace_type=str(item.get("workLocationOption", "")),
                        department=str(detail.get("department", item.get("department", ""))),
                    )
                )
    return jobs


def amd(company: str, queries: list[str], pages: int = 2) -> list[Job]:
    jobs: list[Job] = []
    seen: set[str] = set()
    for query in queries:
        for page in range(1, pages + 1):
            params = urllib.parse.urlencode(
                {"keywords": query, "location": "United States", "page": page, "limit": 20}
            )
            payload = get_json(f"https://careers.amd.com/api/jobs?{params}")
            items = payload.get("jobs", []) if isinstance(payload, dict) else []
            if not items:
                break
            for wrapper in items:
                item = wrapper.get("data", wrapper) if isinstance(wrapper, dict) else {}
                source_id = str(item.get("req_id") or item.get("slug") or "")
                if not source_id or source_id in seen:
                    continue
                seen.add(source_id)
                slug = str(item.get("slug") or source_id)
                apply_url = str(item.get("apply_url") or f"https://careers.amd.com/careers-home/jobs/{slug}")
                categories = item.get("categories") or []
                if isinstance(categories, list):
                    department = ", ".join(
                        str(value.get("name", value)) if isinstance(value, dict) else str(value)
                        for value in categories
                    )
                else:
                    department = str(categories)
                description = " ".join(
                    str(item.get(key, ""))
                    for key in ("description", "responsibilities", "qualifications")
                )
                jobs.append(
                    Job(
                        company=company,
                        source_type="amd",
                        source_id=source_id,
                        title=str(item.get("title", "")).strip(),
                        location=str(item.get("location_name") or ""),
                        url=apply_url,
                        apply_url=apply_url,
                        description=strip_html(description),
                        published_at=parse_date(item.get("posted_date")),
                        updated_at=parse_date(item.get("posted_date")),
                        department=department,
                    )
                )
    return jobs


def workday_job_url(host: str, site: str, external_path: str) -> str:
    """Build a public Workday job URL from the API's external path."""
    site_path = site.strip("/")
    job_path = "/" + external_path.strip("/")
    if job_path == f"/{site_path}" or job_path.startswith(f"/{site_path}/"):
        return f"https://{host}{job_path}"
    return f"https://{host}/{site_path}{job_path}"


def workday(company: str, tenant: str, host: str, site: str, queries: list[str], pages: int = 5) -> list[Job]:
    jobs: list[Job] = []
    seen: set[str] = set()
    detail_cutoff = datetime.now(timezone.utc) - timedelta(hours=96)
    base = f"https://{host}/wday/cxs/{tenant}/{site}"
    for query in queries:
        for page in range(pages):
            payload = post_json(
                f"{base}/jobs",
                # Several Workday tenants return HTTP 400 for limit=100 even
                # though their UI uses smaller pages.
                {"appliedFacets": {}, "limit": 20, "offset": page * 20, "searchText": query},
            )
            postings = payload.get("jobPostings", []) if isinstance(payload, dict) else []
            if not postings:
                break
            for item in postings:
                source_id = str(item.get("externalPath", ""))
                if not source_id or source_id in seen:
                    continue
                seen.add(source_id)
                title = str(item.get("title", "")).strip()
                posted = parse_date(item.get("postedOn"))
                detail = item
                if (
                    re.search(r"engineer|developer|software|backend|frontend|full.?stack|machine learning|\bml\b|\bai\b|data|platform|devops|infrastructure|computer vision|nlp|llm", title, re.I)
                    and (posted is None or posted >= detail_cutoff)
                ):
                    try:
                        detail = get_json(base + source_id).get("jobPostingInfo", item)
                    except SourceError:
                        detail = item
                location = str(item.get("locationsText", ""))
                url = workday_job_url(host, site, source_id)
                jobs.append(
                    Job(
                        company=company,
                        source_type="workday",
                        source_id=source_id,
                        title=title,
                        location=location,
                        url=url,
                        apply_url=url,
                        description=strip_html(str(detail.get("jobDescription", ""))),
                        published_at=posted,
                        updated_at=posted,
                    )
                )
    return jobs


def fetch_source(entry: dict[str, str]) -> list[Job]:
    source_type = entry["type"].lower()
    if source_type == "greenhouse":
        return greenhouse(entry["company"], entry["board"])
    if source_type == "lever":
        return lever(entry["company"], entry["board"])
    if source_type == "ashby":
        return ashby(entry["company"], entry["board"])
    if source_type == "smartrecruiters":
        return smartrecruiters(entry["company"], entry["board"])
    if source_type == "amazon":
        return amazon(entry["company"], entry.get("queries", ["software engineer", "machine learning", "platform engineer"]))
    if source_type == "apple":
        return apple(entry["company"], entry.get("queries", ["software", "machine learning", "backend"]), int(entry.get("pages", 3)))
    if source_type == "google":
        return google(entry["company"], entry.get("queries", ["software engineer", "machine learning", "backend"]), int(entry.get("pages", 2)))
    if source_type == "microsoft":
        return microsoft(entry["company"], entry.get("queries", ["software engineer", "machine learning", "platform engineer"]), int(entry.get("pages", 3)))
    if source_type == "eightfold":
        return eightfold(entry["company"], entry["host"], entry["domain"], entry.get("queries", ["software engineer", "machine learning", "platform engineer"]), int(entry.get("pages", 2)))
    if source_type == "amd":
        return amd(entry["company"], entry.get("queries", ["software engineer", "machine learning", "platform engineer"]), int(entry.get("pages", 2)))
    if source_type == "workday":
        return workday(entry["company"], entry["tenant"], entry["host"], entry["site"], entry.get("queries", ["software engineer", "machine learning", "platform engineer"]), int(entry.get("pages", 5)))
    raise SourceError(f"Unsupported source type: {source_type}")
