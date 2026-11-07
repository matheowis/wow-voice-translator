# WoW Voice Translator

Głosowy tłumacz czatu do **World of Warcraft Forever**: mówisz po polsku, w czacie pojawia się tekst po angielsku.

- Przytrzymujesz klawisz i mówisz po polsku → w polu czatu WoW pojawia się tekst po angielsku. Wysyłasz go sam Enterem.
- Najeżdżasz myszą na angielską wiadomość w czacie → widzisz ją po polsku.
- Rozumie slang z czatu WoW (mb, wc, ty, LFM, WTS…).
- Rozpoznawanie mowy i tłumaczenie działają na Twoim komputerze: bez internetu, kont i opłat.
- Opcjonalnie: **głosy postaci**. Wiadomości z `/say` i `/party` czytane głosem pasującym do rasy i płci postaci (ElevenLabs, płatne). Dźwięk z `/say` jest cichszy, im dalej stoi postać.

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
| **Esc** w trakcie mówienia | anuluje |
| **Enter** | wysyła tekst (aplikacja nigdy nie wysyła sama) |

Najedź myszą na wiadomość w czacie i potrzymaj chwilę – nad nią pojawi się tłumaczenie na polski.

Klawisze, mikrofon i inne ustawienia zmienisz w oknie aplikacji (dwuklik ikony przy zegarku).

## Głosy postaci (opcjonalne, ElevenLabs)

1. Skopiuj plik `.env.example` jako `.env` i wpisz w nim swój klucz: `ELEVENLABS_API_KEY=...`. Plik `.env` nigdy nie trafia do repozytorium.
2. Raz utwórz głosy (100 głosów w Twoim koncie ElevenLabs: 9 ras WoW Forever, w tym Skyborne z jedną pulą na obie odmiany, plus pula dla nieznanej rasy; po 5 na każdą płeć):

   ```
   .venv\Scripts\python -m wvt.voice.design
   ```

3. W grze nad czatem jest panel **WVT: Say | Party | Glos**:
   - **Say / Party** włączają czytanie danego kanału. Domyślnie są wyłączone, więc nic nie kosztują.
   - **Glos EN**: słyszysz oryginał po angielsku, a Twoja postać powtarza Twoją wiadomość swoim głosem.
   - **Glos PL**: słyszysz innych po polsku (tłumaczenie lokalne). Czat zostaje po angielsku.

   To samo robią komendy: `/wvt say`, `/wvt party`, `/wvt lang pl`. Panel przesuniesz, przeciągając go z wciśniętym Shift.

Przypisania głosów do postaci zapisują się w lokalnym MongoDB, jeśli jest zainstalowane, a jeśli nie, w folderze `data`. Dzienny limit znaków i głośność ustawisz w zakładce **Głosy** w oknie aplikacji.

## Coś nie działa?

- **Nie widać tłumaczeń w grze** – ustaw w WoW *Windowed (Fullscreen)* zamiast *Fullscreen*.
- **Instalator nie znalazł WoW** – podaj folder gry (ten, w którym jest `_forever_`):

  ```
  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -Wow "D:\Gry\World of Warcraft"
  ```

- **Nie rozpoznaje mowy** – w oknie aplikacji: *Ustawienia → Mikrofon*, wybierz właściwy i zrób test.
- **Długo się ładuje** – modele są duże, a z wolnego dysku (np. na USB 2.0) start trwa kilka minut. Zostaw aplikację włączoną w tle.
- **Głosy milczą** – sprawdź zakładkę *Głosy* (klucz, liczba głosów, limit dzienny) i czy w grze włączony jest Say/Party. Po aktualizacji addonu wpisz w grze `/reload`.
- **Diagnostyka**: `.venv\Scripts\python -m wvt.cli check`; logi są w folderze `logs`.

## Bezpieczeństwo

Aplikacja nie czyta pamięci gry, niczego do niej nie wstrzykuje i nigdy nie wysyła wiadomości za Ciebie – tylko wkleja tekst do pola czatu. Nagrania nie są zapisywane. Głosy postaci wysyłają do ElevenLabs tylko tekst wiadomości z włączonych kanałów. Treść rozmów nie jest zapisywana; w bazie są tylko przypisania „postać → głos”. Mimo to używasz aplikacji na własną odpowiedzialność: sprawdź zasady Blizzarda dotyczące programów zewnętrznych.

## Więcej

Jak to działa, pomiary, wybór modeli i konfiguracja: [docs/SZCZEGOLY.md](docs/SZCZEGOLY.md).

Licencja kodu: MIT (plik [LICENSE](LICENSE)). Modele pobiera instalator (nie ma ich w repozytorium) i mają własne licencje: NVIDIA Parakeet TDT 0.6B v3 (CC‑BY‑4.0), NVIDIA Nemotron 3.5 Bolt E4B (OpenMDW‑1.1), llama.cpp (MIT). Głosy ElevenLabs podlegają warunkom Twojego planu ElevenLabs.
