#!/usr/bin/env python3
import os
import sys
import json
import urllib.request
import urllib.parse
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

def send_telegram_alert(evaluation_data: dict):
    token = os.getenv("TELEGRAM_TOKEN", TELEGRAM_TOKEN)
    chat_id = os.getenv("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID)

    if not token:
        print("[!] Erro: TELEGRAM_TOKEN não configurado nas variáveis de ambiente.")
        return False

    if not chat_id:
        print("[!] Erro: TELEGRAM_CHAT_ID não configurado nas variáveis de ambiente.")
        return False

    title = evaluation_data.get("title", "Cargo não especificado")
    company = evaluation_data.get("company", "Empresa não especificada")
    url = evaluation_data.get("url", "#")
    score = evaluation_data.get("score", "N/A")
    source = evaluation_data.get("source", "Desconhecida")
    
    reasons = evaluation_data.get("reasons_pt", [])
    risks = evaluation_data.get("interview_risks_pt", [])

    reason_text = f"\n• <b>Motivo:</b> {reasons[0]}" if reasons else ""
    risk_text = f"\n• <b>Alerta:</b> {risks[0]}" if risks else ""

    message = (
        f"🎯 <b>NOVA VAGA ENCONTRADA ({score} pts)</b>\n\n"
        f"🏢 <b>Empresa:</b> {company}\n"
        f"💼 <b>Cargo:</b> {title}\n"
        f"📍 <b>Fonte:</b> {source}\n"
        f"{reason_text}"
        f"{risk_text}\n\n"
        f"🔗 <a href='{url}'>Ver Anúncio da Vaga</a>"
    )

    api_url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }

    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(api_url, data=data)

    try:
        with urllib.request.urlopen(req) as response:
            if response.status == 200:
                print("[+] Notificação enviada com sucesso para o Telegram!")
                return True
    except Exception as e:
        print(f"[!] Erro ao enviar notificação para o Telegram: {e}")
        return False

if __name__ == "__main__":
    if len(sys.argv) > 1:
        with open(sys.argv[1], "r", encoding="utf-8") as f:
            data = json.load(f)
            send_telegram_alert(data)
    else:
        print("Uso: ./venv/bin/python telegram_notify.py <caminho_do_json>")
