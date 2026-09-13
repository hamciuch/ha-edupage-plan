"""Parser harmonogramu obiadów (plik .docx pobrany ręcznie ze strony szkoły/stołówki).

Szkoła publikuje harmonogram obiadów jako plik .docx (link na stronie
"Stołówka" w EduPage), niedostępny przez żadne API - trzeba go pobrać ręcznie
i wskazać ścieżkę w opcjach integracji; ta integracja czyta go LOKALNIE
(bez sięgania do edupage.org).

Prawdziwy plik (sprawdzony na realnym harmonogramie szkoły) to NIE tabela -
to zwykłe akapity, pogrupowane nagłówkami dni tygodnia (PONIEDZIAŁEK, WTOREK,
ŚRODA, CZWARTEK, PIĄTEK), a każdy wiersz w obrębie dnia wygląda tak:

    12.30 – 12.45 – klasa 4a, 4b, 5a
    13.40 – 13.55 – klasa 6abc, 7abc, 8abc

Uwagi wynikające z realnego pliku:
- godziny są zapisane z KROPKĄ, nie dwukropkiem ("12.30", nie "12:30")
- myślnik między godzinami/"klasa" bywa zwykłym "-" albo długim "–" (czasem
  oba w jednym wierszu, z różną liczbą spacji dookoła)
- harmonogram bywa RÓŻNY w różne dni tygodnia dla tej samej klasy (nie jeden
  stały czas obiadu!), stąd zwracamy słownik per dzień tygodnia
- czasem kilka oddziałów danej klasy je razem, zapisane skrótowo:
  "6abc" = klasy 6A + 6B + 6C (cyfra + kilka liter sekcji, bez przecinków)
- niektóre sloty czasowe nie mają żadnej klasy przypisanej (koniec
  harmonogramu, "dojadanie") - pomijamy je
"""
from __future__ import annotations

import logging
import re
import unicodedata
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

_DAY_HEADERS = {
    "PONIEDZIALEK": 0,
    "WTOREK": 1,
    "SRODA": 2,
    "CZWARTEK": 3,
    "PIATEK": 4,
    "SOBOTA": 5,
    "NIEDZIELA": 6,
}

# "12.30 – 12.45 – klasa 4a, 4b, 5a"  /  "13.10  – 13.25 – klasa  3c"
_LINE_RE = re.compile(
    r"^\s*(?P<start>\d{1,2}[.:]\d{2})\s*[-–—]+\s*(?P<end>\d{1,2}[.:]\d{2})\s*[-–—]+\s*klas\w*\s*(?P<classes>.*?)\s*$",
    re.IGNORECASE,
)

# "4b" -> ("4", "b"); "6abc" -> ("6", "abc") - jedna lub więcej liter sekcji po cyfrze klasy.
_CLASS_TOKEN_RE = re.compile(r"^(\d+)\s*([a-hA-H]+)$")


def _strip_diacritics(text: str) -> str:
    # Ł/ł nie mają rozkładu kanonicznego w Unicode (NFKD ich nie rozbije na "L"/"l" +
    # znak diakrytyczny, w przeciwieństwie do np. Ą/Ę/Ó/Ś/Ć/Ź/Ż) - trzeba je podmienić ręcznie,
    # inaczej nagłówek "PONIEDZIAŁEK" nigdy nie dopasuje się do "PONIEDZIALEK".
    text = text.replace("Ł", "L").replace("ł", "l")
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def _normalize_time(raw: str) -> str:
    hour, minute = re.split(r"[.:]", raw)
    return f"{int(hour):02d}:{minute}"


def _expand_class_tokens(raw: str) -> list[str]:
    """"4a, 4b, 5a" -> ["4A","4B","5A"]; "6abc" -> ["6A","6B","6C"] (kilka oddziałów naraz)."""
    out: list[str] = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        match = _CLASS_TOKEN_RE.match(token)
        if not match:
            _LOGGER.debug("Nierozpoznany format klasy w harmonogramie obiadów: %r", token)
            continue
        grade, letters = match.groups()
        for letter in letters:
            out.append(f"{grade}{letter}".upper())
    return out


def parse_lunch_docx(path: str) -> dict[int, dict[str, str]]:
    """Zwraca {dzień_tygodnia (0=Pn..4=Pt, ...): {KLASA: "GG:MM-GG:MM"}} albo {} przy błędzie.

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

    result: dict[int, dict[str, str]] = {}
    current_day: int | None = None

    for para in document.paragraphs:
        text = para.text.strip()
        if not text:
            continue

        header_key = _strip_diacritics(text).upper().rstrip(":")
        if header_key in _DAY_HEADERS:
            current_day = _DAY_HEADERS[header_key]
            continue

        match = _LINE_RE.match(text)
        if not match or current_day is None:
            continue

        classes = _expand_class_tokens(match.group("classes"))
        if not classes:
            continue

        time_range = f"{_normalize_time(match.group('start'))}-{_normalize_time(match.group('end'))}"
        day_map = result.setdefault(current_day, {})
        for class_name in classes:
            day_map[class_name] = time_range

    if not result:
        _LOGGER.warning(
            "Nie znaleziono żadnych godzin obiadu w %s - sprawdź układ pliku (obsługiwany format: "
            "akapity z nagłówkami dni tygodnia PONIEDZIAŁEK/WTOREK/... i wierszami "
            "'GG.MM – GG.MM – klasa X, Y')",
            path,
        )
    return result
