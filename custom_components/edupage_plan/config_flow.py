"""Config flow dla edupage_plan.

Kreator:
  1. user       - subdomena EduPage (np. "promienista") + imię dziecka (etykieta wpisu)
  2. class      - wybór oddziału (klasy) z listy pobranej na żywo z EduPage
  3. divisions  - dla KAŻDEGO podziału klasy z więcej niż jedną grupą
                  (informatyka, drugi język, WF, religia/etyka, ...) - wybór grupy dziecka
  4. extra      - (opcjonalnie) encja kalendarza HA z dodatkowymi zajęciami dziecka,
                  żeby integracja mogła wykrywać kolizje z lekcjami

Options flow pozwala później zmienić wybory grup, encję kalendarza dodatkowych zajęć
oraz ścieżkę do pliku z harmonogramem obiadów - bez usuwania i dodawania wpisu od nowa.
"""
from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import selector

from .api import EdupageApiError, async_fetch_timetable
from .coordinator import get_class_divisions
from .const import (
    CONF_CLASS_ID,
    CONF_CLASS_NAME,
    CONF_EXTRA_CALENDAR_ENTITY,
    CONF_GROUP_CHOICES,
    CONF_LUNCH_DOCX_PATH,
    CONF_STUDENT_NAME,
    CONF_SUBDOMAIN,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class EdupagePlanConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Kreator dodawania ucznia."""

    VERSION = 1

    def __init__(self) -> None:
        self._subdomain: str | None = None
        self._student_name: str | None = None
        self._tables = None
        self._classes: list[dict[str, str]] = []
        self._class_id: str | None = None
        self._class_name: str | None = None
        self._divisions: list[dict] = []
        self._division_idx = 0
        self._group_choices: dict[str, str] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            self._subdomain = user_input[CONF_SUBDOMAIN].strip().lower()
            self._student_name = user_input[CONF_STUDENT_NAME].strip()
            session = async_get_clientsession(self.hass)
            try:
                self._tables = await async_fetch_timetable(session, self._subdomain)
            except EdupageApiError as err:
                _LOGGER.warning("Nie udało się pobrać planu z %s: %s", self._subdomain, err)
                errors["base"] = "cannot_connect"
            except aiohttp.ClientError:
                errors["base"] = "cannot_connect"
            else:
                self._classes = sorted(
                    (
                        {"id": c["id"], "label": (c.get("short") or c.get("name") or c["id"]).strip()}
                        for c in self._tables.rows("classes")
                    ),
                    key=lambda c: c["label"],
                )
                return await self.async_step_class()

        schema = vol.Schema(
            {
                vol.Required(CONF_SUBDOMAIN, default=self._subdomain or ""): str,
                vol.Required(CONF_STUDENT_NAME, default=self._student_name or ""): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_class(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            self._class_id = user_input[CONF_CLASS_ID]
            match = next((c for c in self._classes if c["id"] == self._class_id), None)
            self._class_name = match["label"] if match else self._class_id
            self._divisions = get_class_divisions(self._tables, self._class_id)
            self._division_idx = 0
            return await self.async_step_division()

        schema = vol.Schema(
            {
                vol.Required(CONF_CLASS_ID): selector.selector(
                    {
                        "select": {
                            "options": [
                                {"value": c["id"], "label": c["label"]} for c in self._classes
                            ]
                        }
                    }
                )
            }
        )
        return self.async_show_form(step_id="class", data_schema=schema)

    async def async_step_division(self, user_input: dict[str, Any] | None = None):
        """Jeden krok na jeden podział klasy (informatyka, drugi język, WF, religia/etyka...)."""
        if self._division_idx > 0 and user_input is not None:
            prev_division = self._divisions[self._division_idx - 1]
            self._group_choices[prev_division["division_id"]] = user_input["group_id"]

        if self._division_idx >= len(self._divisions):
            return self.async_create_entry(
                title=self._student_name,
                data={
                    CONF_SUBDOMAIN: self._subdomain,
                    CONF_STUDENT_NAME: self._student_name,
                    CONF_CLASS_ID: self._class_id,
                    CONF_CLASS_NAME: self._class_name,
                    CONF_GROUP_CHOICES: self._group_choices,
                },
            )

        division = self._divisions[self._division_idx]
        self._division_idx += 1
        schema = vol.Schema(
            {
                vol.Required("group_id"): selector.selector(
                    {
                        "select": {
                            "options": [
                                {"value": g["id"], "label": g["name"]} for g in division["groups"]
                            ]
                        }
                    }
                )
            }
        )
        return self.async_show_form(
            step_id="division",
            data_schema=schema,
            description_placeholders={"division_id": division["division_id"]},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> "EdupagePlanOptionsFlow":
        return EdupagePlanOptionsFlow(config_entry)


class EdupagePlanOptionsFlow(config_entries.OptionsFlow):
    """Pozwala później zmienić grupy, kalendarz dodatkowych zajęć i plik z obiadami.

    Podziały klasy (informatyka, drugi język, WF, religia/etyka...) są dociągane
    na żywo z EduPage, więc jeśli szkoła zmieni podział grup w trakcie roku,
    wystarczy ponownie otworzyć te opcje.
    """

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.config_entry = config_entry
        self._divisions: list[dict] = []

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        current = {**self.config_entry.data, **self.config_entry.options}

        if user_input is not None:
            group_choices = dict(current.get(CONF_GROUP_CHOICES, {}))
            for division in self._divisions:
                key = f"group__{division['division_id']}"
                if key in user_input:
                    group_choices[division["division_id"]] = user_input.pop(key)
            user_input[CONF_GROUP_CHOICES] = group_choices
            return self.async_create_entry(title="", data=user_input)

        session = async_get_clientsession(self.hass)
        try:
            tables = await async_fetch_timetable(session, current[CONF_SUBDOMAIN])
            self._divisions = get_class_divisions(tables, current[CONF_CLASS_ID])
        except (EdupageApiError, aiohttp.ClientError):
            # EduPage chwilowo niedostępny - i tak pokażemy resztę ustawień,
            # tylko bez możliwości zmiany grup w tym oknie.
            self._divisions = []

        existing_choices = current.get(CONF_GROUP_CHOICES, {})
        schema_dict: dict[Any, Any] = {}
        for division in self._divisions:
            key = f"group__{division['division_id']}"
            options = [{"value": g["id"], "label": g["name"]} for g in division["groups"]]
            default = existing_choices.get(division["division_id"])
            field = vol.Required(key, default=default) if default else vol.Required(key)
            schema_dict[field] = selector.selector({"select": {"options": options}})

        schema_dict[
            vol.Optional(CONF_EXTRA_CALENDAR_ENTITY, default=current.get(CONF_EXTRA_CALENDAR_ENTITY, ""))
        ] = selector.selector({"entity": {"domain": "calendar"}})
        schema_dict[
            vol.Optional(CONF_LUNCH_DOCX_PATH, default=current.get(CONF_LUNCH_DOCX_PATH, ""))
        ] = str

        return self.async_show_form(step_id="init", data_schema=vol.Schema(schema_dict))
