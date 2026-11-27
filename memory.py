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
    _run(f"CREATE TABLE IF NOT EXISTS stock_log (id {pk}, stock_id INTEGER, delta REAL, unit TEXT, note TEXT, created TEXT)")
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
    return "\n".join(lines) + stock_context()
