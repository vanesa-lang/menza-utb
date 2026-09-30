"""
Stáhne jídelníček menzy UTB (WebKredit, veřejný PDF export),
najde u jídel alergeny a vygeneruje stránku site/index.html,
kde jsou jídla s alergenem 7 (mléko, vč. laktózy) označená červeně.
"""
import datetime as dt
import io
import json
import re
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import pdfplumber
import requests

BASE = "https://jidelnicek.utb.cz/webkredit/Api/Ordering/ExportMenu"
CANTEEN_ID = 2            # menza UTB (ověřeno z veřejných exportů)
TZ = ZoneInfo("Europe/Prague")
ROOT = Path(__file__).parent
OUT = ROOT / "site"
DEBUG = ROOT / "site" / "debug"

DAY_NAMES = ["pondělí", "úterý", "středa", "čtvrtek", "pátek", "sobota", "neděle"]
SKIP = re.compile(r"^(Jídelníček|Alt\s+Jídlo\s+Cena|\d{2}\.\d{2}\.\d{4})$", re.I)
SECTION = re.compile(
    r"^(?P<name>.+?)\s*-\s*(?:" + "|".join(DAY_NAMES) + r")\s+\d{2}\.\d{2}\.\d{4}$", re.I
)
MEAL_START = re.compile(r"^(?:\d+\s+)?\d+\s+(?P<rest>(?:\d+\s*[^\W\d_]|[^\W\d]).*)$")   # "1 Jídlo…" nebo "4 2 Jídlo…"
PRICE = re.compile(r"\s*\d+[.,]\d{2}\s*Kč")
ALLERGEN_LINE = re.compile(r"^Alergeny\s*:\s*(?P<rest>.*)$", re.I)
MILK_WORDS = ("mléko", "laktóz")
SKIP_SECTIONS = {"obaly"}


def week_days(today: dt.date):
    """Pondělí–pátek aktuálního týdne; o víkendu už následující týden."""
    monday = today - dt.timedelta(days=today.weekday())
    if today.weekday() >= 5:
        monday += dt.timedelta(days=7)
    return [monday + dt.timedelta(days=i) for i in range(5)]


def fetch_pdf(day: dt.date) -> bytes:
    local_midnight = dt.datetime.combine(day, dt.time(0, 0), TZ)
    utc = local_midnight.astimezone(dt.timezone.utc)
    params = {
        "canteenId": CANTEEN_ID,
        "dates": utc.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "locale": "cs",
    }
    r = requests.get(BASE, params=params, timeout=30)
    r.raise_for_status()
    return r.content


def pdf_text(data: bytes) -> str:
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        return "\n".join((p.extract_text() or "") for p in pdf.pages)


def split_allergens(text: str):
    """Rozdělí 'lepek (pšenice, žito), mléko' podle čárek mimo závorky."""
    parts, depth, buf = [], 0, ""
    for ch in text:
        depth += ch == "("
        depth -= ch == ")"
        if ch == "," and depth == 0:
            parts.append(buf.strip()); buf = ""
        else:
            buf += ch
    parts.append(buf.strip())
    return [p for p in parts if p]


def parse(text: str):
    sections, section, meal, mode = [], None, None, None

    def close_meal():
        nonlocal meal
        if meal and section is not None:
            name = re.sub(r"\s+", " ", PRICE.sub(" ", meal["name"])).strip(" ,-–")
            allergens = split_allergens(meal["al"]) if meal["al"] is not None else []
            if meal["al"] is None:
                status = "unknown"
            elif any(w in a.lower() for a in allergens for w in MILK_WORDS):
                status = "milk"
            else:
                status = "ok"
            if name:
                section["meals"].append({"name": name, "allergens": allergens, "status": status})
        meal = None

    for raw in text.splitlines():
        line = raw.strip()
        if not line or SKIP.match(line):
            continue
        m = SECTION.match(line)
        if m:
            close_meal()
            name = m.group("name").strip()
            section = None if name.lower() in SKIP_SECTIONS else {"name": name, "meals": []}
            if section:
                sections.append(section)
            mode = None
            continue
        if section is None:
            continue
        a = ALLERGEN_LINE.match(line)
        if a and meal:
            meal["al"] = a.group("rest")
            mode = "al"
            continue
        m = MEAL_START.match(line)
        if m:
            close_meal()
            meal = {"name": m.group("rest"), "al": None}
            mode = "name"
            continue
        if meal and mode == "al":
            meal["al"] += " " + line          # zalomený řádek alergenů
        elif meal:
            meal["name"] += " " + line        # zalomený název jídla
    close_meal()
    return [s for s in sections if s["meals"]]


def main():
    today = dt.datetime.now(TZ).date()
    DEBUG.mkdir(parents=True, exist_ok=True)
    days = []
    for d in week_days(today):
        entry = {"date": d.isoformat(), "label": f"{DAY_NAMES[d.weekday()].capitalize()} {d.day}. {d.month}.",
                 "sections": [], "error": None}
        try:
            text = pdf_text(fetch_pdf(d))
            (DEBUG / f"{d.isoformat()}.txt").write_text(text, encoding="utf-8")
            entry["sections"] = parse(text)
        except Exception as e:  # stránka se vygeneruje i při výpadku jednoho dne
            entry["error"] = str(e)
            print(f"[{d}] chyba: {e}", file=sys.stderr)
        days.append(entry)

    data = {"updated": dt.datetime.now(TZ).strftime("%-d. %-m. %Y v %H:%M"),
            "today": today.isoformat(), "days": days}
    template = (ROOT / "template.html").read_text(encoding="utf-8")
    html = template.replace("/*__DATA__*/null", json.dumps(data, ensure_ascii=False))
    OUT.mkdir(exist_ok=True)
    (OUT / "index.html").write_text(html, encoding="utf-8")
    print("Hotovo:", OUT / "index.html")


if __name__ == "__main__":
    main()
