#!/usr/bin/env python3
"""Armazenamento das vagas (SQLite) e identificacao unica de cada vaga."""
import hashlib
import re
import sqlite3
from datetime import datetime, timezone
from urllib.parse import urlsplit, parse_qs

DB_PATH = "jobs.db"


def job_key(url):
    """Identidade da vaga: 'fonte:id', usando o ID proprio do site."""
    p = urlsplit(url.strip())
    host = p.netloc.lower().replace("www.", "")
    path = p.path.rstrip("/")
    if host == "weworkremotely.com":
        return "wwr:" + path.split("/")[-1].lower()
    if host == "linkedin.com":
        m = re.search(r"/jobs/view/(?:[^/]*-)?(\d+)", path)
        if m:
            return "linkedin:" + m.group(1)
    if host.endswith("indeed.com"):
        jk = parse_qs(p.query).get("jk", [""])[0]
        if jk:
            return "indeed:" + jk
    # fonte desconhecida: endereco sem parametros de rastreio
    return f"{host}:{path.lower()}"


def short_id(key):
    """Codigo curto, seguro para nome de arquivo."""
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def fingerprint(company, title):
    """Impressao digital empresa+titulo, para achar a mesma vaga em outra fonte."""
    text = re.sub(r"[^a-z0-9]+", " ", f"{company} {title}".lower()).strip()
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def connect(path=DB_PATH):
    db = sqlite3.connect(path)
    db.execute("""CREATE TABLE IF NOT EXISTS jobs (
        key TEXT PRIMARY KEY, id TEXT, source TEXT, url TEXT,
        company TEXT, title TEXT, fingerprint TEXT,
        first_seen TEXT, status TEXT DEFAULT 'NEW', evaluation TEXT)""")
    return db


def add_job(db, url, company, title):
    """Devolve ('new' | 'seen' | 'possible_duplicate', chave)."""
    key = job_key(url)
    if db.execute("SELECT 1 FROM jobs WHERE key=?", (key,)).fetchone():
        return "seen", key
    fp = fingerprint(company, title)
    dup = db.execute("SELECT key FROM jobs WHERE fingerprint=?", (fp,)).fetchone()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    db.execute(
        "INSERT INTO jobs (key, id, source, url, company, title, fingerprint, first_seen)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (key, short_id(key), key.split(":")[0], url, company, title, fp, now))
    db.commit()
    return ("possible_duplicate" if dup else "new"), key


if __name__ == "__main__":
    db = connect(":memory:")  # banco temporario, so para o teste
    casos = [
        ("https://weworkremotely.com/remote-jobs/crae-group-ltd-customer-support-agent-remote", "CRAE GROUP LTD", "Customer Support Agent (Remote)"),
        ("https://weworkremotely.com/remote-jobs/crae-group-ltd-customer-support-agent-remote/", "CRAE GROUP LTD", "Customer Support Agent (Remote)"),
        ("https://www.linkedin.com/jobs/view/4473119964/?trackingId=abc&refId=xyz", "SoftExpert", "Analista de Suporte Pleno"),
        ("https://www.linkedin.com/jobs/view/4473119964/?trackingId=OUTRO", "SoftExpert", "Analista de Suporte Pleno"),
        ("https://exemplo.com/vagas/999", "SoftExpert", "Analista de Suporte Pleno"),
    ]
    for url, company, title in casos:
        resultado, key = add_job(db, url, company, title)
        print(f"{resultado:20} {key}")
