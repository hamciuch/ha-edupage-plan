"""Test siatki tygodnia (sensor.py: build_week_grid) - bez Home Assistant.

Sprawdza, że:
  - lekcje o tej samej godzinie w różne dni trafiają w JEDEN wspólny wiersz,
  - dziury (brak lekcji danego dnia w danym wierszu) to po prostu None,
  - w weekend siatka pokazuje od razu NASTĘPNY tydzień szkolny (nie miniony/aktualny),
  - legenda zawiera po jednym wpisie na przedmiot (bez duplikatów), posortowaną wg skrótu.
"""
import sys
from datetime import date

sys.path.insert(0, "custom_components")

from edupage_plan.api import _parse_dbi_tables  # noqa: E402
from edupage_plan.coordinator import build_student_schedule  # noqa: E402
from edupage_plan.sensor import build_week_grid  # noqa: E402

# Ta sama godzina (8:00-8:45) w poniedziałek i wtorek, plus jedna lekcja w środę o innej
# porze - żeby sprawdzić grupowanie wierszy PO GODZINIE, nie po kolejności w danych.
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
                    ],
                },
                {
                    "id": "classes",
                    "data_columns": ["name", "short"],
                    "data_rows": [{"id": "-73", "name": "2A", "short": "2A"}],
                },
                {
                    "id": "subjects",
                    "data_columns": ["name", "short", "color"],
                    "data_rows": [
                        {"id": "-1", "name": "edukacja wczesnoszkolna", "short": "ew", "color": "#66CCFF"},
                        {"id": "-2", "name": "język angielski", "short": "ang", "color": "#66CC66"},
                    ],
                },
                {"id": "teachers", "data_columns": ["short"], "data_rows": [{"id": "-1", "short": "BUR"}]},
                {
                    "id": "classrooms",
                    "data_columns": ["name", "short"],
                    "data_rows": [{"id": "12", "name": "sala 12", "short": "12"}],
                },
                {"id": "groups", "data_columns": ["name", "classid", "entireclass", "ascttdivision", "divisionid"], "data_rows": []},
                {"id": "divisions", "data_columns": ["classid", "ascttdivision", "groupids"], "data_rows": []},
                {
                    "id": "lessons",
                    "data_columns": ["subjectid", "teacherids", "groupids", "classids", "classdata"],
                    "data_rows": [
                        {
                            "id": "*1", "subjectid": "-1", "teacherids": ["-1"], "groupids": [],
                            "classids": ["-73"], "classdata": {"-73": {"divisionid": "", "groups": ""}},
                        },
                        {
                            "id": "*2", "subjectid": "-2", "teacherids": ["-1"], "groupids": [],
                            "classids": ["-73"], "classdata": {"-73": {"divisionid": "", "groups": ""}},
                        },
                    ],
                },
                {
                    "id": "cards",
                    "data_columns": ["lessonid", "locked", "period", "days", "weeks", "classroomids"],
                    "data_rows": [
                        # Poniedziałek 8:00 - ew
                        {"id": "c1", "lessonid": "*1", "locked": False, "period": "1", "days": "1000000", "weeks": "1", "classroomids": ["12"]},
                        # Wtorek 8:00 - też ew (ta sama godzina co poniedziałek -> ten sam wiersz siatki)
                        {"id": "c2", "lessonid": "*1", "locked": False, "period": "1", "days": "0100000", "weeks": "1", "classroomids": ["12"]},
                        # Środa 8:55 - angielski (inna godzina -> osobny wiersz)
                        {"id": "c3", "lessonid": "*2", "locked": False, "period": "2", "days": "0010000", "weeks": "1", "classroomids": ["12"]},
                    ],
                },
            ]
        }
    }
}


def main():
    tables = _parse_dbi_tables(RAW)
    schedule = build_student_schedule(tables, class_id="-73", group_choices={}, today=date(2026, 9, 14))  # poniedziałek

    dni, siatka, legenda = build_week_grid(schedule, today=date(2026, 9, 14))

    assert [d["nazwa"] for d in dni] == ["Pn", "Wt", "Śr", "Czw", "Pi"]
    assert dni[0]["data"] == "14.09"

    assert len(siatka) == 2, f"powinny być 2 różne godziny (8:00 i 8:55), jest {len(siatka)}"
    row_800 = next(r for r in siatka if r["start"] == "08:00")
    row_855 = next(r for r in siatka if r["start"] == "08:55")

    # Poniedziałek i wtorek o 8:00 mają lekcję, reszta dni w tym wierszu jest pusta (None)
    assert row_800["dni"][0]["skrot"] == "ew"
    assert row_800["dni"][1]["skrot"] == "ew"
    assert row_800["dni"][2] is None
    assert row_800["dni"][3] is None
    assert row_800["dni"][4] is None

    # Środa 8:55 - tylko trzeci dzień (indeks 2) ma angielski
    assert row_855["dni"][2]["skrot"] == "ang"
    assert row_855["dni"][0] is None

    # Legenda: po jednym wpisie na przedmiot, posortowana wg skrótu ("ang" < "ew")
    assert [l["skrot"] for l in legenda] == ["ang", "ew"]
    assert legenda[1]["kolor"] == "#66CCFF"

    print("Siatka tygodnia (roboczy):")
    for row in siatka:
        cells = " | ".join((c["skrot"] if c else "--") for c in row["dni"])
        print(f"  {row['start']}-{row['koniec']}: {cells}")

    # W weekend siatka ma pokazać od razu NASTĘPNY tydzień (poniedziałek 21.09, nie 14.09)
    dni_weekend, siatka_weekend, _ = build_week_grid(schedule, today=date(2026, 9, 19))  # sobota
    assert dni_weekend[0]["data"] == "21.09", "w weekend powinien pokazać się już następny tydzień"

    # Brak danych (coordinator jeszcze nic nie pobrał) -> puste struktury, bez wyjątku
    assert build_week_grid(None, today=date(2026, 9, 14)) == ([], [], [])

    print("\nWSZYSTKIE ASERCJE (build_week_grid) PRZESZŁY OK")


if __name__ == "__main__":
    main()
