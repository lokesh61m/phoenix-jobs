from __future__ import annotations

import html
import json
import math
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
    if text.lower() in {"yesterday", "posted yesterday"}:
        return datetime.now(timezone.utc) - timedelta(days=1)
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


def graphql_json(url: str, query: str, variables: dict[str, Any]) -> dict[str, Any]:
    payload = post_json(url, {"query": query, "variables": variables})
    if not isinstance(payload, dict):
        raise SourceError(f"{url}: GraphQL response was not an object")
    if payload.get("errors"):
        raise SourceError(f"{url}: GraphQL errors: {payload['errors']}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise SourceError(f"{url}: GraphQL response did not contain data")
    return data


def get_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html"})
    try:
        with urllib.request.urlopen(request, timeout=30, context=SSL_CONTEXT) as response:
            return response.read().decode("utf-8", "ignore")
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
        raise SourceError(f"{url}: {exc}") from exc


def search_page_count(payload: Any, configured_pages: int, maximum_pages: int = 60, page_size: int = 10) -> int:
    """Use a search API's total count while retaining a safe minimum/cap."""
    data = payload.get("data") if isinstance(payload, dict) else None
    try:
        total = int((data or {}).get("count") or 0)
    except (TypeError, ValueError):
        total = 0
    discovered = math.ceil(total / page_size) if total else 0
    return min(maximum_pages, max(1, configured_pages, discovered))


def get_page_json(url: str, source_label: str, page: int, first_page: int = 0) -> Any | None:
    """Fetch a paginated JSON page without discarding earlier pages on a burst error."""
    for attempt in range(3):
        try:
            return get_json(url)
        except SourceError as exc:
            if attempt == 2:
                if page == first_page:
                    raise
                print(f"[source warning] {source_label}: stopping at page {page} after {exc}")
                return None
            time.sleep(2.0 * (attempt + 1))
    return None


def post_page_json(url: str, payload: dict[str, Any], source_label: str, page: int, first_page: int = 0) -> Any | None:
    """POST a paginated JSON page with the same partial-result protection."""
    for attempt in range(3):
        try:
            return post_json(url, payload)
        except SourceError as exc:
            if attempt == 2:
                if page == first_page:
                    raise
                print(f"[source warning] {source_label}: stopping at page {page} after {exc}")
                return None
            time.sleep(2.0 * (attempt + 1))
    return None


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
        page = 0
        total_pages = 1
        while page < total_pages:
            if jobs:
                time.sleep(0.25)
            url = "https://www.amazon.jobs/en/search.json?" + urllib.parse.urlencode(
                {
                    "base_query": query,
                    "country": "USA",
                    "sort": "recent",
                    "offset": page * 100,
                    "result_limit": 100,
                }
            )
            payload = get_page_json(url, company, page)
            if payload is None:
                break
            if page == 0 and isinstance(payload, dict):
                try:
                    total_pages = min(20, max(1, math.ceil(int(payload.get("hits") or 0) / 100)))
                except (TypeError, ValueError):
                    total_pages = 1
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
            if not (payload.get("jobs") if isinstance(payload, dict) else None):
                break
            page += 1
    return jobs


def apple(company: str, queries: list[str], pages: int = 3) -> list[Job]:
    jobs: list[Job] = []
    seen: set[str] = set()
    detail_cutoff = datetime.now(timezone.utc) - timedelta(hours=96)
    for query in queries:
        page = 0
        total_pages = max(1, pages)
        while page < total_pages:
            payload = post_page_json(
                "https://jobs.apple.com/api/v1/search",
                {
                    "query": query,
                    "filters": {},
                    "page": page,
                    "locale": "en-us",
                    "sort": "newest",
                    "format": {"longDate": "MMMM D, YYYY", "mediumDate": "MMM D, YYYY"},
                },
                company,
                page,
            )
            if payload is None:
                break
            if page == 0:
                try:
                    total_records = int((payload.get("res") or {}).get("totalRecords") or 0)
                    total_pages = min(60, max(1, pages, math.ceil(total_records / 20)))
                except (TypeError, ValueError):
                    total_pages = max(1, pages)
            results = (payload.get("res") or {}).get("searchResults", [])
            if not results:
                break
            posting_dates: list[datetime | None] = []
            for item in results:
                source_id = str(item.get("positionId") or item.get("reqId") or "")
                if not source_id or source_id in seen:
                    continue
                seen.add(source_id)
                posted = parse_date(item.get("postDateInGMT") or item.get("postingDate"))
                posting_dates.append(posted)
                detail = item
                if (
                    re.search(r"engineer|developer|software|backend|frontend|machine learning|\bml\b|\bai\b|data|platform|devops|infrastructure|computer vision|nlp|llm", str(item.get("postingTitle", "")), re.I)
                    and (posted is None or posted >= detail_cutoff)
                ):
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
                        published_at=posted,
                        updated_at=posted,
                        department=str((item.get("team") or {}).get("teamName", "")) if isinstance(item.get("team"), dict) else "",
                    )
                )
            page += 1
            if page > 1 and posting_dates and all(date is not None and date < detail_cutoff for date in posting_dates):
                break
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
            try:
                body = get_text(url)
            except SourceError as exc:
                if page == 1:
                    raise
                print(f"[source warning] {company}: stopping Google page {page} after {exc}")
                break
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
        page = 0
        total_pages = max(1, pages)
        while page < total_pages:
            # Eightfold throttles this endpoint fairly aggressively. A small
            # pause also keeps a frequent monitor from looking like a burst.
            if jobs:
                time.sleep(1.0)
            start = page * 10
            params = urllib.parse.urlencode(
                {"domain": "microsoft.com", "query": query, "location": "United States", "start": start, "sort_by": "date"}
            )
            payload = get_page_json(
                f"https://apply.careers.microsoft.com/api/pcsx/search?{params}", company, page
            )
            if payload is None:
                break
            if page == 0:
                total_pages = search_page_count(payload, pages)
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
            page += 1
    return jobs


def eightfold(company: str, host: str, domain: str, queries: list[str], pages: int = 2) -> list[Job]:
    """Read an Eightfold-powered company board through its public search API."""
    jobs: list[Job] = []
    seen: set[str] = set()
    detail_cutoff = datetime.now(timezone.utc) - timedelta(hours=96)
    for query in queries:
        page = 0
        total_pages = max(1, pages)
        while page < total_pages:
            if jobs:
                time.sleep(1.0)
            start = page * 10
            params = urllib.parse.urlencode(
                {
                    "domain": domain,
                    "query": query,
                    "location": "United States",
                    "start": start,
                    "sort_by": "date",
                }
            )
            payload = get_page_json(f"https://{host}/api/pcsx/search?{params}", company, page)
            if payload is None:
                break
            if page == 0:
                total_pages = search_page_count(payload, pages)
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
            page += 1
    return jobs


def amd(company: str, queries: list[str], pages: int = 2) -> list[Job]:
    jobs: list[Job] = []
    seen: set[str] = set()
    for query in queries:
        page = 1
        total_pages = max(1, pages)
        while page <= total_pages:
            if jobs:
                time.sleep(0.25)
            params = urllib.parse.urlencode(
                {"keywords": query, "location": "United States", "page": page, "limit": 20}
            )
            payload = get_page_json(f"https://careers.amd.com/api/jobs?{params}", company, page, first_page=1)
            if payload is None:
                break
            if page == 1 and isinstance(payload, dict):
                try:
                    total_pages = min(30, max(1, math.ceil(int(payload.get("totalCount") or payload.get("count") or 0) / 20)))
                except (TypeError, ValueError):
                    total_pages = max(1, pages)
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
            page += 1
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
        page = 0
        total_pages = max(1, pages)
        while page < total_pages:
            if jobs:
                time.sleep(0.2)
            payload = post_page_json(
                f"{base}/jobs",
                # Several Workday tenants return HTTP 400 for limit=100 even
                # though their UI uses smaller pages.
                {"appliedFacets": {}, "limit": 20, "offset": page * 20, "searchText": query},
                company,
                page,
            )
            if payload is None:
                break
            if page == 0 and isinstance(payload, dict):
                try:
                    total_pages = min(60, max(1, math.ceil(int(payload.get("total") or 0) / 20)))
                except (TypeError, ValueError):
                    total_pages = max(1, pages)
            postings = payload.get("jobPostings", []) if isinstance(payload, dict) else []
            if not postings:
                break
            posting_dates: list[datetime | None] = []
            for item in postings:
                source_id = str(item.get("externalPath", ""))
                if not source_id or source_id in seen:
                    continue
                seen.add(source_id)
                title = str(item.get("title", "")).strip()
                posted = parse_date(item.get("postedOn"))
                posting_dates.append(posted)
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
            page += 1
            # Workday returns these result sets newest-first. Once an entire
            # page is older than the detail lookback, later pages cannot
            # produce a fresh alert. Keep going when dates are unavailable.
            if page > 1 and posting_dates and all(date is not None and date < detail_cutoff for date in posting_dates):
                break
    return jobs


GOLDMAN_GRAPHQL_URL = "https://api-higher.gs.com/gateway/api/v1/graphql"
GOLDMAN_SEARCH_QUERY = """
query GetRoles($searchQueryInput: RoleSearchQueryInput!) {
  roleSearch(searchQueryInput: $searchQueryInput) {
    totalCount
    items {
      roleId
      corporateTitle
      jobTitle
      jobFunction
      locations { primary state country city }
      status
      division
      skills
      jobType { code description }
      externalSource { sourceId }
    }
  }
}
"""
GOLDMAN_DETAIL_QUERY = """
query GetRole($id: String!, $fetch: Boolean) {
  role(externalSourceId: $id, externalSourceFetch: $fetch) {
    roleId
    corporateTitle
    jobTitle
    jobFunction
    locations { primary state country city }
    status
    division
    shortDescription
    descriptionHtml
    skills
    lastPostedDate
    createdDate
    lastModifiedDate
    jobType { code description }
    externalJobStatus
    applyActive
    recruitingType
    educationLevel
  }
}
"""
TECHNICAL_SOURCE_TERMS = re.compile(
    r"software|engineer|engineering|developer|backend|platform|infrastructure|"
    r"machine learning|artificial intelligence|\bai\b|\bml\b|data|quantitative",
    re.I,
)


def _location_text(locations: Any) -> str:
    if not isinstance(locations, list):
        return str(locations or "")
    values: list[str] = []
    for location in locations:
        if not isinstance(location, dict):
            continue
        text = ", ".join(
            str(location.get(key, "")).strip()
            for key in ("city", "state", "country")
            if str(location.get(key, "")).strip()
        )
        if text and text not in values:
            values.append(text)
    return "; ".join(values)


def goldman(company: str, queries: list[str], pages: int = 2) -> list[Job]:
    """Read Goldman Sachs roles from the public Higher GS GraphQL API."""
    jobs: list[Job] = []
    seen: set[str] = set()
    detail_cutoff = datetime.now(timezone.utc) - timedelta(hours=96)
    page_size = 100
    for query in queries:
        page = 0
        total_pages = max(1, pages)
        while page < total_pages:
            payload = post_page_json(
                GOLDMAN_GRAPHQL_URL,
                {
                    "query": GOLDMAN_SEARCH_QUERY,
                    "variables": {
                        "searchQueryInput": {
                            "page": {"pageSize": page_size, "pageNumber": page},
                            "sort": {"sortStrategy": "POSTED_DATE", "sortOrder": "DESC"},
                            "filters": [],
                            "experiences": ["EARLY_CAREER", "PROFESSIONAL"],
                            "searchTerm": query,
                        }
                    },
                },
                company,
                page,
            )
            if payload is None:
                break
            result = ((payload.get("data") or {}).get("roleSearch") or {}) if isinstance(payload, dict) else {}
            if page == 0:
                try:
                    total_pages = min(20, max(1, pages, math.ceil(int(result.get("totalCount") or 0) / page_size)))
                except (TypeError, ValueError):
                    total_pages = max(1, pages)
            items = result.get("items") or []
            if not items:
                break
            for item in items:
                if not isinstance(item, dict):
                    continue
                source = item.get("externalSource") or {}
                source_id = str(source.get("sourceId") or item.get("roleId") or "").strip()
                if not source_id or source_id in seen:
                    continue
                title = str(item.get("jobTitle") or "").strip()
                function = str(item.get("jobFunction") or "")
                if not TECHNICAL_SOURCE_TERMS.search(f"{title} {function}"):
                    continue
                seen.add(source_id)
                detail: dict[str, Any] = item
                try:
                    detail = (
                        graphql_json(
                            GOLDMAN_GRAPHQL_URL,
                            GOLDMAN_DETAIL_QUERY,
                            {"id": source_id, "fetch": True},
                        ).get("role")
                        or item
                    )
                except SourceError:
                    # Search results still contain enough metadata to retain
                    # the posting when a single detail request is throttled.
                    detail = item
                posted = parse_date(detail.get("lastPostedDate") or detail.get("createdDate"))
                if posted is not None and posted < detail_cutoff and detail is item:
                    # Do not perform any more work for clearly stale fallback
                    # records. The global freshness filter will discard them.
                    continue
                final_title = str(detail.get("jobTitle") or title).strip()
                locations = detail.get("locations") or item.get("locations") or []
                detail_url = f"https://higher.gs.com/roles/{urllib.parse.quote(source_id, safe='')}"
                description = " ".join(
                    str(detail.get(key, ""))
                    for key in ("shortDescription", "descriptionHtml")
                )
                jobs.append(
                    Job(
                        company=company,
                        source_type="goldman",
                        source_id=source_id,
                        title=final_title,
                        location=_location_text(locations),
                        url=detail_url,
                        apply_url=detail_url,
                        description=strip_html(description),
                        published_at=posted,
                        updated_at=parse_date(detail.get("lastModifiedDate") or detail.get("lastPostedDate")),
                        department=str(detail.get("jobFunction") or item.get("jobFunction") or ""),
                    )
                )
            page += 1
    return jobs


def _oracle_finder_url(
    host: str,
    site: str,
    query: str,
    limit: int,
    offset: int,
    posting_days: str = "7",
    us_location_id: str = "300000000289738",
) -> str:
    values = [
        ("siteNumber", site),
        ("keyword", query),
        ("selectedPostingDatesFacet", posting_days),
        ("selectedLocationsFacet", us_location_id),
        ("sortBy", "POSTING_DATE"),
        ("limit", str(limit)),
        ("offset", str(offset)),
    ]
    finder = ",".join(f"{key}={urllib.parse.quote(value, safe='')}" for key, value in values)
    return (
        f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
        f"?onlyData=true&expand=requisitionList.secondaryLocations&finder=findReqs;{finder}"
    )


def jpmorgan(
    company: str,
    host: str,
    site: str,
    queries: list[str],
    pages: int = 3,
    posting_days: str = "7",
    us_location_id: str = "300000000289738",
    source_type: str = "jpmorgan",
    public_host: str | None = None,
    public_prefix: str | None = None,
) -> list[Job]:
    """Read JPMorgan Chase roles from its public Oracle Recruiting Cloud API."""
    jobs: list[Job] = []
    seen: set[str] = set()
    detail_cutoff = datetime.now(timezone.utc) - timedelta(hours=96)
    page_size = 100
    detail_base = f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails"
    for query in queries:
        page = 0
        total_pages = max(1, pages)
        while page < total_pages:
            payload = get_page_json(
                _oracle_finder_url(host, site, query, page_size, page * page_size, posting_days, us_location_id),
                company,
                page,
            )
            if payload is None:
                break
            wrappers = payload.get("items", []) if isinstance(payload, dict) else []
            wrapper = wrappers[0] if wrappers and isinstance(wrappers[0], dict) else {}
            if page == 0:
                try:
                    total_pages = min(20, max(1, pages, math.ceil(int(wrapper.get("TotalJobsCount") or 0) / page_size)))
                except (TypeError, ValueError):
                    total_pages = max(1, pages)
            postings = wrapper.get("requisitionList") or []
            if not postings:
                break
            for item in postings:
                if not isinstance(item, dict):
                    continue
                source_id = str(item.get("Id") or item.get("RequisitionId") or "").strip()
                if not source_id or source_id in seen:
                    continue
                seen.add(source_id)
                title = str(item.get("Title") or "").strip()
                posted = parse_date(item.get("PostedDate"))
                detail: dict[str, Any] = item
                # The list has a date and short description. Fetch the full
                # requisition only for fresh technical titles so the frequent
                # monitor does not download thousands of stale records.
                if (
                    TECHNICAL_SOURCE_TERMS.search(f"{title} {item.get('JobFamily', '')} {item.get('JobFunction', '')}")
                    and (posted is None or posted >= detail_cutoff)
                ):
                    detail_url = f"{detail_base}?expand=all&onlyData=true&finder=ById;Id={urllib.parse.quote(source_id, safe='')}"
                    try:
                        detail_items = (get_json(detail_url) or {}).get("items", [])
                        if detail_items and isinstance(detail_items[0], dict):
                            detail = detail_items[0]
                    except SourceError:
                        detail = item
                final_title = str(detail.get("Title") or title).strip()
                locations = [str(item.get("PrimaryLocation") or "").strip()]
                locations.extend(
                    str(location.get("Name") or "").strip()
                    for location in (item.get("secondaryLocations") or [])
                    if isinstance(location, dict)
                )
                locations = [location for location in dict.fromkeys(locations) if location]
                canonical_host = public_host or host
                canonical_prefix = public_prefix or f"/hcmUI/CandidateExperience/en/sites/{site}/job"
                detail_url = f"https://{canonical_host}{canonical_prefix}/{urllib.parse.quote(source_id, safe='')}"
                description = " ".join(
                    str(detail.get(key, ""))
                    for key in ("ShortDescriptionStr", "ExternalDescriptionStr", "ExternalResponsibilitiesStr", "ExternalQualificationsStr")
                )
                precise_posted = parse_date(detail.get("ExternalPostedStartDate")) or posted
                jobs.append(
                    Job(
                        company=company,
                        source_type=source_type,
                        source_id=source_id,
                        title=final_title,
                        location="; ".join(locations),
                        url=detail_url,
                        apply_url=detail_url,
                        description=strip_html(description),
                        published_at=precise_posted,
                        updated_at=precise_posted,
                        workplace_type=str(item.get("WorkplaceType") or item.get("WorkplaceTypeCode") or ""),
                        department=str(item.get("JobFamily") or item.get("JobFunction") or ""),
                    )
                )
            page += 1
    return jobs


def oracle(
    company: str,
    host: str,
    site: str,
    queries: list[str],
    pages: int = 3,
    posting_days: str = "7",
    us_location_id: str = "300000000149325",
    public_host: str = "careers.oracle.com",
    public_prefix: str = "/en/sites/jobsearch/job",
) -> list[Job]:
    """Read Oracle roles from Oracle's public Recruiting Cloud career feed."""
    return jpmorgan(
        company,
        host,
        site,
        queries,
        pages,
        posting_days,
        us_location_id,
        source_type="oracle",
        public_host=public_host,
        public_prefix=public_prefix,
    )


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
    if source_type == "goldman":
        return goldman(entry["company"], entry.get("queries", ["software engineer", "machine learning", "platform engineer"]), int(entry.get("pages", 2)))
    if source_type == "jpmorgan":
        return jpmorgan(
            entry["company"],
            entry["host"],
            entry["site"],
            entry.get("queries", ["software engineer", "machine learning", "AI engineer"]),
            int(entry.get("pages", 3)),
            entry.get("posting_days", "7"),
            entry.get("us_location_id", "300000000289738"),
        )
    if source_type == "oracle":
        return oracle(
            entry["company"],
            entry["host"],
            entry["site"],
            entry.get("queries", ["software engineer", "machine learning", "AI engineer"]),
            int(entry.get("pages", 3)),
            entry.get("posting_days", "7"),
            entry.get("us_location_id", "300000000149325"),
            entry.get("public_host", "careers.oracle.com"),
            entry.get("public_prefix", "/en/sites/jobsearch/job"),
        )
    raise SourceError(f"Unsupported source type: {source_type}")
