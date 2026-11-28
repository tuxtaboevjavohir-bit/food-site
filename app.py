"""Food house — штаб ИИ-агентов. Запуск: python app.py → http://localhost:8000"""
import json
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from fastapi import FastAPI, File, Form, HTTPException, UploadFile  # noqa: E402
import hashlib  # noqa: E402
import hmac  # noqa: E402

from fastapi import Request  # noqa: E402
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse  # noqa: E402
from pydantic import BaseModel  # noqa: E402

import agents  # noqa: E402
import memory  # noqa: E402
import paloma  # noqa: E402
import whatsapp  # noqa: E402
from fastapi import BackgroundTasks  # noqa: E402

BASE = Path(__file__).parent
APP_VERSION = "2026-11-28.3"
app = FastAPI(title="Food house — штаб агентов")
memory.init_db()

# ---------- вход по паролю ----------
PASSWORD = os.getenv("APP_PASSWORD", "").strip()
TOKEN = hashlib.sha256(("foodhouse:" + PASSWORD).encode()).hexdigest() if PASSWORD else ""
LOGIN_HTML = """<!DOCTYPE html><html lang="ru"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Food house — вход</title><link rel="icon" href="/favicon.png"><link rel="apple-touch-icon" href="/apple-touch-icon.png"><style>body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
background:radial-gradient(900px 500px at 20% 0%,#0b3a5c,transparent 60%),radial-gradient(800px 500px at 100% 100%,#4c1d95,transparent 60%),#03060d;color:#e6f1ff;font-family:system-ui,-apple-system,sans-serif}form{background:rgba(12,22,40,.75);backdrop-filter:blur(12px);border:1px solid #1b2b48;box-shadow:0 0 40px #22d3ee22;
border-radius:16px;padding:32px;width:320px;text-align:center}h1{font-size:22px;margin:0 0 6px}h1 span{background:linear-gradient(90deg,#22d3ee,#a855f7);-webkit-background-clip:text;background-clip:text;color:transparent}p{color:#7f93b3;margin:0 0 20px}
input{width:100%;box-sizing:border-box;padding:12px;border-radius:10px;border:1px solid #3a2b21;background:radial-gradient(900px 500px at 20% 0%,#0b3a5c,transparent 60%),radial-gradient(800px 500px at 100% 100%,#4c1d95,transparent 60%),#03060d;color:#e6f1ff;font-size:16px}
button{margin-top:12px;width:100%;padding:12px;border:0;border-radius:10px;background:linear-gradient(135deg,#22d3ee,#a855f7);color:#020617;font-weight:700;font-size:16px;cursor:pointer}
.err{color:#ff8f8f;margin-top:10px}</style></head><body><form method="post" action="/login">
<img src="/logo.png" alt="Food house" style="width:96px;height:96px;border-radius:50%;box-shadow:0 0 30px #22d3ee55;margin-bottom:10px"><h1>Food <span>house</span></h1><p>Штаб агентов</p><input type="password" name="password" placeholder="Пароль" autofocus>
<button>Войти</button>%ERR%</form></body></html>"""


@app.middleware("http")
async def auth(request: Request, call_next):
    if not PASSWORD or request.url.path == "/p" or request.url.path.startswith("/p/api/") or request.url.path in ("/login", "/logo.png", "/favicon.png", "/favicon.ico", "/apple-touch-icon.png") or request.url.path.startswith("/webhook/"):
        return await call_next(request)
    if hmac.compare_digest(request.cookies.get("fh_auth", ""), TOKEN):
        return await call_next(request)
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "Нужно войти"}, status_code=401)
    return RedirectResponse("/login", status_code=303)


@app.get("/login")
def login_page():
    return HTMLResponse(LOGIN_HTML.replace("%ERR%", ""))


@app.post("/login")
def login(password: str = Form("")):
    if PASSWORD and hmac.compare_digest(password.strip(), PASSWORD):
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie("fh_auth", TOKEN, max_age=60 * 60 * 24 * 30, httponly=True, samesite="lax")
        return resp
    return HTMLResponse(LOGIN_HTML.replace("%ERR%", '<div class="err">Неверный пароль</div>'), status_code=401)


class TaskIn(BaseModel):
    text: str
    lang: str = "ru"


class ReplyIn(BaseModel):
    phone: str
    text: str


class ToggleIn(BaseModel):
    on: bool


class FactIn(BaseModel):
    text: str


@app.api_route("/", methods=["GET", "HEAD"])
def index():
    return FileResponse(BASE / "index.html")


# ---------- файлы 3D-офиса ----------
STATIC_JS = {"three.module.min.js", "OrbitControls.js", "office3d.js"}


@app.get("/{name}.js")
def js_file(name: str):
    fname = name + ".js"
    if fname not in STATIC_JS:
        raise HTTPException(404)
    return FileResponse(BASE / fname, media_type="text/javascript", headers={"Cache-Control": "public, max-age=3600"})


IMAGES = {"logo.png": "image/png", "apple-touch-icon.png": "image/png", "favicon.png": "image/png", "favicon.ico": "image/x-icon"}


@app.get("/logo.png")
@app.get("/apple-touch-icon.png")
@app.get("/favicon.png")
@app.get("/favicon.ico")
def image_file(request: Request):
    name = request.url.path.lstrip("/")
    return FileResponse(BASE / name, media_type=IMAGES[name], headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/info")
def info():
    return {
        "version": APP_VERSION,
        "demo": agents.demo_mode(),
        "db": "postgres" if memory.USE_PG else "sqlite",
        "paloma": paloma.connected(),
        "whatsapp": whatsapp.connected(),
        "model": agents.MODEL,
        "agents": {k: {"name": v["name"], "emoji": v["emoji"]} for k, v in agents.AGENTS.items()},
    }


@app.post("/api/task")
def task(body: TaskIn):
    if not body.text.strip():
        raise HTTPException(400, "Пустая задача")

    def stream():
        try:
            for event in agents.run_task(body.text.strip(), "kk" if body.lang == "kk" else "ru"):
                yield json.dumps(event, ensure_ascii=False) + "\n"
        except Exception as e:  # ошибку показываем на странице
            yield json.dumps({"type": "error", "text": str(e)}, ensure_ascii=False) + "\n"

    return StreamingResponse(stream(), media_type="application/x-ndjson")


@app.get("/api/history")
def history():
    return memory.list_tasks()


@app.get("/api/memory")
def get_memory():
    return memory.list_facts()


@app.post("/api/memory")
def add_memory(body: FactIn):
    if not body.text.strip():
        raise HTTPException(400, "Пустой факт")
    memory.add_fact(body.text)
    return memory.list_facts()


@app.delete("/api/memory/{fact_id}")
def del_memory(fact_id: int):
    memory.delete_fact(fact_id)
    return memory.list_facts()


@app.get("/api/menu")
def get_menu():
    return memory.list_menu()


@app.post("/api/menu/upload")
async def upload_menu(files: list[UploadFile] = File(...), mode: str = Form("replace")):
    images = []
    for f in files:
        data = await f.read()
        is_pdf = (f.content_type == "application/pdf") or data[:5] == b"%PDF-"
        limit = 25 if is_pdf else 5
        if len(data) > limit * 1024 * 1024:
            raise HTTPException(400, f"Файл {f.filename} больше {limit} МБ")
        images.append((data, "application/pdf" if is_pdf else (f.content_type or "image/jpeg")))
    try:
        items = agents.recognize_menu(images)
    except Exception as e:
        raise HTTPException(500, f"Не удалось распознать меню: {e}")
    if mode == "append":
        memory.add_menu_items(items)
    else:
        memory.replace_menu(items)
    return {"recognized": len(items), "menu": memory.list_menu()}


# ---------- продажи Paloma365 ----------
@app.get("/api/sales")
def get_sales(period: str = "week", loc: str = "all"):
    try:
        return paloma.report(period, loc)
    except Exception as e:
        raise HTTPException(502, str(e))


@app.get("/api/shifts")
def get_shifts(period: str = "week", loc: str = "all"):
    try:
        return paloma.shift_report(period, loc)
    except Exception as e:
        raise HTTPException(502, str(e))


@app.get("/api/orders")
def get_orders(period: str = "today", loc: str = "all"):
    try:
        return paloma.orders(period, loc)
    except Exception as e:
        raise HTTPException(502, str(e))


@app.get("/api/orders/{acc}/{oid}")
def get_order(acc: int, oid: str):
    try:
        return paloma.order_detail(acc, oid)
    except Exception as e:
        raise HTTPException(502, str(e))


@app.get("/api/items")
def get_items(period: str = "week", loc: str = "all"):
    try:
        return paloma.items_report(period, loc)
    except Exception as e:
        raise HTTPException(502, str(e))


@app.get("/api/locations")
def get_locations():
    try:
        return {"connected": paloma.connected(), "locations": paloma.locations() if paloma.connected() else [], "points": memory.points()}
    except Exception as e:
        raise HTTPException(502, str(e))


# ---------- склад ----------
class StockIn(BaseModel):
    name: str
    category: str = ""
    qty: float = 0
    unit: str = "pcs"
    min_qty: float | None = None
    point: str = ""
    price: float | None = None


class MoveIn(BaseModel):
    amount: float
    unit: str = ""
    note: str = ""


class PointsIn(BaseModel):
    points: list[str]


@app.get("/api/stock")
def stock_get(point: str = ""):
    return {"items": memory.stock_list(point), "points": memory.points()}


@app.post("/api/stock")
def stock_post(body: StockIn):
    if not body.name.strip():
        raise HTTPException(400, "Введите название")
    memory.stock_add(body.model_dump())
    return {"items": memory.stock_list()}


@app.put("/api/stock/{sid}")
def stock_put(sid: int, body: StockIn):
    memory.stock_update(sid, body.model_dump())
    return {"items": memory.stock_list()}


@app.post("/api/stock/{sid}/move")
def stock_mv(sid: int, body: MoveIn):
    try:
        memory.stock_move(sid, body.amount, body.unit, body.note)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"items": memory.stock_list()}


@app.delete("/api/stock/{sid}")
def stock_del(sid: int):
    memory.stock_delete(sid)
    return {"items": memory.stock_list()}


# ---------- персонал ----------
class StaffIn(BaseModel):
    name: str
    role: str = ""
    point: str = ""
    shift: str = "day"
    pin: str = ""
    phone: str = ""
    active: bool = True


@app.get("/api/staff")
def staff_get():
    return {"staff": memory.staff_list(), "points": memory.points()}


@app.post("/api/staff")
def staff_post(body: StaffIn):
    if not body.name.strip():
        raise HTTPException(400, "Введите имя")
    try:
        memory.staff_add(body.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"staff": memory.staff_list()}


@app.put("/api/staff/{sid}")
def staff_put(sid: int, body: StaffIn):
    try:
        memory.staff_update(sid, body.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"staff": memory.staff_list()}


@app.delete("/api/staff/{sid}")
def staff_del(sid: int):
    memory.staff_delete(sid)
    return {"staff": memory.staff_list()}


def _period_dates(period: str):
    from datetime import timedelta
    t = memory.local_now().date()
    days, back = {"today": (1, 0), "yesterday": (1, 1), "week": (7, 0), "month": (30, 0)}.get(period, (7, 0))
    end = t - timedelta(days=back)
    return (end - timedelta(days=days - 1)).isoformat(), end.isoformat()


@app.get("/api/attendance")
def attendance_get(period: str = "today", point: str = ""):
    f, t = _period_dates(period)
    return memory.attendance_report(f, t, point)


@app.get("/api/ops")
def ops_get(period: str = "today", point: str = ""):
    f, t = _period_dates(period)
    return memory.ops_report(f, t, point)


# ---------- страницы точки: отметка, кухня, курьер (вход по PIN, без пароля владельца) ----------
class CheckIn(BaseModel):
    pin: str
    kind: str = "auto"
    lat: float | None = None
    lng: float | None = None


class MarkIn(BaseModel):
    pin: str = ""
    point: str = ""
    order_key: str
    kind: str
    number: str | None = None
    created: str | None = None
    total: float | None = None


@app.get("/p")
def point_page():
    return FileResponse(BASE / "point.html")


@app.post("/p/api/checkin")
def p_checkin(body: CheckIn):
    try:
        return memory.checkin(body.pin, body.kind, body.lat, body.lng)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/p/api/login")
def p_login(body: CheckIn):
    st = memory.staff_by_pin(body.pin)
    if not st:
        raise HTTPException(400, "Неверный PIN")
    return {"name": st["name"], "role": st["role"], "point": st["point"], "points": memory.points()}


def _point_loc(point: str) -> str:
    acc = next((a for a in paloma.accounts() if a["name"] == point), None)
    return f"{acc['idx']}:" if acc else "all"


@app.get("/p/api/orders")
def p_orders(pin: str, point: str = "", mode: str = "kitchen"):
    st = memory.staff_by_pin(pin)
    if not st:
        raise HTTPException(401, "Неверный PIN")
    point = point or st["point"]
    data = paloma.orders("today", _point_loc(point), 120) if paloma.connected() else {"orders": []}
    orders = data.get("orders", [])
    if mode == "courier":
        orders = [o for o in orders if str(o.get("type") or "").lower() in ("delivery", "доставка")] or orders
    keys = [f"{o['acc']}:{o['id']}" for o in orders]
    marks = memory.order_marks(keys)
    for o in orders:
        o["key"] = f"{o['acc']}:{o['id']}"
        o["marks"] = marks.get(o["key"], {})
    return {"point": point, "orders": orders, "errors": data.get("errors", [])}


@app.post("/p/api/mark")
def p_mark(body: MarkIn):
    st = memory.staff_by_pin(body.pin)
    if not st:
        raise HTTPException(401, "Неверный PIN")
    if body.kind not in ("ready", "picked", "delivered"):
        raise HTTPException(400, "kind")
    try:
        memory.order_event(body.order_key, body.kind, body.point or st["point"], body.number, st["id"], body.created, body.total)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.post("/api/points")
def points_set(body: PointsIn):
    memory.set_points(body.points)
    return {"points": memory.points()}


@app.get("/api/sales/raw")
def sales_raw(group_by: str = "day", days: int = 7, metrics: str = "net,orders"):
    try:
        return paloma.raw(group_by, days, metrics)
    except Exception as e:
        raise HTTPException(502, str(e))


@app.get("/api/sales/catalog")
def sales_catalog():
    try:
        return paloma.metrics_catalog()
    except Exception as e:
        raise HTTPException(502, str(e))


@app.get("/api/sales/check")
def check_sales():
    return paloma.check()


# ---------- WhatsApp ----------
@app.get("/webhook/whatsapp")
def wa_verify(request: Request):
    q = request.query_params
    token = whatsapp.cfg("WA_VERIFY_TOKEN")
    if q.get("hub.mode") == "subscribe" and token and hmac.compare_digest(q.get("hub.verify_token", ""), token):
        return HTMLResponse(q.get("hub.challenge", ""))
    raise HTTPException(403, "verify token mismatch")


@app.post("/webhook/whatsapp")
async def wa_incoming(request: Request, background: BackgroundTasks):
    body = await request.body()
    if not whatsapp.verify_signature(body, request.headers.get("X-Hub-Signature-256", "")):
        raise HTTPException(401, "bad signature")
    try:
        payload = json.loads(body or b"{}")
    except ValueError:
        raise HTTPException(400, "bad json")
    background.add_task(whatsapp.handle_webhook, payload)
    return {"ok": True}


@app.get("/api/wa/chats")
def wa_chats():
    return {"connected": whatsapp.connected(), "autoreply": whatsapp.autoreply_on(), "chats": memory.wa_chats()}


@app.get("/api/wa/history")
def wa_history(phone: str):
    return memory.wa_history(phone, 200)


@app.post("/api/wa/reply")
def wa_reply(body: ReplyIn):
    if not body.text.strip():
        raise HTTPException(400, "Пустое сообщение")
    try:
        whatsapp.send_text(body.phone, body.text.strip())
    except Exception as e:
        raise HTTPException(502, str(e))
    memory.wa_add(body.phone, "out", body.text.strip(), author="менеджер")
    return memory.wa_history(body.phone, 200)


@app.post("/api/wa/autoreply")
def wa_autoreply(body: ToggleIn):
    memory.set_setting("wa_autoreply", "1" if body.on else "0")
    return {"autoreply": body.on}


@app.post("/api/wa/ideas")
def wa_ideas(lang: str = "ru"):
    try:
        return {"text": whatsapp.ideas("kk" if lang == "kk" else "ru")}
    except Exception as e:
        raise HTTPException(500, str(e))


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    print(f"\n  Food house запущен: http://localhost:{port}\n")
    uvicorn.run(app, host="0.0.0.0", port=port)
