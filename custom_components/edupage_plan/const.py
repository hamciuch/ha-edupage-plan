"""Stałe dla integracji edupage_plan."""
from __future__ import annotations

from datetime import timedelta

DOMAIN = "edupage_plan"

# --- Konfiguracja ---
CONF_SUBDOMAIN = "subdomain"          # np. "promienista" z promienista.edupage.org
CONF_STUDENT_NAME = "student_name"    # etykieta ucznia, np. "Gabrysia"
CONF_CLASS_ID = "class_id"            # id klasy w danych EduPage, np. "-73"
CONF_CLASS_NAME = "class_name"        # np. "4B" - tylko do wyświetlania
CONF_GROUP_CHOICES = "group_choices"  # {division_id: group_id} wybory grup ucznia
CONF_EXTRA_CALENDAR_ENTITY = "extra_calendar_entity_id"  # np. calendar.zajecia_dodatkowe_wiktor
CONF_LUNCH_DOCX_PATH = "lunch_docx_path"  # ścieżka do pliku .docx z harmonogramem obiadów
CONF_SUBJECT_COLOR_OVERRIDES = "subject_color_overrides"  # opcjonalne nadpisanie kolorów przedmiotów

DEFAULT_SCAN_INTERVAL = timedelta(hours=3)
FAST_RETRY_INTERVAL = timedelta(minutes=15)

# Publiczne API planu lekcji EduPage (bez logowania)
TIMETABLE_ENDPOINT = "https://{subdomain}.edupage.org/timetable/server/regulartt.js?__func=regularttGetData"
TTVIEWER_ENDPOINT = "https://{subdomain}.edupage.org/timetable/server/ttviewer.js?__func=getTTViewerData"
PUBLIC_GSH = "00000000"

# Kolejność dni tygodnia tak, jak zwraca je tabela "days" (indeks znaku w polu "days" karty)
WEEKDAYS_PL = ["Poniedziałek", "Wtorek", "Środa", "Czwartek", "Piątek", "Sobota", "Niedziela"]
WEEKDAYS_SHORT_PL = ["Pn", "Wt", "Śr", "Czw", "Pi", "Sob", "Nie"]

# Ile dni naprzód budujemy wpisy w kalendarzu
SCHEDULE_HORIZON_DAYS = 21

SIGNAL_EDUPAGE_UPDATED = f"{DOMAIN}_updated"
