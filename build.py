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
MILK_ALLERGEN = "7"       # alergen č. 7 = mléko a výrobky z něj (vč. laktózy)
TZ = ZoneInfo("Europe/Prague")
ROOT = Path(__file__).parent
OUT = ROOT / "site"
DEBUG = ROOT / "site" / "debug"

DAY_NAMES = ["pondělí", "úterý", "středa", "čtvrtek", "pátek", "sobota", "neděle"]
SKIP = re.compile(r"^(Jídelníček|Alt\s+Jídlo\s+Cena|\d{2}\.\d{2}\.\d{4})$", re.I)
SECTION = re.compile(
    r"^(?P<name>.+?)\s*-\s*(?:" + "|".join(DAY_NAMES) + r")\s+\d{2}\.\d{2}\.\d{4}", re.I
)
# alergeny v závorce na konci, např. "(1,3,7)" nebo "(A: 1, 7)"
ALLERGENS = re.compile(r"\((?:A(?:lergeny)?\s*:?\s*)?(\d{1,2}(?:\s*[,.]\s*\d{1,2}[a-z]?)*)\)")
PRICE = re.compile(r"\s+\d+[,.]?\d*\s*(Kč)?$")


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


def parse(text: str):
    sections, current = [], None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or SKIP.match(line):
            continue
        m = SECTION.match(line)
        if m:
            current = {"name": m.group("name").strip(), "meals": []}
            sections.append(current)
            continue
        if current is None:
            current = {"name": "Nabídka", "meals": []}
            sections.append(current)
        found = ALLERGENS.findall(line)
        allergens = sorted({a.strip() for grp in found for a in re.split(r"[,.]", grp) if a.strip()},
                           key=lambda x: int(re.sub(r"\D", "", x) or 0))
        name = ALLERGENS.sub("", line)
        name = PRICE.sub("", name).strip(" -–,")
        if not name:
            continue
        if found:
            status = "milk" if any(re.sub(r"\D", "", a) == MILK_ALLERGEN for a in allergens) else "ok"
        else:
            status = "unknown"
        current["meals"].append({"name": name, "allergens": allergens, "status": status})
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
