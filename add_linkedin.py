#!/usr/bin/env python3
import sys
import hashlib
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent
VENV_PYTHON = BASE_DIR / "venv" / "bin" / "python"
INBOX_DIR = BASE_DIR / "inbox"
WRAPPER_SCRIPT = BASE_DIR / "wrapper.py"
DB_PATH = BASE_DIR / "jobs.db"

def add_custom_job():
    print("=== INGESTÃO MANUAL DE VAGA (LinkedIn / Outros) ===")
    company = input("Empresa: ").strip()
    title = input("Título do Cargo: ").strip()
    url = input("URL da vaga (opcional): ").strip() or "https://linkedin.com"
    
    print("\nCole o TEXTO DA VAGA abaixo. Quando terminar, pressione CTRL+D em uma linha vazia:")
    job_text = sys.stdin.read().strip()

    if not job_text:
        print("Erro: Nenhum texto fornecido.")
        return

    raw_key = f"linkedin:{company}:{title}:{datetime.now().isoformat()}"
    short_id = hashlib.sha256(raw_key.encode('utf-8')).hexdigest()[:12]

    file_content = f"""SOURCE: LinkedIn (Manual)
URL: {url}
COMPANY: {company}
TITLE: {title}
REGION (as listed): Remote / Unspecified

JOB TEXT:
{job_text}
"""
    file_path = INBOX_DIR / f"{short_id}.txt"
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(file_content)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    fingerprint = hashlib.sha256(f"{company.lower()}:{title.lower()}".encode('utf-8')).hexdigest()
    
    cursor.execute(
        "INSERT OR IGNORE INTO jobs (key, id, source, url, company, title, fingerprint, first_seen, status, evaluation) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (raw_key, short_id, "LinkedIn", url, company, title, fingerprint, datetime.now().isoformat(), "NEW", None)
    )
    conn.commit()
    conn.close()

    print(f"\n[+] Vaga salva em {file_path}. Iniciando avaliação do Vargas...")
    subprocess.run([str(VENV_PYTHON), str(WRAPPER_SCRIPT), str(file_path)], check=True)

if __name__ == "__main__":
    add_custom_job()
