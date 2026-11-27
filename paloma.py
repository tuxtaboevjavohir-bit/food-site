"""Интеграция с Paloma365: продажи по дням и по блюдам.

Ключ создаётся владельцем в кабинете Paloma365: «Подключённые приложения» → «Создать интеграцию»
(начинается с ppik_). Нужны права reports:read и catalog:read. Ключ кладём в переменную PALOMA_API_KEY.
"""
import json
import os
import time
from datetime import date, timedelta

import httpx

BASE = os.getenv("PALOMA_BASE_URL", "https://connect.paloma365.com/platform/v1").rstrip("/")
_cache: dict = {}
CACHE_SECONDS = 600


def key() -> str:
    return os.getenv("PALOMA_API_KEY", "").strip()


def connected() -> bool:
    return bool(key())


def _get(path: str, params: dict | None = None):
    r = httpx.get(
        BASE + path,
        params=params or {},
        headers={"Authorization": f"Bearer {key()}", "Accept": "application/json"},
        timeout=30,
    )
    if r.status_code >= 400:
        try:
            err = r.json().get("error", {})
            msg = f"{err.get('code', r.status_code)}: {err.get('message', '')}"
        except Exception:
            msg = f"HTTP {r.status_code}"
        raise RuntimeError(f"Paloma365 {path} — {msg}")
    return r.json()


def _all_pages(path: str, params: dict, max_pages: int = 10):
    """Собирает все страницы (cursor / next_cursor)."""
    out, first, cursor = [], None, None
    for _ in range(max_pages):
        p = dict(params, limit=params.get("limit", 200))
        if cursor:
            p["cursor"] = cursor
        page = _get(path, p)
        if first is None:
            first = page
        rows = page.get("data") if isinstance(page, dict) else None
        if isinstance(rows, list):
            out.extend(rows)
        cursor = page.get("next_cursor") if isinstance(page, dict) else None
        if not cursor:
            break
    if isinstance(first, dict) and out:
        first = dict(first, data=out, next_cursor=None)
    return first


def sales(days: int = 30) -> dict:
    """Выручка по дням и топ блюд за последние `days` дней (до 92)."""
    if not connected():
        return {"connected": False}
    days = max(1, min(int(days), 92))
    ck = f"sales:{days}"
    if ck in _cache and time.time() - _cache[ck][0] < CACHE_SECONDS:
        return _cache[ck][1]
    to = date.today()
    frm = to - timedelta(days=days - 1)
    rng = {"from": frm.isoformat(), "to": to.isoformat()}
    result = {"connected": True, "from": rng["from"], "to": rng["to"], "errors": []}
    try:
        result["daily"] = _get("/reports/sales-daily", rng)
    except Exception as e:
        result["errors"].append(str(e))
    try:
        result["by_item"] = _all_pages("/reports/metrics", dict(rng, metrics="net,orders", group_by="item"), 3)
    except Exception as e:
        result["errors"].append(str(e))
    try:
        result["by_weekday"] = _get("/reports/metrics", dict(rng, metrics="net,orders", group_by="weekday"))
    except Exception as e:
        result["errors"].append(str(e))
    _cache[ck] = (time.time(), result)
    return result


def check() -> dict:
    """Проверка ключа: GET /me."""
    if not connected():
        return {"ok": False, "error": "PALOMA_API_KEY не задан"}
    try:
        return {"ok": True, "me": _get("/me")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def context_for_agents(max_chars: int = 6000) -> str:
    """Краткая выжимка продаж для агентов (сырые данные Paloma в JSON, обрезанные по длине)."""
    if not connected():
        return ""
    try:
        s = sales(30)
    except Exception as e:
        return f"\nПРОДАЖИ (Paloma365): ошибка загрузки — {e}"
    parts = []
    for name, label in (("daily", "Выручка по дням"), ("by_item", "Продажи по блюдам"), ("by_weekday", "По дням недели")):
        if s.get(name) is not None:
            txt = json.dumps(s[name], ensure_ascii=False, separators=(",", ":"))
            parts.append(f"{label}: {txt[: max_chars // 3]}")
    if not parts:
        return "\nПРОДАЖИ (Paloma365): данные недоступны. " + "; ".join(s.get("errors", []))
    return (
        f"\nПРОДАЖИ из кассы Paloma365 за {s['from']} — {s['to']} (суммы — строки в валюте счёта):\n"
        + "\n".join(parts)
    )
