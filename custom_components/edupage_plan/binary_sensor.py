"""Binary sensor: czy w kalendarzu dodatkowych zajęć jest coś kolidującego z lekcją.

Nic nie usuwa ani nie modyfikuje w oryginalnym kalendarzu użytkownika - tylko
sygnalizuje kolizję (żeby dało się np. zrobić automatyzację/powiadomienie),
a "wygaszony" widok bez kolizji dostępny jest jako osobna encja kalendarza
(zobacz calendar.py: EdupageExtraActivitiesCalendar).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval

from .const import CONF_EXTRA_CALENDAR_ENTITY, CONF_STUDENT_NAME, DOMAIN
from .coordinator import EdupagePlanCoordinator

CHECK_INTERVAL = timedelta(minutes=15)
LOOKAHEAD = timedelta(days=7)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    opts = {**entry.data, **entry.options}
    extra_entity_id = opts.get(CONF_EXTRA_CALENDAR_ENTITY)
    if not extra_entity_id:
        return
    coordinator: EdupagePlanCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([EdupageCollisionSensor(hass, coordinator, entry, extra_entity_id)])


class EdupageCollisionSensor(BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_icon = "mdi:calendar-alert"
    _attr_device_class = "problem"

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
        self._attr_unique_id = f"{entry.entry_id}_kolizja_zajec"
        self._attr_name = f"Kolizja zajęć z lekcją - {student}"
        self._is_on = False
        self._conflicting: list[dict] = []

    @property
    def is_on(self) -> bool:
        return self._is_on

    @property
    def extra_state_attributes(self) -> dict:
        return {"kolidujace_zajecia": self._conflicting}

    async def async_added_to_hass(self) -> None:
        await self._async_check(None)
        self.async_on_remove(async_track_time_interval(self.hass, self._async_check, CHECK_INTERVAL))
        self.async_on_remove(self.coordinator.async_add_listener(lambda: None))

    async def _async_check(self, _now: datetime | None) -> None:
        component = self.hass.data.get("calendar")
        if component is None or not self.coordinator.data:
            return
        source = component.get_entity(self._source_entity_id)
        if source is None:
            return

        now = datetime.now()
        events = await source.async_get_events(self.hass, now, now + LOOKAHEAD)

        conflicts = []
        for ev in events:
            for occ in self.coordinator.data.occurrences:
                if occ.start < ev.end and occ.end > ev.start:
                    conflicts.append(
                        {
                            "zajecia": ev.summary,
                            "zajecia_start": ev.start.isoformat(),
                            "lekcja": occ.subject,
                            "lekcja_start": occ.start.isoformat(),
                        }
                    )
                    break

        self._is_on = bool(conflicts)
        self._conflicting = conflicts
        self.async_write_ha_state()
