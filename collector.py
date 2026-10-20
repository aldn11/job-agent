#!/usr/bin/env python3
import html
import os
import re
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from dotenv import load_dotenv
import url_policy

load_dotenv()

import store

BASE_DIR = Path(__file__).parent
DB_FILE = BASE_DIR / "jobs.db"
INBOX_DIR = BASE_DIR / "inbox"
MAX_CHARS = 8000

FEED = os.getenv(
    "WWR_FEED_URL",
    "https://weworkremotely.com/categories/remote-customer-support-jobs.rss"
)
USER_AGENT = os.getenv("USER_AGENT_HTTP", "job-agent/1.0 (personal open-source project)")
MAX_FEED_BYTES = 10 * 1024 * 1024

INCLUDE = [
    "technical support", "support specialist", "support agent",
    "support representative", "support advisor", "support engineer",
    "help desk", "helpdesk", "service desk", "it support",
    "customer support", "customer service"
]
EXCLUDE = [
    "manager", "director", "head of", "lead", "senior", "account",
    "success", "crm", "sales", "social media", "architect", "developer",
    "speaking", "u.s.-based", "us remote", "canada", "appointment"
]

def fetch(url):
    url_policy.validate_allowed_url(url, ("weworkremotely.com",))
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with url_policy.open_allowed_url(
        req, timeout=20, allowed_domains=("weworkremotely.com",)
    ) as resp:
        payload = resp.read(MAX_FEED_BYTES + 1)
        if len(payload) > MAX_FEED_BYTES:
            raise ValueError("O feed RSS excedeu o tamanho máximo permitido.")
        return payload

def clean(text):
    text = html.unescape(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()

def split_title(raw):
    company, sep, role = raw.partition(": ")
    return (company.strip(), role.strip()) if sep else ("", raw.strip())

def parse(xml_bytes):
    root = ET.fromstring(xml_bytes)
    jobs = {}
    for item in root.iter("item"):
        link = (item.findtext("link") or item.findtext("guid") or "").strip()
        if not link or link in jobs:
            continue
        try:
            url_policy.validate_allowed_url(link, ("weworkremotely.com",))
        except ValueError:
            continue
        jobs[link] = {
            "title": html.unescape((item.findtext("title") or "").strip()),
            "link": link,
            "region": (item.findtext("region") or "").strip(),
            "description": clean(item.findtext("description"))[:MAX_CHARS],
        }
    return list(jobs.values())

def keep(job):
    title = job["title"].lower()
    if any(word in title for word in EXCLUDE):
        return False
    return any(word in title for word in INCLUDE)

def save_text(short, job, company, role, note):
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    path = INBOX_DIR / f"{short}.txt"
    with open(path, "w", encoding="utf-8") as f:
        f.write(
            f"SOURCE: We Work Remotely\nURL: {job['link']}\n"
            f"COMPANY: {company}\nTITLE: {role}\n"
            f"REGION (as listed): {job['region']}\n"
            f"NOTE: {note}\n\nJOB TEXT:\n{job['description']}\n"
        )
    return path

def main():
    jobs = parse(fetch(FEED))
    kept = [job for job in jobs if keep(job)]
    db = store.connect(DB_FILE)
    counts = {"new": 0, "seen": 0, "possible_duplicate": 0}
    for job in kept:
        company, role = split_title(job["title"])
        result, key = store.add_job(db, job["link"], company, role)
        counts[result] += 1
        file_path = INBOX_DIR / f"{store.short_id(key)}.txt"
        if result in ("new", "possible_duplicate") or not file_path.exists():
            note = "possible duplicate of a job seen before" if result == "possible_duplicate" else ""
            path = save_text(store.short_id(key), job, company, role, note)
            print(f"[{result}] {job['title']}\n   -> {path}")
    print(
        f"\nNo feed: {len(jobs)} | passaram no filtro: {len(kept)} | "
        f"novas: {counts['new']} | ja vistas: {counts['seen']} | "
        f"possiveis duplicadas: {counts['possible_duplicate']}"
    )

if __name__ == "__main__":
    main()
