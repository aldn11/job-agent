#!/usr/bin/env python3
import sqlite3
import json
from pathlib import Path

DB_PATH = Path(__file__).parent / "jobs.db"

def list_apply_jobs():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    rows = cursor.execute(
        "SELECT id, company, title, url, evaluation FROM jobs WHERE status = 'EVALUATED_APPLY'"
    ).fetchall()
    conn.close()

    print("==========================================================================")
    print(f"            VAGAS RECOMENDADAS PARA CANDIDATURA (TOTAL: {len(rows)})")
    print("==========================================================================\n")

    for short_id, company, title, url, eval_json in rows:
        data = json.loads(eval_json) if eval_json else {}
        score = data.get("score", "N/A")
        reasons = data.get("reasons_pt", [])
        risks = data.get("interview_risks_pt", [])

        print(f"📌 [{score} pts] {title} @ {company}")
        print(f"   🔗 Link: {url}")
        if reasons:
            print(f"   ✅ Motivo: {reasons[0]}")
        if risks:
            print(f"   ⚠️ Alerta: {risks[0]}")
        print("-" * 74)

if __name__ == "__main__":
    list_apply_jobs()
