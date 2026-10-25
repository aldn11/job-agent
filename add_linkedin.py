#!/usr/bin/env python3
import sys
import hashlib
import subprocess
from datetime import datetime
from pathlib import Path
import store
import url_policy

BASE_DIR = Path(__file__).parent
VENV_PYTHON = Path(sys.executable)
INBOX_DIR = BASE_DIR / "inbox"
WRAPPER_SCRIPT = BASE_DIR / "wrapper.py"
DB_PATH = BASE_DIR / "jobs.db"

def add_custom_job():
    print("=== INGESTÃO MANUAL DE VAGA (LinkedIn / Outros) ===")
    company = input("Empresa: ").strip()
    title = input("Título do Cargo: ").strip()
    url = input("URL da vaga (opcional): ").strip()
    if url:
        try:
            url_policy.validate_https_url(url)
        except ValueError as error:
            print(f"Erro: {error}")
            return
    
    print("\nCole o TEXTO DA VAGA abaixo. Quando terminar, pressione CTRL+D em uma linha vazia:")
    job_text = sys.stdin.read().strip()

    if not job_text:
        print("Erro: Nenhum texto fornecido.")
        return

    conn = store.connect(DB_PATH)
    if url:
        result, key = store.add_job(
            conn,
            url,
            company,
            title,
            source="LinkedIn",
        )
        if result == "seen":
            conn.close()
            print("[!] Esta URL já está cadastrada; nenhuma avaliação foi iniciada.")
            return
        row = conn.execute("SELECT id FROM jobs WHERE key = ?", (key,)).fetchone()
        short_id = row[0]
    else:
        content_hash = hashlib.sha256(
            f"{store.fingerprint(company, title)}\n{job_text}".encode("utf-8")
        ).hexdigest()
        key = f"manual:{content_hash}"
        short_id = store.short_id(key)
        exists = conn.execute(
            "SELECT 1 FROM jobs WHERE key = ?", (key,)
        ).fetchone()
        if exists:
            conn.close()
            print(
                "[!] Esta vaga sem URL já foi cadastrada com o mesmo texto; "
                "nenhuma avaliação foi iniciada."
            )
            return

    file_content = f"""SOURCE: LinkedIn (Manual)
URL: {url}
COMPANY: {company}
TITLE: {title}
REGION (as listed): Remote / Unspecified

JOB TEXT:
{job_text}
"""
    file_path = store.write_inbox_file(INBOX_DIR, short_id, file_content)

    if not url:
        conn.execute(
            """INSERT INTO jobs
               (key, id, source, url, company, title, fingerprint, first_seen,
                status, evaluation)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'NEW', NULL)""",
            (
                key,
                short_id,
                "LinkedIn",
                "",
                company,
                title,
                store.fingerprint(company, title),
                datetime.now().astimezone().isoformat(),
            ),
        )
        conn.commit()
    conn.close()

    print(f"\n[+] Vaga salva em {file_path}. Iniciando avaliação do Vargas...")
    subprocess.run([str(VENV_PYTHON), str(WRAPPER_SCRIPT), str(file_path)], check=True)

if __name__ == "__main__":
    add_custom_job()
