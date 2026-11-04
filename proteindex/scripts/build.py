#!/usr/bin/env python3
"""Koppelt actuele supermarktprijzen aan eiwitwaarden en schrijft docs/data.json.

Gebruik: python scripts/build.py [--bron pad/naar/supermarkets.json]
Alleen standaardbibliotheek. Stopt met foutcode als de brondata niet klopt,
zodat de vorige versie van de pagina online blijft.
"""
import argparse
import datetime as dt
import json
import pathlib
import re
import statistics
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
BRON_URL = "https://raw.githubusercontent.com/supermarkt/checkjebon/main/data/supermarkets.json"
COMMITS_URL = "https://api.github.com/repos/supermarkt/checkjebon/commits?path=data/supermarkets.json&per_page=1"
HISTORIE_DAGEN = 120
NORMAAL_VENSTER = 30   # dagen waarover de normale prijs wordt bepaald
MIN_METINGEN = 5       # minimaal aantal metingen voor een eigen normale prijs
DREMPEL_EIGEN = 0.10   # korting t.o.v. eigen normale prijs
DREMPEL_MARKT = 0.15   # korting t.o.v. dezelfde productlijn elders
MIN_RIJEN = 150        # minder resultaten: er is iets mis met de bron

IN = re.compile(r"kwark|skyr|kvarg|quark|prote[iï]n.*(yogh|pudding)|(yogh|pudding).*prote[iï]n|griekse.*yogh|yogh.*griekse", re.I)
UIT = re.compile(
    r"taart|cake|hond|\bkat\b|snack|bonbon|\bijs|ijsje|\brepen?\b|\bbar\b|reep|saus|dressing|poeder|shake|"
    r"kids|baby|kind|toetie|toetjes?\b|danoontje|\bdip\b|spread|smeer|plantaardig|soja|alpro|haver|\boat|"
    r"granola|muesli|cruesli|\bmix\b|koek|knijp|shampoo|masker|crème|wafel|brood|pizza|\bvol(le)?\b|"
    r"smoothie|pakket|variatie|drink|drank|choco(?!la)|ontbijt|vitamine|kaas|tzatziki|kruiden|mayo|soep|hummus|"
    r"cracker|pasta|olijf|olijv|wrap|worst|salami|aviko|knorr|chips|smalec|muscle meat|ella's|garden gourmet|"
    r"nestlé|halfvol|elinas|maaltijd|fetakaas|kofta|(?<![\d,.])10\s?%|bloemcool|royal|bfree|shot\b|panc|mousse|pouch",
    re.I,
)
SMAAK = re.compile(
    r"aardb|vanil|framboos|perzik|mango|bosbes|bosvrucht|bosfruit|blueberry|blauwe bes|kers|citroen|stracc|straci|"
    r"kokos|coconut|banaan|banana|caramel|karamel|sinaas|appel|passie|passion|papaja|rood fruit|honing|toffee|peach|"
    r"strawberry|vrucht|choco|speculaas|kaneel|\bpie\b|limited|raspberry|lemon|mandarin|matcha|assorti|panna",
    re.I,
)
WINKELS = {
    "ah": "AH", "dekamarkt": "DekaMarkt", "dirk": "Dirk", "hoogvliet": "Hoogvliet", "jumbo": "Jumbo",
    "lidl": "Lidl", "plus": "PLUS", "poiesz": "Poiesz", "spar": "SPAR", "vomar": "Vomar",
}


def gram(tekst):
    """Geeft het totaalgewicht in gram, of None. ml telt als gram."""
    t = tekst.lower().replace(",", ".")
    m = re.search(r"(\d+)\s*x\s*(\d+(?:\.\d+)?)\s*(kilogram|kilo|kg|gram|gr|g|ml|l)\b", t)
    if m:
        return int(m[1]) * eenheid(float(m[2]), m[3])
    m = re.search(r"(\d+(?:\.\d+)?)\s*(kilogram|kilo|kg|gram|gr|g|ml|l)\b", t)
    return eenheid(float(m[1]), m[2]) if m else None


def eenheid(waarde, naam):
    return waarde * 1000 if naam in ("kg", "kilo", "kilogram", "l") else waarde


def haal(url):
    aanvraag = urllib.request.Request(url, headers={"User-Agent": "proteindex"})
    with urllib.request.urlopen(aanvraag, timeout=120) as antwoord:
        return antwoord.read()


def laad_bron(pad):
    return json.loads(pathlib.Path(pad).read_text("utf8") if pad else haal(BRON_URL))


def bron_datum():
    try:
        return json.loads(haal(COMMITS_URL))[0]["commit"]["committer"]["date"]
    except Exception as fout:
        print("Brondatum niet opgehaald:", fout, file=sys.stderr)
        return None


def vind_regel(regels, winkel, naam):
    smaak = "smaak" if SMAAK.search(naam) else "naturel"
    for r in regels:
        if r["smaak"] != smaak:
            continue
        if "winkels" in r and winkel not in r["winkels"]:
            continue
        if not re.search(r["naam"], naam, re.I):
            continue
        if "niet" in r and re.search(r["niet"], naam, re.I):
            continue
        return r
    return None


def bouw(data, regels, historie, vandaag, brondatum):
    rijen, onbekend = {}, {}
    for winkel in data:
        code = winkel["n"]
        if code not in WINKELS:
            continue
        for p in winkel["d"]:
            naam = " ".join(p["n"].split())
            if not IN.search(naam) or UIT.search(naam):
                continue
            gewicht = gram(p.get("s", "")) or gram(naam)
            prijs = p.get("p")
            url = winkel["u"] + p["l"]
            regel = vind_regel(regels, code, naam)
            bruikbaar = (
                gewicht and prijs and gewicht >= 80 and 1 <= prijs / gewicht * 1000 <= 30
            )
            if not regel or not bruikbaar:
                sleutel = (code, naam, p.get("s", ""))
                onbekend[sleutel] = {
                    "winkel": WINKELS[code], "naam": naam, "maat": p.get("s", ""), "prijs": prijs,
                    "url": url, "reden": "geen eiwitwaarde" if not regel else "gewicht of prijs onbruikbaar",
                }
                continue
            sleutel = f"{code}|{naam.lower()}|{gewicht:g}"
            bestaand = rijen.get(sleutel)
            if bestaand:
                bestaand["dubbel"] = max(bestaand["dubbel"] or 0, prijs, bestaand["prijs"])
                if prijs < bestaand["prijs"]:
                    bestaand.update(prijs=prijs, url=url)
                continue
            rijen[sleutel] = {
                "sleutel": sleutel, "winkel": WINKELS[code], "naam": naam, "gram": gewicht, "prijs": prijs,
                "url": url, "regel": regel, "dubbel": None,
            }

    for s, r in rijen.items():
        historie.setdefault(s, {})[vandaag] = r["prijs"]
    grens = (dt.date.fromisoformat(vandaag) - dt.timedelta(days=HISTORIE_DAGEN)).isoformat()
    venster = (dt.date.fromisoformat(vandaag) - dt.timedelta(days=NORMAAL_VENSTER)).isoformat()
    for s in list(historie):
        historie[s] = {d: v for d, v in historie[s].items() if d >= grens}
        if not historie[s]:
            del historie[s]

    def per_eiwit(r):
        return r["prijs"] / (r["gram"] * r["regel"]["eiwit"] / 100) * 100

    groepen = {}
    for r in rijen.values():
        groepen.setdefault((r["regel"]["id"], r["gram"]), []).append(r["prijs"])

    uit = []
    for r in rijen.values():
        regel, prijs = r["regel"], r["prijs"]
        recent = [v for d, v in historie[r["sleutel"]].items() if d >= venster]
        normaal = statistics.median(recent) if len(recent) >= MIN_METINGEN else None
        if r["dubbel"] and r["dubbel"] > prijs:
            normaal = max(normaal or 0, r["dubbel"])
        deal = None
        if normaal and prijs <= normaal * (1 - DREMPEL_EIGEN):
            deal = {"soort": "normaal", "pct": round((1 - prijs / normaal) * 100), "normaal": round(normaal, 2)}
        else:
            markt = groepen[(regel["id"], r["gram"])]
            if len(markt) >= 3:
                midden = statistics.median(markt)
                if prijs <= midden * (1 - DREMPEL_MARKT):
                    deal = {"soort": "markt", "pct": round((1 - prijs / midden) * 100), "normaal": round(midden, 2)}
        exact = regel["smaak"] == "naturel" or bool(re.search(regel.get("ref", "$^"), r["naam"], re.I))
        uit.append({
            "winkel": r["winkel"], "naam": r["naam"], "gram": r["gram"], "prijs": prijs,
            "perKg": round(prijs / r["gram"] * 1000, 2), "eiwit": regel["eiwit"], "vet": regel["vet"],
            "perEiwit": round(per_eiwit(r), 2), "soort": regel["soort"], "smaak": regel["smaak"],
            "exact": exact, "community": regel.get("broncheck") == "community", "bron": regel["bron"],
            "url": r["url"], "deal": deal,
        })
    uit.sort(key=lambda r: r["perEiwit"])
    leeg = sorted(onbekend.values(), key=lambda r: (r["winkel"], r["naam"]))
    return {
        "bijgewerkt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes"),
        "brondatum": brondatum, "producten": uit, "onbekend": leeg,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bron")
    args = parser.parse_args()
    regels = json.loads((ROOT / "data/eiwit.json").read_text("utf8"))
    pad_historie = ROOT / "data/historie.json"
    historie = json.loads(pad_historie.read_text("utf8")) if pad_historie.exists() else {}
    vandaag = dt.date.today().isoformat()
    uitvoer = bouw(laad_bron(args.bron), regels, historie, vandaag, bron_datum())
    if len(uitvoer["producten"]) < MIN_RIJEN:
        sys.exit(f"Te weinig producten ({len(uitvoer['producten'])}); pagina niet bijgewerkt.")
    (ROOT / "docs/data.json").write_text(json.dumps(uitvoer, ensure_ascii=False, separators=(",", ":")), "utf8")
    pad_historie.write_text(json.dumps(historie, ensure_ascii=False, separators=(",", ":"), sort_keys=True), "utf8")
    print(f"{len(uitvoer['producten'])} producten, {len(uitvoer['onbekend'])} nog zonder waarde")


if __name__ == "__main__":
    main()
