"""Test parsera harmonogramu obiadów na PRAWDZIWYM pliku szkoły
(tests/fixtures/harmonogram_obiadow_realny.docx - dostarczony przez rodzica).

Prawdziwy plik nie jest tabelą - to akapity z nagłówkami dni tygodnia i
wierszami "GG.MM – GG.MM – klasa X, Y" (patrz lunch.py). Ten test zabezpiecza
przed regresją w parsowaniu tego konkretnego, nietrywialnego formatu.
"""
import sys
from pathlib import Path

sys.path.insert(0, "custom_components")

from edupage_plan.lunch import parse_lunch_docx  # noqa: E402

FIXTURE = Path(__file__).parent / "tests" / "fixtures" / "harmonogram_obiadow_realny.docx"


def main():
    table = parse_lunch_docx(str(FIXTURE))

    assert set(table.keys()) == {0, 1, 2, 3, 4}, f"oczekiwano 5 dni (Pn-Pt), jest {sorted(table)}"

    # Wiktor - 2A
    assert table[0]["2A"] == "11:55-12:10"  # poniedziałek
    assert table[1]["2A"] == "11:55-12:10"  # wtorek
    assert table[2]["2A"] == "11:45-11:55"  # środa
    assert table[3]["2A"] == "12:10-12:30"  # czwartek
    assert table[4]["2A"] == "13:25-13:40"  # piątek

    # Gabrysia - 4B (ta klasa je o tej samej porze każdego dnia w tym harmonogramie)
    for day in range(5):
        assert table[day]["4B"] == "12:30-12:45", f"4B dzień {day}: {table[day].get('4B')}"

    # Skrótowy zapis kilku oddziałów naraz musi się rozwinąć na pojedyncze klasy.
    # W poniedziałek szkoła zapisuje to inaczej niż resztę tygodnia: "6ac,7abc,8abc"
    # o 13.40 (czyli akurat BEZ 6B - ta je wcześniej, razem z 5b/5c o 12.45), a we
    # wtorek-piątek jest to już pełne "6abc,7abc,8abc" - to nie błąd parsera, tak
    # naprawdę wygląda ten plik (sprawdzone na surowym tekście akapitów).
    assert table[0]["6A"] == "13:40-13:55"
    assert table[0]["6C"] == "13:40-13:55"
    assert table[0]["6B"] == "12:45-12:55"  # poniedziałek: 6B je razem z 5b,5c
    for cls in ("7A", "7B", "7C", "8A", "8B", "8C"):
        assert table[0][cls] == "13:40-13:55", f"{cls} poniedziałek: {table[0].get(cls)}"
    for day in (1, 2, 3, 4):
        for cls in ("6A", "6B", "6C", "7A", "7B", "7C", "8A", "8B", "8C"):
            assert table[day][cls] == "13:40-13:55", f"{cls} dzień {day}: {table[day].get(cls)}"

    print("Wiktor (2A) w tygodniu:", {d: table[d]["2A"] for d in range(5)})
    print("Gabrysia (4B) w tygodniu:", {d: table[d]["4B"] for d in range(5)})
    print("\nWSZYSTKIE ASERCJE PRZESZŁY OK")


if __name__ == "__main__":
    main()
