#!/usr/bin/env python3
import imaplib
import email
import json
import time
import urllib.request
import re
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from bs4 import BeautifulSoup
from pathlib import Path
from dotenv import load_dotenv
import store
import url_policy
import remot_parser

load_dotenv()

BASE_DIR = Path(__file__).parent
INBOX_DIR = BASE_DIR / "inbox"
DB_PATH = BASE_DIR / "jobs.db"
MAX_HTML_BYTES = 5 * 1024 * 1024

IMAP_SERVER = os.getenv("IMAP_SERVER", "imap.gmail.com")
EMAIL_USER = os.getenv("EMAIL_USER", "")
EMAIL_PASS = os.getenv("EMAIL_PASS", "")
IMAP_TIMEOUT_SECONDS = int(os.getenv("IMAP_TIMEOUT_SECONDS", "120"))
if IMAP_TIMEOUT_SECONDS <= 0:
    raise ValueError("IMAP_TIMEOUT_SECONDS precisa ser maior que zero.")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7"
}


class ScrapeError(RuntimeError):
    def __init__(self, message, retry_after=0, permanent=False):
        super().__init__(message)
        self.retry_after = retry_after
        self.permanent = permanent


def _job_posting_description(soup):
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or script.get_text())
        except (json.JSONDecodeError, TypeError, RecursionError):
            continue
        pending = [data]
        while pending:
            item = pending.pop()
            if isinstance(item, list):
                pending.extend(item)
                continue
            if not isinstance(item, dict):
                continue
            job_type = item.get("@type", [])
            if isinstance(job_type, str):
                job_type = [job_type]
            if "JobPosting" in job_type and isinstance(item.get("description"), str):
                return BeautifulSoup(
                    item["description"], "html.parser"
                ).get_text(separator="\n").strip()
            pending.extend(item.values())
    return ""


def _retry_after_seconds(value):
    if not value:
        return 0
    try:
        return max(0, int(value))
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return max(
                0,
                int((retry_at - datetime.now(timezone.utc)).total_seconds()),
            )
        except (TypeError, ValueError, OverflowError):
            return 0


def fetch_public_job_text(url: str) -> dict:
    try:
        url_policy.validate_allowed_url(url, ("linkedin.com",))
    except ValueError as error:
        raise ScrapeError(str(error)) from None
    req = urllib.request.Request(url, headers=HEADERS)
    for attempt in range(3):
        try:
            with url_policy.open_allowed_url(
                req, timeout=20, allowed_domains=("linkedin.com",)
            ) as response:
                html = response.read(MAX_HTML_BYTES + 1).decode(
                    "utf-8", errors="replace"
                )
                if len(html.encode("utf-8")) > MAX_HTML_BYTES:
                    raise ScrapeError("A página excedeu o tamanho máximo permitido.")
            break
        except ValueError as error:
            raise ScrapeError(f"Redirecionamento bloqueado: {error}") from None
        except HTTPError as error:
            retry_after = _retry_after_seconds(
                error.headers.get("Retry-After") if error.headers else None
            )
            if error.code == 429 and attempt < 2:
                if retry_after > 30:
                    raise ScrapeError(
                        "LinkedIn retornou HTTP 429; respeitando Retry-After.",
                        retry_after=retry_after,
                    ) from None
                delay = max(5 * (attempt + 1), retry_after)
                print(f"[!] LinkedIn limitou as requisições; nova tentativa em {delay}s.")
                time.sleep(delay)
                continue
            raise ScrapeError(
                f"LinkedIn retornou HTTP {error.code}.",
                retry_after=retry_after,
                permanent=error.code in (404, 410),
            ) from None
        except (URLError, TimeoutError, UnicodeError) as error:
            raise ScrapeError(f"Falha de rede ao acessar a vaga: {error}") from None
    else:
        raise ScrapeError("LinkedIn continuou limitando as requisições após 3 tentativas.")

    try:
        soup = BeautifulSoup(html, 'html.parser')

        title_tag = soup.find('h1') or soup.find('h1', class_=re.compile(r'title'))
        title = title_tag.get_text().strip() if title_tag else ""

        company_tag = soup.find('a', class_=re.compile(r'topcard__org-name-link|company'))
        company = company_tag.get_text().strip() if company_tag else ""

        location_tag = soup.select_one(
            ".topcard__flavor--bullet, .jobs-unified-top-card__bullet, "
            "[class*='location']"
        )
        workplace_tag = soup.find(
            attrs={"class": re.compile(r"workplace-type|workplace", re.I)}
        )
        location = location_tag.get_text(" ", strip=True) if location_tag else ""
        workplace_type = workplace_tag.get_text(" ", strip=True) if workplace_tag else ""

        desc_box = soup.select_one(
            ".show-more-less-html__markup, .description__text, "
            "[class*='description__text'], [class*='description']"
        )
        description_text = (
            desc_box.get_text(separator="\n").strip() if desc_box else ""
        )
        if len(description_text) < 100:
            description_text = _job_posting_description(soup)
        if len(description_text) < 100 or len(description_text.split()) < 15:
            raise ScrapeError("A descrição extraída é curta demais para avaliação.")
        if re.search(
            r"captcha|security verification|unusual activity|access denied|"
            r"sign in to view|join linkedin",
            description_text,
            re.I,
        ):
            raise ScrapeError("A resposta parece ser uma página de bloqueio, não uma descrição de vaga.")

        return {
            "title": title,
            "company": company,
            "description": description_text,
            "location": location,
            "workplace_type": workplace_type,
        }
    except (AttributeError, TypeError, ValueError) as error:
        raise ScrapeError(f"Falha ao interpretar a página pública: {error}") from None

def _message_html(msg):
    html_parts = []
    text_parts = []
    for part in msg.walk() if msg.is_multipart() else [msg]:
        content_type = part.get_content_type()
        if content_type not in ("text/html", "text/plain"):
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        charset = part.get_content_charset() or "utf-8"
        decoded = payload.decode(charset, errors="replace")
        (html_parts if content_type == "text/html" else text_parts).append(decoded)
    return "\n".join(html_parts or text_parts)


def fetch_linkedin_emails():
    print("=== BUSCANDO ALERTAS DO LINKEDIN E DO REMOT NO E-MAIL ===")
    mail = None
    conn = store.connect(DB_PATH)
    try:
        queue_legacy_incomplete_linkedin_files(conn)
        if not EMAIL_USER or not EMAIL_PASS:
            if EMAIL_USER or EMAIL_PASS:
                raise RuntimeError("EMAIL_USER e EMAIL_PASS precisam ser configurados juntos.")
            print("[SKIPPED] Coletor de e-mail desativado: credenciais IMAP não configuradas.")
            process_pending_scrapes(conn)
            return 0

        mail = imaplib.IMAP4_SSL(
            IMAP_SERVER, timeout=IMAP_TIMEOUT_SECONDS
        )
        mail.login(EMAIL_USER, EMAIL_PASS)
        status, _ = mail.select("inbox")
        if status != "OK":
            raise RuntimeError("Não foi possível selecionar a caixa de entrada IMAP.")

        email_ids = []
        search_errors = []
        for criteria in (
            ("UNSEEN", "FROM", "linkedin.com"),
            ("UNSEEN", "BODY", remot_parser.DOMAIN),
        ):
            status, messages = mail.search(None, *criteria)
            if status != "OK":
                search_errors.append(
                    f"A busca IMAP por alertas ({' '.join(criteria[1:])}) falhou."
                )
                continue
            if messages:
                email_ids.extend(messages[0].split())
        email_ids = list(dict.fromkeys(email_ids))

        if email_ids:
            print(f"Encontrados {len(email_ids)} e-mails não lidos para inspeção.")
        else:
            print("Nenhum e-mail de alerta não lido encontrado.")

        processed = 0
        email_errors = search_errors
        for e_id in email_ids:
            email_id = e_id.decode(errors="replace")
            try:
                fetch_status, msg_data = mail.fetch(e_id, "(BODY.PEEK[])")
                if fetch_status != "OK":
                    email_errors.append(f"Falha ao buscar o e-mail IMAP {email_id}.")
                    continue
                parsed = 0
                for response_part in msg_data:
                    if isinstance(response_part, tuple):
                        msg = email.message_from_bytes(response_part[1])
                        body = _message_html(msg)
                        if body:
                            parsed += bool(parse_linkedin_jobs(body, conn))
                            parsed += persist_remot_jobs(body, conn)
                if not parsed:
                    email_errors.append(
                        f"O e-mail IMAP {email_id} não contém anúncio suportado; "
                        "permanece não lido."
                    )
                    continue
                conn.commit()
                flag_status, _ = mail.store(e_id, "+FLAGS", "\\Seen")
                if flag_status != "OK":
                    email_errors.append(
                        f"Não foi possível marcar o e-mail {email_id} como lido."
                    )
                    continue
                processed += 1
            except (
                imaplib.IMAP4.error,
                LookupError,
                UnicodeError,
                ValueError,
                sqlite3.Error,
            ) as error:
                conn.rollback()
                email_errors.append(
                    f"Falha ao processar o e-mail IMAP {email_id}: {error}"
                )

        if processed:
            print(f"[+] Links de {processed} e-mails persistidos; e-mails marcados como lidos.")
        process_pending_scrapes(conn)
        if email_errors:
            for error in email_errors:
                print(f"[!] {error}")
            raise RuntimeError(
                f"{len(email_errors)} e-mail(s) não puderam ser processados; "
                "permanecem não lidos para nova tentativa."
            )
        return processed
    finally:
        conn.close()
        if mail is not None:
            try:
                mail.logout()
            except imaplib.IMAP4.error:
                pass


def persist_remot_jobs(body, db):
    """Persist official Remōt links without evaluating title-only alerts."""
    jobs = remot_parser.parse_remot_jobs(body)
    for job in jobs:
        result, key = store.add_job(
            db,
            job["url"],
            job["company"],
            job["title"],
            status="DESCRIPTION_REQUIRED",
            source=remot_parser.SOURCE_NAME,
            application_url=job["application_url"],
        )
        if result == "seen":
            db.execute(
                """UPDATE jobs SET application_url = COALESCE(application_url, ?)
                   WHERE key = ?""",
                (job["application_url"], key),
            )
        else:
            db.execute(
                """UPDATE jobs SET scrape_last_error = ?
                   WHERE key = ?""",
                (
                    "Alerta Remōt contém título e link, mas não descrição suficiente; "
                    "adicione o texto da vaga manualmente.",
                    key,
                ),
            )
        print(
            f"[!] Remot: anúncio persistido sem descrição suficiente "
            f"({job['title']}); requer ingestão manual antes da avaliação."
        )
    return len(jobs)


def parse_linkedin_jobs(html_content, db):
    soup = BeautifulSoup(html_content, 'html.parser')
    links = soup.find_all('a', href=True)
    count = 0
    found = 0

    for link in links:
        url = link['href']

        if "linkedin.com/jobs/view" in url or "linkedin.com/comm/jobs/view" in url:
            try:
                url_policy.validate_allowed_url(url, ("linkedin.com",))
            except ValueError:
                print(f"[!] Link LinkedIn recusado por política de URL: {url[:200]}")
                continue
            parts = url_policy.urlsplit(url)
            if not re.fullmatch(r"/(?:comm/)?jobs/view/[^/]+/?", parts.path):
                continue
            found += 1
            clean_url = parts._replace(query="", fragment="").geturl()
            clean_url = clean_url.replace("/comm/", "/")
            key = store.job_key(clean_url)
            exists = db.execute(
                "SELECT id, status FROM jobs WHERE key = ? OR url = ? LIMIT 1",
                (key, clean_url),
            ).fetchone()
            if not exists:
                company = "Empresa via LinkedIn"
                title = link.get_text().strip() or "Vaga LinkedIn"
                result, key = store.add_job(
                    db,
                    clean_url,
                    company,
                    title,
                    status="SCRAPE_PENDING",
                )
                if result == "seen":
                    continue
                db.execute(
                    """UPDATE jobs SET scrape_next_attempt = ?
                       WHERE key = ?""",
                    (datetime.now(timezone.utc).isoformat(timespec="seconds"), key),
                )
                db.commit()
                count += 1

    if count > 0:
        print(f"[+] {count} novos links persistidos para extração/repetição.")
    return found


def queue_legacy_incomplete_linkedin_files(db):
    rows = db.execute(
        "SELECT key, id FROM jobs WHERE lower(source) = 'linkedin' AND status = 'NEW'"
    ).fetchall()
    retry_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for key, short_id in rows:
        file_path = INBOX_DIR / f"{short_id}.txt"
        if not file_path.exists():
            db.execute(
                """UPDATE jobs SET status = 'SCRAPE_PENDING',
                   scrape_next_attempt = ?, scrape_last_error = ?
                   WHERE key = ?""",
                (
                    retry_at,
                    "LinkedIn inbox file is missing; re-scrape queued.",
                    key,
                ),
            )
            print(f"[!] Arquivo ausente da vaga {short_id}; scraping reencaminhado.")
            continue
        job_text = file_path.read_text(encoding="utf-8")
        marker = "JOB TEXT:"
        description = job_text.split(marker, 1)[1] if marker in job_text else ""
        description = description.split("\nPUBLIC PAGE METADATA:", 1)[0].strip()
        if len(description) < 100:
            db.execute(
                """UPDATE jobs SET status = 'SCRAPE_PENDING',
                   scrape_next_attempt = ?, scrape_last_error = ?
                   WHERE key = ?""",
                (retry_at, "Legacy inbox file has no usable job description.", key),
            )
            print(f"[!] Vaga {short_id} sem descrição útil movida para retentativa de scraping.")
    db.commit()


def process_pending_scrapes(db):
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    expired = store.retire_stale_pending_scrapes(db)
    if expired:
        print(
            f"[!] {expired} vaga(s) antigas encerradas sem novo scraping "
            f"(mais de {store.SCRAPE_MAX_AGE_DAYS} dias ou sem data válida)."
        )
    rows = db.execute(
        """SELECT key, id, url, scrape_attempts FROM jobs
           WHERE status = 'SCRAPE_PENDING'
             AND (scrape_next_attempt IS NULL OR scrape_next_attempt <= ?)
           ORDER BY first_seen""",
        (now,),
    ).fetchall()
    if rows:
        print(f"[+] Tentando extrair texto de {len(rows)} vaga(s) pendente(s), sem limite por execução.")

    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    for key, short_id, url, attempts in rows:
        print(f"[+] Extraindo descrição pública: {url}")
        try:
            job_data = fetch_public_job_text(url)
            if not job_data:
                raise ScrapeError("O extrator não retornou dados da vaga.")

            title = job_data.get("title") or "Vaga LinkedIn"
            company = job_data.get("company") or "Empresa via LinkedIn"
            description = job_data["description"]
            if len(description.strip()) < 100:
                raise ScrapeError("A descrição extraída é curta demais para avaliação.")

            file_content = f"""SOURCE: LinkedIn Email Alert
URL: {url}
COMPANY: {company}
TITLE: {title}

JOB TEXT:
{description}

PUBLIC PAGE METADATA:
LOCATION: {job_data.get("location") or "Not found"}
WORKPLACE TYPE: {job_data.get("workplace_type") or "Not found"}
"""
            file_path = store.write_inbox_file(INBOX_DIR, short_id, file_content)
            db.execute(
                """UPDATE jobs SET company = ?, title = ?, fingerprint = ?,
                   status = 'NEW',
                   scrape_attempts = 0, scrape_last_error = NULL,
                   scrape_next_attempt = NULL WHERE key = ?""",
                (company, title, store.fingerprint(company, title), key),
            )
            db.commit()
            print(f"[+] Texto salvo e pronto para avaliação: {file_path}")
            time.sleep(2)
        except (ScrapeError, OSError, sqlite3.Error) as error:
            if isinstance(error, ScrapeError) and error.permanent:
                db.execute(
                    """UPDATE jobs SET status = 'SCRAPE_UNAVAILABLE',
                       scrape_attempts = scrape_attempts + 1,
                       scrape_last_error = ?, scrape_next_attempt = NULL
                       WHERE key = ? AND status = 'SCRAPE_PENDING'""",
                    (str(error)[:500], key),
                )
                db.commit()
                print(f"[!] Vaga indisponível permanentemente; retentativas encerradas: {error}")
                continue
            next_attempts = attempts + 1
            delay_seconds = max(
                min(86400, 300 * (2 ** min(attempts, 8))),
                getattr(error, "retry_after", 0),
            )
            next_attempt = (
                datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
            ).isoformat(timespec="seconds")
            db.execute(
                """UPDATE jobs SET scrape_attempts = ?,
                   scrape_last_error = ?, scrape_next_attempt = ?
                   WHERE key = ? AND status = 'SCRAPE_PENDING'""",
                (next_attempts, str(error)[:500], next_attempt, key),
            )
            db.commit()
            print(
                f"[!] Extração pendente ({next_attempts} tentativa(s)); "
                f"nova tentativa após {delay_seconds}s: {error}"
            )

if __name__ == "__main__":
    fetch_linkedin_emails()
