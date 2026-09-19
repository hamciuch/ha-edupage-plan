"""Sensory: aktualna/następna lekcja oraz godzina obiadu."""
from __future__ import annotations

from datetime import date, datetime, timedelta
import logging

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_change, async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CLASS_NAME,
    CONF_LUNCH_DOCX_PATH,
    CONF_STUDENT_NAME,
    DOMAIN,
    WEEKDAYS_PL,
    WEEKDAYS_SHORT_PL,
)
from .coordinator import EdupagePlanCoordinator, LessonOccurrence, StudentSchedule
from .lunch import parse_lunch_docx

_LOGGER = logging.getLogger(__name__)

LUNCH_REFRESH_INTERVAL = timedelta(hours=12)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: EdupagePlanCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = [
        EdupageCurrentLessonSensor(coordinator, entry),
        EdupageNextLessonSensor(coordinator, entry),
        EdupageWeekGridSensor(coordinator, entry),
    ]

    opts = {**entry.data, **entry.options}
    if opts.get(CONF_LUNCH_DOCX_PATH):
        entities.append(EdupageLunchSensor(hass, coordinator, entry))

    async_add_entities(entities)


def _occ_attrs(occ: LessonOccurrence | None) -> dict:
    if occ is None:
        return {}
    return {
        "przedmiot": occ.subject,
        "skrot": occ.subject_short,
        "nauczyciel": occ.teacher or None,
        "sala": occ.room or None,
        "grupa": occ.group_name,
        "kolor": occ.color,
        "start": occ.start.isoformat(),
        "koniec": occ.end.isoformat(),
    }


def _diag_attrs(data: StudentSchedule | None) -> dict:
    """Atrybuty do sprawdzenia "czy dane faktycznie się odświeżyły" bez grzebania w logu.

    Zgłoszenie (2026-09-18): HA pokazywał plan sprzed zmiany na stronie szkoły. Słuszna
    uwaga: odcisk CAŁEJ odpowiedzi EduPage (wszystkie klasy, przedmioty, nauczyciele
    naraz) nic nie mówi o tym, czy zmieniło się coś w planie AKURAT TEGO dziecka - stąd
    dwa różne atrybuty, jeden ogólny i jeden policzony dokładnie z pól, które rodzic by
    porównał ręcznie (dzień, godzina, przedmiot, nauczyciel, sala, grupa):

    - `ostatnia_aktualizacja` - kiedy integracja ostatnio SKUTECZNIE pobrała dane.
    - `wersja_planu` (StudentSchedule.plan_fingerprint, patrz coordinator.py:
      _plan_fingerprint) - odcisk POLICZONY z dnia tygodnia, godziny start/koniec,
      przedmiotu, nauczyciela, sali i grupy - czyli dokładnie tych pól, z których składa
      się plan TEGO ucznia. To jest właściwa odpowiedź na "czy mój plan jest aktualny":
      ten sam `wersja_planu` mimo potwierdzonej zmiany na stronie szkoły = realny problem.
    - `wersja_odpowiedzi_api` (StudentSchedule.source_fingerprint) - odcisk CAŁEJ surowej
      odpowiedzi EduPage, dla WSZYSTKICH klas naraz - może się zmieniać (bo zmieniło się
      coś u innej klasy) nawet gdy `wersja_planu` TEGO dziecka zostaje taka sama, i to
      jest normalne, nie błąd. Przydaje się tylko do odróżnienia "EduPage w ogóle nic nie
      zwróciło innego" od "zwróciło coś innego, ale nie dla tego dziecka".
    """
    if data is None:
        return {}
    return {
        "ostatnia_aktualizacja": data.generated_at.isoformat(),
        "wersja_planu": data.plan_fingerprint or None,
        "wersja_odpowiedzi_api": data.source_fingerprint or None,
    }


class _BaseLessonSensor(SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: EdupagePlanCoordinator, entry: ConfigEntry, kind: str, label: str) -> None:
        self.coordinator = coordinator
        self._entry = entry
        student = ({**entry.data, **entry.options}).get(CONF_STUDENT_NAME, entry.title)
        self._attr_unique_id = f"{entry.entry_id}_{kind}"
        self._attr_name = f"{label} - {student}"

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success


class EdupageCurrentLessonSensor(_BaseLessonSensor):
    _attr_icon = "mdi:school"

    def __init__(self, coordinator: EdupagePlanCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "aktualna_lekcja", "Aktualna lekcja")

    @property
    def native_value(self) -> str:
        occ = self.coordinator.current_occurrence()
        return occ.subject if occ else "Brak zajęć"

    @property
    def extra_state_attributes(self) -> dict:
        return {**_occ_attrs(self.coordinator.current_occurrence()), **_diag_attrs(self.coordinator.data)}


class EdupageNextLessonSensor(_BaseLessonSensor):
    _attr_icon = "mdi:school-outline"

    def __init__(self, coordinator: EdupagePlanCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "nastepna_lekcja", "Następna lekcja")

    @property
    def native_value(self) -> str:
        occ = self.coordinator.next_occurrence()
        return occ.subject if occ else "Brak kolejnych zajęć"

    @property
    def extra_state_attributes(self) -> dict:
        return {**_occ_attrs(self.coordinator.next_occurrence()), **_diag_attrs(self.coordinator.data)}


def build_week_grid(data: StudentSchedule | None, today: date) -> tuple[list[dict], list[dict], list[dict]]:
    """Buduje siatkę tygodnia (nagłówek dni, wiersze godzin, legenda przedmiotów).

    Wydzielone jako CZYSTA funkcja (bez zależności od hass/coordinatora) tak, żeby dało
    się ją przetestować bez uruchamiania Home Assistant - patrz test_week_grid.py.

    Zwraca (dni_naglowek, siatka, legenda):
      - dni_naglowek: [{"nazwa": "Pn", "nazwa_pelna": "Poniedziałek", "data": "14.09"}, ...] (5x, pon-pt)
      - siatka: [{"start": "08:00", "koniec": "08:45", "dni": [cell_pon, cell_wt, ..., cell_pt]}, ...],
                posortowane rosnąco wg godziny startu; cell to None (brak lekcji) albo
                {"przedmiot", "skrot", "sala", "nauczyciel", "grupa", "kolor"}
      - legenda: unikalne przedmioty z tego tygodnia, posortowane wg skrótu:
                 [{"skrot", "przedmiot", "kolor"}, ...]
    """
    if not data:
        return [], [], []

    monday = today - timedelta(days=today.weekday())
    if today.weekday() >= 5:  # sobota/niedziela - pokaż od razu następny tydzień
        monday += timedelta(days=7)
    week_dates = [monday + timedelta(days=i) for i in range(5)]  # pon..pt

    by_day: dict[int, list[LessonOccurrence]] = {i: [] for i in range(5)}
    for occ in data.occurrences:
        for i, day_date in enumerate(week_dates):
            if occ.start.date() == day_date:
                by_day[i].append(occ)
                break

    dni_naglowek = [
        {
            "nazwa": WEEKDAYS_SHORT_PL[i] if i < len(WEEKDAYS_SHORT_PL) else "",
            "nazwa_pelna": WEEKDAYS_PL[i] if i < len(WEEKDAYS_PL) else "",
            "data": day_date.strftime("%d.%m"),
        }
        for i, day_date in enumerate(week_dates)
    ]

    # Wiersze siatki = wszystkie RÓŻNE godziny startu lekcji użyte w tym tygodniu,
    # niezależnie w który dzień - tak, żeby "matematyka o 8:00 w poniedziałek" i
    # "matematyka o 8:00 we wtorek" trafiły w ten sam wiersz, tak jak na papierowym
    # planie lekcji (jeden wiersz = jedna godzina lekcyjna).
    slot_keys: set[tuple[str, str]] = set()
    for occs in by_day.values():
        for occ in occs:
            slot_keys.add((occ.start.strftime("%H:%M"), occ.end.strftime("%H:%M")))

    legenda_map: dict[str, dict] = {}
    siatka = []
    for start_str, end_str in sorted(slot_keys):
        row_cells = []
        for i in range(5):
            match = next(
                (
                    occ
                    for occ in by_day[i]
                    if occ.start.strftime("%H:%M") == start_str and occ.end.strftime("%H:%M") == end_str
                ),
                None,
            )
            if match is None:
                row_cells.append(None)
                continue
            row_cells.append(
                {
                    "przedmiot": match.subject,
                    "skrot": match.subject_short,
                    "sala": match.room or None,
                    "nauczyciel": match.teacher or None,
                    "grupa": match.group_name,
                    "kolor": match.color,
                }
            )
            legenda_map.setdefault(
                match.subject_short,
                {"skrot": match.subject_short, "przedmiot": match.subject, "kolor": match.color},
            )
        siatka.append({"start": start_str, "koniec": end_str, "dni": row_cells})

    legenda = [legenda_map[k] for k in sorted(legenda_map)]
    return dni_naglowek, siatka, legenda


class EdupageWeekGridSensor(_BaseLessonSensor):
    """Cały tydzień szkolny (pon-pt) jako "siatka" - do widoku planu tygodnia na dashboardzie.

    Zwykłe sensory (aktualna/następna lekcja) dają tylko JEDNĄ lekcję na raz - żeby
    zbudować widok całego tygodnia (kolumny = dni, wiersze = godziny lekcyjne, jak na
    papierowym planie lekcji), karta Lovelace potrzebuje z góry POGRUPOWANYCH danych,
    bo szablon Jinja w karcie markdown nie ma wygodnego sposobu na dopasowanie "ta sama
    godzina w różnych dniach = ten sam wiersz". Liczymy to tutaj RAZ, przy każdym
    odświeżeniu coordinatora (patrz build_week_grid powyżej), i wystawiamy jako gotową
    do narysowania strukturę.

    Pokazujemy tydzień zawierający "dziś"; jeśli dziś jest sobota/niedziela, od razu
    pokazujemy NASTĘPNY tydzień szkolny (miniony/kończący się tydzień nikogo już nie
    interesuje w weekend).
    """

    _attr_icon = "mdi:calendar-week"

    def __init__(self, coordinator: EdupagePlanCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "plan_tygodnia", "Plan tygodnia")

    @property
    def native_value(self) -> str:
        data = self.coordinator.data
        if data and data.tt_valid_text:
            return data.tt_valid_text
        return "OK" if data else "Brak danych"

    @property
    def extra_state_attributes(self) -> dict:
        dni_naglowek, siatka, legenda = build_week_grid(self.coordinator.data, dt_util.now().date())
        return {
            "dni": dni_naglowek,
            "siatka": siatka,
            "legenda": legenda,
            **_diag_attrs(self.coordinator.data),
        }


class EdupageLunchSensor(SensorEntity):
    """Godzina obiadu w stołówce - odczytana lokalnie z pliku .docx (patrz lunch.py).

    Prawdziwy harmonogram szkoły bywa RÓŻNY w różne dni tygodnia dla tej samej
    klasy (np. 2A je obiad o innej godzinie w poniedziałek niż w piątek) -
    dlatego trzymamy CAŁY tydzień (`self._week`) i za każdym razem wyliczamy
    wartość na DZISIAJ (`dt_util.now().weekday()`), zamiast jednej stałej
    godziny. Odświeżamy plik co 12h (na wypadek aktualizacji przez szkołę)
    i dodatkowo tuż po północy, żeby stan sam się przełączył na nowy dzień
    bez czekania na najbliższe 12-godzinne odświeżenie.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:food"

    def __init__(self, hass: HomeAssistant, coordinator: EdupagePlanCoordinator, entry: ConfigEntry) -> None:
        self.hass = hass
        self.coordinator = coordinator
        self._entry = entry
        student = ({**entry.data, **entry.options}).get(CONF_STUDENT_NAME, entry.title)
        self._attr_unique_id = f"{entry.entry_id}_obiad"
        self._attr_name = f"Godzina obiadu - {student}"
        self._week: dict[int, dict[str, str]] = {}

    def _today_range(self) -> str | None:
        opts = {**self._entry.data, **self._entry.options}
        class_name = opts.get(CONF_CLASS_NAME, "")
        key = class_name.replace(" ", "").upper()
        weekday = dt_util.now().weekday()
        return self._week.get(weekday, {}).get(key)

    @property
    def native_value(self) -> str:
        rng = self._today_range()
        if rng:
            return rng.split("-")[0]
        # Brak wpisu na dziś - najczęściej weekend (szkoła nie publikuje obiadów na
        # sobotę/niedzielę), ale samo "unknown" w HA wygląda jak błąd integracji,
        # więc zwracamy czytelny komunikat zamiast None.
        weekday = dt_util.now().weekday()
        if weekday >= 5:
            return "Brak (weekend)"
        return "Brak danych na dziś"

    @property
    def extra_state_attributes(self) -> dict:
        weekday = dt_util.now().weekday()
        attrs: dict = {"dzien_tygodnia": WEEKDAYS_PL[weekday] if weekday < len(WEEKDAYS_PL) else None}
        rng = self._today_range()
        if rng:
            start, end = rng.split("-")
            attrs["poczatek"] = start
            attrs["koniec"] = end
            attrs["zakres"] = rng
        return attrs

    async def async_added_to_hass(self) -> None:
        await self._async_do_refresh()
        self.async_on_remove(
            async_track_time_interval(self.hass, self._async_refresh, LUNCH_REFRESH_INTERVAL)
        )
        self.async_on_remove(
            async_track_time_change(self.hass, self._async_refresh, hour=0, minute=1, second=0)
        )

    @callback
    def _async_refresh(self, _now: datetime | None) -> None:
        self.hass.async_create_task(self._async_do_refresh())

    async def _async_do_refresh(self) -> None:
        opts = {**self._entry.data, **self._entry.options}
        path = opts.get(CONF_LUNCH_DOCX_PATH)
        if not path:
            return
        new_week = await self.hass.async_add_executor_job(parse_lunch_docx, path)
        if new_week != self._week:
            self._week = new_week
        self.async_write_ha_state()
