"""Память заведения Food house: SQLite-база с фактами, меню и историей задач."""
import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "foodhouse.db"
_lock = threading.Lock()


def _conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _lock, _conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                created TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS menu (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT,
                name TEXT NOT NULL,
                price REAL,
                description TEXT
            );
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                result TEXT,
                created TEXT NOT NULL
            );
            """
        )
        if c.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 0:
            now = datetime.now().isoformat(timespec="seconds")
            c.executemany(
                "INSERT INTO facts(text, created) VALUES (?, ?)",
                [
                    ("Заведение называется Food house, формат — быстрое питание (кафе/фастфуд).", now),
                    ("Валюта расчётов — тенге (₸).", now),
                ],
            )


# ---------- факты ----------
def add_fact(text: str):
    with _lock, _conn() as c:
        c.execute(
            "INSERT INTO facts(text, created) VALUES (?, ?)",
            (text.strip(), datetime.now().isoformat(timespec="seconds")),
        )


def delete_fact(fact_id: int):
    with _lock, _conn() as c:
        c.execute("DELETE FROM facts WHERE id = ?", (fact_id,))


def list_facts():
    with _conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM facts ORDER BY id")]


# ---------- меню ----------
def replace_menu(items: list[dict]):
    with _lock, _conn() as c:
        c.execute("DELETE FROM menu")
        add_menu_items_conn(c, items)


def add_menu_items(items: list[dict]):
    with _lock, _conn() as c:
        add_menu_items_conn(c, items)


def add_menu_items_conn(c, items):
    for it in items:
        price = it.get("price")
        try:
            price = float(price) if price not in (None, "") else None
        except (TypeError, ValueError):
            price = None
        c.execute(
            "INSERT INTO menu(category, name, price, description) VALUES (?, ?, ?, ?)",
            (it.get("category") or "Без категории", it.get("name") or "?", price, it.get("description") or ""),
        )


def list_menu():
    with _conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM menu ORDER BY category, id")]


# ---------- история ----------
def save_task(text: str, result: dict):
    with _lock, _conn() as c:
        c.execute(
            "INSERT INTO tasks(text, result, created) VALUES (?, ?, ?)",
            (text, json.dumps(result, ensure_ascii=False), datetime.now().isoformat(timespec="seconds")),
        )


def list_tasks(limit: int = 20):
    with _conn() as c:
        rows = c.execute("SELECT * FROM tasks ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["result"] = json.loads(d["result"] or "{}")
        out.append(d)
    return out


def memory_context() -> str:
    """Текст с памятью заведения, который получает каждый агент."""
    facts = list_facts()
    menu = list_menu()
    lines = ["ПАМЯТЬ ЗАВЕДЕНИЯ:"]
    lines += [f"- {f['text']}" for f in facts] or ["- (пока пусто)"]
    lines.append("\nМЕНЮ:")
    if menu:
        for m in menu[:200]:
            price = f"{m['price']:.0f} ₸" if m["price"] is not None else "цена не указана"
            lines.append(f"- [{m['category']}] {m['name']} — {price}")
    else:
        lines.append("- меню ещё не загружено")
    return "\n".join(lines)
