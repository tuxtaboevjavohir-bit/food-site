"""Память заведения Food house: факты, меню и история задач.

Если задана переменная DATABASE_URL (Postgres, например бесплатный Neon), данные хранятся там
и не пропадают при перезапуске. Иначе используется локальный файл foodhouse.db (SQLite).
"""
import json
import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
USE_PG = DATABASE_URL.startswith(("postgres://", "postgresql://"))
DB_PATH = Path(__file__).parent / "foodhouse.db"
_lock = threading.Lock()


def _connect():
    if USE_PG:
        import psycopg
        from psycopg.rows import dict_row

        return psycopg.connect(DATABASE_URL, row_factory=dict_row)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _run(sql: str, params=(), fetch: str | None = None, many: bool = False):
    """Выполнить запрос в любой из двух баз. В SQL пишем «?», для Postgres меняем на %s."""
    if USE_PG:
        sql = sql.replace("?", "%s")
    with _lock:
        conn = _connect()
        try:
            cur = conn.cursor()
            if many:
                cur.executemany(sql, params)
            else:
                cur.execute(sql, params)
            rows = None
            if fetch == "all":
                rows = [dict(r) for r in cur.fetchall()]
            elif fetch == "one":
                r = cur.fetchone()
                rows = dict(r) if r else None
            conn.commit()
            return rows
        finally:
            conn.close()


def _now():
    return datetime.now().isoformat(timespec="seconds")


def init_db():
    pk = "SERIAL PRIMARY KEY" if USE_PG else "INTEGER PRIMARY KEY AUTOINCREMENT"
    _run(f"CREATE TABLE IF NOT EXISTS facts (id {pk}, text TEXT NOT NULL, created TEXT NOT NULL)")
    _run(f"CREATE TABLE IF NOT EXISTS menu (id {pk}, category TEXT, name TEXT NOT NULL, price REAL, description TEXT)")
    _run(f"CREATE TABLE IF NOT EXISTS tasks (id {pk}, text TEXT NOT NULL, result TEXT, created TEXT NOT NULL)")
    _run(
        f"CREATE TABLE IF NOT EXISTS wa_messages (id {pk}, phone TEXT NOT NULL, name TEXT, direction TEXT NOT NULL, "
        "text TEXT, author TEXT, created TEXT NOT NULL)"
    )
    _run("CREATE TABLE IF NOT EXISTS settings (k TEXT PRIMARY KEY, v TEXT)")
    _run(
        f"CREATE TABLE IF NOT EXISTS stock (id {pk}, name TEXT NOT NULL, category TEXT, qty REAL NOT NULL DEFAULT 0, "
        "unit TEXT NOT NULL, min_qty REAL, point TEXT, price REAL, updated TEXT)"
    )
    _run(
        f"CREATE TABLE IF NOT EXISTS staff (id {pk}, name TEXT NOT NULL, role TEXT, point TEXT, shift TEXT, pin TEXT NOT NULL, "
        "phone TEXT, active INTEGER NOT NULL DEFAULT 1, created TEXT)"
    )
    _run(
        f"CREATE TABLE IF NOT EXISTS order_events (id {pk}, order_key TEXT NOT NULL, point TEXT, number TEXT, kind TEXT NOT NULL, "
        "staff_id INTEGER, ts TEXT NOT NULL, order_created TEXT, total REAL)"
    )
    _run(f"CREATE TABLE IF NOT EXISTS attendance (id {pk}, staff_id INTEGER NOT NULL, point TEXT, kind TEXT NOT NULL, ts TEXT NOT NULL, lat REAL, lng REAL)")
    _run(f"CREATE TABLE IF NOT EXISTS stock_log (id {pk}, stock_id INTEGER, delta REAL, unit TEXT, note TEXT, created TEXT)")
    _run(f"CREATE TABLE IF NOT EXISTS suppliers (id {pk}, name TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, sort INTEGER, created TEXT)")
    _run(
        f"CREATE TABLE IF NOT EXISTS purchases (id {pk}, supplier_id INTEGER NOT NULL, kind TEXT NOT NULL, amount REAL NOT NULL, "
        "day TEXT NOT NULL, point TEXT, note TEXT, created TEXT)"
    )
    try:
        _run("ALTER TABLE purchases ADD COLUMN IF NOT EXISTS src TEXT" if USE_PG else "ALTER TABLE purchases ADD COLUMN src TEXT")
    except Exception:
        pass  # колонка уже есть
    if _run("SELECT COUNT(*) AS n FROM suppliers", fetch="one")["n"] == 0:
        _run("INSERT INTO suppliers(name, active, sort, created) VALUES (?, 1, ?, ?)",
             [(n, i, _now()) for i, n in enumerate(DEFAULT_SUPPLIERS)], many=True)
    if _run("SELECT COUNT(*) AS n FROM facts", fetch="one")["n"] == 0:
        _run(
            "INSERT INTO facts(text, created) VALUES (?, ?)",
            [
                ("Заведение называется Food house, формат — быстрое питание (кафе/фастфуд).", _now()),
                ("Валюта расчётов — тенге (₸).", _now()),
            ],
            many=True,
        )


# ---------- факты ----------
def add_fact(text: str):
    _run("INSERT INTO facts(text, created) VALUES (?, ?)", (text.strip(), _now()))


def delete_fact(fact_id: int):
    _run("DELETE FROM facts WHERE id = ?", (fact_id,))


def list_facts():
    return _run("SELECT * FROM facts ORDER BY id", fetch="all")


# ---------- меню ----------
def _menu_rows(items: list[dict]):
    rows = []
    for it in items:
        price = it.get("price")
        try:
            price = float(price) if price not in (None, "") else None
        except (TypeError, ValueError):
            price = None
        rows.append((it.get("category") or "Без категории", it.get("name") or "?", price, it.get("description") or ""))
    return rows


def replace_menu(items: list[dict]):
    _run("DELETE FROM menu")
    add_menu_items(items)


def add_menu_items(items: list[dict]):
    rows = _menu_rows(items)
    if rows:
        _run("INSERT INTO menu(category, name, price, description) VALUES (?, ?, ?, ?)", rows, many=True)


def list_menu():
    return _run("SELECT * FROM menu ORDER BY category, id", fetch="all")


# ---------- история ----------
def save_task(text: str, result: dict):
    _run(
        "INSERT INTO tasks(text, result, created) VALUES (?, ?, ?)",
        (text, json.dumps(result, ensure_ascii=False), _now()),
    )


def list_tasks(limit: int = 20):
    rows = _run("SELECT * FROM tasks ORDER BY id DESC LIMIT ?", (limit,), fetch="all")
    for d in rows:
        d["result"] = json.loads(d["result"] or "{}")
    return rows


# ---------- настройки ----------
def get_setting(k: str, default: str = "") -> str:
    row = _run("SELECT v FROM settings WHERE k = ?", (k,), fetch="one")
    return row["v"] if row else default


def set_setting(k: str, v: str):
    _run("DELETE FROM settings WHERE k = ?", (k,))
    _run("INSERT INTO settings(k, v) VALUES (?, ?)", (k, v))


# ---------- точки ----------
DEFAULT_POINTS = ["28а Food house", "13 Food house", "Озен Food house"]


def points() -> list[str]:
    try:
        v = json.loads(get_setting("points", "") or "[]")
        return v if isinstance(v, list) and v else DEFAULT_POINTS
    except ValueError:
        return DEFAULT_POINTS


def set_points(names: list[str]):
    set_setting("points", json.dumps([n.strip() for n in names if n.strip()], ensure_ascii=False))


# ---------- склад ----------
UNITS = {"g": ("g", 1), "kg": ("g", 1000), "ml": ("ml", 1), "l": ("ml", 1000), "pcs": ("pcs", 1)}


def convert(amount: float, frm: str, to: str) -> float:
    """Перевод между г↔кг и мл↔л; штуки не переводятся."""
    if frm == to:
        return amount
    a, b = UNITS.get(frm), UNITS.get(to)
    if not a or not b or a[0] != b[0]:
        raise ValueError(f"Нельзя перевести {frm} в {to}")
    return amount * a[1] / b[1]


def stock_list(point: str = ""):
    if point:
        return _run("SELECT * FROM stock WHERE point = ? ORDER BY category, name", (point,), fetch="all")
    return _run("SELECT * FROM stock ORDER BY point, category, name", fetch="all")


def stock_add(d: dict):
    unit = d.get("unit") if d.get("unit") in UNITS else "pcs"
    _run(
        "INSERT INTO stock(name, category, qty, unit, min_qty, point, price, updated) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (d["name"].strip(), (d.get("category") or "").strip(), float(d.get("qty") or 0), unit,
         d.get("min_qty"), d.get("point") or "", d.get("price"), _now()),
    )


def stock_update(sid: int, d: dict):
    unit = d.get("unit") if d.get("unit") in UNITS else "pcs"
    _run(
        "UPDATE stock SET name = ?, category = ?, qty = ?, unit = ?, min_qty = ?, point = ?, price = ?, updated = ? WHERE id = ?",
        (d["name"].strip(), (d.get("category") or "").strip(), float(d.get("qty") or 0), unit,
         d.get("min_qty"), d.get("point") or "", d.get("price"), _now(), sid),
    )


def stock_move(sid: int, amount: float, unit: str, note: str = ""):
    row = _run("SELECT * FROM stock WHERE id = ?", (sid,), fetch="one")
    if not row:
        raise ValueError("Позиция не найдена")
    delta = convert(float(amount), unit or row["unit"], row["unit"])
    new_qty = max(0.0, (row["qty"] or 0) + delta)
    _run("UPDATE stock SET qty = ?, updated = ? WHERE id = ?", (new_qty, _now(), sid))
    _run("INSERT INTO stock_log(stock_id, delta, unit, note, created) VALUES (?, ?, ?, ?, ?)", (sid, delta, row["unit"], note, _now()))


def stock_delete(sid: int):
    _run("DELETE FROM stock WHERE id = ?", (sid,))


def _fmt_qty(q, unit):
    q = q or 0
    if unit == "g" and q >= 1000:
        return f"{q / 1000:g} кг"
    if unit == "ml" and q >= 1000:
        return f"{q / 1000:g} л"
    return f"{q:g} " + {"g": "г", "kg": "кг", "ml": "мл", "l": "л", "pcs": "шт"}.get(unit, unit)


def stock_context() -> str:
    rows = stock_list()
    if not rows:
        return ""
    lines = ["\nСКЛАД (остатки):"]
    for r in rows[:300]:
        low = r["min_qty"] is not None and (r["qty"] or 0) <= r["min_qty"]
        mn = f", минимум {_fmt_qty(r['min_qty'], r['unit'])}" if r["min_qty"] is not None else ""
        lines.append(f"- [{r['point'] or 'без точки'}] {r['name']}: {_fmt_qty(r['qty'], r['unit'])}{mn}{' ⚠️ МАЛО' if low else ''}")
    return "\n".join(lines)


# ---------- персонал ----------
SHIFT_START = {"day": 9, "night": 21}
LATE_MIN = 10


def local_now():
    try:
        from zoneinfo import ZoneInfo
        import os as _os
        return datetime.now(ZoneInfo(_os.getenv("APP_TZ", "Asia/Almaty"))).replace(tzinfo=None)
    except Exception:
        from datetime import timedelta as _td
        return datetime.utcnow() + _td(hours=5)


def staff_list(active_only: bool = False):
    q = "SELECT * FROM staff" + (" WHERE active = 1" if active_only else "") + " ORDER BY point, name"
    return _run(q, fetch="all")


def _new_pin():
    import random
    used = {r["pin"] for r in _run("SELECT pin FROM staff", fetch="all")}
    while True:
        pin = f"{random.randint(1000, 9999)}"
        if pin not in used:
            return pin


def staff_add(d: dict):
    pin = (d.get("pin") or "").strip() or _new_pin()
    if _run("SELECT id FROM staff WHERE pin = ?", (pin,), fetch="one"):
        raise ValueError("Такой PIN уже занят")
    _run("INSERT INTO staff(name, role, point, shift, pin, phone, active, created) VALUES (?, ?, ?, ?, ?, ?, 1, ?)",
         (d["name"].strip(), d.get("role") or "", d.get("point") or "", d.get("shift") or "day", pin, d.get("phone") or "", _now()))


def staff_update(sid: int, d: dict):
    pin = (d.get("pin") or "").strip()
    if pin and _run("SELECT id FROM staff WHERE pin = ? AND id <> ?", (pin, sid), fetch="one"):
        raise ValueError("Такой PIN уже занят")
    cur = _run("SELECT * FROM staff WHERE id = ?", (sid,), fetch="one")
    if not cur:
        raise ValueError("Сотрудник не найден")
    _run("UPDATE staff SET name = ?, role = ?, point = ?, shift = ?, pin = ?, phone = ?, active = ? WHERE id = ?",
         (d.get("name", cur["name"]).strip(), d.get("role", cur["role"]), d.get("point", cur["point"]), d.get("shift", cur["shift"]),
          pin or cur["pin"], d.get("phone", cur["phone"]), 1 if d.get("active", cur["active"]) else 0, sid))


def staff_delete(sid: int):
    _run("UPDATE staff SET active = 0 WHERE id = ?", (sid,))


def checkin(pin: str, kind: str, lat=None, lng=None) -> dict:
    st = _run("SELECT * FROM staff WHERE pin = ? AND active = 1", (pin.strip(),), fetch="one")
    if not st:
        raise ValueError("Неверный PIN")
    last = _run("SELECT * FROM attendance WHERE staff_id = ? ORDER BY id DESC LIMIT 1", (st["id"],), fetch="one")
    now = local_now()
    if kind == "auto":
        kind = "out" if last and last["kind"] == "in" else "in"
    if last and last["kind"] == kind and (now - datetime.fromisoformat(last["ts"])).total_seconds() < 120:
        raise ValueError("Уже отмечено — подождите пару минут")
    ts = now.isoformat(timespec="seconds")
    _run("INSERT INTO attendance(staff_id, point, kind, ts, lat, lng) VALUES (?, ?, ?, ?, ?, ?)", (st["id"], st["point"], kind, ts, lat, lng))
    late = None
    if kind == "in":
        late = _late_minutes(st, now)
    return {"name": st["name"], "point": st["point"], "kind": kind, "ts": ts, "late": late}


def _late_minutes(st, t: datetime):
    start_h = SHIFT_START.get(st["shift"] or "day")
    if start_h is None:
        return None
    from datetime import timedelta as _td
    base = t.replace(hour=start_h, minute=0, second=0, microsecond=0)
    # ночная смена: приход до полудня считаем к вчерашнему 21:00 — это не опоздание
    if start_h >= 12 and t.hour < 12:
        base -= _td(days=1)
    diff = (t - base).total_seconds() / 60
    return int(diff) if LATE_MIN < diff < 4 * 60 else 0


def attendance_report(date_from: str, date_to: str, point: str = "") -> dict:
    """Пары приход→уход, часы, опоздания, сводка по сотрудникам, кто сейчас на смене."""
    from datetime import timedelta as _td
    staff = {s["id"]: s for s in staff_list()}
    d_from = datetime.fromisoformat(date_from)
    d_to = datetime.fromisoformat(date_to) + _td(days=1, hours=12)
    rows = _run("SELECT * FROM attendance WHERE ts >= ? AND ts < ? ORDER BY staff_id, ts",
                ((d_from - _td(hours=14)).isoformat(), d_to.isoformat()), fetch="all")
    now = local_now()
    visits, by_staff = [], {}
    for r in rows:
        by_staff.setdefault(r["staff_id"], []).append(r)
    for sid, evs in by_staff.items():
        st = staff.get(sid)
        if not st or (point and (st["point"] or "") != point):
            continue
        i = 0
        while i < len(evs):
            e = evs[i]
            if e["kind"] == "in":
                tin = datetime.fromisoformat(e["ts"])
                out = evs[i + 1] if i + 1 < len(evs) and evs[i + 1]["kind"] == "out" else None
                tout = datetime.fromisoformat(out["ts"]) if out else None
                i += 2 if out else 1
                if tin.date() < d_from.date() or tin.date() > datetime.fromisoformat(date_to).date():
                    continue
                open_ = tout is None
                hours = ((tout or now) - tin).total_seconds() / 3600
                if open_ and hours > 16:  # забыли отметить уход
                    hours, open_ = None, False
                visits.append({"staff_id": sid, "name": st["name"], "role": st["role"], "point": st["point"], "shift": st["shift"],
                               "date": tin.date().isoformat(), "in": tin.isoformat(timespec="minutes"),
                               "out": tout.isoformat(timespec="minutes") if tout else None,
                               "hours": round(hours, 2) if hours is not None else None, "late": _late_minutes(st, tin),
                               "open": open_, "no_out": tout is None and not open_})
            else:
                tout = datetime.fromisoformat(e["ts"])
                i += 1
                if d_from.date() <= tout.date() <= datetime.fromisoformat(date_to).date():
                    visits.append({"staff_id": sid, "name": st["name"], "role": st["role"], "point": st["point"], "shift": st["shift"],
                                   "date": tout.date().isoformat(), "in": None, "out": tout.isoformat(timespec="minutes"),
                                   "hours": None, "late": None, "open": False, "no_in": True})
    visits.sort(key=lambda v: (v["in"] or v["out"] or ""), reverse=True)
    summary = {}
    for v in visits:
        s = summary.setdefault(v["staff_id"], {"staff_id": v["staff_id"], "name": v["name"], "role": v["role"], "point": v["point"],
                                               "days": 0, "hours": 0.0, "lates": 0, "late_min": 0, "missing": 0})
        if v["in"]:
            s["days"] += 1
        s["hours"] += v["hours"] or 0
        if v["late"]:
            s["lates"] += 1
            s["late_min"] += v["late"]
        if v.get("no_out") or v.get("no_in"):
            s["missing"] += 1
    on_shift = [v for v in visits if v["open"]]
    return {"visits": visits, "summary": sorted(summary.values(), key=lambda x: -x["hours"]), "on_shift": on_shift,
            "now": now.isoformat(timespec="minutes"), "late_limit": LATE_MIN}


# ---------- кухня и доставка ----------
def order_event(order_key: str, kind: str, point: str = "", number=None, staff_id=None, order_created=None, total=None):
    if _run("SELECT id FROM order_events WHERE order_key = ? AND kind = ?", (order_key, kind), fetch="one"):
        raise ValueError("Уже отмечено")
    _run("INSERT INTO order_events(order_key, point, number, kind, staff_id, ts, order_created, total) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
         (order_key, point, str(number or ""), kind, staff_id, local_now().isoformat(timespec="seconds"), order_created, total))


def order_marks(keys: list[str]) -> dict:
    """{order_key: {kind: {ts, staff_id}}} для списка заказов."""
    out = {}
    for i in range(0, len(keys), 200):
        chunk = keys[i:i + 200]
        if not chunk:
            continue
        q = "SELECT * FROM order_events WHERE order_key IN (" + ",".join("?" * len(chunk)) + ")"
        for r in _run(q, tuple(chunk), fetch="all"):
            out.setdefault(r["order_key"], {})[r["kind"]] = {"ts": r["ts"], "staff_id": r["staff_id"]}
    return out


def staff_by_pin(pin: str):
    return _run("SELECT * FROM staff WHERE pin = ? AND active = 1", ((pin or "").strip(),), fetch="one")


def _mins(a, b):
    try:
        return round((datetime.fromisoformat(b[:19]) - datetime.fromisoformat(a[:19])).total_seconds() / 60, 1)
    except Exception:
        return None


PREP_NORM, DELIVERY_NORM = 15, 40


def ops_report(date_from: str, date_to: str, point: str = "") -> dict:
    """Время приготовления и доставки по отметкам кухни и курьеров."""
    from datetime import timedelta as _td
    rows = _run("SELECT * FROM order_events WHERE ts >= ? AND ts < ? ORDER BY ts", (date_from, (datetime.fromisoformat(date_to) + _td(days=1, hours=12)).isoformat()), fetch="all")
    staff = {s["id"]: s for s in staff_list()}
    orders = {}
    for r in rows:
        if point and (r["point"] or "") != point:
            continue
        o = orders.setdefault(r["order_key"], {"key": r["order_key"], "point": r["point"], "number": r["number"], "created": r["order_created"], "total": r["total"]})
        o[r["kind"]] = r["ts"]
        if r["staff_id"]:
            o[r["kind"] + "_by"] = staff.get(r["staff_id"], {}).get("name")
        o["created"] = o["created"] or r["order_created"]
    out = []
    for o in orders.values():
        o["prep"] = _mins(o["created"], o["ready"]) if o.get("created") and o.get("ready") else None
        o["wait"] = _mins(o["ready"], o["picked"]) if o.get("ready") and o.get("picked") else None
        o["delivery"] = _mins(o["picked"], o["delivered"]) if o.get("picked") and o.get("delivered") else None
        o["total_time"] = _mins(o["created"], o["delivered"]) if o.get("created") and o.get("delivered") else None
        out.append(o)
    out.sort(key=lambda o: o.get("created") or o.get("ready") or o.get("picked") or "", reverse=True)
    avg = lambda xs: round(sum(xs) / len(xs), 1) if xs else None
    preps = [o["prep"] for o in out if o["prep"] is not None and 0 <= o["prep"] < 240]
    dels = [o["delivery"] for o in out if o["delivery"] is not None and 0 <= o["delivery"] < 300]
    couriers = {}
    for o in out:
        if o.get("delivered_by") or o.get("picked_by"):
            name = o.get("delivered_by") or o.get("picked_by")
            c = couriers.setdefault(name, {"name": name, "count": 0, "times": [], "late": 0})
            c["count"] += 1
            if o["delivery"] is not None:
                c["times"].append(o["delivery"])
                if o["delivery"] > DELIVERY_NORM:
                    c["late"] += 1
    cour = [{"name": c["name"], "count": c["count"], "avg": avg(c["times"]), "max": max(c["times"]) if c["times"] else None, "late": c["late"]} for c in couriers.values()]
    cour.sort(key=lambda c: (c["avg"] is None, c["avg"] or 0))
    hours = {}
    for o in out:
        if o["prep"] is not None and o.get("created"):
            hours.setdefault(o["created"][11:13], []).append(o["prep"])
    return {"orders": out[:300], "prep_avg": avg(preps), "prep_max": max(preps) if preps else None, "prep_slow": sum(1 for p in preps if p > PREP_NORM),
            "delivery_avg": avg(dels), "delivery_max": max(dels) if dels else None, "delivery_late": sum(1 for d in dels if d > DELIVERY_NORM),
            "couriers": cour, "prep_by_hour": [{"hour": h, "avg": avg(v), "count": len(v)} for h, v in sorted(hours.items())],
            "norms": {"prep": PREP_NORM, "delivery": DELIVERY_NORM}}


def ops_context() -> str:
    from datetime import timedelta as _td
    t = local_now().date()
    r = ops_report((t - _td(days=6)).isoformat(), t.isoformat())
    if not r["orders"]:
        return ""
    lines = [f"\nКУХНЯ И ДОСТАВКА (7 дней, по отметкам): приготовление в среднем {r['prep_avg']} мин (дольше {PREP_NORM} мин: {r['prep_slow']}), "
             f"доставка в среднем {r['delivery_avg']} мин (дольше {DELIVERY_NORM} мин: {r['delivery_late']})."]
    if r["couriers"]:
        lines.append("Курьеры: " + "; ".join(f"{c['name']}: {c['count']} доставок, ср. {c['avg']} мин, опозданий {c['late']}" for c in r["couriers"]))
    if r["prep_by_hour"]:
        lines.append("Время приготовления по часам: " + "; ".join(f"{h['hour']}:00 — {h['avg']} мин" for h in r["prep_by_hour"]))
    return "\n".join(lines)


def attendance_context() -> str:
    from datetime import timedelta as _td
    if not _run("SELECT id FROM staff LIMIT 1", fetch="one"):
        return ""
    t = local_now().date()
    rep = attendance_report((t - _td(days=6)).isoformat(), t.isoformat())
    lines = ["\nПЕРСОНАЛ (учёт прихода/ухода за 7 дней; смены: дневная с 09:00, ночная с 21:00, опоздание — больше 10 мин):"]
    for s in rep["summary"]:
        lines.append(f"- {s['name']} ({s['role'] or 'сотрудник'}, {s['point'] or 'без точки'}): {s['days']} смен, {s['hours']:.1f} ч, "
                     f"опозданий {s['lates']} (всего {s['late_min']} мин), без отметки ухода/прихода {s['missing']}")
    if rep["on_shift"]:
        lines.append("Сейчас на смене: " + ", ".join(f"{v['name']} ({v['point']}, с {v['in'][11:]})" for v in rep["on_shift"]))
    return "\n".join(lines)


# ---------- WhatsApp ----------
def wa_add(phone: str, direction: str, text: str, name: str = "", author: str = ""):
    _run(
        "INSERT INTO wa_messages(phone, name, direction, text, author, created) VALUES (?, ?, ?, ?, ?, ?)",
        (phone, name, direction, text, author, _now()),
    )


def wa_chats():
    rows = _run("SELECT * FROM wa_messages ORDER BY id DESC LIMIT 2000", fetch="all")
    chats = {}
    for r in rows:
        c = chats.setdefault(r["phone"], {"phone": r["phone"], "name": "", "last": r["text"], "time": r["created"], "count": 0})
        c["count"] += 1
        if r["name"] and not c["name"]:
            c["name"] = r["name"]
    return list(chats.values())


def wa_history(phone: str, limit: int = 50):
    rows = _run("SELECT * FROM wa_messages WHERE phone = ? ORDER BY id DESC LIMIT ?", (phone, limit), fetch="all")
    return list(reversed(rows))


def wa_recent_incoming(limit: int = 300):
    return _run("SELECT * FROM wa_messages WHERE direction = 'in' ORDER BY id DESC LIMIT ?", (limit,), fetch="all")


def memory_context() -> str:
    """Текст с памятью заведения, который получает каждый агент."""
    facts = list_facts()
    menu = list_menu()
    lines = ["ПАМЯТЬ ЗАВЕДЕНИЯ:", f"- Точки сети: {', '.join(points())}."]
    lines += [f"- {f['text']}" for f in facts] or ["- (пока пусто)"]
    lines.append("\nМЕНЮ:")
    if menu:
        for m in menu[:200]:
            price = f"{m['price']:.0f} ₸" if m["price"] is not None else "цена не указана"
            lines.append(f"- [{m['category']}] {m['name']} — {price}")
    else:
        lines.append("- меню ещё не загружено")
    return "\n".join(lines) + stock_context() + attendance_context() + ops_context() + purchases_context()


# ---------- закуп: поставщики, закупы и оплаты ----------
DEFAULT_SUPPLIERS = ["НУРИЯ АПА", "ТАНШОЛПАН", "ЕТ", "ЛАВАШ", "ОВОЩИ", "ЧИПСЫ"]
MONTHS_RU = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]


def supplier_list(active_only: bool = False):
    q = "SELECT * FROM suppliers" + (" WHERE active = 1" if active_only else "") + " ORDER BY active DESC, sort, id"
    return _run(q, fetch="all")


def supplier_add(name: str):
    name = name.strip()
    if not name:
        raise ValueError("Введите название поставщика")
    ex = _run("SELECT id FROM suppliers WHERE UPPER(name) = UPPER(?)", (name,), fetch="one")
    if ex:
        _run("UPDATE suppliers SET active = 1 WHERE id = ?", (ex["id"],))
        return
    n = _run("SELECT COALESCE(MAX(sort), 0) AS m FROM suppliers", fetch="one")["m"] or 0
    _run("INSERT INTO suppliers(name, active, sort, created) VALUES (?, 1, ?, ?)", (name, n + 1, _now()))


def supplier_update(sid: int, name: str, active: bool = True):
    if not name.strip():
        raise ValueError("Введите название поставщика")
    _run("UPDATE suppliers SET name = ?, active = ? WHERE id = ?", (name.strip(), 1 if active else 0, sid))


def supplier_delete(sid: int):
    """Записи остаются в отчётах; поставщик просто скрывается из списка."""
    if _run("SELECT COUNT(*) AS n FROM purchases WHERE supplier_id = ?", (sid,), fetch="one")["n"]:
        _run("UPDATE suppliers SET active = 0 WHERE id = ?", (sid,))
    else:
        _run("DELETE FROM suppliers WHERE id = ?", (sid,))


def _purchase_clean(d: dict) -> tuple:
    kind = d.get("kind") if d.get("kind") in ("buy", "pay") else "buy"
    amount = float(d.get("amount") or 0)
    if amount <= 0:
        raise ValueError("Введите сумму больше нуля")
    day = (d.get("day") or local_now().date().isoformat())[:10]
    datetime.strptime(day, "%Y-%m-%d")
    sid = int(d.get("supplier_id") or 0)
    if not _run("SELECT id FROM suppliers WHERE id = ?", (sid,), fetch="one"):
        raise ValueError("Выберите поставщика")
    return sid, kind, round(amount, 2), day, (d.get("point") or "").strip(), (d.get("note") or "").strip()


def purchase_add(d: dict):
    _run("INSERT INTO purchases(supplier_id, kind, amount, day, point, note, created) VALUES (?, ?, ?, ?, ?, ?, ?)",
         _purchase_clean(d) + (_now(),))


def purchase_update(pid: int, d: dict):
    _run("UPDATE purchases SET supplier_id = ?, kind = ?, amount = ?, day = ?, point = ?, note = ? WHERE id = ?",
         _purchase_clean(d) + (pid,))


def purchase_delete(pid: int):
    _run("DELETE FROM purchases WHERE id = ?", (pid,))


def purchase_day_get(day: str) -> dict:
    """Дневной закуп таблицей: точка × поставщик (как в общей таблице закупа)."""
    datetime.strptime(day, "%Y-%m-%d")
    cells, saved = {}, None
    for r in _run("SELECT * FROM purchases WHERE day = ? AND kind = 'buy' AND src = 'day'", (day,), fetch="all"):
        c = cells.setdefault(r["point"] or "", {})
        c[str(r["supplier_id"])] = c.get(str(r["supplier_id"]), 0) + r["amount"]
        saved = max(saved or "", r["created"] or "")
    other = _run("SELECT COUNT(*) AS n FROM purchases WHERE day = ? AND (src IS NULL OR src <> 'day')", (day,), fetch="one")["n"]
    return {"day": day, "cells": cells, "saved": saved, "other": other,
            "suppliers": supplier_list(active_only=True), "points": points()}


def purchase_day_save(day: str, cells: dict) -> int:
    """Перезаписать дневной закуп за дату. cells = {точка: {id поставщика: сумма}}."""
    datetime.strptime(day, "%Y-%m-%d")
    valid = {s["id"] for s in supplier_list()}
    rows, now = [], _now()
    for pt, sup in (cells or {}).items():
        for sid, amt in (sup or {}).items():
            try:
                sid, amt = int(sid), float(amt or 0)
            except (TypeError, ValueError):
                continue
            if amt > 0 and sid in valid:
                rows.append((sid, "buy", round(amt, 2), day, pt or "", "", now, "day"))
    _run("DELETE FROM purchases WHERE day = ? AND src = 'day'", (day,))
    if rows:
        _run("INSERT INTO purchases(supplier_id, kind, amount, day, point, note, created, src) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows, many=True)
    return len(rows)


def purchase_report(year: int, month: int = 0, point: str = "") -> dict:
    """Отчёт по поставщикам за год (month=0) или за месяц.
    Долг считается накопительно: долг на начало + закуп − оплачено = долг на конец."""
    if month:
        start = f"{year:04d}-{month:02d}-01"
        end = f"{year + (month == 12):04d}-{month % 12 + 1:02d}-01"
    else:
        start, end = f"{year:04d}-01-01", f"{year + 1:04d}-01-01"
    pq, pp = (" AND p.point = ?", (point,)) if point else ("", ())
    sup = supplier_list()
    rows = {s["id"]: {"id": s["id"], "name": s["name"], "active": s["active"], "opening": 0.0, "buy": 0.0, "pay": 0.0,
                      "months": [0.0] * 12, "months_pay": [0.0] * 12, "count": 0} for s in sup}
    for r in _run("SELECT supplier_id, kind, SUM(amount) AS s FROM purchases p WHERE day < ?" + pq + " GROUP BY supplier_id, kind",
                  (start,) + pp, fetch="all"):
        if r["supplier_id"] in rows:
            rows[r["supplier_id"]]["opening"] += (r["s"] or 0) * (1 if r["kind"] == "buy" else -1)
    recs = _run("SELECT p.*, s.name AS supplier FROM purchases p LEFT JOIN suppliers s ON s.id = p.supplier_id "
                "WHERE day >= ? AND day < ?" + pq + " ORDER BY day DESC, p.id DESC", (start, end) + pp, fetch="all")
    for r in recs:
        x = rows.get(r["supplier_id"])
        if not x:
            continue
        m = int(r["day"][5:7]) - 1
        x[r["kind"]] += r["amount"]
        (x["months"] if r["kind"] == "buy" else x["months_pay"])[m] += r["amount"]
        x["count"] += 1
    out = []
    for x in rows.values():
        x["closing"] = x["opening"] + x["buy"] - x["pay"]
        if x["active"] or x["count"] or abs(x["opening"]) > 0.005:
            out.append(x)
    tot = {k: sum(x[k] for x in out) for k in ("opening", "buy", "pay", "closing")}
    tot["months"] = [sum(x["months"][i] for x in out) for i in range(12)]
    tot["months_pay"] = [sum(x["months_pay"][i] for x in out) for i in range(12)]
    # таблица «по дням»: дата × точка × поставщик (только закуп)
    daily = {}
    for r in recs:
        if r["kind"] != "buy":
            continue
        k = (r["day"], r["point"] or "")
        d = daily.setdefault(k, {"day": r["day"], "point": r["point"] or "", "cells": {}, "total": 0.0})
        d["cells"][str(r["supplier_id"])] = d["cells"].get(str(r["supplier_id"]), 0) + r["amount"]
        d["total"] += r["amount"]
    order = {p: i for i, p in enumerate(points())}
    daily = sorted(daily.values(), key=lambda d: (d["day"], -order.get(d["point"], 99)), reverse=True)
    # по месяцам и точкам (для годового PDF)
    pm = {}
    for r in recs:
        if r["kind"] == "buy":
            k = (int(r["day"][5:7]), r["point"] or "")
            d = pm.setdefault(k, {"month": k[0], "point": k[1], "cells": {}, "total": 0.0})
            d["cells"][str(r["supplier_id"])] = d["cells"].get(str(r["supplier_id"]), 0) + r["amount"]
            d["total"] += r["amount"]
    pm = sorted(pm.values(), key=lambda d: (d["month"], order.get(d["point"], 99)))
    return {"year": year, "month": month, "point": point, "from": start, "to": end,
            "suppliers": out, "totals": tot, "records": recs, "daily": daily, "point_months": pm}


def purchases_context() -> str:
    try:
        rep = purchase_report(local_now().year)
    except Exception:
        return ""
    if not any(x["count"] or x["opening"] for x in rep["suppliers"]):
        return ""
    lines = [f"\nЗАКУП {rep['year']} (по поставщикам: закуп / оплачено / долг на сегодня):"]
    for x in rep["suppliers"]:
        lines.append(f"- {x['name']}: {x['buy']:,.0f} ₸ / {x['pay']:,.0f} ₸ / долг {x['closing']:,.0f} ₸".replace(",", " "))
    t = rep["totals"]
    lines.append(f"- ИТОГО: закуп {t['buy']:,.0f} ₸, оплачено {t['pay']:,.0f} ₸, долг {t['closing']:,.0f} ₸".replace(",", " "))
    return "\n".join(lines)
