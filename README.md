# WoW Voice Translator

A voice chat translator for **World of Warcraft Forever**, made for Polish players: you speak Polish, and English text appears in the chat box.

- **Push-to-talk translation.** Hold a key and speak Polish. The English translation is pasted into the WoW chat box, and you send it yourself with Enter.
- **Hover translation.** Hover the mouse over an English chat line to see it in Polish.
- **WoW chat slang.** It understands slang such as mb, wc, ty, LFM and WTS.
- **Local and offline.** Speech recognition and translation run on your own PC and GPU, with no internet, account or fees.
- **Character voices (optional).** `/say` and `/party` lines are read aloud with a voice that fits each character's race and sex (ElevenLabs, paid). `/say` gets quieter the farther away the speaker stands.
- **Voice assistant "Iskra" (optional).** On a separate key you talk to her in Polish:
  - She invites the players who asked for an invite and summarises chat and whispers.
  - She knows your quest log, plans what to do next and sets map waypoints.
  - She compares loot with what you wear and answers game questions from a local knowledge base.

> The app's window, its on-screen messages and the assistant are in Polish. The chat you send is always English.

## Requirements

- Windows 10 or 11, 64-bit.
- **An NVIDIA GPU** (RTX series) with a current driver. It was tested on an RTX 5080 with 16 GB.
  - Speech recognition and translation run on the GPU next to the game.
  - When there isn't enough free VRAM, speech recognition falls back to the CPU.
- **[Python 3.12](https://www.python.org/downloads/).** Keep *py launcher* ticked in the installer. You don't need the CUDA toolkit, because the Python packages bring the CUDA libraries.
- About **12 GB** of free disk space. Setup downloads about 8 GB.
- A microphone.
- WoW Forever, installed with Battle.net.

Optional:
- an [ElevenLabs](https://elevenlabs.io) account for character voices and the assistant's voice;
- a [Google AI Studio](https://aistudio.google.com) key for a smarter assistant;
- MongoDB for voice assignments. Without it they are kept in local files.

## Installation (once)

1. **Get the code.** Either clone it:

   ```
   git clone https://github.com/matheowis/wow-voice-translator.git
   ```

   or use **Code → Download ZIP** on GitHub and unzip it, for example to `C:\WoWVoiceTranslator`. Avoid folders that need admin rights, such as `Program Files`.

2. **Open PowerShell in that folder.** In File Explorer, click the address bar, type `powershell` and press Enter.

3. **Run the setup script:**

   ```
   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
   ```

   The script:
   - creates a virtual environment `.venv` with Python 3.12 (`py -3.12`) and installs `requirements.txt`;
   - downloads the models into `models\` and llama.cpp into `runtime\` (about 8 GB, resumable, every file SHA-256 verified):
     - Whisper large-v3-turbo (ONNX), for speech recognition;
     - Parakeet TDT 0.6B v3 (ONNX), the fallback recognizer;
     - NVIDIA Nemotron 3.5 Bolt E4B (GGUF), the translator;
     - llama.cpp b12177 for CUDA, which runs the translator;
   - installs the addon into `<WoW folder>\_forever_\Interface\AddOns\WoWVoiceTranslator`. It finds the game through the Battle.net registry entry;
   - creates `.env` from `.env.example`. That is where optional keys go.

   If a download fails, run the script again; it continues where it stopped. Options:

   | Option | Meaning |
   |---|---|
   | `-Wow "D:\Games\World of Warcraft"` | the WoW folder (the one that contains `_forever_`), when it isn't found automatically |
   | `-SkipAddon` | don't install the addon |
   | `-Python "py -3.12"` | how to start Python 3.12 |

4. **Check the installation** (optional). This prints what is installed and what is missing:

   ```
   .venv\Scripts\python -m wvt.cli check
   ```

## Starting

1. **Start the app.** Double-click **`scripts\run.bat`**. An icon appears in the system tray next to the clock.
   - Loading the models takes 1–2 minutes, or several minutes from a slow (USB) drive.
   - Double-click the icon to open the window. `scripts\run.bat --show` opens it right away.
   - The first start creates `config.json` with all settings.
2. **Set the display mode.** Start WoW Forever and, in **Options → Graphics**, set **Display Mode: Windowed (Fullscreen)**.
   - The addon talks to the app through a small grid of coloured squares in the top-left corner of the game.
   - The app reads that grid with an ordinary screen capture, and only while WoW is the active window.
   - In plain *Windowed* mode the grid can come out scaled, and exclusive *Fullscreen* can hide it from screen capture. *Windowed (Fullscreen)* works.
3. **Enable the addon.** At the character selection screen, click **AddOns** and make sure **WoW Voice Translator** is enabled.
4. **Test the connection.** In the game, type `/wvt test`. The app shows **„✓ połączenie z addonem działa”** ("the connection to the addon works") in the middle of the screen.

## Using it

| Key | What it does |
|---|---|
| hold **`** (backtick, under Esc) | speak Polish → English text for **/say** |
| hold **Caps Lock** | speak Polish → English text for **/party** |
| hold **mouse button 5** (side button) | talk to the assistant |
| **Esc** while speaking | cancel |
| **Enter** | send the text. The app never sends anything by itself: it only pastes into the chat box. |

- **Hover translations.** Hover the mouse over a chat line for a moment and the Polish translation appears above it.
- **Settings.** Keys, microphone and other settings are in the app window. The keys react only while WoW is the active window.

Addon commands in the game:

| Command | What it does |
|---|---|
| `/wvt test` | test the connection to the app |
| `/wvt on`, `/wvt off` | hover translations on/off |
| `/wvt say on/off`, `/wvt party on/off` | read /say or /party aloud (character voices, off by default) |
| `/wvt lang en`, `/wvt lang pl` | hear other players in English, or translated to Polish |
| `/wvt panel` | show/hide the voice panel above the chat (Shift-drag moves it) |
| `/wvt voice` | voice status: what is on, how many lines were sent or skipped |
| `/wvt voicedebug on` | print the race, sex and measured distance for every line that is read |
| `/wvt asystent off` | stop sending chat and game state to the assistant |

## Character voices (optional, ElevenLabs)

1. Put your key in `.env`: `ELEVENLABS_API_KEY=...`. The `.env` file is git-ignored; never commit it.
2. Create the voices once, in your own ElevenLabs account. The default is 90 voices: 9 races × 2 sexes × 5 voices each.

   ```
   .venv\Scripts\python -m wvt.voice.design
   ```

   With a smaller plan, use `--slots 1` or `--slots 2`.
3. Restart the app. In the game, switch on **Say** and/or **Party** on the panel above the chat. They are off by default, so nothing is spent until you do.

The full guide is in **[docs/VOICES.md](docs/VOICES.md)**: permissions for the key, credits, re-rolling voices you don't like, rating voices, the assistant's voice and how `/say` volume follows distance.

### Who speaks and writes in which language

| What | Language | How to change |
|---|---|---|
| What you send to chat (` and Caps Lock) | always **English**: you speak Polish, the app translates | – |
| Hover translation | **Polish** | `/wvt on` / `/wvt off` |
| Other players' voices (/say, /party) | **EN**: the English original, or **PL**: translated to Polish | **Glos EN/PL** button on the panel, `/wvt lang pl` / `/wvt lang en` |
| Your own character | repeats your line in its voice: in English in EN mode, in your own Polish words in PL mode | as above; turn it off in the app's **Voices** tab |
| The assistant | always **Polish** | – |

## The assistant (Iskra, Polish only)

Hold **mouse button 5** and say what you want in Polish.
- She answers aloud with an ElevenLabs voice (`assistant.voice_id`). Without a voice she answers in text on screen.
- When something has to happen in the game, she types a ready command into the chat box, for example `/wvt inv Ann Bob`. **It runs only when you press Enter**; Esc cancels.

| Say, for example | What happens |
|---|---|
| „zaproś ich”, „weź go doproś do party”, „zaproś Artasa” | invites everyone who wrote "inv me" lately, the last player who asked or spoke, or a named player (Polish declensions are understood) |
| „wyrzuć Bolka”, „daj lidera Jainie”, „zrób raid”, „ready check” | group commands; the game blocks some of them in combat |
| „co mi napisał?”, „co piszą na trade?”, „kto szuka grupy?” | reads whispers and summarises chat channels in Polish |
| „odpisz mu, że zaraz będę” | translates your reply to English and types it into the chat box |
| „co teraz robić?”, „a potem?” | a plan for now: the shortest route through your quests, what matters most, what to skip |
| „czy warto robić ten quest?”, „gdzie go oddać?” | quest advice: XP, chain, reward; she remembers what you are talking about for 15 minutes |
| „gdzie jest trener magów?”, „zaprowadź mnie tam” | nearest NPC or place, plus a map waypoint |
| „czy ten kij, który znalazłem, jest lepszy?”, „co mogę sprzedać?” | compares loot with what you wear; lists what is in your bags |
| „przypomnij mi za 10 minut…”, „zanotuj, że…” | reminders by time, level or place, and notes |
| „ile expa na godzinę?”, „brać need czy greed?” | session stats; loot roll advice |
| game questions („jak pokonać VanCleefa?”) | answers from the local knowledge base |

**A smarter assistant with Gemini (optional).**
- Put `GOOGLE_AI_API_KEY=...` (from Google AI Studio) in `.env` and set `"assistant": {"llm": "gemini"}` in `config.json`. Only the assistant uses Gemini; chat translation stays local.
- Her answers take 2–3 seconds instead of under a second, but they are much better in Polish.
- If Gemini fails, the local model answers.
- **Privacy:** the assistant's questions, facts from your game and names from your game (quests, items, people in chat) are then sent to Google. A free key also has a small daily limit. `"lexicon": {"embedder": "local"}` keeps the name vectors on your PC.

**Game knowledge (once).** The knowledge base lets her know WoW Forever: quests, NPCs, dungeons, classes and zones.

```
.venv\Scripts\python scripts\download.py --embed-model
.venv\Scripts\python -m wvt.kb.build
```

- **What the first build does:**
  - It downloads about 600 Warcraft Wiki pages, at one page per second (about 12 minutes).
  - It downloads the WoW Forever quest data from QuestieDB.
  - It computes vectors on the CPU (about 4 minutes).
  - It translates quest texts to Polish with the local model. That takes about 40 minutes the first time; later builds translate only new quests, and `--no-translate` skips this step.
- Everything is stored in `data\kb` and works offline.
- **Your own guides:** put `.md` or `.txt` files in `data\kb\guides` and rebuild with `--offline`.

**Names from your game.** The app keeps a list of the names that matter right now: your quests, nearby NPCs, people in chat, your loot and bags.
- Speech recognition gets them as a hint.
- Misheard names are corrected by sound before translation ("Targasa" → Thargas).
- Add your own words (friends, guild, nicknames) to `data\slownik.txt`, one per line.

**Her log.** With `"privacy": {"log_assistant": true}` in `config.json`, every question, how she understood it and her answer go to `logs\assistant.jsonl`. This is off by default.

## Configuration

`config.json` is created on the first start. Most settings are also in the app window.
- `push_to_talk`: keys and channels.
- `audio`: microphone and auto gain.
- `voice`: volumes, distance curve, daily character budget, `/say` safety valve.
- `assistant`: name, voice, LLM, which comments she makes.
- `lexicon`: names from your game.
- `privacy`: what is logged.

Every key is described (in Polish) in [docs/SZCZEGOLY.md](docs/SZCZEGOLY.md).

## Updating

```
git pull
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

The setup script only installs what is new and copies the current addon into the game. Then restart the app and type `/reload` in the game.

## Troubleshooting

- **No hover translations, and `/wvt test` shows nothing.**
  - Use *Windowed (Fullscreen)*, and keep WoW the active window.
  - Make sure the app has finished loading: `logs\wvt.log` says when the models are ready.
  - Keep the top-left corner of the game free of overlays: FPS counters, Discord or Steam overlays.
- **Setup didn't find WoW.** Run it with `-Wow "D:\Games\World of Warcraft"`, the folder that contains `_forever_`.
- **Speech isn't recognised.** In the app window, go to *Settings → Microphone*, pick the right microphone and run the test.
- **The microphone is quiet.**
  - The app boosts quiet speech by up to +30 dB before recognition. The microphone test shows the speech level; −30 to −15 dBFS is good.
  - It is better to raise the level at the source: Windows sound settings, *Recording → Levels*.
- **Voices are silent.**
  - Check the app's **Voices** tab: the key, the number of voices and the daily limit.
  - Check that **Say** or **Party** is on in the game (`/wvt voice`).
  - After an addon update, type `/reload`.
- **Starting is slow.** The models are large. From a slow or USB 2.0 drive, starting takes a few minutes. Leave the app running in the background.
- **Diagnostics.** Run `.venv\Scripts\python -m wvt.cli check`. Logs are in the `logs` folder.

## Safety and privacy

- **Nothing touches the game.** The app doesn't read game memory, injects nothing into the game and never sends chat messages for you. It only pastes text into the chat box, and you press Enter.
- **The addon** uses only the official addon API. It draws the signal grid in the corner of the screen.
- **Recordings and chat.** Recordings are not saved. Chat content is not saved, unless you turn on the assistant's log.
- **Your keys** live only in `.env`, which is git-ignored.
- **ElevenLabs** receives only the text of the lines from channels you switched on.
- **Google** receives data only when you choose Gemini for the assistant (see above).
- **Your responsibility.** Use the app at your own risk, and check Blizzard's rules on third-party software.

## Tests

```
.venv\Scripts\python -m pytest
```

The addon's Lua code is tested with `lupa` (Lua 5.1, like WoW). `tests\test_capture_integration.py` draws on the screen, so it fails while the app's overlay is running.

## More

- [docs/VOICES.md](docs/VOICES.md): creating the character voices (ElevenLabs).
- [docs/SZCZEGOLY.md](docs/SZCZEGOLY.md): how it works, measurements, model choice and all settings (in Polish).

## License

The code is MIT; see [LICENSE](LICENSE).

The models are downloaded by the setup script. They are not part of this repository and have their own licenses:
- OpenAI Whisper large-v3-turbo: MIT;
- NVIDIA Parakeet TDT 0.6B v3: CC-BY-4.0;
- NVIDIA Nemotron 3.5 Bolt E4B: OpenMDW-1.1;
- multilingual-e5-small: MIT;
- WeSpeaker ResNet34: Apache-2.0;
- llama.cpp: MIT.

ElevenLabs voices are covered by the terms of your ElevenLabs plan.

The knowledge base is built on your PC and is not part of the repository:
- Warcraft Wiki texts are CC BY-SA 4.0.
- QuestieDB data has no single clear license, so use it only privately.
