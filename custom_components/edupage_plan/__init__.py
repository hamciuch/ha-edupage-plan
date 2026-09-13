"""Integracja edupage_plan - plan lekcji z publicznego API EduPage w Home Assistant."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import EdupagePlanCoordinator

PLATFORMS = ["calendar", "sensor", "binary_sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = EdupagePlanCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Opcje integracji się zmieniły (wybory grup, plik obiadów, kalendarz dodatkowych zajęć).

    WAŻNE: samo odświeżenie coordinatora (async_request_refresh) NIE wystarczy -
    encje takie jak sensor godziny obiadu, kalendarz "zajęcia dodatkowe bez
    kolizji" czy binary_sensor kolizji są tworzone WARUNKOWO, tylko raz, w
    async_setup_entry każdej platformy (patrz sensor.py/calendar.py/
    binary_sensor.py) - jeśli dana opcja nie była ustawiona przy tamtym
    pierwszym uruchomieniu, taka encja nigdy się nie pojawi, choćbyśmy
    dowolną ilość razy odświeżali coordinator. Trzeba przeładować cały wpis
    konfiguracji, żeby platformy zostały ponownie skonfigurowane z aktualnymi
    opcjami i domotworzyły/usunęły encje zależne od opcji.
    """
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unloaded
