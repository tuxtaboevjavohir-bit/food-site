"""Интеграция с Paloma365: точки, продажи за день / неделю / месяц, топ блюд.

Ключ создаётся владельцем в кабинете Paloma365: «Подключённые приложения» → «Создать интеграцию»
(начинается с ppik_). Нужны права account:read, catalog:read, reports:read.

PALOMA_API_KEY — один ключ, если все точки в одном аккаунте Paloma.
Если точки в разных аккаунтах — несколько ключей через запятую, можно с названием:
    PALOMA_API_KEY=Центр:ppik_aaa, Мега:ppik_bbb, Вокзал:ppik_ccc
APP_TZ — часовой пояс для «сегодня/вчера» (по умолчанию Asia/Almaty, UTC+5).
"""
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

import httpx

BASE = os.getenv("PALOMA_BASE_URL", "https://connect.paloma365.com/platform/v1").rstrip("/")
CACHE_SECONDS = 300
_cache: dict = {}


# ---------------------------------------------------------------- ключи и запросы
def accounts() -> list[dict]:
    """[{idx, name, key, named}] из PALOMA_API_KEY. Находит все ключи ppik_… в строке,
    подпись — текст перед ключом (до двоеточия или «=»). Разделители, кавычки, Bearer, переносы — неважно."""
    raw = os.getenv("PALOMA_API_KEY", "")
    out, pos = [], 0
    for m in re.finditer(r"pp[a-z]{2}_[A-Za-z0-9_\-\.~+/=]+", raw):
        before = raw[pos:m.start()]
        pos = m.end()
        label = re.split(r"[,;\n\r]", before)[-1]
        label = re.sub(r"(?i)bearer", "", label).strip(" \t\"'«»:=：-")
        named = bool(label)
        out.append({"idx": len(out), "name": label or f"Аккаунт {len(out) + 1}", "key": m.group(0), "named": named})
    if not out:  # ключи другого формата: «Название:ключ, ключ2, …»
        for part in (p.strip() for p in re.split(r"[,;\n]", raw) if p.strip()):
            part = re.sub(r"(?i)^bearer\s+", "", part.strip("\"' "))
            named = ":" in part
            name, key = part.split(":", 1) if named else (f"Аккаунт {len(out) + 1}", part)
            out.append({"idx": len(out), "name": name.strip(), "key": key.strip(), "named": named})
    return out


def mask(key: str) -> str:
    return f"{key[:9]}…{key[-4:]} ({len(key)} симв.)" if len(key) > 14 else key


def connected() -> bool:
    return bool(accounts())


def today() -> date:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(os.getenv("APP_TZ", "Asia/Almaty"))).date()
    except Exception:
        return (datetime.utcnow() + timedelta(hours=5)).date()


def _get(key: str, path: str, params: dict | None = None):
    r = httpx.get(BASE + path, params=params or {}, headers={"Authorization": f"Bearer {key}", "Accept": "application/json"}, timeout=30)
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


def _pages(key: str, path: str, params: dict, max_pages: int = 10) -> dict:
    first, rows_all, cursor = None, [], None
    for _ in range(max_pages):
        p = dict(params)
        p.setdefault("limit", 200)
        if cursor:
            p["cursor"] = cursor
        raw = _get(key, path, p)
        page = _unwrap(raw)
        first = first or page
        rows = page.get("rows") if isinstance(page.get("rows"), list) else page.get("data")
        if isinstance(rows, list):
            rows_all.extend(rows)
        cursor = page.get("next_cursor") or (raw.get("next_cursor") if isinstance(raw, dict) else None)
        if not cursor:
            break
    first = dict(first or {})
    first["rows"] = rows_all
    return first


def _num(v):
    try:
        return float(str(v).replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return None


def _dim(row: dict, dim: str):
    dims = row.get("dims") or row.get("key") or {}
    d = dims.get(dim) if isinstance(dims, dict) and dim in dims else (next(iter(dims.values())) if isinstance(dims, dict) and dims else None)
    if isinstance(d, dict):
        rid = d.get("id")
        for k in ("name", "title", "label", "date", "value", "id"):
            if d.get(k) not in (None, ""):
                return str(d[k]), rid
        return json.dumps(d, ensure_ascii=False), rid
    return str(d), d


def _rows(resp: dict, dim: str, names: dict | None = None) -> list[dict]:
    out = []
    for r in resp.get("rows", []):
        label, rid = _dim(r, dim)
        if names and str(rid) in names:
            label = names[str(rid)]
        v = r.get("values") or {}
        out.append({"label": label, "id": rid, "net": _num(v.get("net")) or 0.0, "orders": _num(v.get("orders"))})
    return out


def _totals(resp: dict) -> dict:
    t = resp.get("totals") or {}
    t = t.get("values", t) if isinstance(t, dict) else {}
    return {"net": _num(t.get("net")), "orders": _num(t.get("orders"))}


def _cached(key, fn, ttl=CACHE_SECONDS):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    val = fn()
    _cache[key] = (time.time(), val)
    return val


# ---------------------------------------------------------------- точки и каталог
def locations() -> list[dict]:
    """Все точки всех аккаунтов: [{id: 'acc:loc', name, account}]."""
    def load():
        out = []
        for a in accounts():
            try:
                data = _get(a["key"], "/locations", {"limit": 200})
                rows = [l for l in (data.get("data") if isinstance(data, dict) else None) or [] if isinstance(l, dict) and not l.get("deleted")]
                for loc in rows:
                    nm = loc.get("name") or f"Точка {loc.get('id')}"
                    if a.get("named"):  # подпись из PALOMA_API_KEY важнее названия в Paloma
                        nm = a["name"] if len(rows) == 1 else f"{a['name']} · {nm}"
                    out.append({"id": f"{a['idx']}:{loc.get('id')}", "name": nm, "paloma_name": loc.get("name"), "account": a["name"]})
                if not rows:
                    out.append({"id": f"{a['idx']}:", "name": a["name"], "account": a["name"]})
            except Exception as e:
                out.append({"id": f"{a['idx']}:", "name": a["name"], "account": a["name"], "error": str(e)})
        return out
    return _cached("locations", load, 3600)


def item_names(acc: dict) -> dict:
    def load():
        names = {}
        try:
            for it in _pages(acc["key"], "/items", {}, 15).get("rows", []):
                if isinstance(it, dict) and not it.get("deleted") and it.get("id") is not None:
                    names[str(it["id"])] = it.get("name") or str(it["id"])
        except Exception:
            pass
        return names
    return _cached(f"items:{acc['idx']}", load, 3600)


# ---------------------------------------------------------------- отчёты
PERIODS = {"today": (1, 0, "hour"), "yesterday": (1, 1, "hour"), "week": (7, 0, "day"), "month": (30, 0, "day")}


def _range(period: str):
    days, back, group = PERIODS.get(period, PERIODS["week"])
    end = today() - timedelta(days=back)
    start = end - timedelta(days=days - 1)
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=days - 1)
    return start, end, prev_start, prev_end, group


def _merge(lists: list[list[dict]], sort_key=None) -> list[dict]:
    acc = {}
    for rows in lists:
        for r in rows:
            m = acc.setdefault(r["label"], {"label": r["label"], "id": r["id"], "net": 0.0, "orders": None})
            m["net"] += r["net"] or 0
            if r["orders"] is not None:
                m["orders"] = (m["orders"] or 0) + r["orders"]
    out = list(acc.values())
    if sort_key == "net":
        out.sort(key=lambda r: -r["net"])
    else:
        def k(r):
            v = r["id"] if r["id"] is not None else r["label"]
            try:
                return (0, float(v), "")
            except (TypeError, ValueError):
                return (1, 0, str(v))
        out.sort(key=k)
    return out


def report(period: str = "week", loc: str = "all") -> dict:
    """Отчёт за период по всем точкам или одной (loc = 'acc:location_id')."""
    if not connected():
        return {"connected": False}
    return _cached(f"rep:{period}:{loc}", lambda: _build(period, loc))


def _build(period: str, loc: str) -> dict:
    start, end, pstart, pend, group = _range(period)
    accs = accounts()
    targets = []  # (account, location_id or None)
    if loc and loc != "all" and ":" in loc:
        ai, lid = loc.split(":", 1)
        a = next((x for x in accs if str(x["idx"]) == ai), None)
        if a:
            targets.append((a, lid or None))
    if not targets:
        targets = [(a, None) for a in accs]
    res = {"connected": True, "period": period, "from": start.isoformat(), "to": end.isoformat(),
           "prev_from": pstart.isoformat(), "prev_to": pend.isoformat(), "group": group, "errors": []}

    def q(a, lid, frm, to, metrics, group_by):
        p = {"from": frm.isoformat(), "to": to.isoformat(), "metrics": metrics, "group_by": group_by}
        if lid:
            p["location_id"] = lid
        return _pages(a["key"], "/reports/metrics", p, 5)

    jobs = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for a, lid in targets:
            jobs[(a["idx"], lid, "series")] = pool.submit(q, a, lid, start, end, "net,orders", group)
            jobs[(a["idx"], lid, "prev")] = pool.submit(q, a, lid, pstart, pend, "net,orders", "day")
            jobs[(a["idx"], lid, "item")] = pool.submit(q, a, lid, start, end, "net", "item")
            if lid is None:
                jobs[(a["idx"], lid, "loc")] = pool.submit(q, a, lid, start, end, "net,orders", "location")
            if period == "month":
                jobs[(a["idx"], lid, "wd")] = pool.submit(q, a, lid, start, end, "net,orders", "weekday")

    def get(k):
        try:
            return jobs[k].result()
        except Exception as e:
            res["errors"].append(str(e))
            return None

    series, items, locs, wds = [], [], [], []
    tot = {"net": 0.0, "orders": 0.0}
    prev = {"net": 0.0, "orders": 0.0}
    for a, lid in targets:
        s = get((a["idx"], lid, "series"))
        if s:
            series.append(_rows(s, group))
            t = _totals(s)
            tot["net"] += t["net"] if t["net"] is not None else sum(r["net"] for r in series[-1])
            tot["orders"] += t["orders"] if t["orders"] is not None else sum(r["orders"] or 0 for r in series[-1])
        p = get((a["idx"], lid, "prev"))
        if p:
            t = _totals(p); rows = _rows(p, "day")
            prev["net"] += t["net"] if t["net"] is not None else sum(r["net"] for r in rows)
            prev["orders"] += t["orders"] if t["orders"] is not None else sum(r["orders"] or 0 for r in rows)
        it = get((a["idx"], lid, "item"))
        if it:
            items.append(_rows(it, "item", item_names(a)))
        if (a["idx"], lid, "loc") in jobs:
            lr = get((a["idx"], lid, "loc"))
            if lr:
                rows = _rows(lr, "location")
                for r in rows:
                    if a.get("named"):
                        r["label"] = a["name"] if len(rows) == 1 else f"{a['name']} · {r['label']}"
                    elif r["label"] in ("None", "") or (len(accounts()) > 1 and len(rows) == 1 and not r["label"]):
                        r["label"] = a["name"]
                locs.append(rows)
        if (a["idx"], lid, "wd") in jobs:
            w = get((a["idx"], lid, "wd"))
            if w:
                wds.append(_rows(w, "weekday"))
    res["totals"] = tot
    res["prev"] = prev
    res["series"] = _merge(series)
    res["by_item"] = _merge(items, "net")
    res["by_location"] = _merge(locs, "net")
    res["by_weekday"] = _merge(wds) if wds else []
    res["errors"] = list(dict.fromkeys(res["errors"]))
    return res


def raw(group_by: str = "day", days: int = 7, metrics: str = "net,orders"):
    a = accounts()[0]
    to = today()
    frm = to - timedelta(days=max(1, min(int(days), 92)) - 1)
    return _get(a["key"], "/reports/metrics", {"from": frm.isoformat(), "to": to.isoformat(), "metrics": metrics, "group_by": group_by})


def check() -> dict:
    if not connected():
        return {"ok": False, "error": "В PALOMA_API_KEY не найдено ни одного ключа ppik_…"}
    out = []
    for a in accounts():
        info = {"account": a["name"], "key": mask(a["key"])}
        try:
            me = _get(a["key"], "/me")
            info.update(ok=True, scopes=me.get("scopes") if isinstance(me, dict) else None, account_id=me.get("account_id") if isinstance(me, dict) else None)
        except Exception as e:
            info.update(ok=False, error=str(e))
        out.append(info)
    _cache.pop("locations", None)
    return {"ok": all(x["ok"] for x in out), "keys_found": len(out), "accounts": out, "locations": locations()}


# ---------------------------------------------------------------- для агентов
def context_for_agents(max_rows: int = 40) -> str:
    if not connected():
        return ""
    f = lambda v: "—" if v is None else f"{v:,.0f}".replace(",", " ")
    wd = {1: "Пн", 2: "Вт", 3: "Ср", 4: "Чт", 5: "Пт", 6: "Сб", 7: "Вс"}
    lines = ["\nПРОДАЖИ из кассы Paloma365 (₸):"]
    try:
        for period, title in (("today", "Сегодня"), ("yesterday", "Вчера"), ("week", "Последние 7 дней"), ("month", "Последние 30 дней")):
            r = report(period, "all")
            t, p = r.get("totals", {}), r.get("prev", {})
            ch = f" (пред. период {f(p.get('net'))} ₸)" if p.get("net") else ""
            lines.append(f"{title} {r['from']}…{r['to']}: выручка {f(t.get('net'))} ₸, заказов {f(t.get('orders'))}{ch}")
        m = report("month", "all")
        if m.get("by_location"):
            lines.append("По точкам за 30 дней: " + "; ".join(f"{x['label']}: {f(x['net'])} ₸/{f(x['orders'])} зак." for x in m["by_location"]))
        if m.get("series"):
            lines.append("По дням (30 дн.): " + "; ".join(f"{x['label']}: {f(x['net'])}" for x in m["series"][-31:]))
        if m.get("by_item"):
            lines.append("Топ блюд по выручке (30 дн.): " + "; ".join(f"{x['label']}: {f(x['net'])}" for x in m["by_item"][:max_rows]))
        if m.get("by_weekday"):
            lines.append("По дням недели: " + "; ".join(f"{wd.get(x['id'], x['label']) if isinstance(x['id'], int) else x['label']}: {f(x['net'])}" for x in m["by_weekday"]))
        if m.get("errors"):
            lines.append("(часть отчётов недоступна: " + "; ".join(m["errors"])[:300] + ")")
    except Exception as e:
        lines.append(f"ошибка загрузки — {e}")
    return "\n".join(lines)
