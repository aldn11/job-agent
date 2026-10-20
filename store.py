#!/usr/bin/env python3
"""Armazenamento das vagas (SQLite) e identificacao unica de cada vaga."""
import hashlib
import json
import math
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
    columns = {
        row[1] for row in db.execute("PRAGMA table_info(jobs)").fetchall()
    }
    scrape_columns = (
        ("scrape_attempts", "INTEGER NOT NULL DEFAULT 0"),
        ("scrape_last_error", "TEXT"),
        ("scrape_next_attempt", "TEXT"),
    )
    needs_scrape_migration = any(column not in columns for column, _ in scrape_columns)
    for column, definition in scrape_columns:
        if column not in columns:
            db.execute(f"ALTER TABLE jobs ADD COLUMN {column} {definition}")
    db.execute("""CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY,
        job_key TEXT NOT NULL UNIQUE,
        payload TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'PENDING',
        alert_sent INTEGER NOT NULL DEFAULT 0,
        letter_sent INTEGER NOT NULL DEFAULT 0,
        attempts INTEGER NOT NULL DEFAULT 0,
        last_error TEXT,
        created_at TEXT NOT NULL,
        sent_at TEXT)""")
    notification_columns = {
        row[1] for row in db.execute("PRAGMA table_info(notifications)").fetchall()
    }
    for column in ("alert_sent", "letter_sent"):
        if column not in notification_columns:
            db.execute(
                f"ALTER TABLE notifications ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0"
            )
    if needs_scrape_migration:
        migrate_incomplete_linkedin_evaluations(db)
    return db


def migrate_incomplete_linkedin_evaluations(db):
    rows = db.execute(
        """SELECT key, evaluation FROM jobs
           WHERE lower(source) = 'linkedin' AND status LIKE 'EVALUATED_%'
             AND evaluation IS NOT NULL"""
    ).fetchall()
    pending_keys = []
    for job_key, raw_evaluation in rows:
        try:
            evaluation = json.loads(raw_evaluation)
        except (TypeError, json.JSONDecodeError):
            continue
        score = evaluation.get("score")
        if (
            evaluation.get("info_status") == "INCOMPLETE"
            or not isinstance(score, (int, float))
            or isinstance(score, bool)
            or not math.isfinite(score)
        ):
            pending_keys.append(job_key)

    if pending_keys:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        db.executemany(
            """UPDATE jobs SET status = 'SCRAPE_PENDING',
               scrape_next_attempt = ? WHERE key = ?""",
            [(now, job_key) for job_key in pending_keys],
        )
        db.commit()


def save_evaluation(short_id, evaluation_data, path=DB_PATH, notification_context=None):
    """Persist an evaluation and its Telegram alert atomically."""
    db = connect(path)
    try:
        row = db.execute(
            "SELECT key FROM jobs WHERE id = ? LIMIT 2", (short_id,)
        ).fetchall()
        if len(row) != 1:
            raise LookupError(
                f"Esperava uma vaga para o ID {short_id}; encontrei {len(row)}."
            )

        job_key = row[0][0]
        verdict = evaluation_data.get("verdict", "UNKNOWN")
        payload = json.dumps(evaluation_data, ensure_ascii=False)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        score = evaluation_data.get("score")
        score_is_valid = (
            isinstance(score, (int, float))
            and not isinstance(score, bool)
            and math.isfinite(score)
        )
        evaluation_is_incomplete = (
            evaluation_data.get("info_status") == "INCOMPLETE"
            or not score_is_valid
        )
        status = "NEW" if evaluation_is_incomplete else f"EVALUATED_{verdict}"
        with db:
            db.execute(
                "UPDATE jobs SET status = ?, evaluation = ? WHERE key = ?",
                (status, payload, job_key),
            )
            if not evaluation_is_incomplete and verdict in ("APPLY", "MAYBE"):
                notification_data = dict(evaluation_data)
                if notification_context:
                    notification_data.update(notification_context)
                db.execute(
                    """INSERT INTO notifications
                       (job_key, payload, status, created_at)
                       VALUES (?, ?, 'PENDING', ?)
                       ON CONFLICT(job_key) DO UPDATE SET
                           payload = excluded.payload,
                           status = CASE
                               WHEN notifications.status = 'SENT' THEN 'SENT'
                               ELSE 'PENDING'
                           END,
                           last_error = CASE
                               WHEN notifications.status = 'SENT'
                               THEN notifications.last_error
                               ELSE NULL
                           END""",
                    (
                        job_key,
                        json.dumps(notification_data, ensure_ascii=False),
                        now,
                    ),
                )
            else:
                db.execute(
                    """DELETE FROM notifications
                       WHERE job_key = ? AND status = 'PENDING'""",
                    (job_key,),
                )
    finally:
        db.close()


def update_notification_payload(notification_id, payload, path=DB_PATH):
    db = connect(path)
    try:
        with db:
            db.execute(
                "UPDATE notifications SET payload = ? WHERE id = ? AND status = 'PENDING'",
                (json.dumps(payload, ensure_ascii=False), notification_id),
            )
    finally:
        db.close()


def notification_job_id(notification_id, path=DB_PATH):
    db = connect(path)
    try:
        row = db.execute(
            """SELECT jobs.id FROM notifications
               JOIN jobs ON jobs.key = notifications.job_key
               WHERE notifications.id = ?""",
            (notification_id,),
        ).fetchone()
        return row[0] if row else None
    finally:
        db.close()


def pending_notifications(path=DB_PATH):
    db = connect(path)
    try:
        return db.execute(
            """SELECT id, payload, alert_sent, letter_sent FROM notifications
               WHERE status = 'PENDING' ORDER BY id"""
        ).fetchall()
    finally:
        db.close()


def mark_notification_sent(notification_id, path=DB_PATH):
    db = connect(path)
    try:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with db:
            db.execute(
                """UPDATE notifications
                   SET status = 'SENT', alert_sent = 1, letter_sent = 1,
                       sent_at = ?, last_error = NULL
                   WHERE id = ?""",
                (now, notification_id),
            )
    finally:
        db.close()


def mark_notification_alert_sent(notification_id, path=DB_PATH):
    db = connect(path)
    try:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with db:
            db.execute(
                """UPDATE notifications
                   SET alert_sent = 1,
                       status = CASE WHEN letter_sent = 1 THEN 'SENT' ELSE status END,
                       sent_at = CASE WHEN letter_sent = 1 THEN ? ELSE sent_at END,
                       last_error = NULL
                   WHERE id = ? AND status = 'PENDING'""",
                (now, notification_id),
            )
    finally:
        db.close()


def mark_notification_letter_sent(notification_id, path=DB_PATH):
    db = connect(path)
    try:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with db:
            db.execute(
                """UPDATE notifications
                   SET letter_sent = 1,
                       status = CASE WHEN alert_sent = 1 THEN 'SENT' ELSE status END,
                       sent_at = CASE WHEN alert_sent = 1 THEN ? ELSE sent_at END,
                       last_error = NULL
                   WHERE id = ? AND status = 'PENDING'""",
                (now, notification_id),
            )
    finally:
        db.close()


def mark_notification_failed(notification_id, error, path=DB_PATH):
    db = connect(path)
    try:
        with db:
            db.execute(
                """UPDATE notifications
                   SET attempts = attempts + 1, last_error = ?
                   WHERE id = ? AND status = 'PENDING'""",
                (str(error)[:500], notification_id),
            )
    finally:
        db.close()


def notification_counts(path=DB_PATH):
    db = connect(path)
    try:
        return dict(db.execute(
            "SELECT status, COUNT(*) FROM notifications GROUP BY status"
        ).fetchall())
    finally:
        db.close()


def add_job(db, url, company, title, status="NEW"):
    """Devolve ('new' | 'seen' | 'possible_duplicate', chave)."""
    key = job_key(url)
    if db.execute("SELECT 1 FROM jobs WHERE key=?", (key,)).fetchone():
        return "seen", key
    fp = fingerprint(company, title)
    dup = db.execute("SELECT key FROM jobs WHERE fingerprint=?", (fp,)).fetchone()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    db.execute(
        """INSERT INTO jobs
           (key, id, source, url, company, title, fingerprint, first_seen, status)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (key, short_id(key), key.split(":")[0], url, company, title, fp, now, status))
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
