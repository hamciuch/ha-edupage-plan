"""Klient publicznego (nie wymagającego logowania) API planu lekcji EduPage.

Szkoła publikuje plan lekcji pod adresem
    https://<subdomain>.edupage.org/timetable/
Strona ta (widget "TTViewer") pobiera CAŁY plan szkoły jednym zapytaniem:

    POST /timetable/server/regulartt.js?__func=regularttGetData
    body: {"__args": [null, "<tt_num>"], "__gsh": "00000000"}

gdzie <tt_num> to numer aktualnie obowiązującego planu, pobierany osobnym
wywołaniem (patrz `async_get_default_tt_num`):

    POST /timetable/server/ttviewer.js?__func=getTTViewerData
    body: {"__args": [null, <rok_szkolny:int>], "__gsh": "00000000"}

(oba kształty zapytań potwierdzone przez odczytanie prawdziwego kodu klienta
EduPage - `/timetable/ttviewer.js` i `/global/pics/js/bundles/bundle_main.min.js`
- a nie zgadywane, bo drugi z nich łatwo zgadnąć źle: wymaga roku szkolnego
jako LICZBY, nie samego `null`).
Odpowiedź to samoopisujący się zrzut bazy (DBI) - lista tabel, każda z
`data_columns` (kolejność pól) i `data_rows` (wiersze). Nie trzeba się logować
ani znać danych żadnego ucznia - to są dane całej szkoły (klasy, przedmioty,
nauczyciele, sale, grupy/podziały, lekcje, konkretne "karty" w planie).

Ważne tabele:
    classes    - lista oddziałów (id, name, short)
    subjects   - przedmioty wraz z KOLOREM zdefiniowanym przez szkołę
    teachers   - nauczyciele (tylko short/skrót w danych publicznych)
    classrooms - sale lekcyjne
    groups     - grupy w obrębie klasy (np. "1. Grupa", "hisz", "nie", "Chłopcy")
    divisions  - podziały klasy na grupy (classid, ascttdivision, groupids[])
    lessons    - definicja przedmiotu/nauczyciela/grupy dla danej klasy
    cards      - konkretne wystąpienia w planie: lessonid, period, days (bitmask), weeks
    periods    - godziny poszczególnych jednostek lekcyjnych
    days       - nazwy dni tygodnia (indeks = pozycja w polu "days" karty)

Ten moduł tylko POBIERA i parsuje surowe tabele - logikę łączenia
(dziecko -> klasa -> wybrane grupy -> lekcje -> karty -> plan) ma coordinator.py.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import aiohttp

from .const import PUBLIC_GSH, TIMETABLE_ENDPOINT, TTVIEWER_ENDPOINT

_LOGGER = logging.getLogger(__name__)


class EdupageApiError(Exception):
    """Błąd komunikacji z publicznym API EduPage."""


def _fingerprint(raw_json: dict[str, Any]) -> str:
    """Krótki odcisk palca surowej odpowiedzi - DIAGNOSTYKA "czy dane faktycznie się zmieniły".

    Rodzic zgłosił, że HA pokazywał plan "sprzed zmiany" mimo cyklicznego odświeżania
    co DEFAULT_SCAN_INTERVAL - nie wiadomo było, czy to integracja nie odpytuje EduPage
    na czas, czy EduPage samo serwuje starą (buforowaną po swojej stronie) odpowiedź dla
    stałego `__gsh: "00000000"`, czy to tylko przeglądarka na dashboardzie miała stary
    widok karty kalendarza w pamięci. Ten fingerprint (razem z `generated_at` w
    StudentSchedule, patrz coordinator.py) pozwala to odróżnić: jeśli w logu
    (poziom INFO, patrz async_fetch_timetable) ten sam fingerprint powtarza się
    godzinami mimo potwierdzonej zmiany na stronie szkoły - to znaczy, że EduPage
    zwraca identyczną odpowiedź, czyli winne jest ich własne buforowanie, a nie ta
    integracja.
    """
    try:
        payload = json.dumps(raw_json, sort_keys=True, default=str).encode("utf-8")
    except (TypeError, ValueError):
        return "?"
    return hashlib.sha1(payload).hexdigest()[:10]


@dataclass
class EdupageTables:
    """Sparsowane tabele planu lekcji jednej szkoły."""

    raw: dict[str, Any] = field(repr=False)
    tables: dict[str, dict[str, Any]] = field(default_factory=dict)
    fingerprint: str = ""

    def rows(self, table_id: str) -> list[dict[str, Any]]:
        """Zwraca data_rows danej tabeli jako listę słowników (już z kluczami-nazwami pól)."""
        table = self.tables.get(table_id)
        if not table:
            return []
        return table["rows"]

    def row_by_id(self, table_id: str, row_id: str) -> dict[str, Any] | None:
        for row in self.rows(table_id):
            if row.get("id") == row_id:
                return row
        return None


def _parse_dbi_tables(raw_json: dict[str, Any]) -> EdupageTables:
    """Zamienia surową odpowiedź DBI (data_columns + data_rows) na wygodne listy dictów."""
    try:
        raw_tables = raw_json["r"]["dbiAccessorRes"]["tables"]
    except (KeyError, TypeError) as err:
        error = None
        if isinstance(raw_json.get("r"), dict):
            error = raw_json["r"].get("error")
        raise EdupageApiError(error or "Nieoczekiwany format odpowiedzi EduPage") from err

    parsed: dict[str, dict[str, Any]] = {}
    for table in raw_tables:
        table_id = table.get("id")
        columns: list[str] = table.get("data_columns", [])
        rows_out = []
        for row in table.get("data_rows", []):
            # "id" wiersza jest zawsze osobnym polem obok data_columns
            item = {"id": row.get("id")}
            for col in columns:
                item[col] = row.get(col)
            rows_out.append(item)
        parsed[table_id] = {"columns": columns, "rows": rows_out}

    return EdupageTables(raw=raw_json, tables=parsed, fingerprint=_fingerprint(raw_json))


def _current_school_year(today: date | None = None) -> int:
    """Polski rok szkolny nazywany jest rokiem jego ROZPOCZĘCIA (np. 2026/2027 -> 2026).

    EduPage liczy to tak samo: przełom to wrzesień - od września do sierpnia
    to ten sam "school year" (patrz `req.getSchoolYear()` w kliencie EduPage).
    """
    today = today or date.today()
    return today.year if today.month >= 9 else today.year - 1


async def async_get_default_tt_num(session: aiohttp.ClientSession, subdomain: str) -> str:
    """Pyta EduPage, jaki jest numer aktualnie obowiązującego planu (regular.default_num).

    WAŻNE: to wywołanie wymaga DWÓCH argumentów - (null, rok_szkolny) - inaczej
    EduPage odpowiada błędem. Ustalone przez podejrzenie prawdziwego kodu klienta
    (plik /timetable/ttviewer.js: `getTTViewerData(null, year)`, gdzie `year` to
    liczba całkowita, NIE string) - samo zgadywanie kształtu requestu zawodziło.
    """
    url = TTVIEWER_ENDPOINT.format(subdomain=subdomain)
    body = {"__args": [None, _current_school_year()], "__gsh": PUBLIC_GSH}
    async with session.post(url, json=body, timeout=aiohttp.ClientTimeout(total=20)) as resp:
        if resp.status != 200:
            raise EdupageApiError(f"getTTViewerData HTTP {resp.status}")
        data = await resp.json(content_type=None)

    if data.get("e") or data.get("reload"):
        # "reload": True to sygnał z EduPage "Twoje dane/gsh są nieaktualne, zapytaj od nowa" -
        # logujemy PEŁNĄ odpowiedź na WARNING (nie tylko skrót w wyjątku), bo to jeden z
        # podejrzanych o objaw "HA pokazuje plan sprzed zmiany" (rodzic zgłosił 2026-09-18) -
        # przyda się do porównania, czy to się faktycznie zdarza cyklicznie w logach.
        _LOGGER.warning(
            "EduPage odrzuciło getTTViewerData dla %s (reload=%s, e=%s): %s",
            subdomain,
            data.get("reload"),
            data.get("e"),
            data,
        )
        raise EdupageApiError(
            f"EduPage odrzuciło zapytanie o aktualny plan (getTTViewerData): {data}"
        )

    try:
        default_num = data["r"]["regular"]["default_num"]
    except (KeyError, TypeError) as err:
        raise EdupageApiError("Nie udało się odczytać numeru aktualnego planu (default_num)") from err

    _LOGGER.info("EduPage (%s): aktualny numer planu (default_num) = %s", subdomain, default_num)
    return default_num


async def async_fetch_timetable(
    session: aiohttp.ClientSession, subdomain: str, tt_num: str | None = None
) -> EdupageTables:
    """Pobiera i parsuje pełny plan lekcji szkoły (wszystkie klasy naraz).

    `tt_num=None` oznacza "użyj aktualnie obowiązującego planu" - w takiej sytuacji
    najpierw pytamy o default_num, tak jak robi to strona publiczna.
    """
    if not tt_num:
        tt_num = await async_get_default_tt_num(session, subdomain)

    url = TIMETABLE_ENDPOINT.format(subdomain=subdomain)
    body = {"__args": [None, tt_num], "__gsh": PUBLIC_GSH}

    try:
        async with session.post(url, json=body, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status != 200:
                raise EdupageApiError(f"regularttGetData HTTP {resp.status}")
            data = await resp.json(content_type=None)
    except aiohttp.ClientError as err:
        raise EdupageApiError(f"Błąd sieci przy pobieraniu planu EduPage: {err}") from err

    if data.get("e") or data.get("reload"):
        # Serwer prosi o "świeże" zapytanie albo zwrócił błąd - najczęściej
        # brakujący/niewłaściwy tt_num. Logujemy pełną odpowiedź (patrz komentarz przy
        # analogicznym sprawdzeniu w async_get_default_tt_num) - to jeden z podejrzanych
        # o "HA pokazuje nieaktualny plan".
        _LOGGER.warning(
            "EduPage odrzuciło regularttGetData dla %s (tt_num=%s, reload=%s, e=%s): %s",
            subdomain,
            tt_num,
            data.get("reload"),
            data.get("e"),
            data,
        )
        raise EdupageApiError(
            f"EduPage odrzuciło zapytanie o plan lekcji (regularttGetData): {data}"
        )

    tables = _parse_dbi_tables(data)
    # INFO (nie DEBUG) specjalnie - to jest GŁÓWNA linia do diagnozowania "plan się nie
    # aktualizuje": jeśli w logu ten sam fingerprint powtarza się godzinami mimo
    # potwierdzonej zmiany na stronie szkoły, to znaczy że EduPage serwuje tę samą
    # (buforowaną po ich stronie) odpowiedź, a nie że ta integracja nie odpytuje na czas.
    _LOGGER.info(
        "EduPage (%s): pobrano plan tt_num=%s, %d tabel, fingerprint=%s",
        subdomain,
        tt_num,
        len(tables.tables),
        tables.fingerprint,
    )
    return tables


async def async_list_classes(session: aiohttp.ClientSession, subdomain: str) -> list[dict[str, str]]:
    """Pomocnicza funkcja dla config_flow: lista klas (id/name/short) do wyboru przez użytkownika."""
    tables = await async_fetch_timetable(session, subdomain)
    return [
        {"id": row["id"], "name": (row.get("name") or "").strip(), "short": row.get("short") or ""}
        for row in tables.rows("classes")
    ]
