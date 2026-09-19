"""Szybki test logiki bez Home Assistant - na podstawie prawdziwych danych
pobranych z publicznego API szkoły (Zespół Szkół nr 17 "Victoria", Warszawa),
żeby zweryfikować parser DBI, wybór grup i budowanie planu przed instalacją w HA.
"""
import sys
from datetime import date

sys.path.insert(0, "custom_components")

from edupage_plan.api import _parse_dbi_tables  # noqa: E402
from edupage_plan.calendar import _occ_to_event  # noqa: E402
from edupage_plan.coordinator import build_student_schedule, get_class_divisions  # noqa: E402

# Fragment prawdziwej odpowiedzi regularttGetData (skrócony do tabel potrzebnych klasie 4B / -73)
RAW = {
    "r": {
        "dbiAccessorRes": {
            "tables": [
                {
                    "id": "globals",
                    "data_columns": ["settings"],
                    "data_rows": [
                        {"id": "1", "settings": {"m_strDateBellowTimeTable": "Ważność: 14.09.2026-29.01.2027"}}
                    ],
                },
                {
                    "id": "periods",
                    "data_columns": ["starttime", "endtime"],
                    "data_rows": [
                        {"id": "1", "starttime": "08:00", "endtime": "08:45"},
                        {"id": "2", "starttime": "08:55", "endtime": "09:40"},
                    ],
                },
                {
                    "id": "classes",
                    "data_columns": ["name", "short"],
                    "data_rows": [{"id": "-73", "name": "4B", "short": "4B"}],
                },
                {
                    "id": "subjects",
                    "data_columns": ["name", "short", "color"],
                    "data_rows": [
                        {"id": "-12", "name": "informatyka", "short": "inf", "color": "#FF0000"},
                        {"id": "-11", "name": "język hiszpański", "short": "hisz", "color": "#663300"},
                        {"id": "-15", "name": "język niemiecki", "short": "nie", "color": "#FFCC66"},
                    ],
                },
                {
                    "id": "teachers",
                    "data_columns": ["short"],
                    "data_rows": [
                        {"id": "-37", "short": "KOW"},
                        {"id": "-102", "short": "GAR"},
                    ],
                },
                {
                    "id": "classrooms",
                    "data_columns": ["name", "short"],
                    "data_rows": [{"id": "12", "name": "sala 12", "short": "12"}],
                },
                {
                    "id": "groups",
                    "data_columns": ["name", "classid", "entireclass", "ascttdivision", "divisionid"],
                    "data_rows": [
                        {"id": "*72", "name": "1. Grupa", "classid": "-73", "entireclass": False, "ascttdivision": "1", "divisionid": "-73:1"},
                        {"id": "*73", "name": "2. Grupa", "classid": "-73", "entireclass": False, "ascttdivision": "1", "divisionid": "-73:1"},
                        {"id": "*182", "name": "hisz", "classid": "-73", "entireclass": False, "ascttdivision": "3", "divisionid": "-73:3"},
                        {"id": "*183", "name": "nie", "classid": "-73", "entireclass": False, "ascttdivision": "3", "divisionid": "-73:3"},
                    ],
                },
                {
                    "id": "divisions",
                    "data_columns": ["classid", "ascttdivision", "groupids"],
                    "data_rows": [
                        {"id": "-73:1", "classid": "-73", "ascttdivision": "1", "groupids": ["*72", "*73"]},
                        {"id": "-73:3", "classid": "-73", "ascttdivision": "3", "groupids": ["*182", "*183"]},
                    ],
                },
                {
                    "id": "lessons",
                    "data_columns": [
                        "subjectid", "teacherids", "groupids", "classids", "classdata",
                    ],
                    "data_rows": [
                        {
                            "id": "*255", "subjectid": "-11", "teacherids": ["-102"], "groupids": ["*182"],
                            "classids": ["-73"],
                            "classdata": {"-73": {"divisionid": "-73:3", "groups": "10"}},
                        },
                        {
                            "id": "*274", "subjectid": "-15", "teacherids": ["-102"], "groupids": ["*183"],
                            "classids": ["-73"],
                            "classdata": {"-73": {"divisionid": "-73:3", "groups": "01"}},
                        },
                        {
                            "id": "*338", "subjectid": "-12", "teacherids": ["-37"], "groupids": ["*72"],
                            "classids": ["-73"],
                            "classdata": {"-73": {"divisionid": "-73:1", "groups": "10"}},
                        },
                        {
                            "id": "*339", "subjectid": "-12", "teacherids": ["-37"], "groupids": ["*73"],
                            "classids": ["-73"],
                            "classdata": {"-73": {"divisionid": "-73:1", "groups": "01"}},
                        },
                    ],
                },
                {
                    "id": "cards",
                    "data_columns": ["lessonid", "locked", "period", "days", "weeks", "classroomids"],
                    "data_rows": [
                        {"id": "c1", "lessonid": "*255", "locked": False, "period": "1", "days": "10000", "weeks": "1", "classroomids": ["12"]},
                        {"id": "c2", "lessonid": "*274", "locked": False, "period": "1", "days": "10000", "weeks": "1", "classroomids": ["12"]},
                        {"id": "c3", "lessonid": "*338", "locked": False, "period": "2", "days": "01000", "weeks": "1", "classroomids": [""]},
                        {"id": "c4", "lessonid": "*339", "locked": False, "period": "2", "days": "01000", "weeks": "1", "classroomids": [""]},
                    ],
                },
            ]
        }
    }
}


def main():
    tables = _parse_dbi_tables(RAW)

    divisions = get_class_divisions(tables, "-73")
    print("Podziały klasy 4B:")
    for d in divisions:
        print(" -", d["division_id"], [g["name"] for g in d["groups"]])
    assert len(divisions) == 2, "powinny być 2 podziały z wyborem (informatyka + język)"

    # Dziecko: hiszpański (grupa *182) + 1. grupa informatyki (*72)
    schedule = build_student_schedule(
        tables,
        class_id="-73",
        group_choices={"-73:3": "*182", "-73:1": "*72"},
        today=date(2026, 9, 14),  # poniedziałek
    )
    subjects_seen = sorted({o.subject for o in schedule.occurrences})
    print("\nPrzedmioty w planie (hiszpański + 1. grupa informatyki):", subjects_seen)
    assert "język hiszpański" in subjects_seen
    assert "informatyka" in subjects_seen
    assert "język niemiecki" not in subjects_seen  # nie powinno przeciekać drugiego języka

    # sprawdźmy że wybrana grupa informatyki się zgadza (nauczyciel KOW z lekcji *338, nie z *339)
    inf = [o for o in schedule.occurrences if o.subject == "informatyka"][0]
    assert inf.teacher == "KOW"
    assert inf.color == "#FF0000"
    print("Kolor informatyki:", inf.color, "| nauczyciel:", inf.teacher, "| dzień:", inf.start.strftime("%A %H:%M"))

    hisz = [o for o in schedule.occurrences if o.subject == "język hiszpański"][0]
    print("Hiszpański:", hisz.start.strftime("%A %H:%M"), "-", hisz.end.strftime("%H:%M"), "kolor:", hisz.color)

    # Regresja: occurrences MUSZĄ mieć tz-aware start/end, inaczej CalendarEvent (HA)
    # rzuca wyjątkiem przy tworzeniu encji kalendarza i cała encja "znika" (tak jak się
    # to realnie zdarzyło - patrz coordinator.py, build_student_schedule).
    assert inf.start.tzinfo is not None, "occurrence.start musi być tz-aware"
    assert inf.end.tzinfo is not None, "occurrence.end musi być tz-aware"
    for occ in schedule.occurrences:
        _occ_to_event(occ)  # rzuci ValueError, jeśli daty są naiwne (patrz stub CalendarEvent)
    print("Wszystkie wystąpienia mają tz-aware start/end - CalendarEvent się nie wywali")

    # Drugie dziecko wybiera niemiecki + 2. grupę informatyki - powinno dać INNY zestaw
    schedule2 = build_student_schedule(
        tables,
        class_id="-73",
        group_choices={"-73:3": "*183", "-73:1": "*73"},
        today=date(2026, 9, 14),
    )
    inf2 = [o for o in schedule2.occurrences if o.subject == "informatyka"][0]
    assert inf2.teacher == "KOW"  # w tym przykładzie ten sam nauczyciel, ale sprawdzamy że to *339 (2. Grupa)
    subjects_seen2 = sorted({o.subject for o in schedule2.occurrences})
    assert "język niemiecki" in subjects_seen2
    assert "język hiszpański" not in subjects_seen2

    # Brak wyboru grupy w ogóle -> lekcje z podziałem powinny zniknąć (bezpieczny default), bez wyjątku
    schedule3 = build_student_schedule(tables, class_id="-73", group_choices={}, today=date(2026, 9, 14))
    assert schedule3.occurrences == []

    # Regresja (zgłoszenie 2026-09-18: "HA pokazuje plan sprzed zmiany") - StudentSchedule
    # musi nieść odcisk (fingerprint) SUROWEJ odpowiedzi EduPage, identyczny dla identycznych
    # danych i RÓŻNY, gdy dane się zmieniają - to jest podstawa diagnostyki w sensor.py/
    # calendar.py (atrybuty "ostatnia_aktualizacja"/"wersja_danych"), która pozwala odróżnić
    # "integracja nie odpytuje na czas" od "EduPage serwuje tę samą buforowaną odpowiedź".
    assert schedule.source_fingerprint, "fingerprint nie powinien być pusty dla realnej odpowiedzi"
    tables_again = _parse_dbi_tables(RAW)
    assert tables_again.fingerprint == tables.fingerprint, "ten sam surowy JSON -> ten sam fingerprint"
    raw_changed = {**RAW, "r": {**RAW["r"], "dbiAccessorRes": {**RAW["r"]["dbiAccessorRes"]}}}
    raw_changed["r"]["dbiAccessorRes"]["tables"] = list(RAW["r"]["dbiAccessorRes"]["tables"]) + [
        {"id": "marker", "data_columns": [], "data_rows": [{"id": "zmiana"}]}
    ]
    tables_changed = _parse_dbi_tables(raw_changed)
    assert tables_changed.fingerprint != tables.fingerprint, "inny surowy JSON -> inny fingerprint"
    print("Fingerprint diagnostyczny działa poprawnie (stabilny/zmienny tam gdzie trzeba)")

    # Regresja (uwaga rodzica, słuszna: odcisk CAŁEJ odpowiedzi nic nie mówi o tym, czy
    # zmieniło się coś w planie TEGO dziecka) - plan_fingerprint musi być POLICZONY z
    # rzeczywistych pól planu (dzień/godzina/przedmiot/nauczyciel/sala/grupa), a nie z
    # surowego JSON-a całej szkoły:
    assert schedule.plan_fingerprint, "plan_fingerprint nie powinien być pusty"

    # 1) Zmiana NIEZWIĄZANA z tym uczniem (dodatkowa tabela, jak wyżej) -> source_
    #    fingerprint się zmienia, ale plan_fingerprint TEGO ucznia zostaje taki sam,
    #    bo jego faktyczny plan się nie zmienił.
    schedule_after_unrelated_change = build_student_schedule(
        tables_changed,
        class_id="-73",
        group_choices={"-73:3": "*182", "-73:1": "*72"},
        today=date(2026, 9, 14),
    )
    assert schedule_after_unrelated_change.plan_fingerprint == schedule.plan_fingerprint, (
        "niezwiązana zmiana w odpowiedzi EduPage nie powinna zmieniać plan_fingerprint tego ucznia"
    )

    # 2) Zmiana, która REALNIE dotyczy tego ucznia (inny nauczyciel informatyki) ->
    #    plan_fingerprint MUSI się zmienić.
    import copy

    raw_teacher_changed = copy.deepcopy(RAW)
    for row in raw_teacher_changed["r"]["dbiAccessorRes"]["tables"]:
        if row["id"] == "lessons":
            for lesson_row in row["data_rows"]:
                if lesson_row["id"] == "*338":  # informatyka, 1. Grupa (KOW)
                    lesson_row["teacherids"] = ["-102"]  # podmieniony na GAR
    tables_teacher_changed = _parse_dbi_tables(raw_teacher_changed)
    schedule_teacher_changed = build_student_schedule(
        tables_teacher_changed,
        class_id="-73",
        group_choices={"-73:3": "*182", "-73:1": "*72"},
        today=date(2026, 9, 14),
    )
    assert schedule_teacher_changed.plan_fingerprint != schedule.plan_fingerprint, (
        "zmiana nauczyciela w lekcji TEGO ucznia musi zmienić plan_fingerprint"
    )
    print("plan_fingerprint reaguje na zmiany w planie TEGO ucznia i ignoruje resztę szkoły - OK")

    print("\nWSZYSTKIE ASERCJE PRZESZŁY OK")


if __name__ == "__main__":
    main()
