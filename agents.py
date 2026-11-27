"""ИИ-агенты Food house: директор раздаёт задачи, специалисты выполняют."""
import base64
import json
import os
import random
import re
import time

import memory
import paloma

MODEL = os.getenv("FOODHOUSE_MODEL", "claude-sonnet-5-5")

AGENTS = {
    "director": {
        "name": "Директор",
        "emoji": "👔",
        "role": (
            "Ты директор кафе быстрого питания Food house. Ты получаешь задачу от владельца, "
            "решаешь, кому из команды её поручить, и в конце сводишь ответы в короткий итог с планом действий."
        ),
    },
    "marketer": {
        "name": "Маркетолог",
        "emoji": "📣",
        "role": (
            "Ты маркетолог кафе Food house. Акции, соцсети (TikTok, Instagram), тексты постов, "
            "привлечение и удержание гостей, работа с отзывами. Давай конкретные идеи и готовые тексты."
        ),
    },
    "financier": {
        "name": "Финансист",
        "emoji": "📊",
        "role": (
            "Ты финансист кафе Food house. Себестоимость, наценка, ценообразование, прогноз выручки, "
            "точка безубыточности, бюджет. Считай по шагам и показывай формулы и цифры в тенге."
        ),
    },
    "accountant": {
        "name": "Бухгалтер",
        "emoji": "🧾",
        "role": (
            "Ты бухгалтер кафе Food house в Казахстане. Учёт доходов и расходов, зарплаты, первичные документы, "
            "отчётность. Объясняй просто; если вопрос касается налогового законодательства, напоминай сверить "
            "с актуальными правилами или с налоговым консультантом."
        ),
    },
    "chef": {
        "name": "Шеф-повар",
        "emoji": "👨‍🍳",
        "role": (
            "Ты шеф-повар кафе быстрого питания Food house. Техкарты с граммовкой, себестоимость блюда по "
            "ингредиентам, новые позиции меню, стандарты подачи и скорость отдачи. Давай граммовки и шаги приготовления."
        ),
    },
    "smm": {
        "name": "SMM-щик",
        "emoji": "📱",
        "role": (
            "Ты SMM-специалист кафе Food house. Контент-план по дням, сценарии коротких видео для TikTok и Reels "
            "(хук в первые 3 секунды, кадры, текст на экране), подписи и хештеги. Пиши готовые к публикации тексты."
        ),
    },
    "hr": {
        "name": "HR-менеджер",
        "emoji": "🤝",
        "role": (
            "Ты HR-менеджер кафе Food house. Графики смен, тексты вакансий, собеседования, обучение новичков, "
            "мотивация и удержание персонала. Графики оформляй таблицей."
        ),
    },
    "buyer": {
        "name": "Закупщик",
        "emoji": "📦",
        "role": (
            "Ты закупщик кафе Food house. Списки закупок по меню, расчёт объёмов на неделю, выбор и сравнение "
            "поставщиков, контроль остатков и списаний. Списки оформляй таблицей: продукт, количество, примерная цена."
        ),
    },
}

SPECIALISTS = ["marketer", "financier", "accountant", "chef", "smm", "hr", "buyer"]


def lang_rule(lang: str) -> str:
    if lang == "kk":
        return (
            "\n\nТІЛ: Барлық жауапты тек қазақ тілінде жаз (кириллица). "
            "ЯЗЫК: отвечай только на казахском языке."
        )
    return "\n\nЯЗЫК: отвечай на том языке, на котором написана задача владельца (русский или казахский)."


def _client():
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return None
    import anthropic

    return anthropic.Anthropic(api_key=key)


def demo_mode() -> bool:
    return _client() is None


def _ask(system: str, user_content, max_tokens: int = 4000) -> str:
    client = _client()
    text, stop = "", ""
    for _ in range(2):  # если ответ пустой — пробуем ещё раз
        msg = client.messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_content}],
        )
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
        stop = getattr(msg, "stop_reason", "")
        if text:
            break
    if not text:
        return "⚠️ Агент не смог ответить. Попробуйте поставить задачу ещё раз."
    if stop == "max_tokens":
        text += "\n\n_…ответ получился слишком длинным и обрезан. Попросите продолжить или сузьте задачу._"
    return text


def _extract_json(text: str):
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1)
    start = min([i for i in (text.find("{"), text.find("[")) if i != -1], default=-1)
    if start == -1:
        raise ValueError("JSON не найден в ответе")
    return json.loads(text[start:])


# ------------------------------------------------------------------ задачи
def run_task(task: str, lang: str = "ru"):
    """Генератор событий для анимации: каждое событие — dict."""
    ctx = memory.memory_context() + paloma.context_for_agents() + lang_rule(lang)
    yield {"type": "status", "agent": "director", "state": "thinking", "text": "Читаю задачу…"}

    # 1. Директор планирует
    if demo_mode():
        time.sleep(1)
        plan = _demo_plan(task)
    else:
        system = AGENTS["director"]["role"] + "\n\n" + ctx
        team = ", ".join(f"{a} ({AGENTS[a]['name'].lower()})" for a in SPECIALISTS)
        prompt = (
            f"Задача от владельца: «{task}»\n\n"
            f"Реши, кому поручить. Доступны: {team}. "
            "Выбери только нужных (1–3). Ответь строго JSON без пояснений:\n"
            '{"comment": "одна фраза от директора", "assignments": [{"agent": "marketer", "task": "что сделать"}]}\n'
            "Поля comment и task пиши на языке ответа (см. правило ЯЗЫК)."
        )
        try:
            plan = _extract_json(_ask(system, prompt, 800))
        except Exception:
            plan = {"comment": "Поручаю маркетологу и финансисту.", "assignments": [{"agent": a, "task": task} for a in ("marketer", "financier")]}

    assignments = [a for a in plan.get("assignments", []) if a.get("agent") in SPECIALISTS] or [
        {"agent": "marketer", "task": task}
    ]
    yield {"type": "plan", "agent": "director", "text": plan.get("comment", ""), "assignments": assignments}

    # 2. Специалисты работают
    results = []
    for a in assignments:
        aid = a["agent"]
        yield {"type": "move", "agent": aid, "to": "director", "text": "Иду к директору за задачей"}
        yield {"type": "status", "agent": aid, "state": "working", "text": a["task"]}
        if demo_mode():
            time.sleep(1.5)
            answer = _demo_answer(aid, a["task"])
        else:
            system = (
                AGENTS[aid]["role"] + "\n\n" + ctx + "\n\nОтвечай по-русски, структурно и по делу. "
                "Используй Markdown: заголовки, списки, таблицы. Уложись примерно в 400–600 слов."
            )
            answer = _ask(system, f"Задача от директора: {a['task']}\n\nИсходный запрос владельца: {task}", 6000)
        results.append({"agent": aid, "task": a["task"], "answer": answer})
        yield {"type": "result", "agent": aid, "task": a["task"], "text": answer}
        yield {"type": "status", "agent": aid, "state": "idle", "text": "Готово"}

    # 3. Директор сводит итог
    yield {"type": "status", "agent": "director", "state": "thinking", "text": "Свожу итог…"}
    if demo_mode():
        time.sleep(1)
        summary = "Итог (демо-режим): команда отработала задачу. Добавьте ключ API в файл .env, чтобы агенты отвечали по-настоящему."
    else:
        joined = "\n\n".join(f"### {AGENTS[r['agent']]['name']}\n{r['answer']}" for r in results)
        summary = _ask(
            AGENTS["director"]["role"] + "\n\n" + ctx,
            f"Задача владельца: «{task}»\n\nОтветы команды:\n{joined}\n\n"
            "Сделай короткий итог для владельца: 3–6 пунктов плана действий с ответственными.",
            2500,
        )
    yield {"type": "summary", "agent": "director", "text": summary}
    yield {"type": "status", "agent": "director", "state": "idle", "text": "Жду новую задачу"}
    memory.save_task(task, {"plan": plan, "results": results, "summary": summary})
    yield {"type": "done"}


# ------------------------------------------------------------------ меню по фото
MENU_PROMPT = (
    "На фото или в PDF — меню заведения. Распознай все позиции. Ответь строго JSON-массивом без пояснений:\n"
    '[{"category": "Пицца", "name": "Маргарита", "price": 2500, "description": "состав, если указан"}]\n'
    "Цену пиши числом в тенге без символов; если цены нет — null. Категорию бери из заголовков меню."
)


def recognize_menu(images: list[tuple[bytes, str]]) -> list[dict]:
    """images — список (байты, mime-тип): фото или PDF. Возвращает позиции меню."""
    if demo_mode():
        time.sleep(1)
        return [
            {"category": "Пицца", "name": "Маргарита (демо)", "price": 2500, "description": "томаты, моцарелла"},
            {"category": "Бургеры", "name": "Чизбургер (демо)", "price": 1800, "description": ""},
            {"category": "Напитки", "name": "Кола 0,5 (демо)", "price": 500, "description": ""},
        ]
    items = []
    for data, mime in images:
        b64 = base64.b64encode(data).decode()
        if mime == "application/pdf" or data[:5] == b"%PDF-":
            block = {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": b64}}
        else:
            if mime not in ("image/jpeg", "image/png", "image/webp", "image/gif"):
                mime = "image/jpeg"
            block = {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}}
        content = [block, {"type": "text", "text": MENU_PROMPT}]
        text = _ask("Ты точно распознаёшь меню кафе с фотографий и PDF-файлов.", content, 8000)
        parsed = _extract_json(text)
        if isinstance(parsed, dict):
            parsed = parsed.get("items", [])
        items.extend(parsed)
    return items


# ------------------------------------------------------------------ демо-ответы
def _demo_plan(task: str):
    t = task.lower()
    picks = []
    if any(w in t for w in ("реклам", "акци", "пост", "тикток", "инста", "гост", "продвиж")):
        picks.append("marketer")
    if any(w in t for w in ("цен", "себесто", "прибыл", "выручк", "бюджет", "наценк")):
        picks.append("financier")
    if any(w in t for w in ("налог", "зарплат", "отчёт", "отчет", "учёт", "учет", "документ")):
        picks.append("accountant")
    if any(w in t for w in ("блюд", "рецепт", "техкарт", "граммов", "кухн")):
        picks.append("chef")
    if any(w in t for w in ("контент", "видео", "reels", "хештег", "смм", "smm")):
        picks.append("smm")
    if any(w in t for w in ("смен", "сотрудн", "персонал", "ваканс", "график")):
        picks.append("hr")
    if any(w in t for w in ("закуп", "поставщ", "продукт", "остатк", "склад")):
        picks.append("buyer")
    picks = picks[:3]
    if not picks:
        picks = random.sample(SPECIALISTS, 2)
    return {"comment": "Распределяю задачу по команде.", "assignments": [{"agent": p, "task": task} for p in picks]}


def _demo_answer(agent: str, task: str) -> str:
    return (
        f"{AGENTS[agent]['name']} (демо-режим) взял задачу: «{task}».\n"
        "Чтобы получить настоящий ответ, впишите ключ ANTHROPIC_API_KEY в файл .env и перезапустите программу."
    )
