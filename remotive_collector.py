#!/usr/bin/env python3
"""Ingest Remotive jobs from public RSS feeds only."""
import os
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from bs4 import BeautifulSoup
from dotenv import load_dotenv

import store
import feed_filters
import url_policy

load_dotenv()

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "jobs.db"
INBOX_DIR = BASE_DIR / "inbox"
DOMAIN = "remotive.com"
GENERAL_FEED = "https://remotive.com/remote-jobs/feed"
# These category slugs have been checked against the public RSS endpoints.
SUPPORTED_CATEGORIES = frozenset(
    {
        "all-others",
        "customer-service",
        "data",
        "design",
        "devops",
        "marketing",
        "product",
        "qa",
        "sales",
        "writing",
    }
)
USER_AGENT = os.getenv(
    "USER_AGENT_HTTP", "job-agent/1.0 (personal open-source project)"
)
MAX_FEED_BYTES = 10 * 1024 * 1024
MIN_DESCRIPTION_CHARS = 100
MIN_DESCRIPTION_WORDS = 15
_PROMPT_BOUNDARY_PATTERN = re.compile(
    r"(?im)^\s*(BEGIN|END|PUBLIC PAGE METADATA:)\s*$"
)


def configured_feed_urls(value=None):
    """Return the general feed by default or verified category feed URLs."""
    if value is None:
        value = os.getenv("REMOTIVE_FEED_CATEGORIES", "")
    categories = list(
        dict.fromkeys(
            category.strip().lower()
            for category in value.split(";")
            if category.strip()
        )
    )
    unsupported = sorted(set(categories) - SUPPORTED_CATEGORIES)
    if unsupported:
        raise ValueError(
            "Categoria(s) RSS Remotive não suportada(s): "
            + ", ".join(unsupported)
        )
    if not categories:
        return [GENERAL_FEED]
    return [
        f"https://{DOMAIN}/remote-jobs/{category}/feed"
        for category in categories
    ]


def canonical_job_url(url):
    """Validate official Remotive job links and remove tracking state."""
    url_policy.validate_allowed_url(url, (DOMAIN,))
    parts = urlsplit(url)
    hostname = (parts.hostname or "").lower().rstrip(".")
    if hostname.startswith("www."):
        hostname = hostname[4:]
    if hostname != DOMAIN or parts.path.rstrip("/") == "":
        raise ValueError("A URL não aponta para um anúncio Remotive.")
    if not parts.path.startswith("/remote-jobs/"):
        raise ValueError("A URL não aponta para um anúncio Remotive.")
    return urlunsplit(("https", hostname, parts.path.rstrip("/"), "", ""))


def fetch(url):
    url_policy.validate_allowed_url(url, (DOMAIN,))
    parts = urlsplit(url)
    if (
        parts.hostname not in (DOMAIN, f"www.{DOMAIN}")
        or (
            parts.path != "/remote-jobs/feed"
            and not re.fullmatch(r"/remote-jobs/[a-z0-9-]+/feed", parts.path)
        )
        or parts.query
        or parts.fragment
    ):
        raise ValueError("Endpoint RSS Remotive inválido.")
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/rss+xml, application/xml",
        },
    )
    with url_policy.open_allowed_url(
        request, timeout=20, allowed_domains=(DOMAIN,)
    ) as response:
        payload = response.read(MAX_FEED_BYTES + 1)
    if len(payload) > MAX_FEED_BYTES:
        raise ValueError("O feed RSS do Remotive excedeu o tamanho máximo permitido.")
    return payload


def _child_text(element, name):
    child = next(
        (
            node for node in element
            if node.tag.rsplit("}", 1)[-1] == name
        ),
        None,
    )
    return child.text.strip() if child is not None and child.text else ""


def _one_line(value, fallback=""):
    return re.sub(r"\s+", " ", value or "").strip() or fallback


def _publication_date(value):
    if not value:
        return ""
    try:
        published = parsedate_to_datetime(value)
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        return published.astimezone(timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError, OverflowError):
        return ""


def _description_text(value):
    soup = BeautifulSoup(value or "", "html.parser")
    for image in soup.find_all("img"):
        image.decompose()
    text = "\n".join(
        line.strip()
        for line in soup.get_text(separator="\n").splitlines()
        if line.strip()
    )
    return _PROMPT_BOUNDARY_PATTERN.sub(
        lambda match: f"[{match.group(1)} in untrusted Remotive feed content]",
        text,
    )


def parse(xml_bytes):
    root = ET.fromstring(xml_bytes)
    jobs = {}
    for item in root.iter():
        if item.tag.rsplit("}", 1)[-1] != "item":
            continue
        title = _one_line(_child_text(item, "title"))
        raw_url = _child_text(item, "link")
        if not title or not raw_url:
            continue
        try:
            url = canonical_job_url(raw_url)
        except ValueError:
            continue
        description = _description_text(_child_text(item, "description"))
        if (
            len(description) < MIN_DESCRIPTION_CHARS
            or len(description.split()) < MIN_DESCRIPTION_WORDS
        ):
            continue
        category = _one_line(_child_text(item, "category"))
        job = {
            "url": url,
            "title": title,
            "company": _one_line(_child_text(item, "company"), "Empresa não especificada"),
            "location": _one_line(_child_text(item, "location")),
            "job_type": _one_line(_child_text(item, "type")),
            "category": category,
            "published": _publication_date(_child_text(item, "pubDate")),
            "description": description,
        }
        jobs.setdefault(url, job)
    return list(jobs.values())


def save_text(job_id, job):
    content = (
        "SOURCE: Remotive (public RSS)\n"
        f"URL: {job['url']}\n"
        f"COMPANY: {_one_line(job['company'], 'Empresa não especificada')}\n"
        f"TITLE: {_one_line(job['title'], 'Vaga Remotive')}\n"
        f"REGION (as listed): {_one_line(job['location'], 'Not found')}\n"
        "NOTE: Conteúdo do Remotive RSS. Remotive é a fonte; "
        "acesse o anúncio original pelo link acima.\n\n"
        f"JOB TEXT:\n{job['description']}\n\n"
        "PUBLIC PAGE METADATA:\n"
        f"PUBLISHED: {_one_line(job['published'], 'Not found')}\n"
        f"CATEGORY: {_one_line(job['category'], 'Not found')}\n"
        f"JOB TYPE: {_one_line(job['job_type'], 'Not found')}\n"
        f"LOCATION: {_one_line(job['location'], 'Not found')}\n"
    )
    return store.write_inbox_file(INBOX_DIR, job_id, content)


def main():
    feed_urls = configured_feed_urls()
    jobs_by_url = {}
    for url in feed_urls:
        for job in parse(fetch(url)):
            jobs_by_url.setdefault(job["url"], job)
    all_jobs = list(jobs_by_url.values())
    jobs = feed_filters.filter_jobs(
        all_jobs,
        include_terms=os.getenv("REMOTIVE_INCLUDE_TERMS", ""),
        exclude_terms=os.getenv("REMOTIVE_EXCLUDE_TERMS", ""),
        include_categories=os.getenv("REMOTIVE_INCLUDE_CATEGORIES", ""),
        include_locations=os.getenv("REMOTIVE_INCLUDE_LOCATIONS", ""),
    )

    db = store.connect(DB_PATH)
    counts = {"new": 0, "seen": 0, "possible_duplicate": 0}
    try:
        for job in jobs:
            result, key = store.add_job(
                db,
                job["url"],
                job["company"],
                job["title"],
                source="Remotive",
            )
            counts[result] += 1
            existing = db.execute(
                "SELECT id, status FROM jobs WHERE key = ?", (key,)
            ).fetchone()
            if not existing:
                continue
            job_id, status = existing
            file_path = INBOX_DIR / f"{job_id}.txt"
            if result != "seen" or (status == "NEW" and not file_path.exists()):
                save_text(job_id, job)
                print(f"[{result}] {job['title']} — Remotive")
    finally:
        db.close()

    print(
        f"\nFeeds Remotive: {len(feed_urls)} | anúncios após filtros: "
        f"{len(jobs)} de {len(all_jobs)} | "
        f"novos: {counts['new']} | já vistos: {counts['seen']} | "
        f"possíveis duplicatas: {counts['possible_duplicate']}"
    )


if __name__ == "__main__":
    main()
