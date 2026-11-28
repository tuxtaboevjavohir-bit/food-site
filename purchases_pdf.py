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
        "total": "ИТОГО", "summary": "Сводка по поставщикам", "bymonth": "Закуп по месяцам, ₸", "sp_pt": "Закуп по поставщикам и точкам, ₸",
        "paymonth": "Оплаты по месяцам, ₸", "journal": "Журнал записей", "date": "Дата", "type": "Тип",
        "sum": "Сумма, ₸", "note": "Комментарий", "t_buy": "Закуп", "t_pay": "Оплата", "empty": "За этот период записей нет.",
        "hint": "К оплате = долг на начало + закуп − оплачено. Минус означает переплату (аванс).",
        "months": ["Янв", "Фев", "Мар", "Апр", "Май", "Июн", "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек"],
        "mfull": ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"],
        "page": "Стр.", "daily": "Закуп по дням, ₸", "pm": "Закуп по месяцам и точкам, ₸", "month": "Месяц", "dtotal": "Итого", "rev": "Выручка", "pct": "Закуп %", "bypt": "По точкам: закуп и выручка (по сменам)",
    },
    "kk": {
        "title": "Сатып алу есебі", "year": "жыл", "all": "Барлық нүктелер", "point": "Нүкте", "made": "Жасалды",
        "sup": "Жеткізуші", "open": "Басындағы қарыз", "buy": "Сатып алу", "pay": "Төленді", "debt": "Төлеу керек",
        "total": "БАРЛЫҒЫ", "summary": "Жеткізушілер бойынша қорытынды", "bymonth": "Айлар бойынша сатып алу, ₸", "sp_pt": "Жеткізушілер мен нүктелер бойынша сатып алу, ₸",
        "paymonth": "Айлар бойынша төлемдер, ₸", "journal": "Жазбалар журналы", "date": "Күні", "type": "Түрі",
        "sum": "Сома, ₸", "note": "Түсініктеме", "t_buy": "Сатып алу", "t_pay": "Төлем", "empty": "Бұл кезеңде жазба жоқ.",
        "hint": "Төлеу керек = басындағы қарыз + сатып алу − төленді. Минус — артық төлем (аванс).",
        "months": ["Қаң", "Ақп", "Нау", "Сәу", "Мам", "Мау", "Шіл", "Там", "Қыр", "Қаз", "Қар", "Жел"],
        "mfull": ["Қаңтар", "Ақпан", "Наурыз", "Сәуір", "Мамыр", "Маусым", "Шілде", "Тамыз", "Қыркүйек", "Қазан", "Қараша", "Желтоқсан"],
        "page": "Бет", "daily": "Күндер бойынша сатып алу, ₸", "pm": "Айлар мен нүктелер бойынша сатып алу, ₸", "month": "Ай", "dtotal": "Барлығы", "rev": "Түсім", "pct": "Сатып алу %", "bypt": "Нүктелер бойынша: сатып алу және түсім (ауысымдар)",
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
    dd = rep.get("day")
    period = f"{dd[8:10]}.{dd[5:7]}.{dd[:4]}" if dd else f"{T['mfull'][month - 1]} {year}" if month else f"{year} {T['year']}"
    pdf.cell(0, 8, f"{T['title']} — {period}")
    pdf.set_xy(31, 18)
    pdf.set_font("dv", "", 9)
    pdf.set_text_color(90, 100, 120)
    pdf.cell(0, 5, f"Food house · {T['point']}: {point or T['all']} · {T['made']}: {datetime.now().strftime('%d.%m.%Y %H:%M')}")
    pdf.set_y(30)

    sups, tot = rep["suppliers"], rep["totals"]
    daily = sorted(rep.get("daily", []), key=lambda r: r["day"])

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
            pdf.set_text_color(*(colors or {}).get(j, DARK))
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

    pts = []
    for r in daily:
        if r["point"] not in pts:
            pts.append(r["point"])
    sup_cols = [x for x in sups if x["buy"]] or [x for x in sups if x["active"]]

    if not daily:
        pdf.set_font("dv", "", 10)
        pdf.set_text_color(110, 120, 140)
        pdf.cell(0, 8, T["empty"])
        return bytes(pdf.output())

    # ---- поставщики × точки ----
    if dd:  # отчёт за день: та же таблица, точки — строки (как в кассовой таблице закупа)
        title(T["sp_pt"])
        nw = max(16.0, (W - 40 - 28) / max(1, len(sup_cols)))
        dc = [(T["point"], 40, "L")] + [(x["name"][:16], nw, "R") for x in sup_cols] + [(T["total"], 28, "R")]
        head(dc)
        cs = {str(x["id"]): 0.0 for x in sup_cols}
        for i, r in enumerate(daily):
            vals = [r["cells"].get(str(x["id"]), 0) for x in sup_cols]
            for x, v in zip(sup_cols, vals):
                cs[str(x["id"])] += v
            row([r["point"] or "—"] + [money(v) if v else "·" for v in vals] + [money(r["total"])], dc, i, colors={len(dc) - 1: ACC}, size=9)
        row([T["total"]] + [money(cs[str(x["id"])]) for x in sup_cols] + [money(sum(cs.values()))], dc, 0, bold=True, size=9)
        return bytes(pdf.output())
    title(T["sp_pt"])
    by = {(str(x["id"]), p): 0.0 for x in sup_cols for p in pts}
    for r in daily:
        for k, v in r["cells"].items():
            if (k, r["point"]) in by:
                by[(k, r["point"])] += v
    pw = min(45.0, (W - 60 - 30) / max(1, len(pts)))
    sc = [(T["sup"], 60, "L")] + [(p[:22] or "—", pw, "R") for p in pts] + [(T["total"], 30, "R")]
    head(sc)
    for i, x in enumerate(sup_cols):
        vals = [by[(str(x["id"]), p)] for p in pts]
        row([x["name"]] + [money(v) if v else "·" for v in vals] + [money(sum(vals))], sc, i, colors={len(sc) - 1: ACC})
    ptot = [sum(by[(str(x["id"]), p)] for x in sup_cols) for p in pts]
    row([T["total"]] + [money(v) for v in ptot] + [money(sum(ptot))], sc, 0, bold=True)

    # ---- по месяцам (годовой отчёт) ----
    if not month:
        title(T["bymonth"])
        nw = (W - 36 - 24) / 12
        mc = [(T["sup"], 36, "L")] + [(m, nw, "R") for m in T["months"]] + [(T["total"], 24, "R")]
        head(mc)
        for i, x in enumerate(sup_cols):
            row([x["name"]] + [money(v) if v else "·" for v in x["months"]] + [money(sum(x["months"]))], mc, i, size=7)
        row([T["total"]] + [money(v) if v else "·" for v in tot["months"]] + [money(sum(tot["months"]))], mc, 0, bold=True, size=7)

    # ---- по дням: дата × точка × поставщик ----
    title(T["daily"])
    gw = 26
    nw = max(13.0, (W - 22 - gw - 22) / max(1, len(sup_cols)))
    gc = [(T["date"], 22, "L"), (T["point"], gw, "L")] + [(x["name"][:14], nw, "R") for x in sup_cols] + [(T["dtotal"], 22, "R")]
    head(gc)
    sums = {str(x["id"]): 0.0 for x in sup_cols}
    prev = None
    for i, r in enumerate(daily):
        if pdf.get_y() > pdf.h - 20:
            pdf.add_page()
            head(gc)
            prev = None
        lab = f"{r['day'][8:10]}.{r['day'][5:7]}.{r['day'][:4]}"
        vals = [lab if lab != prev else "", (r["point"] or "—")[:16]]
        prev = lab
        for x in sup_cols:
            v = r["cells"].get(str(x["id"]), 0)
            sums[str(x["id"])] += v
            vals.append(money(v) if v else "·")
        vals.append(money(r["total"]))
        row(vals, gc, i, size=7, colors={len(gc) - 1: ACC})
    row([T["dtotal"], ""] + [money(sums[str(x["id"])]) for x in sup_cols] + [money(sum(sums.values()))], gc, 0, bold=True, size=7)
    return bytes(pdf.output())
