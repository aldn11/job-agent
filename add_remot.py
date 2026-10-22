#!/usr/bin/env python3
"""Manually add a Remōt job description to the evaluation inbox."""
import sys
from pathlib import Path

import remot_parser
import store


BASE_DIR = Path(__file__).parent
INBOX_DIR = BASE_DIR / "inbox"
DB_PATH = BASE_DIR / "jobs.db"
MIN_DESCRIPTION_LENGTH = 100
MIN_DESCRIPTION_WORDS = 15


def add_manual_remot_job(url, company, title, description, inbox_dir=None, db_path=None):
    canonical_url = remot_parser.canonical_job_url(url)
    company = company.strip() or "Empresa via Remōt"
    title = title.strip()
    description = description.strip()
    if not title:
        raise ValueError("O título da vaga é obrigatório.")
    if (
        len(description) < MIN_DESCRIPTION_LENGTH
        or len(description.split()) < MIN_DESCRIPTION_WORDS
    ):
        raise ValueError(
            "A descrição precisa ter ao menos 100 caracteres e 15 palavras."
        )

    inbox_dir = Path(inbox_dir or INBOX_DIR)
    db = store.connect(db_path or DB_PATH)
    try:
        key = store.job_key(canonical_url)
        existing = db.execute(
            "SELECT id, status, application_url FROM jobs WHERE key = ?", (key,)
        ).fetchone()
        if existing and existing[1] != "DESCRIPTION_REQUIRED":
            raise ValueError(
                "Este anúncio já existe e não está aguardando descrição manual."
            )

        short_id = existing[0] if existing else store.short_id(key)
        application_url = existing[2] if existing else None
        file_content = f"""SOURCE: Remōt Email Alert
URL: {canonical_url}
""" + (
            f"APPLICATION URL: {application_url}\n" if application_url else ""
        ) + f"""COMPANY: {company}
TITLE: {title}

JOB TEXT:
{description}
"""
        file_path = store.write_inbox_file(inbox_dir, short_id, file_content)

        if existing:
            cursor = db.execute(
                """UPDATE jobs SET company = ?, title = ?, fingerprint = ?,
                   status = 'NEW', scrape_last_error = NULL
                   WHERE key = ? AND status = 'DESCRIPTION_REQUIRED'""",
                (company, title, store.fingerprint(company, title), key),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("O registro Remōt mudou durante a ingestão manual.")
            db.commit()
        else:
            store.add_job(
                db,
                canonical_url,
                company,
                title,
                status="NEW",
                source=remot_parser.SOURCE_NAME,
            )
        return file_path
    finally:
        db.close()


def main():
    print("=== INGESTAO MANUAL DE VAGA DO REMOT ===")
    url = input("URL do anúncio no Remōt: ").strip()
    company = input("Empresa (opcional): ").strip()
    title = input("Título da vaga: ").strip()
    print("\nCole a descrição completa da vaga e finalize com CTRL+Z e Enter:")
    description = sys.stdin.read()
    try:
        path = add_manual_remot_job(url, company, title, description)
    except ValueError as error:
        print(f"Erro: {error}")
        return
    print(f"\n[+] Vaga adicionada à caixa de entrada: {path}")


if __name__ == "__main__":
    main()
