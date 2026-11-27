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


def _unwrap(page):
    """Находит сам отчёт в ответе: он может лежать сверху или внутри списка (results/data/…)."""
    if not isinstance(page, dict):
        return {}
    if isinstance(page.get("rows"), list) or "totals" in page:
        return page
    for v in page.values():
        if isinstance(v, list) and v and isinstance(v[0], dict) and ("rows" in v[0] or "totals" in v[0]):
            return next((x for x in v if x.get("id") == "result"), v[0])
        if isinstance(v, dict) and ("rows" in v or "totals" in v):
            return v
    return page


def _all_pages(path: str, params: dict, max_pages: int = 10):
    """Собирает все страницы ответа (rows или data; cursor / next_cursor)."""
    first, rows_all, cursor = None, [], None
    for _ in range(max_pages):
        p = dict(params)
        p.setdefault("limit", 200)
        if cursor:
            p["cursor"] = cursor
        raw = _get(path, p)
        page = _unwrap(raw)
        first = first or page
        rows = page.get("rows") if isinstance(page.get("rows"), list) else page.get("data")
        if isinstance(rows, list):
            rows_all.extend(rows)
        cursor = page.get("next_cursor") or (raw.get("next_cursor") if isinstance(raw, dict) else None)
        if not cursor:
            break
    if first is not None:
        key = "rows" if isinstance(first.get("rows"), list) or not isinstance(first.get("data"), list) else "data"
        first = dict(first, **{key: rows_all})
    return first


def raw(group_by: str = "day", days: int = 7, metrics: str = "net,orders"):
    """Сырой ответ Paloma — для диагностики."""
    to = date.today()
    frm = to - timedelta(days=max(1, min(int(days), 92)) - 1)
    return _get("/reports/metrics", {"from": frm.isoformat(), "to": to.isoformat(), "metrics": metrics, "group_by": group_by})


def _num(v):
    try:
        return float(str(v).replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return None


def _label(d):
    if isinstance(d, dict):
        for k in ("name", "title", "label", "date", "value", "id"):
            if d.get(k) not in (None, ""):
                return d[k], d.get("id")
        return json.dumps(d, ensure_ascii=False), None
    return d, d


def _norm(resp: dict, dim: str, names: dict | None = None) -> dict:
    """Приводит ответ отчёта к виду {rows: [{label, id, net, orders}], totals: {net, orders}}."""
    rows = resp.get("rows") if isinstance(resp.get("rows"), list) else resp.get("data") or []
    out = []
    for r in rows:
        dims = r.get("dims") or r.get("key") or {}
        d = dims.get(dim) if isinstance(dims, dict) and dim in dims else (next(iter(dims.values())) if isinstance(dims, dict) and dims else None)
        label, rid = _label(d)
        if names and str(rid) in names:
            label = names[str(rid)]
        vals = r.get("values") or {}
        out.append({"label": str(label), "id": rid, "net": _num(vals.get("net")), "orders": _num(vals.get("orders"))})
    tot = (resp.get("totals") or {})
    tot = tot.get("values", tot) if isinstance(tot, dict) else {}
    return {"rows": out, "totals": {"net": _num(tot.get("net")), "orders": _num(tot.get("orders"))}}


_items_cache = {"t": 0, "names": {}}


def item_names() -> dict:
    """id → название блюда из каталога (кэш на час)."""
    if time.time() - _items_cache["t"] < 3600 and _items_cache["names"]:
        return _items_cache["names"]
    names = {}
    try:
        data = _all_pages("/items", {}, 15)
        for it in (data or {}).get("data", []) or (data or {}).get("rows", []):
            if isinstance(it, dict) and not it.get("deleted") and it.get("id") is not None:
                names[str(it["id"])] = it.get("name") or str(it["id"])
    except Exception:
        pass
    _items_cache.update(t=time.time(), names=names)
    return names


def sales(days: int = 30) -> dict:
    """Выручка по дням, по блюдам и по дням недели за последние `days` дней (до 92)."""
    if not connected():
        return {"connected": False}
    days = max(1, min(int(days), 92))
    ck = f"sales:{days}"
    if ck in _cache and time.time() - _cache[ck][0] < CACHE_SECONDS:
        return _cache[ck][1]
    to = date.today()
    frm = to - timedelta(days=days - 1)
    rng = {"from": frm.isoformat(), "to": to.isoformat()}
    res = {"connected": True, "from": rng["from"], "to": rng["to"], "errors": []}
    # по дням: отчёт metrics с разбивкой по дню (работает даже без обновления базы)
    try:
        res["daily"] = _norm(_all_pages("/reports/metrics", dict(rng, metrics="net,orders", group_by="day"), 3), "day")
    except Exception as e:
        res["errors"].append(str(e))
    # по блюдам: только выручка (заказы по блюдам Paloma не считает)
    try:
        res["by_item"] = _norm(_all_pages("/reports/metrics", dict(rng, metrics="net", group_by="item"), 5), "item", item_names())
        res["by_item"]["rows"].sort(key=lambda r: -(r["net"] or 0))
    except Exception as e:
        res["errors"].append(str(e))
    try:
        res["by_weekday"] = _norm(_all_pages("/reports/metrics", dict(rng, metrics="net,orders", group_by="weekday"), 1), "weekday")
    except Exception as e:
        res["errors"].append(str(e))
    _cache[ck] = (time.time(), res)
    return res


def check() -> dict:
    """Проверка ключа: GET /me."""
    if not connected():
        return {"ok": False, "error": "PALOMA_API_KEY не задан"}
    try:
        return {"ok": True, "me": _get("/me")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def context_for_agents(max_rows: int = 40) -> str:
    """Краткая выжимка продаж для агентов: суммы по дням, топ блюд, дни недели."""
    if not connected():
        return ""
    try:
        s = sales(30)
    except Exception as e:
        return f"\nПРОДАЖИ (Paloma365): ошибка загрузки — {e}"
    f = lambda v: "—" if v is None else f"{v:,.0f}".replace(",", " ")
    wd = {1: "Пн", 2: "Вт", 3: "Ср", 4: "Чт", 5: "Пт", 6: "Сб", 7: "Вс"}
    lines = [f"\nПРОДАЖИ из кассы Paloma365 за {s['from']} — {s['to']} (₸):"]
    if s.get("daily"):
        t = s["daily"]["totals"]
        lines.append(f"Итого: выручка {f(t['net'])} ₸, заказов {f(t['orders'])}")
        lines.append("По дням: " + "; ".join(f"{r['label']}: {f(r['net'])} ₸/{f(r['orders'])} зак." for r in s["daily"]["rows"][-31:]))
    if s.get("by_item"):
        lines.append("Топ блюд по выручке: " + "; ".join(f"{r['label']}: {f(r['net'])} ₸" for r in s["by_item"]["rows"][:max_rows]))
    if s.get("by_weekday"):
        lines.append("По дням недели: " + "; ".join(
            f"{wd.get(r['id'], r['label']) if isinstance(r['id'], int) else r['label']}: {f(r['net'])} ₸/{f(r['orders'])} зак." for r in s["by_weekday"]["rows"]))
    if s.get("errors"):
        lines.append("(часть отчётов недоступна: " + "; ".join(s["errors"])[:300] + ")")
    return "\n".join(lines)
