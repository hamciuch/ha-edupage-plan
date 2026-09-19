# edupage_plan - plan lekcji EduPage w Home Assistant

Integracja Home Assistant, która pobiera plan lekcji **z publicznego, nie
wymagającego logowania API EduPage** (to samo, którego używa strona
"Plan lekcji" na stronie szkoły) i buduje z niego kalendarz oraz sensory
dla wybranego dziecka - z uwzględnieniem podziału na grupy (informatyka,
drugi język, WF, religia/etyka itd.), godziny obiadu i kolizji z innymi
zajęciami dodanymi ręcznie w Home Assistant.

## Jak to działa (w skrócie)

Strona `https://<subdomena>.edupage.org/timetable/` pobiera CAŁY plan
szkoły jednym zapytaniem (`POST /timetable/server/regulartt.js?__func=regularttGetData`)
- bez logowania, bo to dane publiczne całej szkoły (klasy, przedmioty,
nauczyciele, sale, podziały na grupy, lekcje, konkretne godziny). Ta
integracja robi dokładnie to samo zapytanie, a potem sama - lokalnie,
bez żadnego dodatkowego serwisu - wylicza, które konkretne lekcje
dotyczą Twojego dziecka, biorąc pod uwagę wybraną klasę i grupy.

Ponieważ dane publiczne **nie** zawierają informacji "który uczeń jest w
której grupie" (to już dane prywatne), przy dodawaniu integracji trzeba
raz wskazać, do której grupy (informatyka, niemiecki/hiszpański, WF,
religia/etyka...) należy dziecko - integracja sama wykrywa WSZYSTKIE
podziały występujące w danej klasie i pyta tylko o te, w których jest
więcej niż jedna grupa.

## Instalacja

1. Skopiuj folder `custom_components/edupage_plan` do `<config>/custom_components/`
   w Home Assistant.
2. Zrestartuj Home Assistant.
3. Ustawienia -> Urządzenia i usługi -> Dodaj integrację -> "Plan lekcji EduPage".
4. Podaj subdomenę szkoły (dla `https://promienista.edupage.org` wpisz
   `promienista`) oraz imię dziecka.
5. Wybierz klasę z listy (pobranej na żywo ze szkoły).
6. Dla każdego podziału z więcej niż jedną grupą (np. informatyka, drugi
   język) wybierz grupę dziecka.

Powtórz dla drugiego dziecka - każde dziecko to osobny wpis integracji
(nawet jeśli to ta sama szkoła/klasa).

### Później: dodatkowe zajęcia i kolizje

W opcjach integracji (Ustawienia -> Urządzenia i usługi -> Plan lekcji
EduPage -> Konfiguruj) możesz wskazać:

- **Kalendarz z dodatkowymi zajęciami** - to ZWYKŁY kalendarz Home
  Assistant (np. utworzony integracją "Kalendarz lokalny"), do którego
  Ty sam dodajesz kółka, korepetycje, judo itd. Integracja `edupage_plan`
  NIGDY nie modyfikuje ani nie usuwa niczego z tego kalendarza - tylko go
  czyta.
  - Powstanie druga encja kalendarza: **"Zajęcia dodatkowe (bez kolizji)"**
    - to ten sam kalendarz, ale bez wydarzeń, które pokrywają się w
    czasie z lekcją (wygaszone, nie usunięte z oryginału).
  - Powstanie też `binary_sensor` **"Kolizja zajęć z lekcją"** - włącza
    się, gdy coś w Twoim kalendarzu koliduje z lekcją, z listą kolizji
    w atrybutach (można na tym oprzeć automatyzację/powiadomienie).
  - Sam plan lekcji ze szkoły (`calendar.plan_lekcji_...`) jest tylko do
    odczytu - nie da się go przypadkiem skasować ani zmienić przez UI/
    serwis kalendarza HA.

- **Ścieżka do pliku .docx z harmonogramem obiadów** - szkoła publikuje
  ten plik ręcznie (link "Stołówka" na stronie EduPage), więc nie da się
  go pobrać automatycznie z tej integracji. Pobierz plik i zapisz go np.
  jako `/config/edupage_plan/obiady.docx`, a potem wpisz tę ścieżkę w
  opcjach. Integracja sama odczytuje plik lokalnie (bez łączenia się z
  edupage.org) i wystawia sensor **"Godzina obiadu"** dla klasy dziecka.
  Parser jest ogólny (szuka w tabelach par "klasa" + "godzina") - jeśli
  akurat ten plik ma nietypowy układ, daj znać, dostosuję parser.

## Co powstaje (encje)

Dla każdego dziecka:

| Encja | Opis |
|---|---|
| `calendar.plan_lekcji_<dziecko>` | Plan lekcji ze szkoły, tylko do odczytu |
| `sensor.aktualna_lekcja_<dziecko>` | Trwająca teraz lekcja (przedmiot, nauczyciel, sala, kolor, grupa) |
| `sensor.nastepna_lekcja_<dziecko>` | Kolejna lekcja |
| `sensor.godzina_obiadu_<dziecko>` | Godzina obiadu (jeśli skonfigurowano plik) |
| `sensor.plan_tygodnia_<dziecko>` | Cały tydzień szkolny jako gotowa "siatka" (atrybuty `dni`/`siatka`/`legenda`) - do narysowania widoku w stylu papierowego planu lekcji, patrz niżej |
| `calendar.zajecia_dodatkowe_bez_kolizji_<dziecko>` | Tylko jeśli skonfigurowano kalendarz dodatkowych zajęć |
| `binary_sensor.kolizja_zajec_z_lekcja_<dziecko>` | Tylko jeśli skonfigurowano kalendarz dodatkowych zajęć |

## Widok "papierowego" planu tygodnia

Oprócz kalendarza HA (widok tygodnia/miesiąca), dashboard (`plan_lekcji_dashboard_raw.yaml`)
zawiera drugi widok - "Plan tygodnia" - wzorowany na kolorowym plakacie planu lekcji,
jaki drukuje szkoła: siatka dni x godzin, kolorowe pola per przedmiot, sala i
nauczyciel w komórce, legenda kolorów na dole. Budowany jest kartą `markdown` +
szablon Jinja czytający atrybuty `dni`/`siatka`/`legenda` z `sensor.plan_tygodnia_<dziecko>`
(pogrupowane RAZ, po stronie integracji - patrz `sensor.py: build_week_grid` -
bo dopasowanie "ta sama godzina w różnych dniach = jeden wiersz" jest niewygodne
do policzenia samym szablonem). Nie wymaga żadnej dodatkowej konfiguracji ani
custom-karty z HACS Frontend.

## Kolory przedmiotów

Każdy przedmiot ma kolor zdefiniowany PRZEZ SZKOŁĘ w aSc Plan Lekcji
(dokładnie ten sam, którego szkoła używa na wydrukach planu) - integracja
odczytuje go z API i wystawia jako atrybut `kolor` na sensorach oraz
(pośrednio, do wykorzystania w karcie) w `coordinator.data.subject_colors`.
Przykładowa karta Lovelace (lista `entities`/`calendar` + kolorowanie po
atrybucie) - najprościej zrobić to kartą `custom:calendar-card-pro` albo
własnym markdown-em z szablonem Jinja korzystającym z atrybutu `kolor`
sensorów aktualnej/następnej lekcji, np.:

```yaml
type: markdown
content: >
  <span style="color:{{ state_attr('sensor.aktualna_lekcja_wiktor','kolor') }}">
  {{ states('sensor.aktualna_lekcja_wiktor') }}</span>
```

## Aktualizacja planu przez szkołę

Integracja za każdym odświeżeniem (domyślnie co 3h) pyta EduPage o
AKTUALNIE obowiązujący plan (nie trzyma sztywno starego numeru planu),
więc jeśli szkoła opublikuje nowy plan (np. od nowego semestru albo po
zmianie), zostanie on podchwycony automatycznie przy najbliższym
odświeżeniu - nie trzeba nic przekonfigurowywać.

### Diagnostyka: "plan pokazuje coś innego niż strona szkoły"

Każda encja (kalendarz, sensory) ma trzy dodatkowe atrybuty (Developer Tools
-> States), które od razu mówią, CZY i KIEDY integracja faktycznie ostatnio
pobrała dane - bez grzebania w logu:

- `ostatnia_aktualizacja` - kiedy integracja ostatnio SKUTECZNIE pobrała plan
  z EduPage. Jeśli to jest sprzed wielu godzin mimo że powinno odświeżać się
  co 3h - odświeżanie faktycznie stoi (sprawdź log pod kątem błędów,
  Ustawienia -> System -> Logi).
- `wersja_planu` - odcisk POLICZONY z dokładnie tych pól, z których składa
  się plan TEGO dziecka: dzień tygodnia, godzina, przedmiot, nauczyciel,
  sala, grupa - czyli dokładnie to, co widać "na oko" na planie i co
  porównałbyś ręcznie ze stroną szkoły. **To jest właściwy atrybut do
  sprawdzenia, czy Twój plan jest aktualny** - jeśli ten odcisk NIE zmienia
  się mimo potwierdzonej zmiany na stronie szkoły (dla Twojego dziecka), to
  realny problem po stronie integracji/EduPage.
- `wersja_odpowiedzi_api` - odcisk całej surowej odpowiedzi EduPage (dla
  WSZYSTKICH klas w szkole naraz). Może się zmieniać nawet wtedy, gdy
  `wersja_planu` Twojego dziecka zostaje taka sama - bo zmieniło się coś w
  planie innej klasy - i to jest normalne, nie błąd. Przydaje się tylko do
  odróżnienia "EduPage w ogóle nic nie zwróciło innego" (oba odciski takie
  same) od "zwróciło coś innego, ale nie dla Twojego dziecka" (tylko ten się
  zmienił).

Innymi słowy: patrz przede wszystkim na `wersja_planu` - jeśli on się nie
rusza mimo pewności, że lekcja/godzina/nauczyciel Twojego dziecka się
zmieniły na stronie szkoły, to jest sygnał do dalszego kopania (log +
`wersja_odpowiedzi_api`, żeby sprawdzić czy to w ogóle dotarło od EduPage).

Żeby zobaczyć to samo w logu (linia `EduPage (<subdomena>): pobrano plan
tt_num=... fingerprint=...` przy każdym odświeżeniu - to akurat dalej odcisk
całej odpowiedzi, nie per-dziecko), dodaj w `configuration.yaml`:

```yaml
logger:
  default: warning
  logs:
    custom_components.edupage_plan: info
```

Zanim zaczniesz to diagnozować: sprawdź też, czy karta na dashboardzie po
prostu nie pokazuje starego widoku, bo zakładka przeglądarki stoi otworem od
dłuższego czasu (twardy refresh strony, Ctrl+F5, zanim uznasz że to backend).

## Ograniczenia / rzeczy do doszlifowania

- Nie obsługuje jeszcze planów z rotacją tygodni A/B (`weeksdefid` >1) ani
  wielu semestrów w danych - w tej szkole nie występują, ale inna szkoła
  może ich używać. Zgłoś, jeśli trafisz na taki przypadek.
- Zastępstwa/zmiany na dany dzień (tabela "substitutions" w EduPage) nie
  są jeszcze uwzględnione - to tylko STAŁY plan lekcji.
- Parser pliku obiadów jest ogólny (heurystyka klasa+godzina w tabeli) -
  może wymagać dostrojenia pod konkretny plik szkoły.
