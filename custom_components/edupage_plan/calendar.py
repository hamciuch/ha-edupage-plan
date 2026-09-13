"""Encje kalendarza: plan lekcji (tylko do odczytu) + widok zajęć dodatkowych bez kolizji.

Plan lekcji pochodzący ze szkoły NIE jest edytowalny z poziomu Home Assistant -
ta encja nie implementuje create_event/delete_event, więc nie da się go
przypadkiem skasować ani zmienić przez UI/serwis kalendarza HA.

Dodatkowe zajęcia (kółka, korepetycje, judo itd.) rodzic dodaje we WŁASNYM
kalendarzu HA (np. integracja "Local Calendar") - ta integracja go tylko
czyta i wystawia drugi, obliczony kalendarz, w którym zajęcia kolidujące
z lekcją są ukryte (wygaszone), a nie usunięte z oryginału.
"""
from __future__ import annotations

from datetime import datetime

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_EXTRA_CALENDAR_ENTITY, CONF_STUDENT_NAME, DOMAIN
from .coordinator import EdupagePlanCoordinator, LessonOccurrence


def _occ_to_event(occ: LessonOccurrence) -> CalendarEvent:
    # Sala dopisana WPROST do tytułu wydarzenia - karta "calendar" w widoku
    # tygodnia/miesiąca pokazuje tylko summary, a "location" widać dopiero po
    # kliknięciu w wydarzenie. Skoro sala bywa ważna (zmienia się od starszych
    # klas w górę), ma być widoczna od razu, bez klikania.
    summary = f"{occ.subject} (sala {occ.room})" if occ.room else occ.subject

    description_bits = []
    if occ.teacher:
        description_bits.append(f"Nauczyciel: {occ.teacher}")
    if occ.group_name:
        description_bits.append(f"Grupa: {occ.group_name}")
    return CalendarEvent(
        start=occ.start,
        end=occ.end,
        summary=summary,
        location=occ.room or None,
        description=" | ".join(description_bits) or None,
    )


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: EdupagePlanCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[CalendarEntity] = [EdupagePlanCalendar(coordinator, entry)]

    extra_entity_id = ({**entry.data, **entry.options}).get(CONF_EXTRA_CALENDAR_ENTITY)
    if extra_entity_id:
        entities.append(EdupageExtraActivitiesCalendar(hass, coordinator, entry, extra_entity_id))

    async_add_entities(entities)


class EdupagePlanCalendar(CalendarEntity):
    """Kalendarz lekcji ze szkoły - tylko do odczytu."""

    _attr_has_entity_name = True
    _attr_translation_key = "plan_lekcji"

    def __init__(self, coordinator: EdupagePlanCoordinator, entry: ConfigEntry) -> None:
        self.coordinator = coordinator
        self._entry = entry
        student = ({**entry.data, **entry.options}).get(CONF_STUDENT_NAME, entry.title)
        self._attr_unique_id = f"{entry.entry_id}_plan_lekcji"
        self._attr_name = f"Plan lekcji - {student}"

    @property
    def event(self) -> CalendarEvent | None:
        occ = self.coordinator.current_occurrence() or self.coordinator.next_occurrence()
        return _occ_to_event(occ) if occ else None

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        if not self.coordinator.data:
            return []
        return [
            _occ_to_event(occ)
            for occ in self.coordinator.data.occurrences
            if occ.start < end_date and occ.end > start_date
        ]

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))


class EdupageExtraActivitiesCalendar(CalendarEntity):
    """Widok kalendarza dodatkowych zajęć BEZ tych, które kolidują z lekcją.

    Nie modyfikuje oryginalnego kalendarza użytkownika - kolidujące wydarzenia
    są tu po prostu pomijane (wygaszone). Zobacz też binary_sensor - kolizja.
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: EdupagePlanCoordinator,
        entry: ConfigEntry,
        source_entity_id: str,
    ) -> None:
        self.hass = hass
        self.coordinator = coordinator
        self._entry = entry
        self._source_entity_id = source_entity_id
        student = ({**entry.data, **entry.options}).get(CONF_STUDENT_NAME, entry.title)
        self._attr_unique_id = f"{entry.entry_id}_zajecia_bez_kolizji"
        self._attr_name = f"Zajęcia dodatkowe (bez kolizji) - {student}"

    def _overlaps_lesson(self, start: datetime, end: datetime) -> bool:
        if not self.coordinator.data:
            return False
        return any(occ.start < end and occ.end > start for occ in self.coordinator.data.occurrences)

    @property
    def event(self) -> CalendarEvent | None:
        return None  # obliczane na żądanie w async_get_events; brak natywnego "current" tutaj

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        component = hass.data.get("calendar")
        if component is None:
            return []
        source = component.get_entity(self._source_entity_id)
        if source is None:
            return []
        source_events = await source.async_get_events(hass, start_date, end_date)
        return [ev for ev in source_events if not self._overlaps_lesson(ev.start, ev.end)]

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))
