"""Food house — штаб ИИ-агентов. Запуск: python app.py → http://localhost:8000"""
import json
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from fastapi import FastAPI, File, Form, HTTPException, UploadFile  # noqa: E402
from fastapi.responses import FileResponse, StreamingResponse  # noqa: E402
from pydantic import BaseModel  # noqa: E402

import agents  # noqa: E402
import memory  # noqa: E402

BASE = Path(__file__).parent
app = FastAPI(title="Food house — штаб агентов")
memory.init_db()


class TaskIn(BaseModel):
    text: str


class FactIn(BaseModel):
    text: str


@app.api_route("/", methods=["GET", "HEAD"])
def index():
    return FileResponse(BASE / "index.html")


@app.get("/api/info")
def info():
    return {
        "demo": agents.demo_mode(),
        "model": agents.MODEL,
        "agents": {k: {"name": v["name"], "emoji": v["emoji"]} for k, v in agents.AGENTS.items()},
    }


@app.post("/api/task")
def task(body: TaskIn):
    if not body.text.strip():
        raise HTTPException(400, "Пустая задача")

    def stream():
        try:
            for event in agents.run_task(body.text.strip()):
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
        if len(data) > 5 * 1024 * 1024:
            raise HTTPException(400, f"Файл {f.filename} больше 5 МБ — уменьшите фото")
        images.append((data, f.content_type or "image/jpeg"))
    try:
        items = agents.recognize_menu(images)
    except Exception as e:
        raise HTTPException(500, f"Не удалось распознать меню: {e}")
    if mode == "append":
        memory.add_menu_items(items)
    else:
        memory.replace_menu(items)
    return {"recognized": len(items), "menu": memory.list_menu()}


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    print(f"\n  Food house запущен: http://localhost:{port}\n")
    uvicorn.run(app, host="0.0.0.0", port=port)
