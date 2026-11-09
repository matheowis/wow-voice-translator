# WoW Voice Translator

Głosowy tłumacz czatu do **World of Warcraft Forever**: mówisz po polsku, w czacie pojawia się tekst po angielsku.

- Przytrzymujesz klawisz i mówisz po polsku → w polu czatu WoW pojawia się tekst po angielsku. Wysyłasz go sam Enterem.
- Najeżdżasz myszą na angielską wiadomość w czacie → widzisz ją po polsku.
- Rozumie slang z czatu WoW (mb, wc, ty, LFM, WTS…).
- Rozpoznawanie mowy i tłumaczenie działają na Twoim komputerze: bez internetu, kont i opłat.
- Opcjonalnie: **głosy postaci**. Wiadomości z `/say` i `/party` czytane głosem pasującym do rasy i płci postaci (ElevenLabs, płatne). Dźwięk z `/say` jest cichszy, im dalej stoi postać.
- **Asystentka głosowa**: na osobnym klawiszu mówisz do niej po polsku („zaproś tych, co piszą inv”, „gdzie mam się teraz udać?”, „gdzie jest trener magów?”, „przypomnij mi za 10 minut…”), a ona odpowiada głosem, stawia znaczniki na mapie i przygotowuje komendy w czacie. Przy losowaniu łupu sama mówi, czy brać need, czy greed. Na pytania o grę odpowiada z lokalnej bazy wiedzy (Warcraft Wiki i baza questów WoW Forever).

## Czego potrzebujesz

- Windows 10 lub 11.
- Karta graficzna NVIDIA RTX z aktualnym sterownikiem (testowane na RTX 5080 16 GB).
- [Python 3.12](https://www.python.org/downloads/) – w instalatorze zostaw zaznaczone *py launcher*.
- Ok. 12 GB wolnego miejsca na dysku.
- Mikrofon.

## Instalacja (raz)

1. Pobierz projekt: zielony przycisk **Code → Download ZIP** i rozpakuj go, np. do `C:\WoWVoiceTranslator`.
2. Otwórz rozpakowany folder, kliknij w pasek adresu Eksploratora, wpisz `powershell` i naciśnij Enter.
3. W oknie, które się otworzy, wklej i zatwierdź Enterem:

   ```
   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
   ```

   Skrypt sam zainstaluje biblioteki, pobierze modele (ok. 8 GB, to chwilę potrwa) i doda addon do WoW.

## Uruchamianie

1. Kliknij dwa razy `scripts\run.bat`. Przy zegarku pojawi się ikona aplikacji. Modele ładują się 1–2 minuty.
2. Włącz WoW i w **Options → Graphics** ustaw **Display Mode: Windowed (Fullscreen)**.
3. Na ekranie wyboru postaci kliknij **AddOns** i zaznacz **WoW Voice Translator**.
4. W grze wpisz `/wvt test`. Na środku ekranu powinien pojawić się napis „✓ połączenie z addonem działa”.

## Jak używać

| Klawisz | Co robi |
|---|---|
| przytrzymaj **`** (tylda, pod Esc) | mówisz → tekst po angielsku na **/say** |
| przytrzymaj **Caps Lock** | mówisz → tekst po angielsku na **/party** |
| przytrzymaj **boczny przycisk myszy 5** | rozmowa z asystentką (niżej) |
| **Esc** w trakcie mówienia | anuluje |
| **Enter** | wysyła tekst (aplikacja nigdy nie wysyła sama) |

Najedź myszą na wiadomość w czacie i potrzymaj chwilę – nad nią pojawi się tłumaczenie na polski.

Klawisze, mikrofon i inne ustawienia zmienisz w oknie aplikacji (dwuklik ikony przy zegarku).

## Głosy postaci (opcjonalne, ElevenLabs)

1. Skopiuj plik `.env.example` jako `.env` i wpisz w nim swój klucz: `ELEVENLABS_API_KEY=...`. Plik `.env` nigdy nie trafia do repozytorium.
2. Raz utwórz głosy (90 głosów w Twoim koncie ElevenLabs: 9 ras WoW Forever, w tym Skyborne z jedną pulą na obie odmiany, po 5 na każdą płeć):

   ```
   .venv\Scripts\python -m wvt.voice.design
   ```

3. W grze nad czatem jest panel **WVT: Say | Party | Glos**:
   - **Say / Party** włączają czytanie danego kanału. Domyślnie są wyłączone, więc nic nie kosztują.
   - **Glos EN**: słyszysz oryginał po angielsku, a Twoja postać powtarza Twoją wiadomość swoim głosem.
   - **Glos PL**: słyszysz innych po polsku (tłumaczenie lokalne). Czat zostaje po angielsku.

   To samo robią komendy: `/wvt say`, `/wvt party`, `/wvt lang pl`. Panel przesuniesz, przeciągając go z wciśniętym Shift.

Postać, której rasy gra nie poda, mówi głosem człowieka swojej płci, a gdy rasa się wyjaśni, raz przechodzi na głos swojej rasy.

Przypisania głosów do postaci zapisują się w lokalnym MongoDB, jeśli jest zainstalowane, a jeśli nie, w folderze `data`. Dzienny limit znaków i głośność ustawisz w zakładce **Głosy** w oknie aplikacji.

## Języki – kto mówi i pisze w jakim języku

| Co | Język | Jak zmienić |
|---|---|---|
| To, co wysyłasz na czat (` i Caps Lock) | zawsze **angielski** – mówisz po polsku, aplikacja tłumaczy | – |
| Tłumaczenie po najechaniu myszą na czat | **polski** | `/wvt on` / `/wvt off` |
| Głosy innych graczy (/say, /party) | **EN** – oryginał po angielsku albo **PL** – tłumaczenie na polski | przycisk **Glos EN/PL** na panelu nad czatem, `/wvt lang pl` / `/wvt lang en` albo powiedz asystentce „przełącz na polski” |
| Twoja postać (Twoje wiadomości) | w trybie EN powtarza Twoją wiadomość po angielsku swoim głosem, w trybie PL milczy | jak wyżej |
| Asystentka | zawsze **polski** | – |

Czytanie /say i /party włączasz osobno (**Say** / **Party** na panelu, `/wvt say on`, `/wvt party on`); wyłączone kanały nic nie kosztują.

**Bezpiecznik /say** (np. w mieście): zawsze najpierw czytana jest osoba najbliżej Ciebie. Gdy w ciągu 15 s pisze na /say 4 lub więcej osób (albo przychodzi 6 linii), czytani są tylko gracze w promieniu 10 jardów, najwyżej 6 linii na minutę. Gracz, który wysyła więcej niż 3 linie w 20 s, jest na 30 s wyciszany. Pominięte linie nic nie kosztują. Zakładka **Głosy** pokazuje je z powodem („tłok”, „zalew”), a progi zmienisz w `config.json` (`voice.say_valve`).

## Asystentka (Iskra)

Przytrzymaj **boczny przycisk myszy 5** i powiedz po polsku, czego chcesz. Odpowie Ci głosem (ElevenLabs), a jeśli trzeba coś zrobić w grze, wpisze gotową komendę do czatu, np. `/wvt inv Ann Bob Cid`. **Wykonuje się dopiero po Twoim Enterze** (Esc anuluje).

| Powiedz na przykład | Co się stanie |
|---|---|
| „zaproś ich”, „wyzapraszaj tych, co piszą inv” | zaprasza wszystkich, którzy w ostatnich 5 minutach pisali „inv me / inv pls” na say/yell albo w szepcie, ile zmieści się w party |
| „zaproś Artasa”, „wyrzuć Bolka”, „daj lidera Jainie” | dobiera imię z czatu i grupy (rozumie odmianę: Artasa → Arthas) |
| „zrób raid”, „opuść grupę”, „ready check” | komendy grupy (w walce i podczas bossa gra ich nie pozwala – powie Ci to) |
| „kto chce inv?”, „co mi napisał?”, „co oni piszą?” | czyta po polsku szepty i streszcza czat |
| „odpisz mu, że zaraz będę”, „napisz na party, że idę po wodę” | tłumaczy na angielski i wpisuje do czatu (`/w`, `/p`, `/s`…) |
| „ile mam złota?”, „ile do levelu?”, „gdzie jestem?”, „ile miejsca w torbach?”, „kto jest w party?” | odpowiada z danych gry |
| „co mam do zrobienia?”, „ile mi zostało świec?”, „gdzie mam iść?”, „co mogę oddać?”, „który quest najbliżej?”, „o co chodzi w tym queście?” | odpowiada z Twojego dziennika zadań; kierunek i odległość liczy do znacznika celu, który gra pokazuje na mapie |
| „wycisz głosy”, „przełącz na polski”, „wyłącz głosy na say”, „cicho” | steruje głosami postaci |
| „gdzie mam się teraz udać?”, „co teraz robić?”, „gdzie expić na moim poziomie?” | najpierw questy do oddania, potem najbliższy quest z dziennika, potem nowe questy w pobliżu (pasujące do Twojego poziomu, klasy, frakcji i ukończonych questów), a gdy nic nie ma obok – strefa na Twój poziom i jej główny questodawca. Enter stawia znacznik na mapie |
| „gdzie jest trener magów / karczmarz / mistrz lotów / bank?”, „gdzie jest Hogger?”, „gdzie jest wejście do Deadmines?” | najbliższy na Twojej mapie (kierunek i odległość), a gdy go tu nie ma – w stolicy Twojej frakcji. Enter stawia znacznik |
| „zaprowadź mnie do…”, „zaznacz to na mapie”, „usuń znacznik” | znacznik na mapie (`/wvt way`) albo strzałka gry do questa z dziennika (`/wvt track`); „to” = ostatnie miejsce, o którym mówiła |
| „jaki dungeon na mój poziom?” | dungeony pasujące do poziomu (poziomy z Warcraft Wiki, także nowe z WoW Forever) i znacznik przy wejściu do pierwszego |
| „przypomnij mi za 10 minut / o 21:30 / na 20 poziomie, że…”, „jakie mam przypomnienia?”, „usuń przypomnienia” | przypomina głosem we właściwej chwili; przypomnienia przetrwają restart aplikacji |
| „przypomnij mi w Ironforge / jak będę w Orgrimmarze / w mieście / przy aukcji, żeby…” | przypomina, gdy wejdziesz w to miejsce („przy aukcji” = każde miasto z domem aukcyjnym) |
| „zanotuj, że chcę wystawić miecz na AH”, „jakie mam notatki?”, „usuń notatkę o mieczu”, „zrobione” | notatki zostają, aż je odhaczysz; notatka o AH, sprzedaży albo konkretnym mieście przypomina się, gdy tam wejdziesz (najwyżej co pół godziny) |
| *(sama, gdy weźmiesz questa)*, „czy warto robić ten quest?”, „co dalej w tym łańcuchu?” | mówi, czy warto: ile daje expa (część poziomu), który to quest w łańcuchu, ile expa do końca łańcucha, nagroda na końcu (czy możesz ją założyć), dungeon po drodze, elity – albo szczerze, że szkoda czasu |
| *(sama, przy zdarzeniach)* | wejście do nowej strefy (poziomy questów, czy pasuje, kto daje najwięcej questów, dungeon w strefie, krótko z wiki), nowy poziom (nowe umiejętności u trenera), pełne torby |
| „ile expa robię na godzinę?”, „kiedy wbiję poziom?”, „zresetuj statystyki” | tempo tej sesji: doświadczenie i złoto na godzinę, czas do następnego poziomu (przerwy nie liczą się do czasu) |
| *(samo, gdy w grupie zaczyna się losowanie łupu)*, „brać need czy greed?” | porównuje przedmiot z tym, co nosisz, dla Twojej klasy i poziomu: ulepszenie – need, przedmiot nie dla Ciebie albo słabszy – greed |
| pytania o grę: „jak pokonać VanCleefa?”, „co to są Skyborne?”, „czego uczy trener maga na 20 poziomie?” | odpowiedź z lokalnej bazy wiedzy (niżej), dwa zdania |
| „co umiesz?” albo cokolwiek innego | krótka rozmowa (lokalny model – w faktach o grze może się mylić) |

**Charakter.** Iskra to bystra, zadziorna kumpela od grania: ma własne zdanie, droczy się, mówi wprost, kiedy coś jest stratą czasu, i używa gamingowego slangu. Swoje komentarze (quest, strefa, poziom) układa sama z faktów: lokalny model dostaje tylko fakty z bazy, a jeśli doda liczbę spoza faktów, zamiast jego wersji pada zwykła, sprawdzona odpowiedź. Każdy rodzaj komentarzy wyłączysz w `config.json`: `assistant.quest_tips`, `zone_tips`, `level_tips`, `bag_tips`. `assistant.flavor: false` daje stałe, rzeczowe odpowiedzi bez jej komentarzy.

Jej głos wybierasz na stronie ocen głosów (sekcja *Asystentka*, daj 5 temu, który chcesz). Imię, klawisz i charakter zmienisz w `config.json` (sekcja `assistant`, np. `"persona_extra": "mów do mnie Mati"`) i w oknie aplikacji (*Ustawienia → Push-to-talk*, kanał `assistant`). W grze `/wvt asystent off` przestaje przekazywać jej czat i stan postaci.

### Baza wiedzy asystentki (jednorazowo, ok. 20 minut)

Żeby Iskra znała WoW Forever (questy, NPC, dungeony, klasy, nowe strefy), zbuduj lokalną bazę wiedzy:

```
.venv\Scripts\python scripts\download.py --embed-model
.venv\Scripts\python -m wvt.kb.build
```

- Pierwsze budowanie pobiera ok. 600 stron z Warcraft Wiki (jedna strona na sekundę, ok. 12 minut) i dane questów WoW Forever z QuestieDB (13 MB), a potem liczy wektory na procesorze (ok. 4 minut). Wszystko trafia do `data/kb` i działa bez internetu.
- Questy są w bazie po angielsku i po polsku. Opisy questów przetłumaczył raz lokalny model, a tytuły dostały polskie objaśnienie, np. „Bounty on Murlocs (po polsku: Nagroda za murloków)”. Nazwy z gry, czyli NPC, przedmioty, miejsca i tytuły questów, zostają po angielsku. Dlatego „gdzie zebrać świece koboldów” trafia w „Kobold Candles”. Pierwsze tłumaczenie trwa ok. 40 minut, potem budowanie tłumaczy już tylko nowe questy (`--no-translate` pomija ten krok).
- **Swoje poradniki** wrzuć jako pliki `.md` albo `.txt` do `data/kb/guides` i przebuduj bez pobierania: `.venv\Scripts\python -m wvt.kb.build --offline`. Twoje notatki mają pierwszeństwo przed wiki.
- Sprawdzenie bez gry: `.venv\Scripts\python -m wvt.kb.build --search "jak pokonać VanCleefa"`.
- Bez bazy asystentka działa jak dotąd, tylko nie zna faktów o grze i nie doradza, dokąd iść.

## Coś nie działa?

- **Nie widać tłumaczeń w grze** – ustaw w WoW *Windowed (Fullscreen)* zamiast *Fullscreen*.
- **Instalator nie znalazł WoW** – podaj folder gry (ten, w którym jest `_forever_`):

  ```
  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -Wow "D:\Gry\World of Warcraft"
  ```

- **Nie rozpoznaje mowy** – w oknie aplikacji: *Ustawienia → Mikrofon*, wybierz właściwy i zrób test.
- **Cichy mikrofon** – aplikacja sama wzmacnia cichą mowę (do +30 dB) przed rozpoznawaniem; szum nie jest przy tym wzmacniany ponad próg ciszy. Możesz dodać stałe wzmocnienie: *Ustawienia → Mikrofon → Wzmocnienie mikrofonu*. Test mikrofonu pokazuje poziom mowy w dBFS: dobrze, gdy jest między −30 a −15. Najlepiej podnieść poziom u źródła: w ustawieniach dźwięku Windows (*Nagrywanie → Poziomy*) albo w aplikacji producenta (np. RØDE Central).
- **Długo się ładuje** – modele są duże, a z wolnego dysku (np. na USB 2.0) start trwa kilka minut. Zostaw aplikację włączoną w tle.
- **Głosy milczą** – sprawdź zakładkę *Głosy* (klucz, liczba głosów, limit dzienny) i czy w grze włączony jest Say/Party. Po aktualizacji addonu wpisz w grze `/reload`.
- **Diagnostyka**: `.venv\Scripts\python -m wvt.cli check`; logi są w folderze `logs`.

## Bezpieczeństwo

Aplikacja nie czyta pamięci gry, niczego do niej nie wstrzykuje i nigdy nie wysyła wiadomości za Ciebie – tylko wkleja tekst do pola czatu. Nagrania nie są zapisywane. Głosy postaci wysyłają do ElevenLabs tylko tekst wiadomości z włączonych kanałów. Treść rozmów nie jest zapisywana; w bazie są tylko przypisania „postać → głos”. Mimo to używasz aplikacji na własną odpowiedzialność: sprawdź zasady Blizzarda dotyczące programów zewnętrznych.

## Więcej

Jak to działa, pomiary, wybór modeli i konfiguracja: [docs/SZCZEGOLY.md](docs/SZCZEGOLY.md).

Licencja kodu: MIT (plik [LICENSE](LICENSE)). Modele pobiera instalator (nie ma ich w repozytorium) i mają własne licencje: NVIDIA Parakeet TDT 0.6B v3 (CC‑BY‑4.0), NVIDIA Nemotron 3.5 Bolt E4B (OpenMDW‑1.1), multilingual-e5-small (MIT), llama.cpp (MIT). Głosy ElevenLabs podlegają warunkom Twojego planu ElevenLabs. Baza wiedzy powstaje na Twoim komputerze i nie trafia do repozytorium: teksty z Warcraft Wiki są na licencji CC BY-SA 4.0, a dane QuestieDB nie mają jednej jasnej licencji, więc służą tylko do Twojego prywatnego użytku.
