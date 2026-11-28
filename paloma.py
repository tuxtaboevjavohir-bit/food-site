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
import threading
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
        out.append({"idx": len(out), "name": label, "key": m.group(0), "named": named})
    if not out:  # ключи другого формата: «Название:ключ, ключ2, …»
        for part in (p.strip() for p in re.split(r"[,;\n]", raw) if p.strip()):
            part = re.sub(r"(?i)^bearer\s+", "", part.strip("\"' "))
            named = ":" in part
            name, key = part.split(":", 1) if named else (f"Аккаунт {len(out) + 1}", part)
            out.append({"idx": len(out), "name": name.strip() if named else "", "key": key.strip(), "named": named})
    # без подписи — берём названия точек по порядку (28а, 13, Озен) из настроек
    try:
        import memory
        pts = memory.points()
    except Exception:
        pts = []
    for a in out:
        if not a["named"]:
            if a["idx"] < len(pts):
                a["name"], a["named"] = pts[a["idx"]], True
            else:
                a["name"] = f"Аккаунт {a['idx'] + 1}"
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


_locks: dict = {}
_locks_guard = threading.Lock()


def _lock_for(key: str):
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def _get(key: str, path: str, params: dict | None = None):
    """Запрос к Paloma. Отчёты одного аккаунта строятся строго по одному; «занято» — ждём и повторяем."""
    is_report = path.startswith("/reports/")
    lock = _lock_for(key) if is_report else None
    for attempt in range(8):
        if lock:
            lock.acquire()
        try:
            r = httpx.get(BASE + path, params=params or {}, headers={"Authorization": f"Bearer {key}", "Accept": "application/json"}, timeout=40)
        finally:
            if lock:
                lock.release()
        if r.status_code < 400:
            return r.json()
        try:
            err = r.json().get("error", {})
            code, message = err.get("code", r.status_code), err.get("message", "")
        except Exception:
            code, message = r.status_code, ""
        if code in ("temporarily_unavailable", "rate_limited") or r.status_code in (429, 503):
            wait = 1.0 + attempt * 0.8
            try:
                wait = max(wait, float(r.headers.get("Retry-After", 0)))
            except ValueError:
                pass
            time.sleep(min(wait, 6))
            continue
        raise RuntimeError(f"Paloma365 {path} — {code}: {message}")
    raise RuntimeError(f"Paloma365 {path} — отчёт занят слишком долго, попробуйте ещё раз")


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


_refreshing: set = set()


def _cached(key, fn, ttl=CACHE_SECONDS, stale=900):
    """Кэш: свежее — сразу; устаревшее (до `stale` сек) — тоже сразу, а новое считается в фоне."""
    hit = _cache.get(key)
    age = time.time() - hit[0] if hit else None
    if hit and age < ttl:
        return hit[1]
    if hit and age < stale:
        if key not in _refreshing:
            _refreshing.add(key)

            def bg():
                try:
                    _cache[key] = (time.time(), fn())
                except Exception:
                    pass
                finally:
                    _refreshing.discard(key)
            threading.Thread(target=bg, daemon=True).start()
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


def _targets(loc: str):
    accs = accounts()
    if loc and loc != "all" and ":" in loc:
        ai, lid = loc.split(":", 1)
        a = next((x for x in accs if str(x["idx"]) == ai), None)
        if a:
            return [(a, lid or None)]
    return [(a, None) for a in accs]


def report(period: str = "week", loc: str = "all") -> dict:
    """Отчёт за период по всем точкам или одной (loc = 'acc:location_id')."""
    if not connected():
        return {"connected": False}
    return _cached(f"rep:{period}:{loc}", lambda: _build(period, loc), 25 if period == "today" else CACHE_SECONDS)


def _metrics(a, lid, frm, to, metrics, group_by, limit=1000):
    p = {"from": frm.isoformat(), "to": to.isoformat(), "metrics": metrics, "group_by": group_by, "limit": limit}
    if lid:
        p["location_id"] = lid
    try:
        return _pages(a["key"], "/reports/metrics", p, 10)
    except RuntimeError as e:
        if "limit" in str(e).lower() and limit > 200:   # если большой limit не принимают
            p["limit"] = 200
            return _pages(a["key"], "/reports/metrics", p, 10)
        raise


def _parse_dt(v):
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v)[:19])
    except ValueError:
        return None


def _biz_of_shift(opened: datetime, cfg: list[dict]):
    """К какой смене (дата рабочего дня, название) относится смена кассы, открытая в `opened`.
    Берём ближайшее плановое начало смены: 09:00 → дневная, 21:00 → ночная."""
    best = None
    for s_ in cfg:
        for back in (0, 1, -1):
            d = opened.date() - timedelta(days=back)
            start = datetime(d.year, d.month, d.day, int(s_["start"]))
            diff = abs((opened - start).total_seconds())
            if best is None or diff < best[0]:
                best = (diff, d, s_["name"])
    return best[1], best[2]


SHIFT_METRICS = ["net,orders,returns,discounts,return_receipts", "net,orders"]


def _register_shifts(a, lid, frm, to):
    """Смены касс за интервал: [{id, opened, closed, status, workplace, net, orders, returns, discounts, refunds}]."""
    last = None
    for metrics in SHIFT_METRICS:
        try:
            resp = _metrics(a, lid, frm, to, metrics, "shift", 500)
            out = []
            for r in resp.get("rows", []):
                sh = (r.get("dims") or {}).get("shift") or {}
                v = r.get("values") or {}
                opened = _parse_dt(sh.get("opened_at"))
                if not opened:
                    continue
                out.append({"id": sh.get("id"), "opened": opened, "closed": _parse_dt(sh.get("closed_at")), "status": sh.get("status"),
                            "workplace": (sh.get("workplace") or {}).get("name"),
                            "net": _num(v.get("net")) or 0.0, "orders": _num(v.get("orders")) or 0.0,
                            "returns": _num(v.get("returns")), "discounts": _num(v.get("discounts")), "refunds": _num(v.get("return_receipts"))})
            return out
        except Exception as e:
            last = e
            if "metric" not in str(e).lower() and "invalid" not in str(e).lower():
                break
    raise last


def _acc_data(a, lid, period):
    """Данные точки: смены касс (точные итоги, как в Paloma), почасовой график (для дня), продажи по блюдам.
    Запросы строго по очереди. Кэшируется."""
    def load():
        start, end, pstart, pend, group = _range(period)
        out = {"errors": [], "shifts": None, "moments": None, "days": None}
        wto = min(end + timedelta(days=1), today())
        # 1) смены касс — с запасом в день до и после, чтобы смены через полночь попали целиком
        try:
            out["shifts"] = _register_shifts(a, lid, pstart - timedelta(days=1), wto)
        except Exception as e:
            out["errors"].append(f"смены: {e}")
        # 2) график по часам — только для «сегодня»/«вчера» (или если смены недоступны)
        if group == "hour" or out["shifts"] is None:
            try:
                out["moments"] = _moments_day_hour(_metrics(a, lid, start if out["shifts"] is not None else pstart, wto, "net,orders", "day,hour"))
            except Exception as e:
                out["errors"].append(str(e))
        # 3) продажи по блюдам
        try:
            out["items"] = _rows(_metrics(a, lid, start, end, "net", "item"), "item", item_names(a))
        except Exception as e:
            out["items"] = []
            out["errors"].append(str(e))
        return out
    return _cached(f"acc:{a['idx']}:{lid}:{period}", load, 25 if period in ("today", "yesterday") else CACHE_SECONDS)


def _build(period: str, loc: str) -> dict:
    start, end, pstart, pend, group = _range(period)
    targets = _targets(loc)
    cfg = shifts_cfg()
    res = {"connected": True, "period": period, "from": start.isoformat(), "to": end.isoformat(),
           "prev_from": pstart.isoformat(), "prev_to": pend.isoformat(), "group": group, "errors": [], "business_day": True}
    with ThreadPoolExecutor(max_workers=6) as pool:   # точки — параллельно, внутри точки — по очереди
        datas = list(pool.map(lambda t: (t, _acc_data(t[0], t[1], period)), targets))
    tot = {"net": 0.0, "orders": 0.0, "returns": 0.0, "discounts": 0.0, "refunds": 0.0}
    prev = {"net": 0.0, "orders": 0.0}
    series, items, locs, wds = {}, [], [], {}
    has_extra = False

    def add_series(k, label, net, orders):
        r = series.setdefault(k, {"label": label, "id": k, "net": 0.0, "orders": 0.0})
        r["net"] += net; r["orders"] += orders

    for (a, lid), d in datas:
        res["errors"] += d["errors"]
        acc = {"net": 0.0, "orders": 0.0}
        if d.get("shifts") is not None:
            for sh in d["shifts"]:
                bd, _name = _biz_of_shift(sh["opened"], cfg)
                if start <= bd <= end:
                    acc["net"] += sh["net"]; acc["orders"] += sh["orders"]
                    for k in ("returns", "discounts", "refunds"):
                        if sh[k] is not None:
                            tot[k] += sh[k]; has_extra = True
                    if group == "day":
                        add_series(bd.isoformat(), bd.isoformat(), sh["net"], sh["orders"])
                    if period == "month":
                        w = wds.setdefault(bd.isoweekday(), {"label": str(bd.isoweekday()), "id": bd.isoweekday(), "net": 0.0, "orders": 0.0})
                        w["net"] += sh["net"]; w["orders"] += sh["orders"]
                elif pstart <= bd <= pend:
                    prev["net"] += sh["net"]; prev["orders"] += sh["orders"]
            if group == "hour" and d.get("moments"):
                for dt, net, orders in d["moments"]:
                    bd, _sn = _shift_of(dt, cfg)
                    if start <= bd <= end:
                        add_series(dt.hour, f"{dt.hour:02d}:00", net, orders)
        elif d.get("moments") is not None:          # запасной путь: по часам
            for dt, net, orders in d["moments"]:
                bd, _sn = _shift_of(dt, cfg)
                if start <= bd <= end:
                    acc["net"] += net; acc["orders"] += orders
                    add_series(dt.hour if group == "hour" else bd.isoformat(), f"{dt.hour:02d}:00" if group == "hour" else bd.isoformat(), net, orders)
                elif pstart <= bd <= pend:
                    prev["net"] += net; prev["orders"] += orders
        tot["net"] += acc["net"]; tot["orders"] += acc["orders"]
        items.append(d.get("items") or [])
        locs.append([{"label": a["name"], "id": a["idx"], "net": acc["net"], "orders": acc["orders"]}])
    if not has_extra:
        for k in ("returns", "discounts", "refunds"):
            tot[k] = None
    res["totals"], res["prev"] = tot, prev
    first_h = min((int(x["start"]) for x in cfg), default=0)
    res["series"] = sorted(series.values(), key=lambda r: (r["id"] - first_h) % 24 if isinstance(r["id"], int) else str(r["id"]))
    res["by_item"] = _merge(items, "net")
    res["by_location"] = _merge(locs, "net") if len(targets) > 1 else []
    res["by_weekday"] = sorted(wds.values(), key=lambda r: r["id"])
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
        try:
            sr = shift_report("week", "all")
            if sr.get("rows"):
                lines.append("По сменам за 7 дней (дневная 09–21, ночная 21–09): " + "; ".join(
                    f"{b['date']} {b['shift']}: {f(b['net'])} ₸/{f(b['orders'])} зак." for b in sr["rows"][:14]))
        except Exception:
            pass
        if m.get("errors"):
            lines.append("(часть отчётов недоступна: " + "; ".join(m["errors"])[:300] + ")")
    except Exception as e:
        lines.append(f"ошибка загрузки — {e}")
    return "\n".join(lines)


# ---------------------------------------------------------------- смены
DEFAULT_SHIFTS = [{"name": "Дневная", "start": 9, "end": 21}, {"name": "Ночная", "start": 21, "end": 9}]


def shifts_cfg() -> list[dict]:
    try:
        import memory
        v = json.loads(memory.get_setting("shifts", "") or "[]")
        if isinstance(v, list) and v:
            return v
    except Exception:
        pass
    return DEFAULT_SHIFTS


def _shift_of(dt: datetime, cfg: list[dict]):
    """Возвращает (дата начала смены, имя смены) для момента dt."""
    h = dt.hour
    for s in cfg:
        a, b = int(s["start"]), int(s["end"])
        if a < b and a <= h < b:
            return dt.date(), s["name"]
        if a > b and (h >= a or h < b):  # смена через полночь
            return (dt.date() if h >= a else dt.date() - timedelta(days=1)), s["name"]
    return dt.date(), "—"


_ISO = re.compile(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}):(\d{2})")


def _hour_of(v):
    if isinstance(v, dict):
        v = v.get("id", v.get("name", v.get("value")))
    m = re.search(r"\d{1,2}", str(v))
    return int(m.group(0)) if m else None


def _date_of(v):
    if isinstance(v, dict):
        v = v.get("id") or v.get("date") or v.get("name") or v.get("value")
    m = re.search(r"\d{4}-\d{2}-\d{2}", str(v))
    return date.fromisoformat(m.group(0)) if m else None


def _moments_day_hour(resp):
    """Строки «день × час» → [(datetime, net, orders)]."""
    out = []
    for r in resp.get("rows", []):
        dims = r.get("dims") or r.get("key") or {}
        d = h = None
        for k, v in dims.items():
            if "day" in k or "date" in k:
                d = _date_of(v)
            elif "hour" in k:
                h = _hour_of(v)
        if d is None or h is None:
            return None
        vals = r.get("values") or {}
        out.append((datetime(d.year, d.month, d.day, h), _num(vals.get("net")) or 0, _num(vals.get("orders")) or 0))
    return out


def _moments_receipts(resp):
    """Строки «чек» → [(datetime закрытия/открытия, net, 1)]."""
    out = []
    for r in resp.get("rows", []):
        txt = json.dumps(r.get("dims") or r.get("key") or {}, ensure_ascii=False)
        times = _ISO.findall(txt)
        if not times:
            return None
        d, hh, mm = times[-1]  # последнее время — обычно закрытие чека
        y, mo, da = map(int, d.split("-"))
        vals = r.get("values") or {}
        out.append((datetime(y, mo, da, int(hh), int(mm)), _num(vals.get("net")) or 0, 1))
    return out


def shift_report(period: str = "week", loc: str = "all") -> dict:
    if not connected():
        return {"connected": False}
    return _cached(f"shift:{period}:{loc}", lambda: _build_shifts(period, loc), 25 if period in ("today", "yesterday") else CACHE_SECONDS)


def _build_shifts(period: str, loc: str) -> dict:
    start, end, *_ = _range(period)
    cfg = shifts_cfg()
    targets = _targets(loc)
    res = {"connected": True, "from": start.isoformat(), "to": end.isoformat(), "shifts": cfg, "errors": [], "method": "register_shifts"}
    buckets = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        datas = list(pool.map(lambda t: (t, _acc_data(t[0], t[1], period)), targets))
    open_now = None
    for (a, lid), d in datas:
        if d.get("shifts") is None:
            # запасной путь — по часам
            moments = d.get("moments")
            if moments is None:
                res["errors"].append(f"{a['name']}: " + "; ".join(d["errors"]))
                continue
            res["method"] = "day×hour"
            for dt, net, orders in moments:
                sd, sname = _shift_of(dt, cfg)
                if start <= sd <= end:
                    b = buckets.setdefault((sd.isoformat(), sname), {"date": sd.isoformat(), "shift": sname, "net": 0.0, "orders": 0.0, "by_point": {}, "registers": []})
                    b["net"] += net; b["orders"] += orders
                    b["by_point"][a["name"]] = b["by_point"].get(a["name"], 0) + net
            continue
        for sh in d["shifts"]:
            sd, sname = _biz_of_shift(sh["opened"], cfg)
            if not (start <= sd <= end):
                continue
            b = buckets.setdefault((sd.isoformat(), sname), {"date": sd.isoformat(), "shift": sname, "net": 0.0, "orders": 0.0, "by_point": {}, "registers": []})
            b["net"] += sh["net"]; b["orders"] += sh["orders"]
            b["by_point"][a["name"]] = b["by_point"].get(a["name"], 0) + sh["net"]
            b["registers"].append({"point": a["name"], "workplace": sh["workplace"], "opened": sh["opened"].isoformat(timespec="minutes"),
                                   "closed": sh["closed"].isoformat(timespec="minutes") if sh["closed"] else None, "status": sh["status"],
                                   "net": sh["net"], "orders": sh["orders"]})
            if sh["status"] == "open":
                open_now = (sd.isoformat(), sname)
    for b in buckets.values():
        regs = b["registers"]
        b["opened"] = min((r["opened"] for r in regs), default=None)
        b["closed"] = None if any(r["status"] == "open" for r in regs) else max((r["closed"] or "" for r in regs), default=None) or None
        b["open"] = any(r["status"] == "open" for r in regs)
        regs.sort(key=lambda r: (r["point"], r["workplace"] or ""))
    order = {s_["name"]: i for i, s_ in enumerate(cfg)}
    rows = sorted(buckets.values(), key=lambda b: (b["date"], order.get(b["shift"], 9)), reverse=True)
    res["rows"] = rows
    res["totals"] = {s_["name"]: {"net": sum(b["net"] for b in rows if b["shift"] == s_["name"]),
                                  "orders": sum(b["orders"] for b in rows if b["shift"] == s_["name"]),
                                  "count": sum(1 for b in rows if b["shift"] == s_["name"])} for s_ in cfg}
    seen = {p for b in rows for p in b["by_point"]}
    res["points"] = [a["name"] for a, _ in targets if a["name"] in seen] if len(targets) > 1 else []
    if open_now:
        res["current"] = {"date": open_now[0], "shift": open_now[1]}
    else:
        try:
            from zoneinfo import ZoneInfo
            now = datetime.now(ZoneInfo(os.getenv("APP_TZ", "Asia/Almaty"))).replace(tzinfo=None)
        except Exception:
            now = datetime.utcnow() + timedelta(hours=5)
        cd, cs = _shift_of(now, cfg)
        res["current"] = {"date": cd.isoformat(), "shift": cs}
    res["errors"] = list(dict.fromkeys(res["errors"]))
    return res


# ---------------------------------------------------------------- заказы и товары
def _pick(d: dict, *keys):
    for k in keys:
        cur = d
        for part in k.split("."):
            cur = cur.get(part) if isinstance(cur, dict) else None
        if cur not in (None, ""):
            return cur
    return None


def _money(v):
    if isinstance(v, dict):
        v = v.get("amount", v.get("value"))
    return _num(v)


def orders(period: str = "today", loc: str = "all", limit: int = 150) -> dict:
    if not connected():
        return {"connected": False}
    return _cached(f"orders:{period}:{loc}", lambda: _orders(period, loc, limit), 25)


def _orders(period, loc, limit):
    start, end, *_ = _range(period)
    tz = os.getenv("APP_TZ_OFFSET", "+05:00")
    after = f"{start.isoformat()}T00:00:00{tz}"
    res = {"connected": True, "from": start.isoformat(), "to": end.isoformat(), "orders": [], "errors": [], "sample_keys": []}

    def load(a, lid):
        p = {"created_after": after, "limit": 200}
        if lid:
            p["location_id"] = lid
        return _pages(a["key"], "/orders", p, 5).get("rows", [])

    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = [(a, pool.submit(load, a, lid)) for a, lid in _targets(loc)]
    for a, f in futs:
        try:
            rows = f.result()
        except Exception as e:
            res["errors"].append(f"{a['name']}: {e}")
            continue
        if rows and not res["sample_keys"]:
            res["sample_keys"] = sorted(rows[0].keys())[:40]
        for o in rows:
            if not isinstance(o, dict) or o.get("deleted"):
                continue
            created = str(_pick(o, "created_at", "opened_at", "closed_at", "time", "date") or "")
            if created[:10] and created[:10] > end.isoformat():
                continue
            lines = _pick(o, "lines", "items", "positions") or []
            res["orders"].append({
                "acc": a["idx"], "id": _pick(o, "id"), "number": _pick(o, "number", "num", "receipt_number"),
                "point": a["name"], "status": _pick(o, "status", "state"),
                "type": _pick(o, "fulfillment.type", "type", "source", "channel"),
                "total": _money(_pick(o, "total", "total_with_tip", "sum", "amount", "net")),
                "created": created, "lines": len(lines) if isinstance(lines, list) else None,
                "customer": _pick(o, "customer.name", "client.name", "customer_name"),
            })
    res["orders"].sort(key=lambda o: o["created"] or "", reverse=True)
    res["count"] = len(res["orders"])
    res["sum"] = sum(o["total"] or 0 for o in res["orders"])
    res["orders"] = res["orders"][:limit]
    return res


def order_detail(acc: int, oid: str) -> dict:
    a = next((x for x in accounts() if x["idx"] == int(acc)), None)
    if not a:
        raise RuntimeError("Аккаунт не найден")
    o = _get(a["key"], f"/orders/{oid}")
    o = o.get("data", o) if isinstance(o, dict) and isinstance(o.get("data"), dict) else o
    lines, names = [], item_names(a)
    for ln in _pick(o, "lines", "items", "positions") or []:
        if isinstance(ln, dict):
            iid = _pick(ln, "item_id", "item.id")
            lines.append({"name": _pick(ln, "name", "item.name", "title", "item_name") or names.get(str(iid)) or iid,
                          "qty": _num(_pick(ln, "quantity", "qty", "count")),
                          "price": _money(_pick(ln, "price", "unit_price")),
                          "total": _money(_pick(ln, "total", "sum", "amount"))})
    return {"order": o, "lines": lines}


def items_report(period: str = "week", loc: str = "all") -> dict:
    if not connected():
        return {"connected": False}
    return _cached(f"items:{period}:{loc}", lambda: _items(period, loc), 60 if period == "today" else 300)


def _items(period, loc):
    rep = report(period, loc)
    sold = {r["label"]: r["net"] for r in rep.get("by_item", [])}
    catalog = {}
    errors = list(rep.get("errors", []))
    for a, _ in _targets(loc):
        try:
            for it in _pages(a["key"], "/items", {}, 15).get("rows", []):
                if not isinstance(it, dict) or it.get("deleted"):
                    continue
                name = it.get("name") or str(it.get("id"))
                c = catalog.setdefault(name, {"name": name, "price": _money(it.get("price")), "category": _pick(it, "category.name", "category_name", "group.name"),
                                              "points": [], "sold": 0.0})
                if a["name"] not in c["points"]:
                    c["points"].append(a["name"])
        except Exception as e:
            errors.append(f"{a['name']}: {e}")
    for name, net in sold.items():
        catalog.setdefault(name, {"name": name, "price": None, "category": None, "points": [], "sold": 0.0})["sold"] = net
    items = sorted(catalog.values(), key=lambda x: (-(x["sold"] or 0), x["name"]))
    return {"connected": True, "from": rep.get("from"), "to": rep.get("to"), "items": items, "errors": list(dict.fromkeys(errors))}


def metrics_catalog():
    """Список доступных показателей Paloma (для подбора «чеков»)."""
    out = {}
    for a in accounts()[:1]:
        out[a["name"]] = _get(a["key"], "/reports/metrics/catalog")
    return out


# ---------- выручка по сменам для закупа ----------
def _point_name(a) -> str:
    """Имя точки аккаунта так, как оно записано в закупе (28а / 13 / Озен Food house)."""
    try:
        import memory
        pts = memory.points()
    except Exception:
        pts = []
    if a["name"] in pts:
        return a["name"]
    return pts[a["idx"]] if a["idx"] < len(pts) else a["name"]


def revenue(date_from: str, date_to: str) -> dict:
    """Выручка по настоящим сменам касс за интервал дат (включительно): {точка: {дата: {day, night, total, orders, open}}}.
    Дата — рабочий день: дневная смена (~09:00) + ночная (~21:00 до утра). Один запрос на точку, с кэшем."""
    if not connected():
        return {"connected": False, "points": {}, "errors": []}
    a, b = date.fromisoformat(date_from), date.fromisoformat(date_to)
    t = today()
    b = min(b, t)
    if a > b:
        return {"connected": True, "points": {}, "errors": []}

    def load():
        cfg = shifts_cfg()
        kind = {s_["name"]: ("day" if i == 0 else "night") for i, s_ in enumerate(cfg)}
        out, errs = {}, []
        for acc in accounts():              # по очереди: Paloma даёт один отчёт за раз
            name = _point_name(acc)
            try:
                for sh in _register_shifts(acc, None, a - timedelta(days=1), min(b + timedelta(days=1), t)):
                    bd, sname = _biz_of_shift(sh["opened"], cfg)
                    if not (a <= bd <= b):
                        continue
                    r = out.setdefault(name, {}).setdefault(bd.isoformat(), {"day": 0.0, "night": 0.0, "total": 0.0, "orders": 0.0, "open": False})
                    r[kind.get(sname, "day")] += sh["net"]
                    r["total"] += sh["net"]
                    r["orders"] += sh["orders"]
                    if not sh["closed"]:
                        r["open"] = True
            except Exception as e:
                errs.append(f"{name}: {e}")
        return {"connected": True, "points": out, "errors": errs}
    recent = b >= t - timedelta(days=1)
    return _cached(f"rev:{a}:{b}", load, 60 if recent else 6 * 3600, stale=900 if recent else 24 * 3600)
