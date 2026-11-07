"""Voice Design briefs that make the five voices of a pool clearly different.

The first designs varied only one short phrase (age) between the slots of a pool, and the
voices came out nearly identical (speaker-embedding similarity 0.63 inside a pool vs 0.74
for the same voice reading two texts).  Here every slot differs in age, register and
texture, accent, pace and attitude at the same time, and gets its own in-character
preview line - Voice Design shapes the voice after the line it performs.

The slot order matches the earlier design (1 young, 2 middle-aged, 3 elderly, 4 playful,
5 calm), so voices kept from the first round stay in a fitting slot.
"""

from __future__ import annotations

SLOT_LABELS = {
    "Male": ["młody zapaleniec", "dowódca, niski głos", "zrzędliwy weteran", "kombinator, nosowy", "spokojny uzdrowiciel"],
    "Female": ["młoda, energiczna", "surowa kapitan, alt", "stara matrona", "zadziorna łotrzyca", "łagodna kapłanka"],
}

AGE = ["Young adult", "Middle-aged", "Elderly", "Adult", "Adult"]
PACE = [
    "Talks fast, eagerly, with rising excitement",
    "Speaks slowly and firmly, with natural authority",
    "Speaks slowly, with pauses, sighs and grumbles",
    "Quick, clipped and conspiratorial, as if sharing a secret",
    "Calm, unhurried and soothing",
]
ATTITUDE = {
    "Male": ["an excitable young adventurer", "a battle-hardened commander", "a grumpy veteran who has seen it all",
             "a sly, sarcastic trickster", "a kind, patient healer"],
    "Female": ["a cheerful, energetic young adventurer", "a stern captain who expects obedience",
               "a wise but impatient old matriarch", "a teasing, mischievous rogue", "a serene, gentle priestess"],
}
# register of each slot, per voice family
REGISTERS = {
    "normal": {
        "Male": ["bright, higher-pitched and lively", "very deep, booming bass", "thin, creaky and raspy",
                 "mid-pitched, slightly nasal and wiry", "low, warm and soft"],
        "Female": ["high, bright and bubbly", "low, husky alto", "frail, wavering and crackly",
                   "smoky, sly and slightly nasal", "soft, airy mezzo"],
    },
    "high": {  # gnomes: mostly small and high, with two deliberate outliers so the pool spreads out
        "Male": ["very high, squeaky and chirpy", "surprisingly deep, gruff and gravelly",
                 "high, reedy and wheezing", "mid-pitched, nasal and whiny", "soft, mid-pitched and breathy"],
        "Female": ["very high, squeaky and giggly", "surprisingly low, husky and bossy", "high, thin and trembling",
                   "bratty, nasal and mid-pitched", "soft, low and sing-song"],
    },
    "low": {  # orcs, tauren
        "Male": ["rough and gravelly but lighter than most of his kind", "extremely deep, thunderous bass",
                 "deep, croaking and worn out", "low, hoarse and menacing, almost a growl", "deep, warm and gentle rumble"],
        "Female": ["strong, raspy and youthful", "deep, powerful contralto", "low, cracked and ancient",
                   "low, smoky and dangerous", "warm, deep and motherly"],
    },
    "hollow": {  # undead
        "Male": ["raspy and hoarse but youthful", "deep, hollow and echoing", "dry, croaking and ancient",
                 "shrill, manic and cackling", "flat, whispery monotone"],
        "Female": ["breathy, eerie and youthful", "cold, hollow and commanding", "dry, rattling and ancient",
                   "shrill, theatrical and unhinged", "soft, ghostly whisper"],
    },
    "airy": {  # night elves, Skyborne
        "Male": ["light, airy and quick", "deep, resonant and regal", "aged, slow and whispery",
                 "crisp, sharp and sardonic", "soft, breathy and serene"],
        "Female": ["light, musical and bright", "cool, regal and commanding", "ancient, slow and whispery",
                   "silky, sharp and teasing", "soft, ethereal and serene"],
    },
}
RACES = {
    "Human": {"who": {"Male": "human man", "Female": "human woman"}, "family": "normal",
              "accents": ["General American", "refined Received Pronunciation British", "rural West Country English",
                          "London Cockney", "soft Irish"]},
    "Dwarf": {"who": {"Male": "stocky, barrel-chested dwarf", "Female": "sturdy dwarf woman"}, "family": "normal",
              "accents": ["broad Glaswegian Scottish", "deep Highland Scottish", "thick Aberdeenshire Scottish with rolled r's",
                          "Yorkshire English", "gentle Northern Irish"]},
    "NightElf": {"who": {"Male": "night elf from ancient moonlit forests", "Female": "night elf woman from ancient moonlit forests"},
                 "family": "airy",
                 "accents": ["lilting, Welsh-tinged", "regal, formal British", "hushed, archaic and formal",
                             "crisp, Scandinavian-tinged", "dreamy, soft-spoken"]},
    "Gnome": {"who": {"Male": "tiny gnome tinkerer", "Female": "tiny gnome engineer"}, "family": "high",
              "accents": ["fast-talking General American", "posh, fussy British", "chirpy Australian",
                          "rapid New York", "Midwestern American"]},
    "Orc": {"who": {"Male": "hulking orc warrior", "Female": "muscular orc warrior woman"}, "family": "low",
            "accents": ["harsh, barking", "thunderous war-chief", "gravelly, shamanic", "growling, menacing",
                        "rough but patient"]},
    "Undead": {"who": {"Male": "undead revenant", "Female": "undead revenant woman"}, "family": "hollow",
               "accents": ["sneering upper-class British", "hollow, echoing", "croaking old English",
                           "theatrical, stage-actor British", "deadpan, emotionless"]},
    "Tauren": {"who": {"Male": "massive bull-like tauren", "Female": "tall, strong tauren woman"}, "family": "low",
               "accents": ["earthy, plainspoken", "booming, ceremonial", "ancient, meditative",
                           "gruff, rural", "soft, lullaby-like"]},
    "Troll": {"who": {"Male": "lanky jungle troll with tusks", "Female": "lanky jungle troll woman with tusks"},
              "family": "normal",
              "accents": ["laid-back Caribbean", "bouncy Jamaican-style patois", "deep, slow Caribbean drawl",
                          "quick, hissing island", "sing-song Caribbean"]},
    "Skyborne": {"who": {"Male": "Skyborne elf from a floating island in the sky",
                         "Female": "Skyborne elf woman from a floating island in the sky"}, "family": "airy",
                 "accents": ["crisp, aristocratic High Order", "breezy, free-spirited Windshaper", "precise, scholarly",
                             "ethereal, echoing", "proud, ringing sky-captain"]},
    "Unknown": {"who": {"Male": "wandering adventurer", "Female": "wandering adventurer woman"}, "family": "normal",
                "accents": ["General American", "Australian", "Scottish", "Northern English Mancunian", "Irish"]},
}
LINES = [
    "Oh, this is it! Our first real dungeon together! Come on, come on, I'll grab the loot, you keep the healer "
    "alive. Let's go, let's go, before someone else gets there first!",
    "Listen up. Tank takes the skull, mages sheep the moon, and nobody pulls until I give the word. Hold your "
    "cooldowns for the boss. Move out.",
    "Bah. In my day we walked to Ironforge. Uphill. In the snow. Now hush, and let these old bones rest before the "
    "next fight, or I'll go back home.",
    "Psst. Over here. Need a rare sword? Fell off a caravan, no questions asked. Twenty gold and you never saw me. "
    "Deal? Wise choice, friend.",
    "Easy now, you're safe. Breathe slowly. I'll mend those wounds, and then we'll rest by the fire for a while. "
    "There's no hurry at all.",
]


def describe(race: str, gender: str, slot: int) -> str:
    from .briefs import BRIEFS

    b = BRIEFS.get((race, gender, slot))
    if b:  # round 3: a hand-written character
        return b["brief"] + " High-quality, crisp audio with no background noise or music."
    r = RACES[race]
    i = (slot - 1) % 5
    register = REGISTERS[r["family"]][gender][i]
    return (f"{AGE[i]} {r['who'][gender]} with a {register} voice and a {r['accents'][i]} accent. "
            f"{PACE[i]}. Sounds like {ATTITUDE[gender][i]}. "
            "High-quality, crisp audio with no background noise or music.")


def preview_line(slot: int, race: str = "", gender: str = "") -> str:
    from .briefs import BRIEFS

    b = BRIEFS.get((race, gender, slot))
    return b["line"] if b else LINES[(slot - 1) % 5]


def label(gender: str, slot: int, race: str = "") -> str:
    from .briefs import BRIEFS

    b = BRIEFS.get((race, gender, slot))
    return b["label"] if b else SLOT_LABELS.get(gender, SLOT_LABELS["Male"])[(slot - 1) % 5]
