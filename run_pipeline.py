#!/usr/bin/env python3
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent
VENV_PYTHON = os.getenv("PYTHON_EXECUTABLE", str(sys.executable))
INBOX_DIR = BASE_DIR / "inbox"
WRAPPER_SCRIPT = BASE_DIR / "wrapper.py"
DB_PATH = BASE_DIR / "jobs.db"

def run_pipeline():
    print("=== 1. EXECUTANDO COLETOR RSS (We Work Remotely) ===")
    try:
        subprocess.run([VENV_PYTHON, str(BASE_DIR / "collector.py")], check=True)
    except Exception as e:
        print(f"[!] Aviso no coletor RSS: {e}")

    print("\n=== 2. EXECUTANDO COLETOR DE E-MAIL (LinkedIn Alerts) ===")
    try:
        subprocess.run([VENV_PYTHON, str(BASE_DIR / "email_collector.py")], check=True)
    except Exception as e:
        print(f"[!] Aviso no coletor de e-mail: {e}")

    print("\n=== 3. PROCESSANDO VAGAS PENDENTES NA CAIXA DE ENTRADA ===")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    rows = cursor.execute("SELECT id FROM jobs WHERE status = ?", ("NEW",)).fetchall()
    conn.close()

    if not rows:
        print("Nenhuma vaga nova pendente para avaliação.")
        return

    for (short_id,) in rows:
        file_path = INBOX_DIR / f"{short_id}.txt"
        if file_path.exists():
            print(f"\n[+] Avaliando vaga ID: {short_id}...")
            try:
                subprocess.run([VENV_PYTHON, str(WRAPPER_SCRIPT), str(file_path)], check=True)
            except subprocess.CalledProcessError as e:
                print(f"[!] Erro ao avaliar {short_id}: {e}")
        else:
            print(f"[!] Arquivo de entrada não encontrado: {file_path}")

    print("\n=== PIPELINE CONCLUÍDO COM SUCESSO ===")

if __name__ == "__main__":
    run_pipeline()
