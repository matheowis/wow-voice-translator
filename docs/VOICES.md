# Character voices (ElevenLabs)

The app can read `/say` and `/party` chat aloud. Every character gets a voice that fits its race and sex, and keeps it. This page explains how to create those voices in **your own** ElevenLabs account.

Voices are optional. Speech-to-chat translation and hover translations work without them.

## How it works

- **Pools.** A pool is one race and one sex, for example *Gnome Female*.
  - WoW Forever has 9 pools per sex: Human, Dwarf, NightElf, Gnome, Orc, Undead, Tauren, Troll and Skyborne. Both Skyborne factions use one game model, so they share a pool.
  - That makes 18 pools in total.
- **Slots.** Every pool has 5 slots (`voice.slots_per_pool`), so the default set is **90 voices**.
  - Each slot has its own brief: age, register, accent, pace, attitude and an in-character preview line. That keeps the five voices of a pool clearly different.
- **Assignment.** The first time a character speaks, it gets a voice from its pool and keeps it.
  - Assignments are stored locally: in MongoDB when `MONGODB_URI` is set in `.env`, otherwise in `data/voice_profiles.json` and `data/player_voices.json`.
- **Unknown race.** While the game doesn't report a character's race, it speaks with a Human voice of its sex. It moves to its own pool once the race is known.
- **Other races.** Races from other WoW versions use the closest pool (BloodElf → NightElf, Worgen → Human…). `voice.race_aliases` in `config.json` adds your own mappings.

## What you need

- **An ElevenLabs account and an API key** (*Developers → API Keys* on elevenlabs.io).
  - The key needs Text to Speech, plus Voices with permission to create voices, because Voice Design saves new voices to your account.
  - The app doesn't need any other permission.
- **Enough custom-voice slots in your plan.** The default set is 90 voices. On a smaller plan, use fewer slots per pool: `--slots 1` gives 18 voices, `--slots 2` gives 36.
- **Credits.**
  - Designing voices spends credits on previews.
  - Reading chat costs about 0.5 credit per character with the default model `eleven_v4_turbo` (`voice.model_id`).
  - The app stops speaking once it has sent `voice.daily_char_budget` characters in a day (default 30,000). You can change the limit in the app's **Voices** tab.

## Create the voices

All commands run from the project folder in PowerShell.

1. **Add the key to `.env`.** Setup creates the file from `.env.example`. Add this line:

   ```
   ELEVENLABS_API_KEY=your-key-here
   ```

   `.env` is git-ignored. Never commit it and never paste the key anywhere else.

2. **Download the speaker model (recommended).** It keeps the voices of a pool distinct:

   ```
   .venv\Scripts\python scripts\download.py --speaker-model
   ```

   This is WeSpeaker ResNet34 (Apache-2.0), saved to `models\speaker`. With it, every Voice Design request is scored:
   - The preview that sounds least like the voices already in the pool wins.
   - A request whose best preview is still too close is repeated with another seed.
   - The preview is transcribed (Parakeet on the CPU), so a voice that mumbles is rejected.

   Without the model, the first preview of each request is used and nothing is checked.

3. **Preview the plan for free.** This prints the brief of every slot that would be created and spends no credits:

   ```
   .venv\Scripts\python -m wvt.voice.design --dry-run
   ```

4. **Create the voices:**

   ```
   .venv\Scripts\python -m wvt.voice.design
   ```

   - It fills every missing slot, 4 at a time (`--workers`). Running it again skips the slots that already have a voice.
   - In ElevenLabs the voices are named `WVT <Race> <Gender> <slot>`, for example `WVT Gnome Female 3`.
   - Useful options:
     - `--slots 2` sets the number of voices per pool;
     - `--races Gnome,Dwarf --genders Female` limits the run to some pools;
     - `--attempts 3` sets how many Voice Design requests one slot may use;
     - `--no-speech-check` skips the intelligibility check.

5. **Restart the app** (`scripts\run.bat`). The **Voices** tab of the app window shows whether the key works, how many voices there are and today's usage.

6. **Switch reading on in the game.**
   - Above the chat there is a panel **WVT: Say | Party | Glos**. Shift-drag moves it.
   - **Say** and **Party** turn reading of that channel on. Both are **off by default**, so nothing is spent until you switch them on.
   - **Glos EN / PL**:
     - **EN**: you hear other players in English, and your own character repeats your line in its voice.
     - **PL**: you hear other players translated to Polish.
   - The same as commands: `/wvt say on`, `/wvt party on`, `/wvt lang pl`, `/wvt lang en`.

## How `/say` is read

- **Volume by distance.** Volume falls with the speaker's distance: 100% up to 5 yards, 50% at 15 yards, silent from 30 yards (`voice.distance_curve`).
  - The addon measures distance only for characters it can see: your target, mouseover, group members and visible nameplates. Turn on friendly nameplates (**Shift+V**) so almost everyone near you can be measured.
  - For other players WoW gives addons only ranges (about 8, 11 or 28 yards). Exact yards are available only for group members.
  - A speaker whose distance is unknown is read quieter (`voice.unknown_distance`, `quiet_gain` 0.35).
- **Lines that are skipped.**
  - The same line from the same player within 15 seconds.
  - Anyone who writes more than 3 lines in 20 seconds; they are muted for 30 seconds.
  - Lines in the other faction's language.
  - In a crowd (6 lines or 4 different speakers within 15 seconds), only players within 10 yards are read, at most 6 lines a minute. You set this in `voice.say_valve`.
  - Skipped lines are never sent to ElevenLabs, so they cost nothing.

## Re-roll voices you don't like

```
.venv\Scripts\python -m wvt.voice.design --only Gnome:Male:2 --attempts 6      # one voice
.venv\Scripts\python -m wvt.voice.design --replace --races Human --genders Male  # a whole pool
.venv\Scripts\python -m wvt.voice.design --replace --keep VOICE_ID1,VOICE_ID2    # everything except these
```

- A redesigned voice gets the suffix ` v2` (`--suffix`).
- Characters that used the old voice move to the new one, unless you assigned their voice by hand.
- The old voices stay in your ElevenLabs account and are listed in `data/replaced_voices.json`. Delete them in the ElevenLabs dashboard if you don't want them; the app never deletes voices.

## Rating voices and candidates (optional)

Instead of re-rolling by hand, you can rate voices from 1 to 5 and let the app design extra candidates for the slots you don't like.

- **Ratings** are a folder with one file per voice, named `<voiceId>.json`, containing `{"score": 5}`.
  - A **5** locks the voice: the design tools never touch it again, even with `--replace`.
  - A pool with five locked voices is closed.

```
.venv\Scripts\python -m wvt.voice.candidates status                    # locked voices and open slots per pool
.venv\Scripts\python -m wvt.voice.candidates apply --ratings RATINGS   # store the scores, lock the 5s
.venv\Scripts\python -m wvt.voice.candidates make                      # candidates for pools with a slot rated 1-4
.venv\Scripts\python -m wvt.voice.candidates prune --samples-dir SAMPLES   # drop candidates that sound like another voice
```

- **Candidates.**
  - `make` designs one candidate per character brief and lists them in `data/voice_candidates.json`.
  - Candidates are never heard in the game. Rate them the same way and run `apply` again.
  - The best-rated candidate takes the lowest-rated slot of its pool, but only if it scored higher than the voice it replaces.
- **Samples.** `--samples-dir` points to samples named `<voiceId>_en.mp3` and `<voiceId>_pl.mp3`.
  - `prune` needs them.
  - `design` and `make` synthesise one short line instead when a voice has no sample, which costs about 45 credits.
- `apply`, `make` and `prune` accept `--dry-run`.

## The assistant's voice

The assistant (Iskra) always speaks Polish.

1. Pick a voice that speaks Polish well. It can be one of your own designs or a voice from the ElevenLabs Voice Library that you added to your account.
2. Put its ID in `config.json`:

```json
"assistant": { "voice_id": "YOUR_VOICE_ID" }
```

Without a `voice_id`, Iskra answers as text on screen only. If you rate candidate voices, a 5 for an assistant candidate sets this field for you (`candidates apply`).
