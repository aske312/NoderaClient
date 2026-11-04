"""Offline country hints, never IP geolocation or a claim about the user's location."""

import json
import re
from functools import lru_cache
from pathlib import Path

RESOURCE = Path(__file__).resolve().parent / "resources"


@lru_cache(maxsize=1)
def map_data():
    return json.loads((RESOURCE / "world-map.json").read_text(encoding="utf-8"))


def country(code):
    data = map_data()["countries"].get(str(code).upper())
    return {"code": str(code).upper(), **data} if data else None


CITY_COUNTRIES = {
    "DE": "frankfurt berlin nuremberg munich дюссельдорф франкфурт берлин нюрнберг мюнхен",
    "FI": "helsinki хельсинки",
    "NL": "amsterdam rotterdam ams амстердам роттердам",
    "GB": "london manchester лондон манчестер",
    "FR": "paris marseille париж марсель",
    "SE": "stockholm стокгольм",
    "NO": "oslo осло",
    "CH": "zurich geneva цюрих женева",
    "PL": "warsaw варшава",
    "CZ": "prague прага",
    "AT": "vienna вена",
    "ES": "madrid barcelona мадрид барселона",
    "IT": "milan rome милан рим",
    "TR": "istanbul стамбул",
    "RU": "moscow msk spb москва петербург",
    "JP": "tokyo osaka токио осака",
    "KR": "seoul сеул",
    "AE": "dubai дубай",
    "CA": "toronto montreal vancouver торонто монреаль ванкувер",
    "AU": "sydney melbourne сидней мельбурн",
    "US": "chicago dallas seattle miami чикаго даллас сиэтл майами",
    "LV": "riga рига",
    "LT": "vilnius вильнюс",
    "EE": "tallinn таллин",
    "RO": "bucharest бухарест",
    "BG": "sofia софия",
    "RS": "belgrade белград",
    "HU": "budapest будапешт",
    "PT": "lisbon лиссабон",
    "IE": "dublin дублин",
    "DK": "copenhagen копенгаген",
    "GR": "athens афины",
    "MD": "chisinau кишинёв кишинев",
    "KZ": "almaty astana алматы астана",
    "AM": "yerevan ереван",
    "GE": "tbilisi тбилиси",
    "UZ": "tashkent ташкент",
    "KG": "bishkek бишкек",
    "AZ": "baku баку",
    "BH": "manama манама",
    "ZA": "johannesburg йоханнесбург",
    "KE": "nairobi найроби",
    "NG": "lagos лагос",
    "BR": "fortaleza форталеза",
    "CL": "santiago сантьяго",
    "AR": "buenosaires",
    "MX": "queretaro керетаро",
    "MY": "cyberjaya киберджая",
    "TH": "bangkok бангкок",
    "VN": "hanoi ханой",
    "ID": "jakarta джакарта",
    "PH": "manila манила",
    "NZ": "auckland окленд",
    "IL": "haifa хайфа",
    "LU": "bissen биссен",
}
PHRASES = {
    "gremany": "DE",
    "deutschland": "DE",
    "suomi": "FI",
    "netherland": "NL",
    "holland": "NL",
    "молдова": "MD",
    "беларусь": "BY",
    "кыргызстан": "KG",
    "косово": "XK",
    "сейшелы": "SC",
    "czechia": "CZ",
    "czech republic": "CZ",
    "republic of korea": "KR",
    "republic of moldova": "MD",
    "bosnia and herzegovina": "BA",
    "north macedonia": "MK",
    "македония": "MK",
    "босния": "BA",
    "dominican republic": "DO",
    "cape verde": "CV",
    "ivory coast": "CI",
    "curacao": "CW",
    "cote d ivoire": "CI",
    "reunion": "RE",
    "usa": "US",
    "сша": "US",
    "uk": "GB",
    "оаэ": "AE",
    "new york": "US",
    "los angeles": "US",
    "hong kong": "HK",
    "нью йорк": "US",
    "лос анджелес": "US",
    "гонконг": "HK",
    "singapore": "SG",
    "сингапур": "SG",
    "south korea": "KR",
    "united kingdom": "GB",
    "united states": "US",
}


def normalized(value):
    return " ".join(re.findall(r"[^\W_]+", str(value).casefold()))


def infer_country(node, override=None):
    if override:
        return country(override)
    countries = map_data()["countries"]
    for key in ("country", "country-code", "country_code"):
        value = str(node.get(key, ""))
        if value.upper() in countries:
            return country(value)
        if value:
            for code, data in countries.items():
                if any(normalized(value) == normalized(alias) for alias in data["aliases"]):
                    return country(code)
    hints = country_hints(str(node.get("name", "")))
    return country(next(iter(hints))) if len(hints) == 1 else None


def country_hints(name):
    countries = map_data()["countries"]
    found = set()
    for first, second in zip(name, name[1:]):
        if all(0x1F1E6 <= ord(char) <= 0x1F1FF for char in (first, second)):
            code = "".join(chr(ord(char) - 0x1F1E6 + 65) for char in (first, second))
            if code in countries:
                found.add(code)
    text = " " + normalized(name) + " "
    for phrase, code in PHRASES.items():
        if " " + phrase + " " in text:
            found.add(code)
    for code, cities in CITY_COUNTRIES.items():
        if any(" " + city + " " in text for city in cities.split()):
            found.add(code)
    for code, data in countries.items():
        if any(" " + normalized(alias) + " " in text for alias in data["aliases"]):
            found.add(code)
    for match in re.finditer(r"(?<![A-Za-z])([A-Z]{2})(?:\d{1,3})?(?![A-Za-z])", name):
        code = match[1]
        if code in countries and code not in {"SS", "ID", "NO", "IN", "IT", "IS"}:
            found.add(code)
    return found


def active_locations(profile, groups, connected):
    from nodera.routes import active_route_views

    result = []
    for index, route in enumerate(active_route_views(profile, groups, connected)):
        for step, hop in enumerate(route["hops"]):
            location = country(hop["country"])
            if location:
                result.append(
                    {
                        **location,
                        "route": index,
                        "step": step,
                        "role": hop["role"],
                        "source": hop["source"],
                        "order_known": route["order_known"],
                    }
                )
    return result
