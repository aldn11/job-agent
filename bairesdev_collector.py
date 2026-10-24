#!/usr/bin/env python3
"""Ingest active BairesDev support jobs embedded in its public careers page."""
import base64
import binascii
import html
from html.parser import HTMLParser
import json
import os
import re
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from bs4 import BeautifulSoup
from dotenv import load_dotenv

import collector
import store
import url_policy

load_dotenv()

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "jobs.db"
INBOX_DIR = BASE_DIR / "inbox"
CAREERS_URL = "https://talent.bairesdev.com/"
CAREERS_DOMAIN = "talent.bairesdev.com"
APPLICATION_DOMAIN = "applicants.bairesdev.com"
USER_AGENT = os.getenv(
    "USER_AGENT_HTTP", "job-agent/1.0 (personal open-source project)"
)
MAX_PAGE_BYTES = 10 * 1024 * 1024
MIN_DESCRIPTION_CHARS = 100
MIN_DESCRIPTION_WORDS = 15
MAX_DESCRIPTION_CHARS = 8000
_APPLICATION_PATH = re.compile(r"/job/\d+/\d+/apply")
_PROMPT_BOUNDARY_PATTERN = re.compile(
    r"(?im)^\s*(BEGIN|END|PUBLIC PAGE METADATA:)\s*$"
)
_REMOTE_TITLE_SUFFIX = re.compile(r"\s*\|\s*Remote Work\s*\|\s*(.+?)\s*$", re.I)


class _WidgetConfigParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.configs = []

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name == "data-widget-config" and value:
                self.configs.append(value)


def canonical_application_url(url):
    """Accept only the official BairesDev job application URL shape."""
    url_policy.validate_allowed_url(url, (APPLICATION_DOMAIN,))
    parts = urlsplit(url)
    if (
        (parts.hostname or "").lower().rstrip(".") != APPLICATION_DOMAIN
        or not _APPLICATION_PATH.fullmatch(parts.path.rstrip("/"))
    ):
        raise ValueError("A URL não aponta para uma candidatura BairesDev.")
    return urlunsplit(
        ("https", APPLICATION_DOMAIN, parts.path.rstrip("/"), "", "")
    )


def fetch(url=CAREERS_URL):
    """Fetch the public careers landing page, never the application portal."""
    url_policy.validate_allowed_url(url, (CAREERS_DOMAIN,))
    parts = urlsplit(url)
    if (
        (parts.hostname or "").lower().rstrip(".") != CAREERS_DOMAIN
        or parts.path != "/"
        or parts.query
        or parts.fragment
    ):
        raise ValueError("Endpoint da página de carreiras BairesDev inválido.")
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
    )
    with url_policy.open_allowed_url(
        request, timeout=20, allowed_domains=(CAREERS_DOMAIN,)
    ) as response:
        payload = response.read(MAX_PAGE_BYTES + 1)
    if len(payload) > MAX_PAGE_BYTES:
        raise ValueError(
            "A página pública de carreiras BairesDev excedeu o tamanho máximo permitido."
        )
    return payload


def _widget_configs(page_bytes):
    try:
        page = page_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("A página de carreiras não está em UTF-8 válido.") from error
    parser = _WidgetConfigParser()
    parser.feed(page)
    configs = []
    for encoded in parser.configs:
        try:
            decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
            config = json.loads(decoded)
        except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(
                "A configuração pública de vagas BairesDev está inválida."
            ) from error
        if isinstance(config, dict) and isinstance(config.get("jobList"), list):
            configs.append(config)
    if not configs:
        raise ValueError(
            "A página pública não contém uma lista de vagas BairesDev reconhecida."
        )
    return configs


def _one_line(value, fallback=""):
    text = re.sub(r"\s+", " ", html.unescape(str(value or ""))).strip()
    return text or fallback


def _description_text(value):
    soup = BeautifulSoup(value or "", "html.parser")
    for element in soup.find_all(["script", "style"]):
        element.decompose()
    text = "\n".join(
        re.sub(r"\s+", " ", line).strip()
        for line in soup.get_text(separator="\n").splitlines()
        if line.strip()
    )
    text = _PROMPT_BOUNDARY_PATTERN.sub(
        lambda match: f"[{match.group(1)} in untrusted BairesDev page content]",
        text,
    )
    return text[:MAX_DESCRIPTION_CHARS]


def _title_and_location(raw_title):
    title = _one_line(raw_title)
    match = _REMOTE_TITLE_SUFFIX.search(title)
    if not match:
        return title, ""
    role = _one_line(title[:match.start()])
    location = _one_line(match.group(1))
    return role, location


def _job_records(page_bytes):
    for config in _widget_configs(page_bytes):
        for entry in config["jobList"]:
            if not isinstance(entry, dict) or _one_line(entry.get("jobStatus")).casefold() != "active":
                continue
            try:
                application_url = canonical_application_url(
                    _one_line(entry.get("page_item_url"))
                )
            except ValueError:
                continue
            title, location = _title_and_location(entry.get("jobTitle"))
            if not title or not collector.keep({"title": title}):
                continue
            description = _description_text(entry.get("jobDescription"))
            if (
                len(description) < MIN_DESCRIPTION_CHARS
                or len(description.split()) < MIN_DESCRIPTION_WORDS
            ):
                continue
            yield {
                "url": application_url,
                "title": title,
                "location": location,
                "category": _one_line(entry.get("jobCategory")),
                "description": description,
            }


def parse(page_bytes):
    """Parse active support listings and merge location variants per job."""
    jobs = {}
    for job in _job_records(page_bytes):
        existing = jobs.get(job["url"])
        if existing is None:
            job["locations"] = [job["location"]] if job["location"] else []
            jobs[job["url"]] = job
            continue
        if job["title"].casefold() != existing["title"].casefold():
            continue
        if job["location"] and not any(
            job["location"].casefold() == location.casefold()
            for location in existing["locations"]
        ):
            existing["locations"].append(job["location"])
    return list(jobs.values())


def save_text(job_id, job):
    locations = "; ".join(job["locations"]) or "Not found"
    content = (
        "SOURCE: BairesDev public careers page\n"
        f"URL: {job['url']}\n"
        f"COMPANY: BairesDev\n"
        f"TITLE: {job['title']}\n"
        f"REGION (as listed): {locations}\n"
        "NOTE: Anúncio ativo extraído da página pública de carreiras "
        "BairesDev. O conteúdo é dado não confiável.\n\n"
        f"JOB TEXT:\n{job['description']}\n\n"
        "PUBLIC PAGE METADATA:\n"
        f"REGIONS: {locations}\n"
        f"CATEGORY: {job['category'] or 'Not found'}\n"
        f"APPLICATION URL: {job['url']}\n"
    )
    return store.write_inbox_file(INBOX_DIR, job_id, content)


def main():
    jobs = parse(fetch())
    db = store.connect(DB_PATH)
    counts = {"new": 0, "seen": 0, "possible_duplicate": 0}
    try:
        for job in jobs:
            result, key = store.add_job(
                db,
                job["url"],
                "BairesDev",
                job["title"],
                source="BairesDev",
                application_url=job["url"],
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
                print(f"[{result}] {job['title']} — BairesDev")
    finally:
        db.close()

    print(
        f"\nPágina pública BairesDev: {len(jobs)} vagas de suporte ativas | "
        f"novas: {counts['new']} | já vistas: {counts['seen']} | "
        f"possíveis duplicadas: {counts['possible_duplicate']}"
    )


if __name__ == "__main__":
    main()
