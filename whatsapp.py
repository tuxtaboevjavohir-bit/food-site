"""WhatsApp Cloud API: приём сообщений клиентов, автоответ ИИ-администратора, ручные ответы.

Переменные окружения:
  WA_TOKEN        — постоянный токен доступа (System User token) из Meta Business
  WA_PHONE_ID     — Phone number ID из раздела WhatsApp → API Setup
  WA_VERIFY_TOKEN — любое слово, которое вы придумаете; то же слово вписывается в настройках вебхука в Meta
  WA_APP_SECRET   — App Secret приложения (для проверки подписи входящих запросов, рекомендуется)
  WA_API_VERSION  — версия Graph API (по умолчанию v26.0)
"""
import hashlib
import hmac
import os

import httpx

import agents
import memory

API_VERSION = os.getenv("WA_API_VERSION", "v26.0")


def cfg(name: str) -> str:
    return os.getenv(name, "").strip()


def connected() -> bool:
    return bool(cfg("WA_TOKEN") and cfg("WA_PHONE_ID"))


def autoreply_on() -> bool:
    return memory.get_setting("wa_autoreply", "1") == "1"


def verify_signature(body: bytes, header: str) -> bool:
    secret = cfg("WA_APP_SECRET")
    if not secret:
        return True  # подпись не проверяем, если секрет не задан
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header[7:], expected)


def send_text(phone: str, text: str) -> dict:
    if not connected():
        raise RuntimeError("WhatsApp не подключён: задайте WA_TOKEN и WA_PHONE_ID")
    r = httpx.post(
        f"https://graph.facebook.com/{API_VERSION}/{cfg('WA_PHONE_ID')}/messages",
        headers={"Authorization": f"Bearer {cfg('WA_TOKEN')}"},
        json={"messaging_product": "whatsapp", "to": phone, "type": "text", "text": {"body": text[:4000]}},
        timeout=30,
    )
    if r.status_code >= 400:
        try:
            err = r.json().get("error", {})
            msg = err.get("message", r.text)
        except Exception:
            msg = r.text
        raise RuntimeError(f"WhatsApp: {msg}")
    return r.json()


OPERATOR_ROLE = (
    "Ты администратор кафе быстрого питания Food house и переписываешься с гостями в WhatsApp. "
    "Отвечай коротко (1–4 предложения), тепло и по делу, без Markdown-разметки. "
    "Пиши на том языке, на котором пишет гость: казахский — по-казахски, русский — по-русски. "
    "Опирайся только на меню и факты из памяти заведения. Не выдумывай цены, адреса, акции и время доставки: "
    "если информации нет, скажи, что уточнишь у менеджера и скоро ответишь. "
    "Если гость жалуется — извинись, поблагодари за отзыв и пообещай передать руководителю. "
    "Не обещай скидок и компенсаций от себя."
)


def ai_reply(phone: str) -> str:
    history = memory.wa_history(phone, 12)
    convo = "\n".join(
        f"{'Гость' if m['direction'] == 'in' else 'Кафе'}: {m['text']}" for m in history if m.get("text")
    )
    if agents.demo_mode():
        return "Здравствуйте! Спасибо за сообщение, менеджер скоро ответит. (демо-режим)"
    system = OPERATOR_ROLE + "\n\n" + memory.memory_context()
    return agents._ask(system, f"Переписка с гостем:\n{convo}\n\nНапиши следующий ответ кафе.", 600)


def handle_webhook(payload: dict):
    """Разбирает вебхук Meta, сохраняет входящие и при включённом автоответе отвечает."""
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            names = {c.get("wa_id"): c.get("profile", {}).get("name", "") for c in value.get("contacts", [])}
            for msg in value.get("messages", []):
                phone = msg.get("from", "")
                mtype = msg.get("type")
                if mtype == "text":
                    text = msg.get("text", {}).get("body", "")
                elif mtype == "button":
                    text = msg.get("button", {}).get("text", "")
                elif mtype == "interactive":
                    it = msg.get("interactive", {})
                    text = (it.get("button_reply") or it.get("list_reply") or {}).get("title", "")
                else:
                    text = f"[{mtype}]"
                memory.wa_add(phone, "in", text, names.get(phone, ""))
                if autoreply_on() and connected():
                    try:
                        if mtype in ("text", "button", "interactive"):
                            answer = ai_reply(phone)
                        else:
                            answer = "Спасибо! Пока я понимаю только текст — напишите, пожалуйста, словами. / Рахмет! Мәтінмен жазыңызшы."
                        send_text(phone, answer)
                        memory.wa_add(phone, "out", answer, author="ИИ")
                    except Exception as e:
                        memory.wa_add(phone, "out", f"⚠️ Ответ не отправлен: {e}", author="ошибка")


def ideas(lang: str = "ru") -> str:
    """Маркетолог анализирует сообщения гостей и выдаёт идеи и жалобы."""
    msgs = memory.wa_recent_incoming(300)
    if not msgs:
        return "Пока нет сообщений от гостей." if lang != "kk" else "Әзірге қонақтардан хабарлама жоқ."
    if agents.demo_mode():
        return f"Демо-режим: получено {len(msgs)} сообщений. Добавьте ключ API, чтобы маркетолог проанализировал их."
    joined = "\n".join(f"- {m['text']}" for m in reversed(msgs) if m.get("text"))[:30000]
    system = agents.AGENTS["marketer"]["role"] + "\n\n" + memory.memory_context() + agents.lang_rule(lang)
    return agents._ask(
        system,
        "Вот сообщения гостей из WhatsApp. Сделай отчёт в Markdown:\n"
        "1) О чём чаще всего спрашивают (топ-5)\n2) Жалобы и проблемы\n3) Что хвалят\n"
        "4) 5 конкретных идей для меню, сервиса и акций на основе этих сообщений\n"
        "5) Какие ответы стоит добавить в память заведения, чтобы ИИ-администратор отвечал точнее\n\n" + joined,
        4000,
    )
