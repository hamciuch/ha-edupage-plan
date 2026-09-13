"""Klient publicznego (nie wymagającego logowania) API planu lekcji EduPage.

Szkoła publikuje plan lekcji pod adresem
    https://<subdomain>.edupage.org/timetable/
Strona ta (widget "TTViewer") pobiera CAŁY plan szkoły jednym zapytaniem:

    POST /timetable/server/regulartt.js?__func=regularttGetData
    body: {"__args": [null, "<tt_num>"], "__gsh": "00000000"}

gdzie <tt_num> to numer aktualnie obowiązującego planu (patrz `async_get_default_tt_num`).
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

import logging
from dataclasses import dataclass, field
from typing import Any

import aiohttp

from .const import PUBLIC_GSH, TIMETABLE_ENDPOINT, TTVIEWER_ENDPOINT

_LOGGER = logging.getLogger(__name__)


class EdupageApiError(Exception):
    """Błąd komunikacji z publicznym API EduPage."""


@dataclass
class EdupageTables:
    """Sparsowane tabele planu lekcji jednej szkoły."""

    raw: dict[str, Any] = field(repr=False)
    tables: dict[str, dict[str, Any]] = field(default_factory=dict)

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

    return EdupageTables(raw=raw_json, tables=parsed)


async def async_get_default_tt_num(session: aiohttp.ClientSession, subdomain: str) -> str:
    """Pyta EduPage, jaki jest numer aktualnie obowiązującego planu (regular.default_num)."""
    url = TTVIEWER_ENDPOINT.format(subdomain=subdomain)
    body = {"__args": [None], "__gsh": PUBLIC_GSH}
    async with session.post(url, json=body, timeout=aiohttp.ClientTimeout(total=20)) as resp:
        if resp.status != 200:
            raise EdupageApiError(f"getTTViewerData HTTP {resp.status}")
        data = await resp.json(content_type=None)

    try:
        return data["r"]["regular"]["default_num"]
    except (KeyError, TypeError) as err:
        raise EdupageApiError("Nie udało się odczytać numeru aktualnego planu (default_num)") from err


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

    if data.get("reload"):
        # Serwer prosi o "świeże" zapytanie - najczęściej brakujący/niewłaściwy tt_num.
        raise EdupageApiError(
            "EduPage poprosił o ponowne załadowanie strony (reload) - sprawdź numer planu (tt_num)."
        )

    return _parse_dbi_tables(data)


async def async_list_classes(session: aiohttp.ClientSession, subdomain: str) -> list[dict[str, str]]:
    """Pomocnicza funkcja dla config_flow: lista klas (id/name/short) do wyboru przez użytkownika."""
    tables = await async_fetch_timetable(session, subdomain)
    return [
        {"id": row["id"], "name": (row.get("name") or "").strip(), "short": row.get("short") or ""}
        for row in tables.rows("classes")
    ]
