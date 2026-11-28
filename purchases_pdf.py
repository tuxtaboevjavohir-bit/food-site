"""PDF-отчёт по закупу: сколько закупили у каждого поставщика, сколько оплатили и сколько должны."""
from datetime import datetime
from pathlib import Path

from fpdf import FPDF

BASE = Path(__file__).parent
FONT_DIRS = [BASE, Path("/usr/share/fonts/truetype/dejavu")]

L = {
    "ru": {
        "title": "Отчёт по закупу", "year": "год", "all": "Все точки", "point": "Точка", "made": "Сформирован",
        "sup": "Поставщик", "open": "Долг на начало", "buy": "Закуп", "pay": "Оплачено", "debt": "К оплате",
        "total": "ИТОГО", "summary": "Сводка по поставщикам", "bymonth": "Закуп по месяцам, ₸",
        "paymonth": "Оплаты по месяцам, ₸", "journal": "Журнал записей", "date": "Дата", "type": "Тип",
        "sum": "Сумма, ₸", "note": "Комментарий", "t_buy": "Закуп", "t_pay": "Оплата", "empty": "За этот период записей нет.",
        "hint": "К оплате = долг на начало + закуп − оплачено. Минус означает переплату (аванс).",
        "months": ["Янв", "Фев", "Мар", "Апр", "Май", "Июн", "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек"],
        "mfull": ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"],
        "page": "Стр.",
    },
    "kk": {
        "title": "Сатып алу есебі", "year": "жыл", "all": "Барлық нүктелер", "point": "Нүкте", "made": "Жасалды",
        "sup": "Жеткізуші", "open": "Басындағы қарыз", "buy": "Сатып алу", "pay": "Төленді", "debt": "Төлеу керек",
        "total": "БАРЛЫҒЫ", "summary": "Жеткізушілер бойынша қорытынды", "bymonth": "Айлар бойынша сатып алу, ₸",
        "paymonth": "Айлар бойынша төлемдер, ₸", "journal": "Жазбалар журналы", "date": "Күні", "type": "Түрі",
        "sum": "Сома, ₸", "note": "Түсініктеме", "t_buy": "Сатып алу", "t_pay": "Төлем", "empty": "Бұл кезеңде жазба жоқ.",
        "hint": "Төлеу керек = басындағы қарыз + сатып алу − төленді. Минус — артық төлем (аванс).",
        "months": ["Қаң", "Ақп", "Нау", "Сәу", "Мам", "Мау", "Шіл", "Там", "Қыр", "Қаз", "Қар", "Жел"],
        "mfull": ["Қаңтар", "Ақпан", "Наурыз", "Сәуір", "Мамыр", "Маусым", "Шілде", "Тамыз", "Қыркүйек", "Қазан", "Қараша", "Желтоқсан"],
        "page": "Бет",
    },
}

DARK = (15, 23, 42)
ACC = (8, 145, 178)
HEAD = (224, 242, 254)
ZEBRA = (245, 248, 252)
RED = (185, 28, 28)
GREEN = (21, 128, 61)


def money(v) -> str:
    v = round(v or 0)
    s = f"{abs(v):,}".replace(",", " ")
    return ("−" if v < 0 else "") + s


def _font_path(name: str) -> str:
    for d in FONT_DIRS:
        if (d / name).exists():
            return str(d / name)
    raise FileNotFoundError(f"Нет шрифта {name}: загрузите DejaVuSans.ttf и DejaVuSans-Bold.ttf в репозиторий")


class _PDF(FPDF):
    lang = "ru"

    def footer(self):
        self.set_y(-10)
        self.set_font("dv", "", 7)
        self.set_text_color(120, 130, 150)
        self.cell(0, 5, f"Food house · {L[self.lang]['page']} {self.page_no()}/{{nb}}", align="C")


def build(rep: dict, lang: str = "ru") -> bytes:
    T = L.get(lang, L["ru"])
    year, month, point = rep["year"], rep["month"], rep["point"]
    pdf = _PDF(orientation="L", unit="mm", format="A4")
    pdf.lang = lang if lang in L else "ru"
    pdf.add_font("dv", "", _font_path("DejaVuSans.ttf"))
    pdf.add_font("dv", "B", _font_path("DejaVuSans-Bold.ttf"))
    pdf.set_auto_page_break(True, 14)
    pdf.set_margins(10, 10, 10)
    pdf.alias_nb_pages()
    pdf.add_page()
    W = pdf.w - 20

    # ---- шапка ----
    logo = BASE / "logo.png"
    if logo.exists():
        pdf.image(str(logo), x=10, y=8, w=18, h=18)
    pdf.set_xy(31, 9)
    pdf.set_font("dv", "B", 18)
    pdf.set_text_color(*DARK)
    period = f"{T['mfull'][month - 1]} {year}" if month else f"{year} {T['year']}"
    pdf.cell(0, 8, f"{T['title']} — {period}")
    pdf.set_xy(31, 18)
    pdf.set_font("dv", "", 9)
    pdf.set_text_color(90, 100, 120)
    pdf.cell(0, 5, f"Food house · {T['point']}: {point or T['all']} · {T['made']}: {datetime.now().strftime('%d.%m.%Y %H:%M')}")
    pdf.set_y(30)

    sups, tot = rep["suppliers"], rep["totals"]

    def head(cols):
        pdf.set_font("dv", "B", 8)
        pdf.set_fill_color(*ACC)
        pdf.set_text_color(255, 255, 255)
        for txt, w, al in cols:
            pdf.cell(w, 7, txt, border=0, align=al, fill=True)
        pdf.ln()

    def row(vals, cols, i, bold=False, colors=None, size=8):
        pdf.set_font("dv", "B" if bold else "", size)
        pdf.set_fill_color(*(HEAD if bold else ZEBRA if i % 2 else (255, 255, 255)))
        for j, (v, (_, w, al)) in enumerate(zip(vals, cols)):
            c = (colors or {}).get(j, DARK)
            pdf.set_text_color(*c)
            pdf.cell(w, 6.5, v, border=0, align=al, fill=True)
        pdf.ln()

    def title(t):
        if pdf.get_y() > pdf.h - 40:
            pdf.add_page()
        pdf.ln(3)
        pdf.set_font("dv", "B", 12)
        pdf.set_text_color(*DARK)
        pdf.cell(0, 7, t)
        pdf.ln(8)

    # ---- сводка ----
    title(T["summary"])
    cols = [(T["sup"], W * .32, "L"), (T["open"], W * .17, "R"), (T["buy"], W * .17, "R"),
            (T["pay"], W * .17, "R"), (T["debt"], W * .17, "R")]
    head(cols)
    debt_color = lambda v: {4: RED if v > 0.5 else GREEN if v < -0.5 else DARK}
    for i, x in enumerate(sups):
        row([x["name"], money(x["opening"]), money(x["buy"]), money(x["pay"]), money(x["closing"])], cols, i,
            colors=debt_color(x["closing"]))
    row([T["total"], money(tot["opening"]), money(tot["buy"]), money(tot["pay"]), money(tot["closing"])], cols, 0,
        bold=True, colors=debt_color(tot["closing"]))
    pdf.set_font("dv", "", 7)
    pdf.set_text_color(110, 120, 140)
    pdf.cell(0, 6, T["hint"])
    pdf.ln(4)

    # ---- по месяцам (только годовой отчёт) ----
    if not month:
        for key, ttl in (("months", T["bymonth"]), ("months_pay", T["paymonth"])):
            title(ttl)
            nw = (W - 36 - 24) / 12
            mc = [(T["sup"], 36, "L")] + [(m, nw, "R") for m in T["months"]] + [(T["total"], 24, "R")]
            head(mc)
            for i, x in enumerate(sups):
                vals = [x["name"]] + [money(v) if v else "·" for v in x[key]] + [money(sum(x[key]))]
                row(vals, mc, i, size=7)
            row([T["total"]] + [money(v) if v else "·" for v in tot[key]] + [money(sum(tot[key]))], mc, 0, bold=True, size=7)

    # ---- журнал ----
    title(T["journal"])
    recs = rep["records"]
    if not recs:
        pdf.set_font("dv", "", 9)
        pdf.set_text_color(110, 120, 140)
        pdf.cell(0, 6, T["empty"])
    else:
        jc = [(T["date"], W * .11, "L"), (T["sup"], W * .2, "L"), (T["type"], W * .11, "L"),
              (T["point"], W * .17, "L"), (T["sum"], W * .13, "R"), (T["note"], W * .28, "L")]
        by = {}
        for r in recs:
            by.setdefault(r["supplier"] or "?", []).append(r)
        head(jc)
        i = 0
        for name in [x["name"] for x in sups if x["name"] in by] + [n for n in by if n not in {x["name"] for x in sups}]:
            for r in sorted(by[name], key=lambda r: (r["day"], r["id"])):
                if pdf.get_y() > pdf.h - 20:
                    pdf.add_page()
                    head(jc)
                d = r["day"]
                is_buy = r["kind"] == "buy"
                note = (r.get("note") or "")[:60]
                row([f"{d[8:10]}.{d[5:7]}.{d[:4]}", name, T["t_buy"] if is_buy else T["t_pay"], r.get("point") or "—",
                     money(r["amount"]), note], jc, i, colors={2: DARK if is_buy else GREEN, 4: DARK if is_buy else GREEN})
                i += 1
    return bytes(pdf.output())
