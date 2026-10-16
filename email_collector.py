#!/usr/bin/env python3
import imaplib
import email
import hashlib
import sqlite3
import time
import urllib.request
import re
import os
from datetime import datetime
from bs4 import BeautifulSoup
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent
INBOX_DIR = BASE_DIR / "inbox"
DB_PATH = BASE_DIR / "jobs.db"

IMAP_SERVER = os.getenv("IMAP_SERVER", "imap.gmail.com")
EMAIL_USER = os.getenv("EMAIL_USER", "")
EMAIL_PASS = os.getenv("EMAIL_PASS", "")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7"
}

def fetch_public_job_text(url: str) -> dict:
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=10) as response:
            html = response.read().decode('utf-8', errors='ignore')
            
        soup = BeautifulSoup(html, 'html.parser')

        title_tag = soup.find('h1') or soup.find('h1', class_=re.compile(r'title'))
        title = title_tag.get_text().strip() if title_tag else "Vaga LinkedIn"

        company_tag = soup.find('a', class_=re.compile(r'topcard__org-name-link|company'))
        company = company_tag.get_text().strip() if company_tag else "Empresa via LinkedIn"

        desc_box = (
            soup.find('div', class_=re.compile(r'show-more-less-html__markup|description__text'))
            or soup.find('section', class_=re.compile(r'description'))
        )
        
        if desc_box:
            description_text = desc_box.get_text(separator="\n").strip()
        else:
            description_text = soup.get_text(separator="\n").strip()[:3000]

        return {
            "title": title,
            "company": company,
            "description": description_text
        }
    except Exception as e:
        print(f"[!] Não foi possível extrair HTML público da URL {url}: {e}")
        return None

def fetch_linkedin_emails():
    print("=== BUSCANDO ALERTAS DO LINKEDIN NO E-MAIL ===")
    
    if not EMAIL_USER or not EMAIL_PASS:
        print("[!] Configure EMAIL_USER e EMAIL_PASS nas variáveis de ambiente (.env)")
        return

    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(EMAIL_USER, EMAIL_PASS)
        mail.select("inbox")

        status, messages = mail.search(None, '(FROM "linkedin.com")')
        email_ids = messages[0].split()

        if not email_ids:
            print("Nenhum e-mail do LinkedIn encontrado.")
            return

        print(f"Encontrados {len(email_ids)} e-mails de alerta do LinkedIn.")

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        for e_id in email_ids[-5:]:
            _, msg_data = mail.fetch(e_id, '(RFC822)')
            for response_part in msg_data:
                if isinstance(response_part, tuple):
                    msg = email.message_from_bytes(response_part[1])
                    
                    html_content = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            if part.get_content_type() == "text/html":
                                html_content = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                    else:
                        html_content = msg.get_payload(decode=True).decode('utf-8', errors='ignore')

                    if html_content:
                        parse_linkedin_jobs(html_content, cursor)

        conn.commit()
        conn.close()
        mail.logout()

    except Exception as e:
        print(f"[!] Erro ao conectar no e-mail: {e}")

def parse_linkedin_jobs(html_content, cursor):
    soup = BeautifulSoup(html_content, 'html.parser')
    links = soup.find_all('a', href=True)
    count = 0

    for link in links:
        url = link['href']

        if "linkedin.com/jobs/view" in url or "linkedin.com/comm/jobs/view" in url:
            clean_url = url.split("?")[0].replace("/comm/", "/")

            raw_key = f"linkedin:{clean_url}"
            short_id = hashlib.sha256(raw_key.encode('utf-8')).hexdigest()[:12]

            exists = cursor.execute("SELECT 1 FROM jobs WHERE key = ?", (raw_key,)).fetchone()
            if not exists:
                print(f"[+] Baixando conteúdo público da vaga: {clean_url}...")
                
                job_data = fetch_public_job_text(clean_url)
                
                if job_data and len(job_data["description"]) > 100:
                    title = job_data["title"]
                    company = job_data["company"]
                    description = job_data["description"]
                else:
                    title = link.get_text().strip() or "Vaga LinkedIn"
                    company = "Empresa via LinkedIn"
                    description = f"Link original para consulta: {clean_url}"

                file_content = f"""SOURCE: LinkedIn Email Alert
URL: {clean_url}
COMPANY: {company}
TITLE: {title}
REGION (as listed): Remote / Unspecified

JOB TEXT:
{description}
"""
                file_path = INBOX_DIR / f"{short_id}.txt"
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(file_content)

                fingerprint = hashlib.sha256(f"{company.lower()}:{title.lower()}".encode('utf-8')).hexdigest()
                cursor.execute(
                    "INSERT INTO jobs (key, id, source, url, company, title, fingerprint, first_seen, status, evaluation) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (raw_key, short_id, "LinkedIn", clean_url, company, title, fingerprint, datetime.now().isoformat(), "NEW", None)
                )
                count += 1
                time.sleep(2)

    if count > 0:
        print(f"[+] {count} novas vagas com TEXTO COMPLETO baixadas e enviadas para a inbox.")

if __name__ == "__main__":
    fetch_linkedin_emails()
