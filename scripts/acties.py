"""Actuele acties van PrijsProfeet (https://www.prijsprofeet.nl) over de gewone prijzen leggen.

De gratis API levert alleen aanbiedingen. Gewone prijzen blijven van Checkjebon.
Voorwaarden: zichtbare bronvermelding, key geheim (GitHub-secret), en deze data
niet in de repository opslaan. Daarom komt alles alleen in docs/data.json,
dat niet wordt gecommit maar direct naar GitHub Pages gaat.
"""
import datetime as dt
import json
import re
import time
import unicodedata
import urllib.parse
import urllib.request

API = "https://www.prijsprofeet.nl/api/v1/search"
USER_AGENT = "Proteindex/1.0"
TERMEN = ["kwark", "skyr", "kvarg", "quark", "griekse yoghurt", "protein", "proteine", "high protein"]
MAX_PAGINAS = 5
WINKELS = {
    "albert_heijn": ("ah", "AH"), "jumbo": ("jumbo", "Jumbo"), "aldi": ("aldi", "Aldi"), "lidl": ("lidl", "Lidl"),
    "plus": ("plus", "PLUS"), "poiesz": ("poiesz", "Poiesz"), "spar": ("spar", "SPAR"),
    "dekamarkt": ("dekamarkt", "DekaMarkt"), "hoogvliet": ("hoogvliet", "Hoogvliet"), "vomar": ("vomar", "Vomar"),
    "dirk": ("dirk", "Dirk"), "coop": ("coop", "Coop"), "gewoon_coop": ("gewoon_coop", "Gewoon Coop"),
    "nettorama": ("nettorama", "Nettorama"), "boni": ("boni", "Boni"), "ekoplaza": ("ekoplaza", "Ekoplaza"),
    "mcd": ("mcd", "MCD"), "boons_markt": ("boons_markt", "Boon's Markt"), "dagwinkel": ("dagwinkel", "Dagwinkel"),
}


def haal_json(url, key):
    aanvraag = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "X-API-Key": key, "Accept": "application/json"})
    with urllib.request.urlopen(aanvraag, timeout=60) as antwoord:
        return json.loads(antwoord.read())


def haal_acties(key, haal=haal_json):
    """Alle lopende en komende acties voor de zoektermen, ontdubbeld."""
    gezien = {}
    for status in ("active", "upcoming"):
        for term in TERMEN:
            for pagina in range(1, MAX_PAGINAS + 1):
                query = urllib.parse.urlencode(
                    {"q": term, "promotion_status": status, "page": pagina, "page_size": 100}
                )
                antwoord = haal(f"{API}?{query}", key)
                for p in antwoord.get("results", []):
                    sleutel = p.get("base_product_id") or p.get("product_id")
                    if sleutel:
                        gezien.setdefault((status, p.get("retailer"), sleutel), p)
                if pagina * antwoord.get("page_size", 100) >= antwoord.get("total", 0):
                    break
                time.sleep(0.5)
            time.sleep(0.5)
    return [dict(p, _status=status) for (status, _, _), p in gezien.items()]


def gewone_tekst(t):
    t = unicodedata.normalize("NFKD", t.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"\d+\s*x\s*\d+\s*\w*|\b\d+[.,]?\d*\s*(g|gr|gram|kg|kilo|ml|l|stuks?)\b", " ", t)
    return " ".join(sorted(re.sub(r"[^a-z0-9%]+", " ", t).split()))


def url_sleutel(url):
    if not url:
        return None
    u = url.lower().split("?")[0].rstrip("/")
    for patroon in (r"ah\.nl/.*/(wi\d+)", r"jumbo\.com/.*-(\d+[a-z]+)$", r"plus\.nl/.*-(\d+)$"):
        m = re.search(patroon, u)
        if m:
            return m[1]
    return re.sub(r"^https?://(www\.)?", "", u)


def normaliseer(p, gram, vandaag):
    """Zet één API-resultaat om naar prijs per stuk, gewicht en actietekst, of None."""
    if p.get("retailer") not in WINKELS or p.get("price") is None:
        return None
    if p["_status"] == "active" and p.get("valid_until") and p["valid_until"][:10] < vandaag:
        return None
    code, label = WINKELS[p["retailer"]]
    naam = " ".join((p.get("name") or "").split())
    aantal = p.get("multi_buy_quantity") or 1
    prijs = p["price"]
    if aantal > 1 and p["retailer"] == "aldi":
        bundel = prijs
        per_stuk = prijs / aantal
    elif aantal > 1 and p.get("multi_buy_price"):
        bundel = p["multi_buy_price"]
        per_stuk = bundel / aantal
    else:
        bundel, per_stuk = None, prijs
    gewicht = gram(p.get("quantity") or "") or gram(naam)
    if not gewicht and p.get("unit") in ("kg", "L") and p.get("unit_price"):
        gewicht = round(prijs / p["unit_price"] * 1000)
    normaal = p.get("original_price")
    if bundel:
        tekst = f"{aantal} voor €{bundel:.2f}".replace(".", ",")
    elif normaal and normaal > per_stuk:
        tekst = f"{round((1 - per_stuk / normaal) * 100)}% korting"
    else:
        tekst = "Actie"
    return {
        "code": code, "winkel": label, "naam": naam, "gram": gewicht, "prijs": round(per_stuk, 2),
        "url": p.get("product_url"), "status": p["_status"],
        "deal": {
            "soort": "actie", "tekst": tekst, "normaal": normaal, "aantal": aantal if aantal > 1 else None,
            "vanaf": (p.get("valid_from") or "")[:10] or None, "tot": (p.get("valid_until") or "")[:10] or None,
            "geschat": bool(p.get("valid_until_estimated")),
        },
    }


def verrijk(uitvoer, ruw, regels, vind_regel, gram, filters, vandaag):
    """Legt acties over de producten, voegt nieuwe actieproducten toe en vult 'binnenkort'."""
    IN, UIT = filters
    producten = uitvoer["producten"]
    for p in producten:
        if p.get("deal") and p["deal"]["soort"] in ("normaal", "markt"):
            p["deal"] = None   # echte actiegegevens vervangen de afgeleide labels
    op_url = {url_sleutel(p["url"]): p for p in producten}
    op_naam = {(p["winkel"], gewone_tekst(p["naam"]), p["gram"]): p for p in producten}
    binnenkort, nieuw, onbekend, telling = [], [], {}, {"actief": 0, "gekoppeld": 0, "nieuw": 0, "binnenkort": 0}

    for p in ruw:
        a = normaliseer(p, gram, vandaag)
        if not a or not IN.search(a["naam"]) or UIT.search(a["naam"]):
            continue
        regel = vind_regel(regels, a["code"], a["naam"])
        bruikbaar = a["gram"] and a["gram"] >= 80 and 1 <= a["prijs"] / a["gram"] * 1000 <= 30
        if a["status"] == "active":
            telling["actief"] += 1
            bestaand = op_url.get(url_sleutel(a["url"])) or op_naam.get((a["winkel"], gewone_tekst(a["naam"]), a["gram"]))
            if bestaand:
                if not a["deal"]["normaal"] or a["deal"]["normaal"] <= a["prijs"]:
                    a["deal"]["normaal"] = bestaand["prijs"] if bestaand["prijs"] > a["prijs"] else None
                bestaand.update(prijs=a["prijs"], deal=a["deal"])
                bestaand["perKg"] = round(a["prijs"] / bestaand["gram"] * 1000, 2)
                bestaand["perEiwit"] = round(a["prijs"] / (bestaand["gram"] * bestaand["eiwit"] / 100) * 100, 2)
                telling["gekoppeld"] += 1
                continue
        if not regel or not bruikbaar:
            if a["status"] == "active":
                onbekend[(a["winkel"], a["naam"])] = {
                    "winkel": a["winkel"], "naam": a["naam"], "maat": p.get("quantity") or "", "prijs": a["prijs"],
                    "url": a["url"], "reden": "geen eiwitwaarde" if not regel else "gewicht of prijs onbruikbaar",
                }
            continue
        rij = {
            "winkel": a["winkel"], "naam": a["naam"], "gram": a["gram"], "prijs": a["prijs"],
            "perKg": round(a["prijs"] / a["gram"] * 1000, 2), "eiwit": regel["eiwit"], "vet": regel["vet"],
            "perEiwit": round(a["prijs"] / (a["gram"] * regel["eiwit"] / 100) * 100, 2),
            "soort": regel["soort"], "smaak": regel["smaak"],
            "exact": regel["smaak"] == "naturel" or bool(re.search(regel.get("ref", "$^"), a["naam"], re.I)),
            "community": regel.get("broncheck") == "community", "bron": regel["bron"], "url": a["url"],
            "deal": a["deal"],
        }
        if a["status"] == "active":
            nieuw.append(rij)
            telling["nieuw"] += 1
        elif a["deal"]["vanaf"] and a["deal"]["vanaf"] > vandaag:
            binnenkort.append(rij)
            telling["binnenkort"] += 1

    producten.extend(nieuw)
    producten.sort(key=lambda r: r["perEiwit"])
    bekend = {(r["winkel"], r["naam"]) for r in uitvoer["onbekend"]}
    uitvoer["onbekend"].extend(v for k, v in onbekend.items() if k not in bekend)
    uitvoer["onbekend"].sort(key=lambda r: (r["winkel"], r["naam"]))
    uitvoer["binnenkort"] = sorted(binnenkort, key=lambda r: r["perEiwit"])[:12]
    uitvoer["acties"] = dict(telling, ok=True, opgehaald=dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes"))
    return uitvoer
