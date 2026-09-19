"""DataUpdateCoordinator - pobiera plan EduPage i buduje plan lekcji jednego ucznia."""
from __future__ import annotations

import hashlib
import logging
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import EdupageApiError, EdupageTables, async_fetch_timetable
from .const import (
    CONF_CLASS_ID,
    CONF_GROUP_CHOICES,
    CONF_SUBDOMAIN,
    CONF_SUBJECT_COLOR_OVERRIDES,
    DEFAULT_SCAN_INTERVAL,
    FAST_RETRY_INTERVAL,
    SCHEDULE_HORIZON_DAYS,
)

_LOGGER = logging.getLogger(__name__)

# Domyślna paleta - używana tylko jeśli szkoła nie zdefiniowała koloru przedmiotu.
FALLBACK_COLOR = "#9E9E9E"


@dataclass
class LessonOccurrence:
    """Jedno konkretne wystąpienie lekcji w planie (konkretny dzień + godzina)."""

    start: datetime
    end: datetime
    subject: str
    subject_short: str
    color: str
    teacher: str
    room: str
    group_name: str | None
    locked: bool

    @property
    def summary(self) -> str:
        return self.subject


@dataclass
class StudentSchedule:
    """Wynik przetworzenia planu dla jednego ucznia (jednego config entry)."""

    class_id: str
    class_name: str
    occurrences: list[LessonOccurrence]
    tt_valid_text: str | None
    subject_colors: dict[str, str]
    generated_at: datetime
    # DIAGNOSTYKA "czy plan faktycznie się odświeża" - DWA różne odciski, celowo różnej
    # "ziarnistości" (słuszna uwaga rodzica: odcisk całej odpowiedzi API nic nie mówi o
    # tym, czy zmieniło się coś w PLANIE TEGO DZIECKA - może się zmienić z powodu innej
    # klasy, innego nauczyciela, w ogóle niezwiązanej rzeczy w danych całej szkoły):
    #
    # - source_fingerprint: odcisk CAŁEJ surowej odpowiedzi EduPage (patrz
    #   api.py: _fingerprint) - wszystkie klasy, przedmioty, nauczyciele naraz. Mówi
    #   tylko "czy EduPage w ogóle zwróciło coś innego niż ostatnio", nie "czy to dotyczy
    #   TEGO dziecka".
    # - plan_fingerprint: odcisk POLICZONY z dokładnie tych samych pól, które faktycznie
    #   trafiają do planu tego ucznia - dzień tygodnia, godzina start/koniec, przedmiot,
    #   nauczyciel, sala, grupa (patrz _plan_fingerprint niżej) - dokładnie to, co rodzic
    #   by porównał "ręcznie" patrząc na plan. To JEST właściwa odpowiedź na "czy coś się
    #   zmieniło w moim planie": ten sam plan_fingerprint mimo potwierdzonej zmiany na
    #   stronie szkoły oznacza realny problem (integracja nie widzi zmiany), a source_
    #   fingerprint zmieniający się przy niezmienionym plan_fingerprint oznacza po prostu
    #   że zmieniło się coś u innej klasy/przedmiotu - nie błąd.
    source_fingerprint: str = ""
    plan_fingerprint: str = ""


def _decode_weekday(days_mask: str) -> int | None:
    """'days' z tabeli cards to np. '10000' - indeks znaku '1' to dzień tygodnia (0=Pn)."""
    if not days_mask:
        return None
    idx = days_mask.find("1")
    return idx if idx >= 0 else None


def _lesson_applies_to_student(
    lesson: dict, class_id: str, divisions_by_id: dict[str, dict], group_choices: dict[str, str]
) -> tuple[bool, str | None]:
    """Sprawdza, czy dana lekcja dotyczy naszego ucznia (z uwzględnieniem wybranej grupy).

    Zwraca (czy_dotyczy, nazwa_grupy_do_wyswietlenia).
    """
    classdata = lesson.get("classdata") or {}
    entry = classdata.get(class_id)
    if entry is None:
        return False, None

    division_id = entry.get("divisionid")
    groups_mask = entry.get("groups") or ""
    division = divisions_by_id.get(division_id)

    if division is None or not division.get("ascttdivision"):
        # Podział "cała klasa" (ascttdivision == "") - dotyczy zawsze, wyboru grupy nie trzeba.
        return True, None

    group_ids = division.get("groupids", [])
    included_group_ids = [gid for gid, bit in zip(group_ids, groups_mask) if bit == "1"]

    chosen_group_id = group_choices.get(division_id)
    if chosen_group_id is None:
        # Rodzic nie skonfigurował jeszcze wyboru dla tego podziału - pomijamy tę lekcję,
        # żeby nie pokazywać losowo cudzej grupy. Ostrzeżenie zobaczysz w logach HA.
        _LOGGER.warning(
            "Brak wybranej grupy dla podziału %s (klasa %s) - lekcja pominięta. "
            "Skonfiguruj wybór grupy w opcjach integracji.",
            division_id,
            class_id,
        )
        return False, None

    return chosen_group_id in included_group_ids, None


def _plan_fingerprint(weekday_occ_templates: dict[int, list[dict]]) -> str:
    """Odcisk PLANU TEGO UCZNIA - dokładnie na podstawie tych pól, które by porównał rodzic:
    dzień tygodnia, godzina start/koniec, przedmiot, nauczyciel, sala, grupa.

    Celowo NIE jest to odcisk całej surowej odpowiedzi EduPage (patrz api.py: _fingerprint /
    StudentSchedule.source_fingerprint) - ten mówiłby "czy EduPage zwróciło cokolwiek innego
    niż ostatnio" dla CAŁEJ szkoły, a nie "czy zmieniło się coś w planie TEGO dziecka". Dwie
    kolejne odpowiedzi EduPage mogą różnić się bajt w bajt (bo zmieniła się np. sala innej
    klasy) - source_fingerprint się zmieni, ale plan_fingerprint TEGO ucznia zostanie taki
    sam, bo jego plan faktycznie się nie zmienił. To jest ten drugi przypadek, który ma
    znaczenie przy pytaniu "czy plan mojego dziecka jest aktualny".
    """
    parts = []
    for weekday in sorted(weekday_occ_templates):
        rows = sorted(
            weekday_occ_templates[weekday],
            key=lambda t: (t["start_time"], t["subject"], t["group_name"] or ""),
        )
        for tmpl in rows:
            parts.append(
                "|".join(
                    [
                        str(weekday),
                        tmpl["start_time"],
                        tmpl["end_time"],
                        tmpl["subject"],
                        tmpl["teacher"],
                        tmpl["room"],
                        tmpl["group_name"] or "",
                    ]
                )
            )
    raw = "\n".join(parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]


def get_class_divisions(tables: EdupageTables, class_id: str) -> list[dict]:
    """Zwraca podziały (divisions) danej klasy WRAZ z rozwiniętymi grupami - do config_flow.

    Pomija podział "cała klasa" (ascttdivision == "") bo tam nie ma czego wybierać.
    Każdy element: {"division_id", "label", "groups": [{"id", "name"}]}
    """
    groups_by_id = {g["id"]: g for g in tables.rows("groups")}
    out = []
    for division in tables.rows("divisions"):
        if division.get("classid") != class_id:
            continue
        if not division.get("ascttdivision"):
            continue  # cała klasa - nic do wyboru
        group_ids = division.get("groupids", [])
        groups = [groups_by_id[gid] for gid in group_ids if gid in groups_by_id]
        if len(groups) < 2:
            continue
        out.append(
            {
                "division_id": division["id"],
                "groups": [{"id": g["id"], "name": g.get("name") or g["id"]} for g in groups],
            }
        )
    return out


def _subject_display_names(
    subjects_by_id: dict[str, dict], used_subject_ids: set[str]
) -> dict[str, str]:
    """subjectid -> nazwa wyświetlana w kalendarzu/sensorach.

    Szkoła może zdefiniować kilka RÓŻNYCH przedmiotów o tej samej pełnej nazwie i odróżniać
    je tylko skrótem - typowo w klasach 1-3: "ew" (edukacja wczesnoszkolna) i "ewf" (to samo
    pole "name", ale faktycznie WF). Widok szkolny pokazuje skrót, więc tam różnica jest widoczna;
    my braliśmy samo `name`, przez co lekcja WF wyglądała jak zwykła edukacja wczesnoszkolna.
    Gdy w planie TEGO ucznia dwa różne przedmioty mają tę samą nazwę, doklejamy skrót:
    "edukacja wczesnoszkolna (ewf)". Nazwy unikalne zostają bez zmian (bez "(mat)" wszędzie).

    Zliczamy tylko przedmioty z planu tego ucznia, nie całej szkoły - inaczej zmiana w planie
    innej klasy zmieniałaby nazwy lekcji tego dziecka (i jego plan_fingerprint).
    """
    def base_name(subject: dict) -> str:
        return (subject.get("name") or subject.get("short") or "?").strip()

    counts = Counter(
        base_name(subjects_by_id[sid]).casefold() for sid in used_subject_ids if sid in subjects_by_id
    )
    out: dict[str, str] = {}
    for sid, subject in subjects_by_id.items():
        name = base_name(subject)
        short = (subject.get("short") or "").strip()
        if counts.get(name.casefold(), 0) > 1 and short and short.casefold() != name.casefold():
            name = f"{name} ({short})"
        out[sid] = name
    return out


def build_student_schedule(
    tables: EdupageTables,
    class_id: str,
    group_choices: dict[str, str],
    color_overrides: dict[str, str] | None = None,
    horizon_days: int = SCHEDULE_HORIZON_DAYS,
    today: date | None = None,
) -> StudentSchedule:
    """Główna logika: klasa + wybory grup -> konkretne wystąpienia lekcji w kalendarzu."""
    color_overrides = color_overrides or {}
    today = today or date.today()

    classes_by_id = {c["id"]: c for c in tables.rows("classes")}
    subjects_by_id = {s["id"]: s for s in tables.rows("subjects")}
    teachers_by_id = {t["id"]: t for t in tables.rows("teachers")}
    classrooms_by_id = {r["id"]: r for r in tables.rows("classrooms")}
    divisions_by_id = {d["id"]: d for d in tables.rows("divisions")}
    lessons_by_id = {l["id"]: l for l in tables.rows("lessons")}
    periods_by_id = {p["id"]: p for p in tables.rows("periods")}

    class_row = classes_by_id.get(class_id, {})
    class_name = (class_row.get("short") or class_row.get("name") or class_id).strip()

    # Prefiltrujemy lekcje dotyczące tej klasy i (jeśli trzeba) wybranej grupy - raz, nie per-dzień.
    relevant_lesson_ids: dict[str, str | None] = {}
    for lesson_id, lesson in lessons_by_id.items():
        if class_id not in (lesson.get("classids") or []):
            continue
        applies, group_name = _lesson_applies_to_student(lesson, class_id, divisions_by_id, group_choices)
        if applies:
            relevant_lesson_ids[lesson_id] = group_name

    used_subject_ids = {
        lessons_by_id[lid].get("subjectid") for lid in relevant_lesson_ids if lessons_by_id[lid].get("subjectid")
    }
    display_names = _subject_display_names(subjects_by_id, used_subject_ids)

    # Kolory kluczowane WYŚWIETLANĄ nazwą (rozróżnia "ew" od "ewf"). Nadpisanie kolorem po
    # pełnej nazwie z opcji nadal działa (kompatybilność wsteczna) - wyświetlana nazwa ma pierwszeństwo.
    subject_colors: dict[str, str] = {}
    for sid, subj in subjects_by_id.items():
        raw_name = subj.get("name") or subj.get("short") or ""
        display = display_names[sid]
        subject_colors[display] = (
            color_overrides.get(display) or color_overrides.get(raw_name) or subj.get("color") or FALLBACK_COLOR
        )

    weekday_occ_templates: dict[int, list[dict]] = {}
    for card in tables.rows("cards"):
        lesson_id = card.get("lessonid")
        if lesson_id not in relevant_lesson_ids:
            continue
        weekday = _decode_weekday(card.get("days") or "")
        if weekday is None or weekday > 6:
            continue
        period = periods_by_id.get(card.get("period"))
        if not period or not period.get("starttime") or not period.get("endtime"):
            continue

        lesson = lessons_by_id[lesson_id]
        subject = subjects_by_id.get(lesson.get("subjectid"), {})
        subject_name = display_names.get(lesson.get("subjectid")) or subject.get("name") or subject.get("short") or "?"
        teacher_names = ", ".join(
            teachers_by_id[t].get("short", t) for t in (lesson.get("teacherids") or []) if t in teachers_by_id
        )
        room_ids = card.get("classroomids") or []
        room_names = ", ".join(
            classrooms_by_id[r].get("short") or classrooms_by_id[r].get("name", r)
            for r in room_ids
            if r and r in classrooms_by_id
        )

        weekday_occ_templates.setdefault(weekday, []).append(
            {
                "start_time": period["starttime"],
                "end_time": period["endtime"],
                "subject": subject_name,
                "subject_short": subject.get("short") or subject_name,
                "color": subject_colors.get(subject_name, FALLBACK_COLOR),
                "teacher": teacher_names,
                "room": room_names,
                "group_name": relevant_lesson_ids[lesson_id],
                "locked": bool(card.get("locked")),
            }
        )

    occurrences: list[LessonOccurrence] = []
    for day_offset in range(horizon_days):
        current_date = today + timedelta(days=day_offset)
        weekday = current_date.weekday()  # 0=poniedziałek, tak samo jak w danych EduPage
        for tmpl in weekday_occ_templates.get(weekday, []):
            # WAŻNE: Home Assistant (CalendarEvent) wymaga dat ZE STREFĄ CZASOWĄ - naiwny
            # datetime powoduje wyjątek przy tworzeniu wydarzenia i całą encję kalendarza
            # znika (HA oznacza ją jako "nie jest już dostarczana przez integrację").
            start_dt = datetime.combine(current_date, _parse_hhmm(tmpl["start_time"])).replace(
                tzinfo=dt_util.DEFAULT_TIME_ZONE
            )
            end_dt = datetime.combine(current_date, _parse_hhmm(tmpl["end_time"])).replace(
                tzinfo=dt_util.DEFAULT_TIME_ZONE
            )
            occurrences.append(
                LessonOccurrence(
                    start=start_dt,
                    end=end_dt,
                    subject=tmpl["subject"],
                    subject_short=tmpl["subject_short"],
                    color=tmpl["color"],
                    teacher=tmpl["teacher"],
                    room=tmpl["room"],
                    group_name=tmpl["group_name"],
                    locked=tmpl["locked"],
                )
            )

    occurrences.sort(key=lambda o: o.start)

    globals_row = (tables.rows("globals") or [{}])[0]
    valid_text = (globals_row.get("settings") or {}).get("m_strDateBellowTimeTable")

    return StudentSchedule(
        class_id=class_id,
        class_name=class_name,
        occurrences=occurrences,
        tt_valid_text=valid_text,
        subject_colors=subject_colors,
        generated_at=dt_util.now(),
        source_fingerprint=tables.fingerprint,
        plan_fingerprint=_plan_fingerprint(weekday_occ_templates),
    )


def _parse_hhmm(value: str) -> time:
    hour, minute = value.split(":")
    return time(hour=int(hour), minute=int(minute))


class EdupagePlanCoordinator(DataUpdateCoordinator[StudentSchedule]):
    """Coordinator dla jednego ucznia (jeden config entry)."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"edupage_plan ({entry.title})",
            update_interval=DEFAULT_SCAN_INTERVAL,
        )
        self.entry = entry
        self._session = async_get_clientsession(hass)

    @property
    def _options(self) -> dict:
        # opcje (options flow) mają pierwszeństwo nad danymi z pierwotnej konfiguracji
        return {**self.entry.data, **self.entry.options}

    async def _async_update_data(self) -> StudentSchedule:
        opts = self._options
        subdomain = opts[CONF_SUBDOMAIN]
        class_id = opts[CONF_CLASS_ID]
        group_choices = opts.get(CONF_GROUP_CHOICES, {})
        color_overrides = opts.get(CONF_SUBJECT_COLOR_OVERRIDES, {})

        try:
            tables = await async_fetch_timetable(self._session, subdomain)
        except EdupageApiError as err:
            self.update_interval = FAST_RETRY_INTERVAL
            raise UpdateFailed(str(err)) from err

        self.update_interval = DEFAULT_SCAN_INTERVAL
        return build_student_schedule(tables, class_id, group_choices, color_overrides)

    def current_occurrence(self, now: datetime | None = None) -> LessonOccurrence | None:
        now = now or dt_util.now()
        if not self.data:
            return None
        for occ in self.data.occurrences:
            if occ.start <= now < occ.end:
                return occ
        return None

    def next_occurrence(self, now: datetime | None = None) -> LessonOccurrence | None:
        now = now or dt_util.now()
        if not self.data:
            return None
        for occ in self.data.occurrences:
            if occ.start > now:
                return occ
        return None
