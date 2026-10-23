#!/usr/bin/env python3
import html
import json
import math
import os
import sys
import sqlite3
import uuid
from pathlib import Path
import urllib.parse
import urllib.request
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
HERMES_TIMEOUT_SECONDS = int(os.getenv("HERMES_TIMEOUT_SECONDS", "1800"))
if HERMES_TIMEOUT_SECONDS <= 0:
    raise ValueError("HERMES_TIMEOUT_SECONDS precisa ser maior que zero.")
BASE_DIR = Path(__file__).parent

def send_telegram_alert(evaluation_data: dict):
    score = evaluation_data.get("score")
    if (
        evaluation_data.get("verdict") not in ("APPLY", "MAYBE")
        or not isinstance(score, (int, float))
        or isinstance(score, bool)
        or not math.isfinite(score)
        or evaluation_data.get("info_status") == "INCOMPLETE"
    ):
        raise ValueError("Alerta exige avaliação completa e pontuação numérica.")

    token = os.getenv("TELEGRAM_TOKEN", TELEGRAM_TOKEN)
    chat_id = os.getenv("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID)

    if not token:
        raise RuntimeError("TELEGRAM_TOKEN não configurado.")

    if not chat_id:
        raise RuntimeError("TELEGRAM_CHAT_ID não configurado.")

    def safe_text(value):
        return html.escape(str(value or ""), quote=True)

    title = safe_text(evaluation_data.get("title", "Cargo não especificado"))
    company = safe_text(evaluation_data.get("company", "Empresa não especificada"))
    raw_url = str(evaluation_data.get("url", ""))
    url_parts = urlsplit(raw_url)
    encoded_url = html.escape(raw_url, quote=True)
    url = (
        encoded_url
        if url_parts.scheme in ("http", "https")
        and url_parts.netloc
        else "#"
    )
    score = safe_text(score)
    source = safe_text(evaluation_data.get("source", "Desconhecida"))
    
    reasons = evaluation_data.get("reasons_pt", [])
    risks = evaluation_data.get("interview_risks_pt", [])

    reason_text = f"\n• <b>Motivo:</b> {safe_text(reasons[0])}" if reasons else ""
    risk_text = f"\n• <b>Alerta:</b> {safe_text(risks[0])}" if risks else ""

    message = (
        f"🎯 <b>NOVA VAGA ENCONTRADA ({score} pts)</b>\n\n"
        f"🏢 <b>Empresa:</b> {company}\n"
        f"💼 <b>Cargo:</b> {title}\n"
        f"📍 <b>Fonte:</b> {source}\n"
        f"{reason_text}"
        f"{risk_text}\n\n"
        f"🔗 <a href='{url}'>Ver Anúncio da Vaga</a>"
    )

    if len(message) > 4096:
        raise ValueError("O alerta excedeu o limite de 4096 caracteres do Telegram.")
    api_url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }
    request_data = urllib.parse.urlencode(payload).encode("utf-8")
    request = urllib.request.Request(api_url, data=request_data)
    return _send_telegram_request(request)


def _send_telegram_request(request):
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        raise RuntimeError(f"Telegram retornou HTTP {error.code}.") from None
    except (URLError, TimeoutError, OSError):
        raise RuntimeError("Falha de rede ao enviar alerta para o Telegram.") from None

    if not result.get("ok"):
        raise RuntimeError("Telegram recusou o alerta.")
    return True


def deliver_pending_notifications(db_path):
    import store

    failed = 0
    store.recover_expired_notifications(db_path)
    for notification_id, payload, alert_sent, letter_sent in store.pending_notifications(db_path):
        if not store.claim_notification(
            notification_id, HERMES_TIMEOUT_SECONDS + 600, db_path
        ):
            continue
        try:
            notification = json.loads(payload)
            if notification.get("verdict") in ("APPLY", "MAYBE") and not notification.get("cover_letter"):
                from cover_letter import generate_cover_letter, save_cover_letter

                job_id = notification.get("job_id")
                job_text = notification.get("job_text")
                if not job_id or not job_text:
                    job_id = store.notification_job_id(notification_id, db_path)
                    if job_id:
                        inbox_file = BASE_DIR / "inbox" / f"{job_id}.txt"
                        if inbox_file.exists():
                            job_text = inbox_file.read_text(encoding="utf-8")
                if not job_id or not job_text:
                    raise ValueError(
                        "Dados insuficientes para gerar a carta desta vaga."
                    )
                letter = generate_cover_letter(job_text, notification)
                save_cover_letter(job_id, letter, BASE_DIR / "cover_letters")
                notification["cover_letter"] = letter
                store.update_notification_payload(
                    notification_id, notification, db_path
                )
            if not alert_sent:
                send_telegram_alert(notification)
                store.mark_notification_alert_sent(notification_id, db_path)
            print(f"[+] Notificação {notification_id} entregue ao Telegram.")
        except (RuntimeError, ValueError, OSError, sqlite3.Error) as error:
            store.mark_notification_failed(notification_id, error, db_path)
            print(f"[!] Notificação {notification_id} pendente: {error}")
            failed += 1
    return failed

if __name__ == "__main__":
    if len(sys.argv) > 1:
        with open(sys.argv[1], "r", encoding="utf-8") as f:
            data = json.load(f)
            send_telegram_alert(data)
    else:
        print("Uso: ./venv/bin/python telegram_notify.py <caminho_do_json>")
