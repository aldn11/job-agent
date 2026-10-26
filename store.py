#!/usr/bin/env python3
"""Armazenamento das vagas (SQLite) e identificacao unica de cada vaga."""
import hashlib
import json
import math
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

DB_PATH = "jobs.db"
SCRAPE_MAX_AGE_DAYS = int(os.getenv("SCRAPE_MAX_AGE_DAYS", "30"))
if SCRAPE_MAX_AGE_DAYS <= 0:
    raise ValueError("SCRAPE_MAX_AGE_DAYS precisa ser maior que zero.")


def job_key(url):
    """Identidade da vaga: 'fonte:id', usando o ID proprio do site."""
    p = urlsplit(url.strip())
    host = p.netloc.lower().replace("www.", "")
    path = p.path.rstrip("/")
    if host == "weworkremotely.com":
        return "wwr:" + path.split("/")[-1].lower()
    if host == "workremot.com" or host.endswith(".workremot.com"):
        query = "&".join(sorted(p.query.split("&"))) if p.query else ""
        return "remot:" + path + (f"?{query}" if query else "")
    if host == "jobspresso.co" or host.endswith(".jobspresso.co"):
        return "jobspresso:" + path.lower()
    if host == "remotive.com" or host.endswith(".remotive.com"):
        return "remotive:" + path.lower()
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


def write_inbox_file(inbox_dir, job_id, content):
    """Atomically write a complete job description to the shared inbox."""
    inbox_dir = Path(inbox_dir)
    inbox_dir.mkdir(parents=True, exist_ok=True)
    path = inbox_dir / f"{job_id}.txt"
    temp_path = path.with_suffix(".txt.tmp")
    temp_path.write_text(content, encoding="utf-8")
    temp_path.replace(path)
    return path


def fingerprint(company, title):
    """Impressao digital empresa+titulo, para achar a mesma vaga em outra fonte."""
    text = re.sub(r"[^a-z0-9]+", " ", f"{company} {title}".lower()).strip()
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def connect(path=DB_PATH):
    db = sqlite3.connect(path, timeout=30)
    db.execute("""CREATE TABLE IF NOT EXISTS jobs (
        key TEXT PRIMARY KEY, id TEXT, source TEXT, url TEXT,
        company TEXT, title TEXT, fingerprint TEXT,
        first_seen TEXT, status TEXT DEFAULT 'NEW', evaluation TEXT,
        application_url TEXT)""")
    columns = {
        row[1] for row in db.execute("PRAGMA table_info(jobs)").fetchall()
    }
    if "application_url" not in columns:
        db.execute("ALTER TABLE jobs ADD COLUMN application_url TEXT")
    scrape_columns = (
        ("scrape_attempts", "INTEGER NOT NULL DEFAULT 0"),
        ("scrape_last_error", "TEXT"),
        ("scrape_next_attempt", "TEXT"),
        ("evaluation_attempts", "INTEGER NOT NULL DEFAULT 0"),
        ("evaluation_last_error", "TEXT"),
        ("evaluation_next_attempt", "TEXT"),
        ("evaluation_lease_until", "TEXT"),
    )
    for column, definition in scrape_columns:
        if column not in columns:
            db.execute(f"ALTER TABLE jobs ADD COLUMN {column} {definition}")
    db.execute("""CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY,
        job_key TEXT NOT NULL UNIQUE,
        payload TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'PENDING',
        alert_sent INTEGER NOT NULL DEFAULT 0,
        attempts INTEGER NOT NULL DEFAULT 0,
        last_error TEXT,
        created_at TEXT NOT NULL,
        sent_at TEXT)""")
    db.execute(
        """CREATE TABLE IF NOT EXISTS email_review_queue (
            message_fingerprint TEXT PRIMARY KEY,
            imap_sequence_id TEXT NOT NULL,
            message_id TEXT,
            sender TEXT,
            subject TEXT,
            sent_at TEXT,
            reason TEXT NOT NULL,
            recorded_at TEXT NOT NULL
        )"""
    )
    notification_columns = {
        row[1] for row in db.execute("PRAGMA table_info(notifications)").fetchall()
    }
    for column in ("alert_sent",):
        if column not in notification_columns:
            db.execute(
                f"ALTER TABLE notifications ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0"
            )
    if "next_attempt_at" not in notification_columns:
        db.execute("ALTER TABLE notifications ADD COLUMN next_attempt_at TEXT")
    if "delivery_lease_until" not in notification_columns:
        db.execute("ALTER TABLE notifications ADD COLUMN delivery_lease_until TEXT")
    db.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations (
            migration_id TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        )"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS ai_usage (
            id INTEGER PRIMARY KEY,
            job_id TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            model TEXT NOT NULL,
            prompt_chars INTEGER NOT NULL,
            input_tokens_estimate INTEGER NOT NULL,
            response_chars INTEGER NOT NULL,
            output_tokens_estimate INTEGER NOT NULL,
            outcome TEXT NOT NULL,
            error_type TEXT,
            result_summary TEXT,
            error_detail TEXT
        )"""
    )
    ai_usage_columns = {
        row[1] for row in db.execute("PRAGMA table_info(ai_usage)").fetchall()
    }
    for column in ("result_summary", "error_detail"):
        if column not in ai_usage_columns:
            db.execute(f"ALTER TABLE ai_usage ADD COLUMN {column} TEXT")
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_ai_usage_job_id ON ai_usage(job_id, id)"
    )
    migration_id = "restore-evaluated-jobs-from-legacy-scrape-backfill-v1"
    if not db.execute(
        "SELECT 1 FROM schema_migrations WHERE migration_id = ?",
        (migration_id,),
    ).fetchone():
        restore_legacy_evaluations_from_scrape_queue(db)
        db.execute(
            "INSERT INTO schema_migrations (migration_id, applied_at) VALUES (?, ?)",
            (migration_id, datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )
        db.commit()
    stale_migration_id = "retire-stale-pending-scrapes-v2"
    if not db.execute(
        "SELECT 1 FROM schema_migrations WHERE migration_id = ?",
        (stale_migration_id,),
    ).fetchone():
        retired = retire_stale_pending_scrapes(db)
        db.execute(
            "INSERT INTO schema_migrations (migration_id, applied_at) VALUES (?, ?)",
            (
                stale_migration_id,
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
        )
        db.commit()
        if retired:
            print(
                f"[!] Migração: {retired} vaga(s) antigas ou sem data válida "
                "foram retiradas da fila de scraping."
            )
    review_migration_id = "review-exhausted-short-descriptions-v1"
    if not db.execute(
        "SELECT 1 FROM schema_migrations WHERE migration_id = ?",
        (review_migration_id,),
    ).fetchone():
        reviewed = transition_exhausted_short_description_scrapes(db)
        db.execute(
            "INSERT INTO schema_migrations (migration_id, applied_at) VALUES (?, ?)",
            (
                review_migration_id,
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
        )
        db.commit()
        if reviewed:
            print(
                f"[!] Migração: {reviewed} vaga(s) com descrição curta após "
                "5 tentativas foram encaminhadas para revisão manual."
            )
    return db


def transition_exhausted_short_description_scrapes(db, max_attempts=5):
    """Move only known short-description failures to the manual-review queue."""
    with db:
        return db.execute(
            """UPDATE jobs SET status = 'SCRAPE_REVIEW',
               scrape_next_attempt = NULL
               WHERE status = 'SCRAPE_PENDING'
                 AND scrape_attempts >= ?
                 AND scrape_last_error = ?""",
            (
                max_attempts,
                "A descrição extraída é curta demais para avaliação.",
            ),
        ).rowcount


def record_unrecognized_email(
    db,
    message_fingerprint,
    imap_sequence_id,
    *,
    message_id="",
    sender="",
    subject="",
    sent_at="",
    reason,
):
    """Persist minimal metadata for an unsupported email before it is consumed."""
    with db:
        cursor = db.execute(
            """INSERT OR IGNORE INTO email_review_queue
               (message_fingerprint, imap_sequence_id, message_id, sender,
                subject, sent_at, reason, recorded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                message_fingerprint,
                str(imap_sequence_id),
                message_id[:998],
                sender[:500],
                subject[:998],
                sent_at[:200],
                reason[:500],
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
        )
        return cursor.rowcount == 1


def retire_stale_pending_scrapes(db):
    """Retire historical scrape work, including rows with unusable timestamps."""
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=SCRAPE_MAX_AGE_DAYS)
    ).isoformat(timespec="seconds")
    with db:
        return db.execute(
            """UPDATE jobs SET status = 'SCRAPE_UNAVAILABLE',
               scrape_next_attempt = NULL,
               scrape_last_error = 'Historical job exceeded the scraping age window or has no usable timestamp.'
               WHERE status = 'SCRAPE_PENDING'
                 AND (
                     first_seen IS NULL
                     OR trim(first_seen) = ''
                     OR julianday(first_seen) IS NULL
                     OR julianday(first_seen) < julianday(?)
                 )""",
            (cutoff,),
        ).rowcount


def restore_legacy_evaluations_from_scrape_queue(db):
    """Undo the old schema-triggered backfill without discarding stored evaluations."""
    rows = db.execute(
        """SELECT key, evaluation FROM jobs
           WHERE status = 'SCRAPE_PENDING' AND evaluation IS NOT NULL"""
    ).fetchall()
    restored = []
    unavailable = []
    for job_key, raw_evaluation in rows:
        try:
            evaluation = json.loads(raw_evaluation)
        except (TypeError, json.JSONDecodeError, RecursionError):
            unavailable.append(job_key)
            continue
        if not isinstance(evaluation, dict):
            unavailable.append(job_key)
            continue
        score = evaluation.get("score")
        verdict = evaluation.get("verdict")
        valid_score = (
            isinstance(score, (int, float))
            and not isinstance(score, bool)
            and math.isfinite(score)
        )
        if (
            evaluation.get("info_status") != "INCOMPLETE"
            and verdict in ("APPLY", "MAYBE", "SKIP")
            and (valid_score or verdict == "SKIP")
        ):
            restored.append((f"EVALUATED_{verdict}", job_key))
        else:
            unavailable.append(job_key)

    with db:
        db.executemany(
            """UPDATE jobs SET status = ?, scrape_next_attempt = NULL,
               scrape_last_error = NULL WHERE key = ?""",
            restored,
        )
        db.executemany(
            """UPDATE jobs SET status = 'SCRAPE_UNAVAILABLE',
               scrape_next_attempt = NULL,
               scrape_last_error = 'Legacy evaluation retained; automatic historical backfill disabled.'
               WHERE key = ?""",
            [(job_key,) for job_key in unavailable],
        )
        retire_stale_pending_scrapes(db)


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
        )
        status = "NEW" if evaluation_is_incomplete else f"EVALUATED_{verdict}"
        retry_at = None
        if evaluation_is_incomplete:
            attempts_row = db.execute(
                "SELECT evaluation_attempts FROM jobs WHERE key = ?", (job_key,)
            ).fetchone()
            attempts = attempts_row[0] + 1
            delay_seconds = min(86400, 300 * (2 ** min(attempts - 1, 8)))
            retry_at = (
                datetime.now(timezone.utc).timestamp() + delay_seconds
            )
            retry_at = datetime.fromtimestamp(
                retry_at, timezone.utc
            ).isoformat(timespec="seconds")
        with db:
            db.execute(
                """UPDATE jobs SET status = ?, evaluation = ?,
                   evaluation_attempts = CASE WHEN ? THEN evaluation_attempts + 1 ELSE 0 END,
                   evaluation_last_error = CASE WHEN ? THEN ? ELSE NULL END,
                   evaluation_next_attempt = ?, evaluation_lease_until = NULL
                   WHERE key = ?""",
                (
                    status,
                    payload,
                    int(evaluation_is_incomplete),
                    int(evaluation_is_incomplete),
                    "AI evaluation returned incomplete information."
                    if evaluation_is_incomplete
                    else None,
                    retry_at,
                    job_key,
                ),
            )
            if (
                not evaluation_is_incomplete
                and verdict in ("APPLY", "MAYBE")
                and score_is_valid
            ):
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
            """SELECT id, payload, alert_sent FROM notifications
               WHERE status = 'PENDING'
                 AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                 AND (delivery_lease_until IS NULL OR delivery_lease_until <= ?)
               ORDER BY id""",
            (
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
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
                   SET status = 'SENT', alert_sent = 1,
                       sent_at = ?, last_error = NULL, delivery_lease_until = NULL
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
                   SET status = 'SENT', alert_sent = 1,
                       sent_at = ?, last_error = NULL, delivery_lease_until = NULL
                   WHERE id = ? AND status = 'PENDING'""",
                (now, notification_id),
            )
    finally:
        db.close()


def mark_notification_failed(notification_id, error, path=DB_PATH):
    db = connect(path)
    try:
        row = db.execute(
            "SELECT attempts FROM notifications WHERE id = ? AND status = 'PENDING'",
            (notification_id,),
        ).fetchone()
        if row is None:
            return
        attempts = row[0] + 1
        delay_seconds = min(86400, 60 * (2 ** min(attempts - 1, 10)))
        next_attempt = (
            datetime.now(timezone.utc).timestamp() + delay_seconds
        )
        next_attempt = datetime.fromtimestamp(
            next_attempt, timezone.utc
        ).isoformat(timespec="seconds")
        with db:
            db.execute(
                """UPDATE notifications SET attempts = ?, last_error = ?,
                   next_attempt_at = ?, delivery_lease_until = NULL
                   WHERE id = ? AND status = 'PENDING'""",
                (attempts, str(error)[:500], next_attempt, notification_id),
            )
    finally:
        db.close()


def claim_notification(notification_id, lease_seconds, path=DB_PATH):
    db = connect(path)
    try:
        now = datetime.now(timezone.utc)
        now_text = now.isoformat(timespec="seconds")
        lease_until = datetime.fromtimestamp(
            now.timestamp() + lease_seconds, timezone.utc
        ).isoformat(timespec="seconds")
        with db:
            cursor = db.execute(
                """UPDATE notifications SET delivery_lease_until = ?
                   WHERE id = ? AND status = 'PENDING'
                     AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                     AND (delivery_lease_until IS NULL OR delivery_lease_until <= ?)""",
                (lease_until, notification_id, now_text, now_text),
            )
            return cursor.rowcount == 1
    finally:
        db.close()


def recover_expired_notifications(path=DB_PATH):
    db = connect(path)
    try:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        rows = db.execute(
            """SELECT id, attempts FROM notifications
               WHERE status = 'PENDING' AND delivery_lease_until <= ?""",
            (now,),
        ).fetchall()
        with db:
            for notification_id, attempts_so_far in rows:
                attempts = attempts_so_far + 1
                delay_seconds = min(86400, 60 * (2 ** min(attempts - 1, 10)))
                retry_at = datetime.fromtimestamp(
                    datetime.now(timezone.utc).timestamp() + delay_seconds,
                    timezone.utc,
                ).isoformat(timespec="seconds")
                db.execute(
                    """UPDATE notifications SET attempts = ?, next_attempt_at = ?,
                       last_error = 'Recovered expired delivery lease.',
                       delivery_lease_until = NULL
                       WHERE id = ? AND status = 'PENDING'
                         AND delivery_lease_until <= ?""",
                    (attempts, retry_at, notification_id, now),
                )
    finally:
        db.close()


def recover_expired_evaluations(path=DB_PATH):
    db = connect(path)
    try:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        rows = db.execute(
            """SELECT id, evaluation_attempts FROM jobs
               WHERE status = 'EVALUATING'
                 AND (evaluation_lease_until IS NULL OR evaluation_lease_until <= ?)""",
            (now,),
        ).fetchall()
        with db:
            for short_id, attempts_so_far in rows:
                attempts = attempts_so_far + 1
                delay_seconds = min(86400, 300 * (2 ** min(attempts - 1, 8)))
                retry_at = datetime.fromtimestamp(
                    datetime.now(timezone.utc).timestamp() + delay_seconds,
                    timezone.utc,
                ).isoformat(timespec="seconds")
                db.execute(
                    """UPDATE jobs SET status = 'NEW', evaluation_attempts = ?,
                       evaluation_next_attempt = ?,
                       evaluation_last_error = 'Recovered expired evaluation lease.',
                       evaluation_lease_until = NULL
                       WHERE id = ? AND status = 'EVALUATING'""",
                    (attempts, retry_at, short_id),
                )
    finally:
        db.close()


def pending_evaluation_jobs(path=DB_PATH):
    db = connect(path)
    try:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        return db.execute(
            """SELECT id FROM jobs WHERE status = 'NEW'
               AND (evaluation_next_attempt IS NULL OR evaluation_next_attempt <= ?)
               ORDER BY first_seen, id""",
            (now,),
        ).fetchall()
    finally:
        db.close()


def claim_evaluation(short_id, lease_seconds, path=DB_PATH):
    db = connect(path)
    try:
        now = datetime.now(timezone.utc)
        now_text = now.isoformat(timespec="seconds")
        lease_until = datetime.fromtimestamp(
            now.timestamp() + lease_seconds, timezone.utc
        ).isoformat(timespec="seconds")
        with db:
            cursor = db.execute(
                """UPDATE jobs SET status = 'EVALUATING',
                   evaluation_lease_until = ?
                   WHERE id = ? AND status = 'NEW'
                     AND (evaluation_next_attempt IS NULL OR evaluation_next_attempt <= ?)""",
                (lease_until, short_id, now_text),
            )
            return cursor.rowcount == 1
    finally:
        db.close()


def defer_missing_description(short_id, error, path=DB_PATH):
    """Move a claimed job with no usable description out of AI evaluation."""
    db = connect(path)
    try:
        row = db.execute(
            "SELECT source, url FROM jobs WHERE id = ? AND status = 'EVALUATING'",
            (short_id,),
        ).fetchone()
        if row is None:
            return None
        _, url = row
        parsed_url = urlsplit(url or "")
        is_linkedin_job_url = (
            parsed_url.hostname in ("linkedin.com", "www.linkedin.com")
            and (
                parsed_url.path.startswith("/jobs/view/")
                or parsed_url.path.startswith("/comm/jobs/view/")
            )
        )
        status = "SCRAPE_PENDING" if is_linkedin_job_url else "SCRAPE_REVIEW"
        retry_at = (
            datetime.now(timezone.utc).isoformat(timespec="seconds")
            if is_linkedin_job_url
            else None
        )
        with db:
            db.execute(
                """UPDATE jobs SET status = ?, scrape_next_attempt = ?,
                   scrape_last_error = ?, evaluation_next_attempt = NULL,
                   evaluation_lease_until = NULL
                   WHERE id = ? AND status = 'EVALUATING'""",
                (status, retry_at, str(error)[:500], short_id),
            )
        return status
    finally:
        db.close()


def defer_invalid_job_url(short_id, error, path=DB_PATH):
    """Move a claimed job with a malformed reference to manual input review."""
    db = connect(path)
    try:
        with db:
            cursor = db.execute(
                """UPDATE jobs SET status = 'INPUT_REVIEW',
                   evaluation_last_error = ?, evaluation_next_attempt = NULL,
                   evaluation_lease_until = NULL
                   WHERE id = ? AND status = 'EVALUATING'""",
                (str(error)[:500], short_id),
            )
            return cursor.rowcount == 1
    finally:
        db.close()


def record_ai_usage(
    short_id,
    model,
    prompt_chars,
    response_chars,
    outcome,
    error_type=None,
    path=DB_PATH,
    result_summary=None,
    error_detail=None,
):
    """Record usage, a compact result summary, and bounded failure diagnostics."""
    input_tokens_estimate = (prompt_chars + 3) // 4
    output_tokens_estimate = (response_chars + 3) // 4
    db = connect(path)
    try:
        with db:
            db.execute(
                """INSERT INTO ai_usage
                   (job_id, recorded_at, model, prompt_chars,
                    input_tokens_estimate, response_chars,
                    output_tokens_estimate, outcome, error_type,
                    result_summary, error_detail)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    short_id,
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    str(model)[:100],
                    max(0, int(prompt_chars)),
                    input_tokens_estimate,
                    max(0, int(response_chars)),
                    output_tokens_estimate,
                    str(outcome)[:50],
                    str(error_type)[:100] if error_type else None,
                    str(result_summary)[:2000] if result_summary else None,
                    str(error_detail)[:2000] if error_detail else None,
                ),
            )
    finally:
        db.close()


def mark_evaluation_failed(
    short_id, error, path=DB_PATH, retry_after_seconds=None
):
    db = connect(path)
    try:
        row = db.execute(
            "SELECT evaluation_attempts FROM jobs WHERE id = ? AND status = 'EVALUATING'",
            (short_id,),
        ).fetchone()
        if row is None:
            return
        attempts = row[0] + 1
        delay_seconds = (
            min(86400, 300 * (2 ** min(attempts - 1, 8)))
            if retry_after_seconds is None
            else max(0, int(retry_after_seconds))
        )
        next_attempt = datetime.fromtimestamp(
            datetime.now(timezone.utc).timestamp() + delay_seconds,
            timezone.utc,
        ).isoformat(timespec="seconds")
        with db:
            db.execute(
                """UPDATE jobs SET status = 'NEW', evaluation_attempts = ?,
                   evaluation_last_error = ?, evaluation_next_attempt = ?,
                   evaluation_lease_until = NULL
                   WHERE id = ? AND status = 'EVALUATING'""",
                (attempts, str(error)[:500], next_attempt, short_id),
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


def add_job(db, url, company, title, status="NEW", source=None, application_url=None):
    """Devolve ('new' | 'seen' | 'possible_duplicate', chave)."""
    key = job_key(url)
    if db.execute("SELECT 1 FROM jobs WHERE key=?", (key,)).fetchone():
        return "seen", key
    fp = fingerprint(company, title)
    dup = db.execute("SELECT key FROM jobs WHERE fingerprint=?", (fp,)).fetchone()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    db.execute(
        """INSERT INTO jobs
           (key, id, source, url, company, title, fingerprint, first_seen,
            status, application_url)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            key, short_id(key), source or key.split(":")[0], url, company,
            title, fp, now, status, application_url,
        ))
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
