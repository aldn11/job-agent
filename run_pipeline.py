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
HERMES_TIMEOUT_SECONDS = int(os.getenv("HERMES_TIMEOUT_SECONDS", "1800"))
if HERMES_TIMEOUT_SECONDS <= 0:
    raise ValueError("HERMES_TIMEOUT_SECONDS precisa ser maior que zero.")

def run_stage(label, command, failures, timeout=None):
    print(f"\n=== {label} ===")
    try:
        subprocess.run(command, check=True, timeout=timeout)
        return True
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        print(f"[!] Falha em {label}: {error}")
        failures.append(label)
        return False


def run_pipeline():
    failures = []
    store.connect(DB_PATH).close()
    store.recover_expired_evaluations(DB_PATH)

    run_stage(
        "1. EXECUTANDO COLETOR RSS (We Work Remotely)",
        [VENV_PYTHON, str(BASE_DIR / "collector.py")],
        failures,
    )
    run_stage(
        "2. EXECUTANDO COLETOR RSS (Jobspresso)",
        [VENV_PYTHON, str(BASE_DIR / "jobspresso_collector.py")],
        failures,
    )
    run_stage(
        "3. EXECUTANDO COLETOR RSS (Remotive)",
        [VENV_PYTHON, str(BASE_DIR / "remotive_collector.py")],
        failures,
    )
    run_stage(
        "4. EXECUTANDO COLETOR DE CARREIRAS (BairesDev)",
        [VENV_PYTHON, str(BASE_DIR / "bairesdev_collector.py")],
        failures,
    )
    run_stage(
        "5. EXECUTANDO COLETOR DE E-MAIL (LinkedIn e Remot)",
        [VENV_PYTHON, str(BASE_DIR / "email_collector.py")],
        failures,
    )

    print("\n=== 6. PROCESSANDO VAGAS PENDENTES NA CAIXA DE ENTRADA ===")
    rows = store.pending_evaluation_jobs(DB_PATH)

    for (short_id,) in rows:
        lease_seconds = HERMES_TIMEOUT_SECONDS + 600
        if not store.claim_evaluation(short_id, lease_seconds, DB_PATH):
            continue
        file_path = INBOX_DIR / f"{short_id}.txt"
        if file_path.exists():
            print(f"\n[+] Avaliando vaga ID: {short_id}...")
            succeeded = run_stage(
                f"Avaliação da vaga {short_id}",
                [VENV_PYTHON, str(WRAPPER_SCRIPT), str(file_path), "--defer-notifications"],
                failures,
                timeout=HERMES_TIMEOUT_SECONDS + 120,
            )
            if not succeeded:
                store.mark_evaluation_failed(
                    short_id,
                    "O processo de avaliação falhou ou excedeu o timeout.",
                    DB_PATH,
                )
        else:
            print(f"[!] Arquivo de entrada não encontrado: {file_path}")
            failures.append(f"Arquivo ausente para a vaga {short_id}")
            store.mark_evaluation_failed(
                short_id, f"Arquivo de entrada ausente: {file_path}", DB_PATH
            )

    db = sqlite3.connect(DB_PATH)
    try:
        pending_evaluations = db.execute(
            "SELECT COUNT(*) FROM jobs WHERE status IN ('NEW', 'EVALUATING')"
        ).fetchone()[0]
    finally:
        db.close()
    if pending_evaluations:
        print(
            f"[!] {pending_evaluations} vaga(s) continuam na fila de avaliação "
            "e serão tentadas novamente conforme o backoff."
        )
        failures.append("Vagas pendentes de avaliação")

    print("\n=== 7. ENVIANDO NOTIFICAÇÕES PENDENTES ===")
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
