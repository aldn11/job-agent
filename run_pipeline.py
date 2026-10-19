#!/usr/bin/env python3
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from dotenv import load_dotenv
import store

load_dotenv()

BASE_DIR = Path(__file__).parent
VENV_PYTHON = os.getenv("PYTHON_EXECUTABLE", str(sys.executable))
INBOX_DIR = BASE_DIR / "inbox"
WRAPPER_SCRIPT = BASE_DIR / "wrapper.py"
DB_PATH = BASE_DIR / "jobs.db"

def run_stage(label, command, failures):
    print(f"\n=== {label} ===")
    try:
        subprocess.run(command, check=True)
        return True
    except (OSError, subprocess.CalledProcessError) as error:
        print(f"[!] Falha em {label}: {error}")
        failures.append(label)
        return False


def run_pipeline():
    failures = []
    store.connect(DB_PATH).close()

    run_stage(
        "1. EXECUTANDO COLETOR RSS (We Work Remotely)",
        [VENV_PYTHON, str(BASE_DIR / "collector.py")],
        failures,
    )
    run_stage(
        "2. EXECUTANDO COLETOR DE E-MAIL (LinkedIn Alerts)",
        [VENV_PYTHON, str(BASE_DIR / "email_collector.py")],
        failures,
    )

    print("\n=== 3. PROCESSANDO VAGAS PENDENTES NA CAIXA DE ENTRADA ===")
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(
            "SELECT id FROM jobs WHERE status = ?", ("NEW",)
        ).fetchall()
    finally:
        conn.close()

    for (short_id,) in rows:
        file_path = INBOX_DIR / f"{short_id}.txt"
        if file_path.exists():
            print(f"\n[+] Avaliando vaga ID: {short_id}...")
            run_stage(
                f"Avaliação da vaga {short_id}",
                [VENV_PYTHON, str(WRAPPER_SCRIPT), str(file_path), "--defer-notifications"],
                failures,
            )
        else:
            print(f"[!] Arquivo de entrada não encontrado: {file_path}")
            failures.append(f"Arquivo ausente para a vaga {short_id}")

    print("\n=== 4. ENVIANDO NOTIFICAÇÕES PENDENTES ===")
    from telegram_notify import deliver_pending_notifications
    if deliver_pending_notifications(DB_PATH):
        failures.append("Entrega de notificações Telegram")

    counts = store.notification_counts(DB_PATH)
    pending = counts.get("PENDING", 0)
    if pending:
        print(f"[!] Permanecem {pending} notificação(ões) pendente(s) no SQLite.")
        if "Entrega de notificações Telegram" not in failures:
            failures.append("Notificações Telegram ainda pendentes")

    if failures:
        print("\n=== PIPELINE CONCLUÍDO COM FALHAS ===")
        print("Etapas com falha: " + "; ".join(failures))
        return 1

    print("\n=== PIPELINE CONCLUÍDO COM SUCESSO ===")
    return 0

if __name__ == "__main__":
    sys.exit(run_pipeline())
