# WoW Voice Translator

Głosowy tłumacz czatu do **World of Warcraft Forever**: mówisz po polsku, w czacie pojawia się tekst po angielsku.

- Przytrzymujesz klawisz i mówisz po polsku → w polu czatu WoW pojawia się tekst po angielsku. Wysyłasz go sam Enterem.
- Najeżdżasz myszą na angielską wiadomość w czacie → widzisz ją po polsku.
- Rozumie slang z czatu WoW (mb, wc, ty, LFM, WTS…).
- Wszystko działa na Twoim komputerze: bez internetu, kont i opłat.

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

## Coś nie działa?

- **Nie widać tłumaczeń w grze** – ustaw w WoW *Windowed (Fullscreen)* zamiast *Fullscreen*.
- **Instalator nie znalazł WoW** – podaj folder gry (ten, w którym jest `_forever_`):

  ```
  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -Wow "D:\Gry\World of Warcraft"
  ```

- **Nie rozpoznaje mowy** – w oknie aplikacji: *Ustawienia → Mikrofon*, wybierz właściwy i zrób test.
- **Długo się ładuje** – modele są duże, a z wolnego dysku (np. na USB 2.0) start trwa kilka minut. Zostaw aplikację włączoną w tle.
- **Diagnostyka**: `.venv\Scripts\python -m wvt.cli check`; logi są w folderze `logs`.

## Bezpieczeństwo

Aplikacja nie czyta pamięci gry, niczego do niej nie wstrzykuje i nigdy nie wysyła wiadomości za Ciebie – tylko wkleja tekst do pola czatu. Nagrania nie są zapisywane. Mimo to używasz jej na własną odpowiedzialność: sprawdź zasady Blizzarda dotyczące programów zewnętrznych.

## Więcej

Jak to działa, pomiary, wybór modeli i konfiguracja: [docs/SZCZEGOLY.md](docs/SZCZEGOLY.md).

Licencja kodu: MIT (plik [LICENSE](LICENSE)). Modele pobiera instalator (nie ma ich w repozytorium) i mają własne licencje: NVIDIA Parakeet TDT 0.6B v3 (CC‑BY‑4.0), NVIDIA Nemotron 3.5 Bolt E4B (OpenMDW‑1.1), llama.cpp (MIT).
