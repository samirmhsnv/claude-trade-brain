"""Telegram alerts (optional). Falls back to printing."""
import requests

from .config import SETTINGS


def send(text: str) -> None:
    print(f"[alert] {text}")
    if not (SETTINGS.telegram_token and SETTINGS.telegram_chat_id):
        return
    try:
        requests.post(f"https://api.telegram.org/bot{SETTINGS.telegram_token}/sendMessage",
                      json={"chat_id": SETTINGS.telegram_chat_id, "text": text[:4000]}, timeout=15)
    except requests.RequestException as e:
        print(f"[alert] telegram failed: {e}")
