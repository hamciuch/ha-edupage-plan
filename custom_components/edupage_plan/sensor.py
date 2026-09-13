"""Sensory: aktualna/następna lekcja oraz godzina obiadu."""
from __future__ import annotations

from datetime import datetime, timedelta
import logging

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_change, async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import CONF_CLASS_NAME, CONF_LUNCH_DOCX_PATH, CONF_STUDENT_NAME, DOMAIN, WEEKDAYS_PL
from .coordinator import EdupagePlanCoordinator, LessonOccurrence
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
        return _occ_attrs(self.coordinator.current_occurrence())


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
        return _occ_attrs(self.coordinator.next_occurrence())


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
