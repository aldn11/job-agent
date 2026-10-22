"""Conservative parser for Remōt job links found in alert email bodies."""
import re
from urllib.parse import urlsplit, urlunsplit

from bs4 import BeautifulSoup

import url_policy


SOURCE_NAME = "Remōt"
DOMAIN = "workremot.com"
_URL_PATTERN = re.compile(r"https://[^\s<>\"']+", re.IGNORECASE)
_TRACKING_PARAMETER = re.compile(
    r"^(?:utm_.+|ref|source|campaign|tracking(?:id)?|mc_cid|mc_eid)$",
    re.IGNORECASE,
)
_APPLICATION_LABEL = re.compile(
    r"\b(?:apply(?:\s+now)?|application|candidatar(?:-se)?|inscrever(?:-se)?)\b",
    re.IGNORECASE,
)


def canonical_job_url(url):
    """Validate and normalize a Remōt listing URL without following redirects."""
    url_policy.validate_allowed_url(url, (DOMAIN,))
    parts = url_policy.urlsplit(url)
    hostname = (parts.hostname or "").lower().rstrip(".")
    if hostname.startswith("www."):
        hostname = hostname[4:]
    path = parts.path.rstrip("/")
    if not path:
        raise ValueError("A URL não aponta para um anúncio Remōt.")
    try:
        port = parts.port
    except ValueError:
        raise ValueError("URL inválida.") from None
    netloc = hostname if port in (None, 443) else f"{hostname}:{port}"
    query_items = []
    for item in parts.query.split("&") if parts.query else ():
        name = item.partition("=")[0]
        if name and not _TRACKING_PARAMETER.match(name):
            query_items.append(item)
    query = "&".join(sorted(query_items))
    return urlunsplit(("https", netloc, path, query, ""))


def _safe_application_url(url):
    try:
        parts = url_policy.urlsplit(url)
        hostname = parts.hostname
        port = parts.port
    except (AttributeError, TypeError, ValueError):
        return None
    if (
        parts.scheme.lower() != "https"
        or not hostname
        or parts.username is not None
        or parts.password is not None
        or port not in (None, 443)
    ):
        return None
    return url


def parse_remot_jobs(body):
    """Return listing URLs and any unambiguous external application link.

    Email markup varies and is not assumed to be a specific Remōt template.
    Only explicit listing URLs on workremot.com are ingested; CTA destinations
    are retained for transparency but are never fetched.
    """
    soup = BeautifulSoup(body or "", "html.parser")
    candidates = []
    application_links = []

    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href", "").strip()
        label = anchor.get_text(" ", strip=True)
        is_application_link = bool(_APPLICATION_LABEL.search(label))
        if is_application_link:
            safe_url = _safe_application_url(href)
            if safe_url:
                application_links.append(safe_url)
            continue
        try:
            listing_url = canonical_job_url(href)
        except ValueError:
            continue
        title = label or "Vaga Remōt"
        candidates.append((listing_url, title))

    # Plain-text messages do not have anchors; accept only explicit HTTPS URLs.
    for match in _URL_PATTERN.findall(soup.get_text(" ", strip=True)):
        raw_url = match.rstrip(".,;:!?)\"]}")
        try:
            listing_url = canonical_job_url(raw_url)
        except ValueError:
            continue
        candidates.append((listing_url, "Vaga Remōt"))

    application_url = None
    if len({url for url, _ in candidates}) == 1 and len(set(application_links)) == 1:
        application_url = application_links[0]

    jobs = {}
    for url, title in candidates:
        jobs.setdefault(
            url,
            {
                "url": url,
                "title": title,
                "company": "Empresa via Remōt",
                "application_url": application_url,
            },
        )
    return list(jobs.values())
