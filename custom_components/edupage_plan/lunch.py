"""Parser harmonogramu obiadów (plik .docx pobrany ręcznie ze strony szkoły/stołówki).

Szkoła publikuje harmonogram obiadów jako plik .docx (link na stronie
"Stołówka" w EduPage) - to zwykły dokument z tabelą "klasa -> godzina",
niedostępny przez żadne API. Ponieważ ten plik nie jest w publicznym API
i bywa aktualizowany ręcznie przez stołówkę, podejście jest takie:

  1. Rodzic pobiera aktualny plik .docx ze strony szkoły i zapisuje go
     np. jako /config/edupage_plan/obiady.docx (ścieżkę podaje się w opcjach
     integracji).
  2. Ta integracja odczytuje plik LOKALNIE (bez sięgania do edupage.org)
     i sama znajduje w tabelach wiersz pasujący do klasy dziecka.

Parser jest celowo tolerancyjny - różne szkoły/lata mogą mieć inny układ
kolumn. Szuka w każdej komórce tabeli tokenu wyglądającego jak nazwa klasy
(np. "4B") oraz tokenu wyglądającego jak godzina (np. "12:30" albo "12:30-12:45"),
i łączy je, jeśli są w tym samym wierszu.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

_CLASS_RE = re.compile(r"^\s*([1-8]\s?[A-Ea-e]|4[AB]\s?LO)\s*$")
_TIME_RE = re.compile(r"([01]?\d|2[0-3])[:.]([0-5]\d)")


def _normalise_class(token: str) -> str:
    return token.replace(" ", "").upper()


def parse_lunch_docx(path: str) -> dict[str, str]:
    """Zwraca mapowanie {KLASA: "GG:MM"} (godzina rozpoczęcia obiadu) albo {} przy błędzie.

    Wymaga pakietu `python-docx` (deklarowanego w manifest.json).
    """
    file_path = Path(path)
    if not file_path.exists():
        _LOGGER.warning("Plik harmonogramu obiadów nie istnieje: %s", path)
        return {}

    try:
        import docx  # python-docx; import lokalny, żeby nie wymagać go, gdy funkcja obiadów jest nieużywana
    except ImportError:
        _LOGGER.error("Brak pakietu python-docx - dodaj go do requirements albo zainstaluj ręcznie")
        return {}

    try:
        document = docx.Document(str(file_path))
    except Exception:  # noqa: BLE001 - dowolny błędny/uszkodzony plik nie może wywalić HA
        _LOGGER.exception("Nie udało się otworzyć pliku %s jako .docx", path)
        return {}

    result: dict[str, str] = {}
    for table in document.tables:
        for row in table.rows:
            cell_texts = [cell.text.strip() for cell in row.cells]
            classes_in_row = [_normalise_class(c) for c in cell_texts if _CLASS_RE.match(c)]
            time_in_row = None
            for cell_text in cell_texts:
                match = _TIME_RE.search(cell_text)
                if match:
                    time_in_row = f"{int(match.group(1)):02d}:{match.group(2)}"
                    break
            if classes_in_row and time_in_row:
                for class_short in classes_in_row:
                    result[class_short] = time_in_row

    if not result:
        _LOGGER.warning(
            "Nie znaleziono żadnych par klasa/godzina w %s - sprawdź układ tabeli w pliku", path
        )
    return result
