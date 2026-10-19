#!/usr/bin/env python3
import html
import json
import math
import os
import sys
import sqlite3
import uuid
from pathlib import Path
import urllib.request
import urllib.parse
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
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

    def safe_text(value, limit=300):
        escaped = []
        for character in str(value or ""):
            encoded = html.escape(character, quote=True)
            if sum(map(len, escaped)) + len(encoded) > limit:
                break
            escaped.append(encoded)
        return "".join(escaped)

    title = safe_text(evaluation_data.get("title", "Cargo não especificado"), 100)
    company = safe_text(evaluation_data.get("company", "Empresa não especificada"), 100)
    raw_url = str(evaluation_data.get("url", ""))
    url_parts = urlsplit(raw_url)
    encoded_url = html.escape(raw_url, quote=True)
    url = (
        encoded_url
        if url_parts.scheme in ("http", "https")
        and url_parts.netloc
        and len(encoded_url) <= 300
        else "#"
    )
    score = safe_text(score, 30)
    source = safe_text(evaluation_data.get("source", "Desconhecida"), 60)
    
    reasons = evaluation_data.get("reasons_pt", [])
    risks = evaluation_data.get("interview_risks_pt", [])

    reason_text = f"\n• <b>Motivo:</b> {safe_text(reasons[0], 80)}" if reasons else ""
    risk_text = f"\n• <b>Alerta:</b> {safe_text(risks[0], 80)}" if risks else ""

    message = (
        f"🎯 <b>NOVA VAGA ENCONTRADA ({score} pts)</b>\n\n"
        f"🏢 <b>Empresa:</b> {company}\n"
        f"💼 <b>Cargo:</b> {title}\n"
        f"📍 <b>Fonte:</b> {source}\n"
        f"{reason_text}"
        f"{risk_text}\n\n"
        f"🔗 <a href='{url}'>Ver Anúncio da Vaga</a>"
    )

    cover_letter = evaluation_data.get("cover_letter")
    if evaluation_data.get("verdict") == "APPLY" and not cover_letter:
        raise ValueError("Vaga APPLY exige uma carta de apresentação.")

    if cover_letter:
        boundary = f"----JobAgent{uuid.uuid4().hex}"
        fields = {
            "chat_id": chat_id,
            "caption": message,
            "parse_mode": "HTML",
        }
        chunks = []
        for name, value in fields.items():
            chunks.extend(
                [
                    f"--{boundary}\r\n".encode(),
                    f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                    str(value).encode("utf-8"),
                    b"\r\n",
                ]
            )
        filename = f"cover-letter-{evaluation_data.get('job_id', 'job')}.txt"
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                (
                    'Content-Disposition: form-data; name="document"; '
                    f'filename="{filename}"\r\n'
                ).encode(),
                b"Content-Type: text/plain; charset=utf-8\r\n\r\n",
                str(cover_letter).encode("utf-8"),
                b"\r\n",
                f"--{boundary}--\r\n".encode(),
            ]
        )
        api_url = f"https://api.telegram.org/bot{token}/sendDocument"
        request_data = b"".join(chunks)
        request_headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    else:
        api_url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": False
        }
        request_data = urllib.parse.urlencode(payload).encode("utf-8")
        request_headers = {}

    request = urllib.request.Request(api_url, data=request_data, headers=request_headers)
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
    for notification_id, payload in store.pending_notifications(db_path):
        try:
            notification = json.loads(payload)
            if notification.get("verdict") == "APPLY" and not notification.get("cover_letter"):
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
                        "Dados insuficientes para gerar a carta desta vaga APPLY."
                    )
                letter = generate_cover_letter(job_text, notification)
                save_cover_letter(job_id, letter, BASE_DIR / "cover_letters")
                notification["cover_letter"] = letter
                store.update_notification_payload(
                    notification_id, notification, db_path
                )
            send_telegram_alert(notification)
            store.mark_notification_sent(notification_id, db_path)
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
