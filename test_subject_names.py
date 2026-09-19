"""Test: różne przedmioty o TEJ SAMEJ nazwie w EduPage (np. "ew" i "ewf" - WF w klasach 1-3)
muszą być w planie odróżnialne, a przedmioty o unikalnej nazwie zostają bez zmian.

Odtwarza piątek klasy 2a: ang, ew, ewf (sala M-SG), ew.
Uruchomienie (jak test_logic.py): python test_subject_names.py
"""
import sys

sys.path.insert(0, "custom_components")

from edupage_plan.api import _parse_dbi_tables  # noqa: E402
from edupage_plan.coordinator import build_student_schedule  # noqa: E402

EW = "edukacja wczesnoszkolna"

RAW = {
    "r": {
        "dbiAccessorRes": {
            "tables": [
                {
                    "id": "periods",
                    "data_columns": ["starttime", "endtime"],
                    "data_rows": [
                        {"id": "1", "starttime": "08:00", "endtime": "08:45"},
                        {"id": "2", "starttime": "08:55", "endtime": "09:40"},
                        {"id": "3", "starttime": "09:55", "endtime": "10:40"},
                        {"id": "4", "starttime": "10:50", "endtime": "11:35"},
                    ],
                },
                {"id": "classes", "data_columns": ["name", "short"], "data_rows": [{"id": "-1", "name": "2a", "short": "2a"}]},
                {
                    "id": "subjects",
                    "data_columns": ["name", "short", "color"],
                    "data_rows": [
                        {"id": "-1", "name": "język angielski", "short": "ang", "color": "#3366FF"},
                        {"id": "-2", "name": EW, "short": "ew", "color": "#33AA33"},
                        {"id": "-3", "name": EW, "short": "ewf", "color": "#FF8800"},
                        # przedmiot spoza planu 2a, o tej samej nazwie co "ang" - NIE może zmieniać
                        # nazwy angielskiego w planie 2a (stabilność planu / plan_fingerprint)
                        {"id": "-4", "name": "język angielski", "short": "ang-r", "color": "#000000"},
                    ],
                },
                {"id": "teachers", "data_columns": ["short"], "data_rows": [{"id": "t1", "short": "JED"}, {"id": "t2", "short": "BUR"}]},
                {
                    "id": "classrooms",
                    "data_columns": ["name", "short"],
                    "data_rows": [{"id": "r29", "name": "29", "short": "29"}, {"id": "rsg", "name": "M-SG", "short": "M-SG"}],
                },
                {"id": "groups", "data_columns": ["name", "classid", "entireclass", "ascttdivision", "divisionid"], "data_rows": []},
                {"id": "divisions", "data_columns": ["classid", "ascttdivision", "groupids"], "data_rows": []},
                {
                    "id": "lessons",
                    "data_columns": ["subjectid", "teacherids", "groupids", "classids", "classdata"],
                    "data_rows": [
                        {"id": "L1", "subjectid": "-1", "teacherids": ["t1"], "groupids": [], "classids": ["-1"], "classdata": {"-1": {"divisionid": "", "groups": ""}}},
                        {"id": "L2", "subjectid": "-2", "teacherids": ["t2"], "groupids": [], "classids": ["-1"], "classdata": {"-1": {"divisionid": "", "groups": ""}}},
                        {"id": "L3", "subjectid": "-3", "teacherids": ["t2"], "groupids": [], "classids": ["-1"], "classdata": {"-1": {"divisionid": "", "groups": ""}}},
                    ],
                },
                {
                    "id": "cards",
                    "data_columns": ["lessonid", "locked", "period", "days", "weeks", "classroomids"],
                    "data_rows": [
                        {"id": "c1", "lessonid": "L1", "locked": False, "period": "1", "days": "00001", "weeks": "1", "classroomids": ["r29"]},
                        {"id": "c2", "lessonid": "L2", "locked": False, "period": "2", "days": "00001", "weeks": "1", "classroomids": ["r29"]},
                        {"id": "c3", "lessonid": "L3", "locked": False, "period": "3", "days": "00001", "weeks": "1", "classroomids": ["rsg"]},
                        {"id": "c4", "lessonid": "L2", "locked": False, "period": "4", "days": "00001", "weeks": "1", "classroomids": ["r29"]},
                    ],
                },
            ]
        }
    }
}


def main():
    tables = _parse_dbi_tables(RAW)
    schedule = build_student_schedule(tables, class_id="-1", group_choices={})
    friday = [o for o in schedule.occurrences if o.start.weekday() == 4]
    by_start = {}
    for o in friday:
        by_start.setdefault(o.start.strftime("%H:%M"), o)

    got = [(t, by_start[t].subject, by_start[t].subject_short, by_start[t].room) for t in sorted(by_start)]
    for row in got:
        print(" ", row)

    assert by_start["08:00"].subject == "język angielski", "unikalna nazwa nie może dostać sufiksu"
    assert by_start["08:55"].subject == f"{EW} (ew)"
    assert by_start["09:55"].subject == f"{EW} (ewf)", "3. lekcja (WF, sala M-SG) musi się różnić od zwykłej ew"
    assert by_start["09:55"].room == "M-SG"
    assert by_start["10:50"].subject == f"{EW} (ew)"
    assert by_start["09:55"].subject_short == "ewf"

    # różne przedmioty => różne kolory (wcześniej kolory kluczowane nazwą się nadpisywały)
    assert by_start["08:55"].color == "#33AA33"
    assert by_start["09:55"].color == "#FF8800"

    # kolor po starej, pełnej nazwie nadal działa (kompatybilność wsteczna), o ile nazwa jest unikalna
    s2 = build_student_schedule(tables, class_id="-1", group_choices={}, color_overrides={"język angielski": "#123456"})
    assert [o for o in s2.occurrences if o.subject == "język angielski"][0].color == "#123456"

    print("\nOK: WF (ewf) odróżnione od ew, unikalne nazwy bez zmian")


if __name__ == "__main__":
    main()
