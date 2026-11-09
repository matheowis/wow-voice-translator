"""Hand-written character briefs (round 3), written after the user's ratings of round 2.

What the ratings taught (notes in quotes):
* a "young adult, higher-pitched" male came out as a child or a woman ("szczeniak", "baba",
  "dziecko? albo dziewczyna") -> every man here is an adult with an explicit age;
* "nasal" made any race sound like a gnome ("znowu gnom") -> only gnomes may be nasal;
* "soft, breathy" men sounded female -> men get a register word (baritone, bass) instead;
* "squeaky, very high" gnomes were Smurfs ("smerf") -> gnomes are quick, clever adults with a
  natural, slightly higher voice;
* "ethereal, echoing, hollow" produced odd, processed audio (Skyborne: "dziwne") -> natural,
  grounded voices, no effect words;
* undead must sound like their throat was torn out, full of suffering, may be older;
* night elves sound about 25, always.

Each brief names one character, its age, register and texture, accent, pace and attitude, and
has its own preview line - Voice Design performs the line, so the line sets the character.
"""

from __future__ import annotations

# (race, gender, slot) -> label (Polish, for the rating page), brief, preview line
BRIEFS: dict[tuple[str, str, int], dict[str, str]] = {
    # ------------------------------------------------------------------ Undead: torn throats, suffering
    ("Undead", "Male", 1): dict(
        label="rozdarte gardło",
        brief="Undead man in his fifties whose throat was torn open and crudely stitched shut. A wet, ragged, "
              "painful rasp; every word is dragged out with effort, with short gasps between phrases. Bitter and "
              "weary. Standard English accent.",
        line="Do not look at me like that. Every word I speak scrapes this ruined throat raw. Still, I would rather "
             "suffer than stay silent while you lead us to our deaths."),
    ("Undead", "Male", 2): dict(
        label="kościany dowódca",
        brief="Old undead soldier in his sixties, voice shredded to a dry, low, gravelly rattle like grinding bones. "
              "Slow, cold and commanding, with open contempt for the living. Clipped British accent.",
        line="Form a line. The living tire, they bleed, they weep. We do not. Hold this gate until dawn, and do not "
             "waste my time with your fear."),
    ("Undead", "Male", 3): dict(
        label="plagowy uczony",
        brief="Ancient undead plague scholar, around seventy. A cracked, papery voice with a wheezing breath and a "
              "faint gurgle in the chest; pedantic and darkly amused. Upper-class English accent.",
        line="Fascinating. Hold still, this will only hurt for a moment. Well, for a long moment. The plague does "
             "its finest work slowly, you see."),
    ("Undead", "Male", 4): dict(
        label="złamany szyderca",
        brief="Undead rogue in his forties with a hoarse, broken voice that cracks and strains on every phrase, "
              "a nasty chuckle caught in his ruined throat. Quick, sneering, sarcastic. London accent.",
        line="Heh. You want the key? Dead men keep their pockets full, friend. Pay me, or join me down here in the "
             "dirt. Your choice, I'm in no hurry."),
    ("Undead", "Male", 5): dict(
        label="żałobny kapłan",
        brief="Undead former priest in his sixties. Deep, rasping, sorrowful voice full of pain and grief; slow, "
              "mournful, each sentence ending in a long, pained exhale. Soft Northern English accent.",
        line="I prayed once. I sang hymns in this very chapel. Now my voice is ash and my faith is rot. Come, let "
             "me tend your wounds while I still remember how."),
    ("Undead", "Female", 1): dict(
        label="rozdarte gardło",
        brief="Undead woman in her forties, throat ravaged and raw. A harsh, strained, painful rasp; words forced "
              "out with effort, short gasps between them. Haunted and bitter. Standard English accent.",
        line="It hurts to speak. It hurts to remember. But I will say this once: whoever did this to me is still "
             "breathing, and I want him to stop."),
    ("Undead", "Female", 2): dict(
        label="lodowata komendantka",
        brief="Undead commander woman in her fifties. A cold, cracked, gravelly voice, dry as dust; slow, "
              "deliberate and menacing, never raised. Refined British accent.",
        line="You will march when I say march, and you will die when I say die. Fortunately for you, today I only "
             "need you to walk. Move."),
    ("Undead", "Female", 3): dict(
        label="stara zielarka",
        brief="Old undead herbalist woman, around seventy. A brittle, crackling voice with a wet wheeze, trembling "
              "and breathless, yet morbidly cheerful. Rural English accent.",
        line="Oh, don't mind the smell, dearie, that's just me. Now, drink this. It might cure you. It might also "
             "kill you. Either way, you'll feel better."),
    ("Undead", "Female", 4): dict(
        label="zadymiona łotrzyca",
        brief="Undead rogue woman in her thirties. A smoky, shredded, hoarse voice, quick and sardonic, with a "
              "rough, painful chuckle under every line. Irish accent.",
        line="Shh. Don't scream, it's bad for my nerves, what's left of them. Hand over the map and I'll let you "
             "keep both your eyes. Generous, aren't I?"),
    ("Undead", "Female", 5): dict(
        label="smutna aptekarka",
        brief="Undead apothecary woman in her fifties. A low, deep rasp, weary and full of quiet suffering; slow, "
              "deliberate, every word heavy. Soft Scottish accent.",
        line="Rest now. I know the cold in your bones, I carry it in mine. This draught will ease the pain for a "
             "while. That is all any of us can hope for."),
    # ------------------------------------------------------------------ Skyborne: noble elves, natural voices
    ("Skyborne", "Male", 1): dict(
        label="zwiadowca wiatru",
        brief="Skyborne elf man in his late twenties, a Windshaper scout. A clear, confident baritone with a light, "
              "breezy rhythm and a warm laugh; natural and grounded. Neutral British accent.",
        line="Feel that? The wind turned east an hour ago. Storm's coming, and it's a big one. Grab your things, "
             "we fly before it hits. Come on, it'll be fun!"),
    ("Skyborne", "Male", 4): dict(
        label="arystokrata magii",
        brief="Skyborne High Order spellblade in his thirties. A crisp, precise, cool baritone, aristocratic "
              "Received Pronunciation, dry wit and a faint air of superiority.",
        line="Do try to keep up. The ley lines bend here, which means your little fireball will bend with them. "
             "Aim slightly to the left. No, the other left."),
    ("Skyborne", "Male", 5): dict(
        label="nawigator podniebny",
        brief="Skyborne sky-ship navigator in his forties. A rich, resonant, deep baritone with a slight weathered "
              "roughness from years of wind and sun; calm, steady and reassuring. Soft Welsh accent.",
        line="Steady hands on the rigging, lads. Clouds below, stars above, and the old girl's holding together "
             "nicely. We'll be in Valanaar by sunrise. Trust the wind."),
    ("Skyborne", "Female", 1): dict(
        label="wolny duch",
        brief="Skyborne Windshaper woman around twenty-five. A bright, clear, lively mezzo voice, free-spirited and "
              "playful, natural modern-sounding delivery. Light Irish accent.",
        line="Last one to the edge of the island buys the drinks! What, scared of a little fall? The wind always "
             "catches me. Usually. Come on!"),
    ("Skyborne", "Female", 2): dict(
        label="arkanistka",
        brief="Skyborne High Order arcanist woman in her thirties. A cool, poised, precise voice, refined Received "
              "Pronunciation, elegant and quietly confident.",
        line="The spell is simple, really. Draw the power from the ley line, shape it, release it. If you feel your "
             "fingers burning, you have done it wrong."),
    ("Skyborne", "Female", 3): dict(
        label="przywoływaczka burz",
        brief="Skyborne storm-caller woman around thirty. A strong, slightly husky voice full of passion and "
              "intensity; she speaks with fierce conviction. Scottish accent.",
        line="Listen to the thunder! The spirits are angry tonight, and so am I. Stand behind me and keep your head "
             "down. This is going to be loud."),
    ("Skyborne", "Female", 4): dict(
        label="podniebna piratka",
        brief="Skyborne sky-pirate woman in her late twenties. A sly, smoky, teasing voice, quick and witty, "
              "always half-laughing. London accent.",
        line="That cargo? Oh, it's ours now. Finders keepers, love. If you want it back, you'll have to catch my "
             "ship first. Good luck with that."),
    ("Skyborne", "Female", 5): dict(
        label="uzdrowicielka",
        brief="Skyborne healer woman around twenty-five. A warm, gentle, melodic voice, soothing but clear and "
              "natural, with a kind smile in it. Soft Canadian accent.",
        line="There we go, breathe with me. The wind carries pain away if you let it. You're going to be fine, I "
             "promise. Just stay with me a little longer."),
    # ------------------------------------------------------------------ Night elves: about 25, clearly gendered
    ("NightElf", "Male", 1): dict(
        label="strażnik lasu",
        brief="Night elf sentinel man who looks about thirty. A smooth, low, calm baritone with quiet confidence; "
              "slow, measured speech with a hint of wildness. Neutral British accent.",
        line="The forest is too quiet tonight. Something is moving between the trees, downwind of us. Keep your "
             "bow ready, and do not speak again until I do."),
    ("NightElf", "Male", 4): dict(
        label="mroczny łowca",
        brief="Night elf warden man around thirty-five. A dark, husky, low voice with quiet menace; short, clipped "
              "sentences. Cool, measured Scandinavian-tinged English accent.",
        line="Three of them. Two in the camp, one on the ridge. I take the ridge. You wait for my signal. Then "
             "it ends quickly, and quietly."),
    ("NightElf", "Male", 5): dict(
        label="druid",
        brief="Night elf druid man who looks about thirty-five. A warm, resonant, deep voice, unhurried and wise, "
              "like a quiet forest at night. Soft Irish accent.",
        line="Sit, rest a while. The old trees have watched ten thousand years of wars come and go. Yours will pass "
             "too. Breathe, and listen to them."),
    ("NightElf", "Female", 1): dict(
        label="łowczyni",
        brief="Night elf huntress around twenty-five. A clear, confident, bright young woman's voice with an "
              "energetic edge and a playful smirk; natural delivery. Neutral American accent.",
        line="Ha! Did you see that shot? Right between the eyes, from across the river. Your turn. Try not to hit "
             "the panther this time, she's still mad at you."),
    ("NightElf", "Female", 3): dict(
        label="kapłanka księżyca",
        brief="Night elf priestess of the moon around twenty-five. A serene, warm, melodic young woman's voice, "
              "graceful and clear, with a gentle smile in it. Soft British accent.",
        line="Elune watches over us, even on the darkest nights. Kneel with me for a moment. Let her light find "
             "your wounds, and then we ride."),
    ("NightElf", "Female", 5): dict(
        label="psotna druidka",
        brief="Night elf druid woman around twenty-five. A light, bright, feminine voice, playful and quick to "
              "laugh, soft-spoken but lively. Irish accent.",
        line="Shh, don't move. There's a baby owl right above your head. Isn't she lovely? Oh, she likes you! No, "
             "don't swat at her, honestly."),
    # ------------------------------------------------------------------ Gnomes: clever adults, never cartoonish
    ("Gnome", "Male", 1): dict(
        label="szalony wynalazca",
        brief="Gnome engineer in his thirties. A fast-talking, quick-witted, slightly nasal, mid-high natural "
              "adult male voice; enthusiastic about gadgets, crisp diction. Brisk American accent.",
        line="Okay, okay, so if I reroute the steam valve through the cogwheel array, then the whole thing should "
             "go at least twice as fast. Probably. Stand back. Further."),
    ("Gnome", "Male", 4): dict(
        label="cwany handlarz",
        brief="Gnome merchant in his forties. A sly, raspy, nasal voice with a quick sales patter and a con man's "
              "charm. New York accent.",
        line="Friend! You look like a gnome of taste. This mechanical chicken lays real eggs. Well, mostly real. "
             "For you, a special price, today only."),
    ("Gnome", "Male", 5): dict(
        label="stary majsterkowicz",
        brief="Elderly gnome tinker in his seventies. A thin, crackly voice with a faint whistle on the s's; "
              "absent-minded, kindly and slow. Posh British accent.",
        line="Now where did I put my spectacles? Ah, on my head, of course. Hand me that spanner, would you? The "
             "little one. No, the other little one."),
    ("Gnome", "Female", 1): dict(
        label="radosna wynalazczyni",
        brief="Gnome inventor woman in her late twenties. A lively, quick, clear mid-high adult woman's voice, "
              "cheerful and nerdy, natural delivery. Australian accent.",
        line="It works! It actually works! Okay, it's on fire a little bit, but that's a feature. Grab the bucket, "
             "would you? Quick, before it spreads."),
    ("Gnome", "Female", 2): dict(
        label="wykładowczyni magii",
        brief="Gnome mage woman in her forties. A crisp, precise, slightly nasal woman's voice with a no-nonsense "
              "lecturer's tone and dry humor. Clipped British accent.",
        line="As I explained three times already, the arcane missile does not bounce. If yours bounced, you have "
             "invented something new, and frankly, terrifying."),
    ("Gnome", "Female", 5): dict(
        label="kapłanka gnomów",
        brief="Gnome priestess woman in her thirties. A gentle, bright, sing-song but natural adult woman's voice, "
              "kind and soothing. Soft Midwestern American accent.",
        line="There, there, it's only a little explosion burn. Hold still. A bit of light, a bit of patience, and "
             "you'll be back to tinkering by supper."),
    # ------------------------------------------------------------------ Orcs: deep, guttural, rough
    ("Orc", "Male", 1): dict(
        label="wściekły wojownik",
        brief="Orc warrior in his thirties. A rough, deep, gravelly voice with a constant growl; loud, aggressive "
              "and hungry for battle. Harsh, barking delivery.",
        line="Enough talking! My axe is thirsty and the enemy is close. Follow me, and if you fall behind, I will "
             "not come back for you. For the Horde!"),
    ("Orc", "Male", 4): dict(
        label="złowrogi najeźdźca",
        brief="Orc raider in his forties. A low, gritty, menacing voice with growling consonants and a cruel, "
              "rumbling laugh; slow threats. Deep, guttural delivery.",
        line="You hear that? That is the sound of your caravan burning. Hand over the gold, slowly, and maybe I let "
             "you walk home. Maybe."),
    ("Orc", "Female", 1): dict(
        label="dumna wojowniczka",
        brief="Orc warrior woman in her thirties. A powerful, low, raspy female voice, fierce and proud, full of "
              "battle-cry energy. Harsh, rough delivery.",
        line="You call that a swing? My grandmother hits harder, and she has been dead for ten years. Again! Put "
             "your back into it this time!"),
    ("Orc", "Female", 2): dict(
        label="kapitan wojenna",
        brief="Orc war-captain woman in her forties. A deep, commanding, gravelly female voice, harsh and "
              "authoritative, used to being obeyed instantly.",
        line="Wolves to the left flank, archers on the hill. When the drums stop, we charge. Nobody retreats. "
             "Nobody. Is that understood?"),
    ("Orc", "Female", 4): dict(
        label="łowczyni",
        brief="Orc huntress in her thirties. A husky, growling, low female voice, sly and dangerous, speaking in "
              "short, clipped sentences.",
        line="Tracks. Fresh ones. Two boars, one wounded. We follow quietly. When it turns, you do not run. You "
             "stand, or you die."),
    ("Orc", "Female", 5): dict(
        label="stara szamanka",
        brief="Orc shaman woman in her sixties. A deep, warm, rough female voice, earthy and wise, slow and "
              "deliberate, like stones grinding by a fire.",
        line="Sit by the fire, child. The spirits of earth and sky are restless tonight. Listen with me. They are "
             "trying to tell us something."),
    # ------------------------------------------------------------------ Dwarves: hearty, gruff, very Scottish
    ("Dwarf", "Male", 1): dict(
        label="hulaka z karczmy",
        brief="Dwarf warrior in his thirties, young for a dwarf. A booming, hearty, gruff baritone with a thick "
              "Scottish accent and loud rowdy laughter; tavern energy.",
        line="Ha ha! Another round for everyone, on me! And when we're done drinking, we go smash some kobolds. "
             "Best plan I've had all week, eh?"),
    ("Dwarf", "Male", 4): dict(
        label="poszukiwacz skarbów",
        brief="Dwarf treasure hunter in his fifties. A raspy, gravelly, conspiratorial voice with a greedy chuckle "
              "and a thick Glaswegian accent.",
        line="Keep your voice down, lad. There's a vault under this mountain nobody's opened in a thousand years. "
             "Gold, gems, the lot. We split it sixty-forty. My sixty."),
    ("Dwarf", "Male", 5): dict(
        label="kapłan-ojciec",
        brief="Dwarf priest in his sixties. A deep, warm, rumbling bass voice with a thick Highland Scottish "
              "accent; gentle, steady and fatherly.",
        line="Easy there, laddie. You took a nasty hit, but you'll live. The Light's got plenty left for a stubborn "
             "one like you. Now hold still."),
    ("Dwarf", "Female", 1): dict(
        label="piwowarka",
        brief="Dwarf brewmaster woman in her thirties. A loud, warm, hearty voice with a thick Scottish accent, "
              "laughing easily, rough around the edges.",
        line="Try this one, it's my best stout yet! Puts hair on your chest, so it does. What, too strong for you? "
             "Ha! Pass it back then, more for me!"),
    ("Dwarf", "Female", 3): dict(
        label="matriarcha klanu",
        brief="Old dwarf matriarch in her seventies. A gravelly, strong, slightly cracked voice with a thick "
              "Aberdeenshire accent; stern but caring.",
        line="In my day we dug these tunnels by hand, and we were grateful for it. Now eat your stew, wipe your "
             "beard, and stop complaining about the cold."),
    ("Dwarf", "Female", 4): dict(
        label="sprytna łotrzyca",
        brief="Dwarf rogue woman in her forties. A sly, raspy, husky voice with a broad Glaswegian accent and a "
              "quick wit.",
        line="Oh aye, the lock's tricky, but so am I. Give me two minutes and a hairpin. And if anyone asks, we "
             "were never here, right?"),
    # ------------------------------------------------------------------ Humans
    ("Human", "Male", 1): dict(
        label="pewny siebie poszukiwacz przygód",
        brief="Human adventurer in his early thirties. A confident, energetic, clear mid-low male voice with cocky "
              "charm and a grin in it. General American accent.",
        line="Relax, I've done this a hundred times. Well, twice. Okay, once, but it went great. Just follow my "
             "lead and try to keep up, alright?"),
    ("Human", "Female", 3): dict(
        label="cięta matrona",
        brief="Human woman in her sixties. A strong, slightly raspy, warm voice with a rural English accent; "
              "sharp-tongued and impatient, but kind underneath.",
        line="Wipe your boots before you come in, I've only just swept. Sit down, eat something, you look half "
             "starved. Honestly, young people these days."),
    ("Human", "Female", 4): dict(
        label="niebezpieczna łotrzyca",
        brief="Human rogue woman in her thirties. A smoky, low, confident voice with a London accent, teasing and "
              "dangerous.",
        line="Looking for someone, love? Funny, so am I. Tall fellow, owes me money. You wouldn't happen to know "
             "where he's hiding? Think carefully."),
    # ------------------------------------------------------------------ Tauren: deep, warm, earthy
    ("Tauren", "Male", 1): dict(
        label="myśliwy z równin",
        brief="Tauren hunter in his thirties. A deep, warm, earthy voice, steady and friendly, speaking at a slow, "
              "relaxed pace. Rumbling chest resonance.",
        line="The herd moves north with the rains. We follow, we take only what we need, and we thank the Earth "
             "Mother for every hunt. Walk with me."),
    ("Tauren", "Male", 4): dict(
        label="przebiegły kupiec",
        brief="Tauren trader in his forties. A deep, rumbling, slightly gravelly voice, shrewd and humorous, slow "
              "and deliberate, with a low chuckle.",
        line="Hmm. You offer three furs for this totem? It was carved by my grandfather's grandfather. Five furs, "
             "and I will throw in a story about him."),
    ("Tauren", "Female", 1): dict(
        label="spokojna łowczyni",
        brief="Tauren huntress in her thirties. A low, warm, strong female voice, calm and grounded, steady pace, "
              "rich chest resonance.",
        line="Easy. The wolf is only curious. Lower your weapon, breathe slowly, and let her pass. Not every "
             "creature on these plains is an enemy."),
    ("Tauren", "Female", 2): dict(
        label="wodzini",
        brief="Tauren chieftain woman in her forties. A deep, powerful, resonant female voice, ceremonial and "
              "dignified, slow and measured.",
        line="Hear me, people of the plains. The council has spoken. We stand together with our allies, and we "
             "will not be moved from this land."),
    ("Tauren", "Female", 3): dict(
        label="stara wieszczka",
        brief="Elderly tauren seer woman in her seventies. A low, warm, slightly gravelly female voice, slow, wise "
              "and kind, with long thoughtful pauses.",
        line="I have seen this in the smoke, little one. A long road, a great darkness, and a light at the end of "
             "it. Do not be afraid. Walk it anyway."),
    # ------------------------------------------------------------------ Trolls: Caribbean-style lilt
    ("Troll", "Male", 2): dict(
        label="wódz wojenny",
        brief="Troll war-chief in his forties. A deep, booming voice with a strong Caribbean accent and a "
              "commanding, rhythmic delivery; proud and fearless.",
        line="Listen up, mon! We hit dem fast, we hit dem hard, and nobody leaves until da job be done. Da spirits "
             "be watchin' us today!"),
    ("Troll", "Male", 4): dict(
        label="łowca cieni",
        brief="Troll shadow hunter in his thirties. A low, raspy, sly voice with a Caribbean accent, hissing s's "
              "and playful menace.",
        line="Ssshh. Ya hear dat? Da jungle be whisperin' your name, mon. Don't worry, I only be takin' a little "
             "bit of your soul. Just a little."),
    ("Troll", "Female", 1): dict(
        label="radosna łowczyni",
        brief="Troll huntress in her late twenties. A lively, warm voice with a strong Caribbean accent, playful "
              "and confident, quick to laugh.",
        line="Hey, mon, you be slow today! Da raptors already be halfway across da river. Come on, come on, dis da "
             "best part of da hunt!"),
    ("Troll", "Female", 2): dict(
        label="kapłanka-komendantka",
        brief="Troll priestess commander in her forties. A deep, smoky, authoritative female voice with a "
              "Caribbean accent, slow and powerful.",
        line="Da loa demand respect, an' so do I. You stand straight, you speak true, an' you never turn your back "
             "on da enemy. Understood?"),
    ("Troll", "Female", 4): dict(
        label="wiedźma voodoo",
        brief="Troll witch doctor woman in her fifties. A raspy, mysterious, low female voice with a Caribbean "
              "accent and a sly cackle.",
        line="Heh heh heh. Ya want a charm for luck, eh? Dat cost you a feather, a tooth, an' a secret. Da secret "
             "be da expensive part, mon."),
    ("Troll", "Female", 5): dict(
        label="łagodna uzdrowicielka",
        brief="Troll healer woman in her thirties. A warm, soothing, rich female voice with a gentle Caribbean "
              "lilt, calm and caring.",
        line="Shh, shh, lie still now. Mama Zula got you. Dis poultice gonna sting a little, den da pain go away. "
             "You be fine, I promise you."),
}


# ====================================================================== round 4: candidates
# The user's rule: a voice rated 5 is final; for a slot rated below 5 "give me more choices".
# Each pool with open slots gets several candidate characters to choose from (they do not take
# a slot until the user rates them - see candidates.py).
#
# What round 3 taught about women (every female voice rated 1 was "dziwny/nienaturalny"):
# * old age (sixties, seventies), "cracked", "gravelly", "raspy" -> weird, unnatural;
# * "nasal" and "sing-song" -> weird even for gnomes;
# * "sly, raspy, husky, quick" -> "zbyt piskliwa" (too squeaky);
# * every woman rated 5 is an adult of 25-45 with a clear, natural voice whose character comes
#   from accent, pace and attitude (and the line), not from an unusual texture.
# Pitch is not the problem: the liked gnome has the highest median F0 of all (296 Hz).
CANDIDATE_BRIEFS: dict[tuple[str, str], list[dict[str, str]]] = {
    ("Dwarf", "Female"): [
        dict(label="archeolożka",
             brief="Dwarf archaeologist woman of the Explorers' League in her mid thirties. A bright, articulate, "
                   "enthusiastic mezzo voice, quick and eager, a natural storyteller. Edinburgh Scottish accent.",
             line="Look at these carvings! Titan work, I'd stake my best pickaxe on it. Hold the lantern steady, would "
                  "you? I want to sketch every line before this whole cave decides to fall on our heads."),
        dict(label="kowalka",
             brief="Dwarf blacksmith woman in her forties. A strong, full, low mezzo voice, steady and matter-of-fact, "
                   "with dry humour; never in a hurry. Fife Scottish accent.",
             line="Aye, I can fix your sword. Again. Third time this week, is it? Here's a thought: stop hitting rocks "
                  "with it. Come back at sundown, and bring coin, not excuses."),
        dict(label="strzelczyni z gór",
             brief="Dwarf mountaineer and rifle hunter in her late twenties. A clear, confident, steady alto voice, "
                   "calm and patient, with a cheeky smile in it. Aberdeen Scottish accent.",
             line="Quiet now. The ram's just past the ridge, and the wind's in our favour. One shot is all I need. "
                  "Breathe out, squeeze, and... there. Dinner's sorted, and you owe me a pint."),
        dict(label="karczmarka",
             brief="Dwarf tavern keeper woman in her early forties. A warm, full-bodied, motherly mezzo voice, brisk "
                   "and chatty, with a laugh never far away. Soft Glasgow accent.",
             line="Come in, come in, out of the snow with you! Sit yourself by the fire. Stew's hot, ale's cold, and if "
                  "anyone starts a fight in here, I'll be the one finishing it. What'll it be, love?"),
        dict(label="paladynka",
             brief="Dwarf paladin woman in her thirties. A proud, ringing, resonant alto voice, steady and noble, "
                   "speaking with quiet conviction. Scottish accent.",
             line="Stand behind me and keep your head down. The Light has never once failed me, and I've no intention "
                  "of failing it today. When I say go, we charge together. Ready? Then go!"),
        dict(label="brygadzistka górników",
             brief="Dwarf mining foreman woman in her forties. A loud, energetic, bossy voice with a strong chest "
                   "resonance; quick orders and quicker jokes. Geordie accent.",
             line="Right, you lot! Picks up, helmets on, and nobody lights a candle near the gas pocket this time. Yes, "
                  "Bruni, I mean you. Dig till the whistle blows, then the first round's on me!"),
        dict(label="tarczowniczka",
             brief="Dwarf shieldmaiden warrior in her early thirties. A punchy, fierce, confident low mezzo voice, "
                   "short sentences, grinning through every fight. Dundee Scottish accent.",
             line="Ha! Is that all they've got? Shields up, here comes the second wave. Keep the line tight, and save me "
                  "the big one with the ugly helmet. He's mine, and I've been waiting all day."),
        dict(label="jeźdźczyni gryfów",
             brief="Dwarf gryphon rider woman in her late twenties. A lively, warm, clear mid-range voice, "
                   "fast-talking, fearless and fond of a dare. Orkney Scottish lilt.",
             line="Hop on, hold tight, and whatever you do, don't look down. Stormbeak hates screaming. The last fellow "
                  "who screamed ended up in a snowdrift. Ah, he was fine. Mostly fine."),
    ],
    ("Gnome", "Female"): [
        dict(label="mechaniczka tramwaju",
             brief="Gnome mechanic woman in her early thirties. A bright, clear, mid-high adult woman's voice, "
                   "fast-talking and upbeat, with a laugh in it; natural delivery. New Zealand accent.",
             line="Right, so the good news is the tram's running again. The bad news is it only runs backwards. Hop in "
                  "anyway, I'll sort out the gears while we go. What could possibly go wrong?"),
        dict(label="magini",
             brief="Gnome mage woman in her thirties. A warm, bright, confident adult woman's voice with playful "
                   "sarcasm and quick comic timing. Irish accent.",
             line="Stand still, I'm trying to polymorph the troll, not you. Although, honestly, you'd make a lovely "
                  "sheep. Fine, fine. On three. One, two... oh, he moved. Typical."),
        dict(label="uprzejma czarnoksiężniczka",
             brief="Gnome warlock woman in her thirties. A light, sweet, very polite adult woman's voice with a "
                   "mischievous, mock-innocent undertone. Posh English accent.",
             line="Oh, don't mind the imp, he's perfectly harmless. Mostly. He only bites people I don't like. Now, "
                  "would you be a dear and pass me that soul shard? Thank you ever so much."),
        dict(label="szpieg",
             brief="Gnome spy woman in her late twenties. A quick, cheeky, conspiratorial adult woman's voice, kept "
                   "low and confidential but clear. Canadian accent.",
             line="Okay, listen closely, because I'm only saying this once. The guard changes at midnight, the key is in "
                  "his left boot, and the dog loves cheese. Any questions? Good. Go."),
        dict(label="wesoła uzdrowicielka",
             brief="Gnome priest woman in her mid thirties. A kind, cheerful, bubbly and warm adult woman's voice, "
                   "reassuring and gently teasing. Welsh accent.",
             line="Oh, look at you, all singed eyebrows and a big brave face. Come here, let me have a look. A bit of "
                  "Light, a cup of tea, and you'll be blowing things up again in no time."),
        dict(label="pilotka",
             brief="Gnome pilot woman in her late twenties. A bold, brash, excited adult woman's voice, clear and "
                   "bright, full of daredevil confidence. Californian accent.",
             line="Strap in, the flying machine is fully tested! Well, it flew once. Upside down, but it flew. Goggles "
                  "on, hold onto something heavy, and if we crash, it was the wind's fault."),
        dict(label="alchemiczka",
             brief="Gnome alchemist woman in her early thirties. A precise but excitable, rapid-fire, nerdy adult "
                   "woman's voice with crisp diction. Manchester accent.",
             line="Don't touch the green one. Or the blue one. Actually, don't touch anything. Right, a pinch of "
                  "mageroyal and it should turn purple. It's turning orange. That's new. Duck!"),
        dict(label="handlarka",
             brief="Gnome merchant woman in her forties. A bubbly, persuasive, chatty, warm adult woman's voice with "
                   "a quick sales patter. Brooklyn accent.",
             line="Honey, you need this. A self-stirring spoon, fully clockwork, only blew up twice in testing. For "
                  "you? Ten silver. Fine, eight, but only because you've got an honest face. Deal?"),
        dict(label="szefowa warsztatu",
             brief="Gnome workshop chief woman in her forties. A warm, brisk, organised adult woman's voice, bossy "
                   "in a kind way and completely unflappable. South African accent.",
             line="Goggles on, everyone. Fizzbolt, that wrench goes back on the wall, not in your pocket. We test the "
                  "boiler at noon, and this time we all stand behind the line. Yes, you too."),
        dict(label="zadziorna wojowniczka",
             brief="Gnome warrior woman in her thirties. A feisty, punchy, fearless adult woman's voice, strong and "
                   "clear, much louder than you'd expect. Southern American accent.",
             line="Oh, you think I'm too small to tank? Watch this. Hey, ugly! Yeah, you, with the club! Come and get "
                  "me! See? Works every time. Now hit him while he's busy with me."),
        dict(label="odkrywczyni",
             brief="Gnome explorer woman in her late twenties. A curious, upbeat, clear adult woman's voice, warm and "
                   "quick, full of wonder. General American accent.",
             line="Oh, wow. Come and look at this! The whole cavern is glowing. Some kind of crystal, I think. I've "
                  "never seen anything like it. Write this down, quickly, before it stops."),
        dict(label="bajarka",
             brief="Gnome storyteller woman in her thirties. An expressive, theatrical, warm adult woman's voice with "
                   "lively, playful timing and a twinkle in it. Northern Irish accent.",
             line="And then, my friends, the dragon looked down at our little gnome and said, I'll eat you in one bite. "
                  "And she said, you could try, big fella, but I'm full of springs. True story."),
    ],
    ("Human", "Female"): [
        dict(label="paladynka",
             brief="Human paladin woman in her mid thirties. A clear, resonant, noble mezzo voice, earnest and steady, "
                   "with calm conviction. Received Pronunciation English accent.",
             line="Hold the line. Whatever comes through that gate, it does not pass us. The Light is with us, and so "
                  "am I. Heal the wounded, guard the weak, and keep your shields high."),
        dict(label="karczmarka",
             brief="Human innkeeper woman in her early forties. A warm, friendly, full voice, chatty and motherly but "
                   "brisk. Yorkshire accent.",
             line="Mind the step, love, and shut that door, you're letting the cold in. Room's upstairs, second on the "
                  "left, breakfast at dawn. And no dancing on the tables this time, I mean it."),
        dict(label="magini z Dalaranu",
             brief="Human mage woman of Dalaran in her early thirties. A cool, articulate, precise voice, a little "
                   "aloof, with dry wit. Posh English accent.",
             line="Please don't touch the floating books, they bite. No, really. Now, the portal to Stormwind opens in "
                  "two minutes. Do try to arrive with all your limbs. It reflects poorly on me otherwise."),
        dict(label="farmerka z Westfall",
             brief="Human farm woman from Westfall in her late twenties. A plainspoken, steady, warm voice, a little "
                   "tired but stubborn. General American accent.",
             line="The Defias burned the north field again last night. That's the third time this month. I'm not "
                  "leaving. This is our land. So, are you here to help, or just passing through?"),
        dict(label="bardka",
             brief="Human bard woman in her early thirties. A bright, expressive, charming voice with theatrical "
                   "flair and a wink in every line. Welsh accent.",
             line="Gather round, gather round! Tonight's tale is a true one, I swear it: how a single paladin, a goat, "
                  "and one very confused murloc saved Goldshire. Coins in the hat, please. Thank you, sir!"),
    ],
    ("NightElf", "Female"): [
        dict(label="kapitan Strażniczek",
             brief="Night elf Sentinel captain around thirty. A cool, low, calm mezzo voice with quiet authority; she "
                   "never needs to raise it. Soft French-tinged accent.",
             line="We move at moonrise. The ridge must be cleared before dawn. Keep to the shadows, strike once, and do "
                  "not let the fires of their camp see your face. Go, and may Elune guide you."),
        dict(label="strażniczka więzienia",
             brief="Night elf warden woman around thirty. A cold, quiet, intense low alto voice, clipped and precise, "
                   "with no warmth to spare. Received Pronunciation English accent.",
             line="For ten thousand years I have guarded the prisoners of this vault. Do you know how many escaped? "
                  "None. Step aside. The hunt is mine, and I do not share it with anyone."),
        dict(label="wojowniczka Sentinel",
             brief="Night elf Sentinel warrior around twenty-five. A firm, strong, clear voice, battle-ready and "
                   "direct, with a confident grin. Welsh accent.",
             line="Glaives ready! They're coming through the trees. Stay close to me and watch your left side. I've got "
                  "your back, sister. Now let's show them what Ashenvale is made of!"),
        dict(label="opiekunka lasu",
             brief="Night elf druid keeper around twenty-eight. A warm, deep, grounded alto voice, unhurried and wise "
                   "beyond her years. Icelandic-tinged accent.",
             line="The forest remembers everything. Every footstep, every fire, every broken branch. Walk gently here, "
                  "little one. The ancients are sleeping, and they wake up in a very bad mood."),
        dict(label="łowczyni demonów",
             brief="Night elf demon hunter woman around thirty. A low, smoky, intense voice, dry and sardonic, with "
                   "controlled menace. Neutral American accent.",
             line="You think you know darkness? I gave my eyes to hunt it. Stay behind me, keep quiet, and if you see "
                  "something with horns, don't introduce yourself. Just run. Fast."),
    ],
}


# ====================================================================== round 5: more candidates
# What the round-4 ratings added:
# * the tauren seer "in her seventies" was rejected too: no old women, whatever the race;
# * all four round-3 troll women (each speaking a line spelled in dialect: "da", "mon", "be")
#   were rejected, while the round-2 troll matron - described by her body ("lanky jungle troll
#   woman with tusks") and speaking a plain line - was kept.  New trolls describe the body,
#   vary the Caribbean accent and mostly speak plain English; the accent comes from the brief;
# * Skyborne women: "husky, fierce" and "sly, smoky, teasing" were rejected, the kept ones are
#   clear, poised or warm;
# * men: the orc "menacing raider" and the soft, friendly tauren hunter were rejected - the
#   kept men are each a strong, distinct character (fury, command, age, wit, gentleness).
CANDIDATE_BRIEFS.update({
    ("Orc", "Male"): [
        dict(label="szaman żywiołów",
             brief="Orc shaman in his fifties. A deep, resonant, rough-edged voice with calm spiritual gravity; he "
                   "speaks slowly and reverently about the elements, never shouting. Low, earthy delivery.",
             line="Listen. The earth is restless today, and the wind carries smoke from the south. The spirits do not "
                  "lie, young one. Sharpen your axe, but first, give thanks to the fire that warms you."),
        dict(label="kowal z Orgrimmaru",
             brief="Orc blacksmith in his forties. A gruff, deep, matter-of-fact baritone, blunt and practical with "
                   "dry humour; short, heavy sentences, unimpressed by everything.",
             line="This blade? Cheap human steel. It bends like grass. Leave it here. Come back tomorrow, and I will "
                  "give you something that bites. Do not haggle with me. I do not haggle."),
        dict(label="czarnoksiężnik",
             brief="Orc warlock in his forties. A low, smooth, sinister baritone, quiet and calculating, every word "
                   "dripping with dark confidence; he never raises his voice.",
             line="You fear the shadows? How quaint. I have bargained with things that would swallow your little "
                  "village whole. Bring me the shard, and I may let you keep your soul. Most of it."),
        dict(label="wesoły grunt",
             brief="Orc grunt in his thirties. A big, hearty, boisterous bass-baritone, loud and friendly, with "
                   "booming laughter and simple, cheerful bravado.",
             line="Ha! You fight good, for a tiny one! Come, sit, eat! Roasted boar, still warm. Tomorrow we smash "
                  "more quilboars, yes? Pass the grog before Grukk drinks it all!"),
        dict(label="milczący łowca",
             brief="Orc hunter in his thirties. A low, rough, terse voice, speaking quietly and economically, patient "
                   "and watchful like a predator; deep and calm.",
             line="Quiet. Tracks, here. Two raptors, maybe three. Fresh. We wait downwind until they drink. Then one "
                  "arrow each. Do not miss. If you miss, run. I will not carry you."),
    ],
    ("Tauren", "Male"): [
        dict(label="słoneczny wojownik",
             brief="Tauren sunwalker paladin in his thirties. A powerful, noble, resonant bass-baritone, earnest and "
                   "proud, clear and strong; he speaks with unshakable conviction.",
             line="Stand firm, friends. The sun has risen over Mulgore every morning since the dawn of the world, and "
                  "it will rise again tomorrow. Hold the line with me, and we will see it together."),
        dict(label="zapalony wojownik",
             brief="Tauren warrior in his early thirties. A big, booming, enthusiastic bass voice, loud and cheerful, "
                   "eager for every fight, laughing as he charges.",
             line="Hah! Did you see that? I knocked him clear across the river! Come on, there are more of them over "
                  "the hill. Last one there carries the supplies back to camp!"),
        dict(label="surowy strażnik",
             brief="Tauren guard of Thunder Bluff in his forties. A stern, deep, no-nonsense voice, terse and "
                   "watchful, low and steady, with clipped answers.",
             line="State your business. The lift runs until sundown, no later. Keep your weapons sheathed on the "
                  "bluffs, and do not feed the kodos. They bite. Move along."),
        dict(label="gawędziarz",
             brief="Tauren storyteller in his fifties. A deep, warm, expressive bass with dramatic pauses, theatrical "
                   "and wise, enjoying every word of his tale.",
             line="Come closer to the fire, little ones. Long ago, before the first kodo walked these plains, the "
                  "Earth Mother sang the world awake. And do you know what she sang first? Rain."),
        dict(label="szaman burzy",
             brief="Tauren storm shaman in his forties. An intense, rough, powerful bass voice, urgent and fierce, as "
                   "if calling down the thunder itself; clear diction.",
             line="Can you feel it? The sky is angry. Stand behind me, and do not let go of the totem, no matter what "
                  "you hear. When I raise my hands, the storm will answer. Now!"),
    ],
    ("Tauren", "Female"): [
        dict(label="wojowniczka",
             brief="Tauren warrior woman in her thirties. A strong, bold, loud alto, fierce and proud, with "
                   "battle-ready energy and a clear, ringing call.",
             line="Shields up! They are charging from the ridge! Hold your ground beside me, and do not take one step "
                  "back. Mulgore does not fall today. Not while I still stand!"),
        dict(label="druidka",
             brief="Tauren druid woman in her thirties. A warm, earthy, gently amused mezzo-alto, unhurried and kind, "
                   "with a soft laugh never far away.",
             line="Oh, don't be afraid of the bear. That was just me, five minutes ago. Ha! Come, the herbs in this "
                  "valley are perfect after the rain. Help me gather some, and mind the thorns."),
        dict(label="zwiadowczyni",
             brief="Tauren scout woman in her late twenties. A clear, steady, alert alto, quick and practical, with "
                   "low-key humour; she reports like a soldier.",
             line="Three centaur camps past the river, maybe twenty riders. They post no guards at night, the fools. "
                  "We go in quiet, take the totem, and we are back before the moon sets."),
        dict(label="kupcowa z Thunder Bluff",
             brief="Tauren trader woman in her forties. A hearty, friendly, warm alto with a booming laugh, chatty "
                   "and good at bargaining, full of life.",
             line="Welcome, welcome! Finest kodo leather on the whole bluff, I promise you. Feel that. Soft as a "
                  "calf's ear. For you, a fair price. For your friend there, well, we will see."),
        dict(label="szamanka wiatru",
             brief="Tauren shaman woman in her forties. A deep, calm, softly intense alto, mystical yet grounded, as "
                   "if listening to the wind while she speaks.",
             line="Hush. The wind is speaking. It says the herds are moving north, and something follows them. "
                  "Something hungry. We should leave before the stars come out."),
    ],
    ("Troll", "Female"): [
        dict(label="łowczyni z dżungli",
             brief="Tall, lanky jungle troll woman with tusks, a huntress in her thirties. A confident, relaxed, warm "
                   "voice with an easy Jamaican lilt; cool and teasing, never in a hurry.",
             line="Easy now, mon. The raptor won't bite if you stop waving your arms like that. Slow. Watch me. See? "
                  "She likes me already. Now you try, and don't be scared."),
        dict(label="kapłanka loa",
             brief="Tall jungle troll woman with tusks, a priestess of the loa in her forties. A rich, low, hypnotic "
                   "voice with a Haitian Creole-tinged accent; calm, deliberate and mysterious, but warm.",
             line="Sit with me by the fire. The spirits are close tonight, can you feel them? Do not speak. Just "
                  "listen. They will tell us which path is safe, and which path is hungry."),
        dict(label="wojowniczka z wysp",
             brief="Lanky jungle troll warrior woman with tusks, in her late twenties. A bold, punchy, energetic voice "
                   "with a strong Trinidadian accent; fearless, loud and quick to grin.",
             line="Come on, come on! Is that all you got? I been fighting since I could walk! Swing harder, or go home "
                  "to your mama. Ha! Again! This time, mean it!"),
        dict(label="handlarka z Sen'jin",
             brief="Jungle troll trader woman with tusks, in her forties. A bright, chatty, haggling voice with a "
                   "Bajan accent; quick, funny and impossible to out-talk.",
             line="Ah, ah, don't walk away, friend! You didn't even see the good stuff yet. Look here, a real shrunken "
                  "head, very rare, very lucky. For you? Half price. Okay, almost half."),
        dict(label="szamanka",
             brief="Tall jungle troll shaman woman with tusks, in her fifties. A deep, grounded, smoky alto with a slow "
                   "Jamaican accent; wise, patient and quietly amused.",
             line="Patience. The storm doesn't come because you shout at the sky. You ask nicely, you wait, you "
                  "listen. There. You hear the thunder? Now she's ready to talk."),
        dict(label="zielarka",
             brief="Jungle troll herbalist woman with small tusks, in her thirties. A soft, warm, kind voice with a "
                   "gentle Caribbean lilt and a playful sense of humour.",
             line="Chew this leaf, but slowly. It tastes like old boots, I know, but your fever will be gone by "
                  "morning. And next time, maybe don't drink from the swamp. Just a thought."),
        dict(label="łotrzyca",
             brief="Lanky jungle troll rogue woman with tusks, in her thirties. A low, silky, quiet voice with a "
                   "Jamaican accent, dangerous and half-amused, every word slow and deliberate.",
             line="Shh. You walk like a kodo, you know that? Every guard on the wall can hear you. Stay behind me, "
                  "step where I step, and keep that pretty sword in its sheath."),
        dict(label="magini",
             brief="Jungle troll mage woman with tusks, in her early thirties. A clear, sharp, confident voice with a "
                   "light Caribbean accent; quick-witted, sarcastic and a little smug.",
             line="No, no, no. You don't throw the fireball, you guide it. Like this. See? Nobody's eyebrows got "
                  "burned this time. Well, except yours. They'll grow back. Probably."),
        dict(label="tropicielka",
             brief="Lanky jungle troll scout woman with tusks, in her late twenties. A quiet, alert, clipped voice "
                   "with a Jamaican accent; focused, practical and calm under pressure.",
             line="Two patrols on the north path, one by the river. They change at dusk. We cross when the drums stop, "
                  "not before. Stay low, stay quiet, and keep up with me."),
        dict(label="matka wojowników",
             brief="Tall jungle troll war mother with tusks, in her forties. A strong, commanding but warm voice with a "
                   "slow Caribbean drawl; loud, protective and proud of her warriors.",
             line="Listen to me, all of you! Eat now, sleep early, and check your spears twice. Tomorrow we take back "
                  "the beach. And I want every one of you back here by sundown. Every one!"),
        dict(label="wiedźma z bagien",
             brief="Tall jungle troll witch doctor woman with tusks, in her fifties. A deep, slow, theatrical voice "
                   "with a Haitian Creole-tinged accent and a low, knowing laugh.",
             line="Ah, you came back. They always come back. The charm worked, yes? Of course it did. Now, about my "
                  "payment. I don't want gold, friend. I want a favour. A small one."),
        dict(label="kapitan łodzi",
             brief="Jungle troll boat captain woman with tusks, in her forties. A strong, salty, cheerful voice with a "
                   "Guyanese accent, loud over the waves, bossy but funny.",
             line="All aboard, quick quick! The tide won't wait for you, and neither will I. Sit down in the middle, "
                  "hold the rope, and if you get sick, do it over the side, not on my deck!"),
    ],
    ("Skyborne", "Male"): [
        dict(label="kapitan podniebnych piratów",
             brief="Skyborne sky-pirate captain in his thirties. A charming, roguish, warm baritone, swaggering and "
                   "humorous, always sounding like he is about to wink. Irish accent.",
             line="Welcome aboard the Gale Dancer, friend! Mind the rigging, mind the cannons, and above all, mind the "
                  "edge. It's a long way down, and the clouds are not as soft as they look."),
        dict(label="mnich wiatru",
             brief="Skyborne wind monk in his forties. A calm, centred, soft-spoken but clear baritone, measured and "
                   "kind, with long thoughtful pauses. Scandinavian-tinged accent.",
             line="Breathe in. Feel how the wind moves around you, never against you. That is the lesson. Do not "
                  "fight the storm. Become the space it passes through. Now, again, slower."),
        dict(label="łowca burz",
             brief="Skyborne storm hunter in his thirties. An intense, low, slightly rough baritone, terse and "
                   "focused, a man of few words. Scottish accent.",
             line="Storm's coming in from the east. Big one. We fly into it, not around it. That's where the "
                  "lightning drakes nest. Strap in, keep your head down, and don't look at the sky."),
        dict(label="rycerz High Order",
             brief="Skyborne High Order knight in his thirties. A noble, resonant, earnest baritone, proud and "
                   "dutiful, warm rather than cold. Standard Southern English accent.",
             line="The High Order does not abandon its people. Not to the storm, not to the void, not to anyone. Take "
                  "my shield if you need it. I will stand in the open, and I will not fall."),
        dict(label="handlarz chmur",
             brief="Skyborne cloud merchant in his forties. A smooth, friendly, persuasive mid-low voice, chatty and "
                   "quick, a born salesman. General American accent.",
             line="Ah, a traveller from the ground! You'll want a feather cloak, believe me. Up here, the wind steals "
                  "everything that isn't tied down. Including your hat. Especially your hat."),
    ],
    ("Skyborne", "Female"): [
        dict(label="kapitan floty powietrznej",
             brief="Skyborne sky-fleet captain woman in her thirties. A confident, clear, commanding alto, crisp and "
                   "decisive, with dry humour. Australian accent.",
             line="All hands, listen up. We're cutting through the storm wall, so tie down anything you love. "
                  "Including yourselves. Helm, hard to port on my mark. And try to look like you've done this before."),
        dict(label="zwiadowczyni wiatru",
             brief="Skyborne wind scout woman in her late twenties. A light, quick, adventurous voice, upbeat and "
                   "curious, always on the move. General American accent.",
             line="Okay, so the good news is I found the lost island. The bad news is it's moving. Like, actually "
                  "drifting. Grab your gear, we've got maybe an hour before it floats out of reach!"),
        dict(label="astronomka",
             brief="Skyborne astronomer woman in her thirties. A soft-spoken, thoughtful, warm alto, dreamy but clear, "
                   "slow and fascinated by everything. French-tinged accent.",
             line="Look up. No, higher. Do you see that faint blue star, just above the moon? It moves a little every "
                  "night. I don't think it's a star at all. I think something is coming closer."),
        dict(label="wojowniczka chmur",
             brief="Skyborne cloud warrior woman in her thirties. A strong, steady, low mezzo, disciplined and fierce "
                   "but controlled, no wasted words. Scandinavian-tinged accent.",
             line="Form up. Wings out, blades low. When they dive, we rise. When they scatter, we hunt them one by one. "
                  "No heroics. Return alive, or don't bother returning at all."),
        dict(label="inżynierka sterowców",
             brief="Skyborne airship engineer woman in her late twenties. A cheerful, practical, energetic voice, "
                   "fast-talking and hands-on, laughing at her own mistakes. Yorkshire accent.",
             line="Right, so that bang? Totally normal. Mostly. The left engine just sneezed. I'll have her purring in "
                  "five minutes, just hand me the big wrench. No, the bigger one. Lovely."),
        dict(label="przemytniczka",
             brief="Skyborne smuggler woman in her thirties. A low, cool, amused voice, laid-back and confident, never "
                   "in a rush. South African accent.",
             line="Relax. Nobody checks the cargo hold of a fishing skiff. Especially not one that smells this bad. Sit "
                  "tight next to the crates, and if anyone asks, you're my cousin. A quiet one."),
        dict(label="kapłanka burzy",
             brief="Skyborne storm priestess in her thirties. A clear, resonant, passionate mezzo, intense but "
                   "controlled, speaking with quiet fire. Welsh accent.",
             line="The storm is not our enemy. It is our oldest friend, and our harshest teacher. Raise your hands "
                  "with me. Feel the charge in the air. Now, let it go, all at once!"),
    ],
})


# ---------------------------------------------------------------------- undead, round 5
# The user (8 Nov): "undead to takie zombi, ma rozjebany głos, nawet trochę zza światów" - the
# undead are zombies with wrecked voices, even a little otherworldly.  So: rotting, gurgling,
# rattling throats, and - for undead only - a hollow, ghostly, from-beyond-the-grave colour, which
# elsewhere ("ethereal, echoing") made voices odd.  Age limits do not apply to corpses.
CANDIDATE_BRIEFS.update({
    ("Undead", "Male"): [
        dict(label="gnijące gardło",
             brief="Undead man in his forties, a rotting corpse risen from the grave. A wet, gurgling, ragged voice "
                   "through decayed vocal cords, every word dragged out of a ruined throat, with a rattling breath "
                   "between phrases. Bitter and cold. Standard English accent.",
             line="Do not stare. Yes, the jaw is loose, and yes, something lives in my ribs now. I stopped caring "
                  "years ago. The grave was cold, but the living are colder. What do you want?"),
        dict(label="szept z krypty",
             brief="Undead crypt keeper in his fifties. A hoarse, hollow, sepulchral voice with a faint ghostly "
                   "resonance, as if speaking from inside a stone tomb; slow, quiet and unsettling, but every word "
                   "clear. Refined British accent.",
             line="Welcome to the crypt, traveller. Mind the bones, they bruise easily. The dead do not sleep here. "
                  "They wait. And they listen. Every word you say, they hear. Choose them carefully."),
        dict(label="zombi-wojownik",
             brief="Undead warrior in his thirties, half rotten and freshly risen. A brutal, distorted, gravelly roar "
                   "of a voice, torn and wet, shouting through a broken throat; savage and hungry.",
             line="Flesh! Fresh flesh! I can smell your fear from here, living one. My blade is rusted, my heart is "
                  "dust, but my arm still swings. Come closer. Let me show you."),
        dict(label="plagowy aptekarz",
             brief="Undead plague apothecary in his fifties. A dry, rattling, wheezing voice like dust in a broken "
                   "bellows, with a wet cough and a cruel, gurgling chuckle; precise and cold. Clipped British accent.",
             line="Breathe deeply. Go on. Ah, you noticed the green mist? A little gift from the apothecaries. It will "
                  "only hurt for a moment. Well, a long moment. Do write that down."),
        dict(label="upiorny rycerz",
             brief="Undead death knight in his forties. A deep, cold, hollow voice with a ghostly, otherworldly "
                   "reverberation, as if two voices speak at once, rough with decay underneath; slow, commanding and "
                   "merciless.",
             line="Kneel. I have marched through ice and fire, and I have forgotten what fear was. Today you will "
                  "learn what I learned. The cold does not forgive, and neither do I. Rise, or rot."),
        dict(label="zgniły grabarz",
             brief="Undead gravedigger in his sixties. A croaking, slurred, wet voice through a half-rotten jaw, "
                   "mumbling yet understandable, darkly cheerful. Rural English accent.",
             line="Another one for the pit, eh? Don't mind me. I dig them, I fill them, sometimes they climb back out. "
                  "Like I did! Ha. Hand me the shovel, would you? The arm's not what it was."),
        dict(label="rozkładający się mag",
             brief="Undead mage in his forties. A sharp, ragged, rasping voice cracking with decay, venomous and "
                   "arrogant, every s hissing like steam escaping a corpse. Upper-class English accent.",
             line="Fire, frost, shadow, it makes no difference to me now. I cannot feel any of them. Stand back, "
                  "unless you wish to learn how quickly the living burn. Very quickly, I promise."),
        dict(label="zszyty męczennik",
             brief="Undead man in his thirties, tortured and stitched back together. A strained, broken, groaning "
                   "voice full of agony, words breaking into painful gasps and wet rattles; desperate and haunted.",
             line="It hurts. Every step, every breath I don't even need, it hurts. They stitched me back together "
                  "wrong. If you find the one who did this, send him to me. I only want to talk."),
        dict(label="zgniły łotr",
             brief="Undead rogue in his forties. A sneering, rotten, raspy hiss of a voice with a wet, clicking jaw "
                   "and a nasty gurgling chuckle; quick and sarcastic. London accent.",
             line="Psst. Over here, in the shadows. No, lower. That's it. You want a quiet way into the keep? Twenty "
                  "gold. Or your left hand. I'm not picky. I've got spares."),
        dict(label="głos spod ziemi",
             brief="Undead spirit-binder in his fifties. A low, hollow, distant voice that sounds like it comes from "
                   "deep underground, rough and decayed, with a faint ghostly echo after each phrase; eerie and calm.",
             line="The dead whisper to me. All of them, all the time. Your grandfather says hello. He also says you "
                  "still owe him three silver. I would pay him, if I were you. He is very patient."),
    ],
    ("Undead", "Female"): [
        dict(label="gnijąca wdowa",
             brief="Undead woman in her forties, a rotting corpse risen from the grave. A wet, gurgling, ragged voice "
                   "through decayed vocal cords, each word dragged out of a ruined throat, with a rattling breath. "
                   "Cold and bitter. Standard English accent.",
             line="Don't flinch. I know what I look like. The worms took my face, but they left my memory. I remember "
                  "who put me in that grave, and I am going to visit him tonight."),
        dict(label="banshee",
             brief="Undead banshee woman in her thirties. A hollow, mournful, otherworldly voice with a faint ghostly "
                   "echo, hoarse and torn underneath; slow, haunting and sad, but every word clear.",
             line="I used to sing in the old cathedral, before the plague. Now my voice is ash and broken glass. Stay a "
                  "while, traveller. The dead get lonely, and you have such a warm heartbeat."),
        dict(label="plagowa alchemiczka",
             brief="Undead plague alchemist woman in her forties. A dry, rasping, wheezing voice like a broken "
                   "bellows, cold and clinical, with a wet cough and a thin, cruel laugh. Clipped British accent.",
             line="Hold still. This needle is clean. Well, cleaner than you. The serum will either cure the rot or "
                  "speed it up. Fascinating either way. Now count backwards from ten. Out loud."),
        dict(label="żołnierka z rozciętym gardłem",
             brief="Undead soldier woman in her thirties, throat slashed and crudely stitched. A harsh, strained, "
                   "painful rasp forced out in short bursts, fierce and grim.",
             line="Line up. Shields together. The living tire, the living bleed, the living run. We do not. Hold this "
                  "bridge until the sun comes up, and then hold it some more."),
        dict(label="obłąkana ghulica",
             brief="Undead woman in her thirties, half mad from the grave. A cracked, distorted, gurgling voice that "
                   "swings between a hiss and a rough giggle; unsettling and unpredictable.",
             line="Oh, you're warm. You're so warm. Can I touch your hand? No? Fine, fine. I'll just walk next to you. "
                  "Very close. Don't mind the smell. I don't notice it anymore."),
        dict(label="mroczna łowczyni",
             brief="Undead dark ranger woman in her forties. A cold, low, hollow voice with a ghostly undertone, dry "
                   "and rasping, calm and lethal, never raised.",
             line="You hesitate. The living always hesitate. I stopped hesitating the day they buried me. Nock the "
                  "arrow. Breathe out. And remember, they would not hesitate to kill you."),
        dict(label="stara grabarka",
             brief="Undead gravedigger woman in her sixties. A croaking, slurred, wet voice through a loose, rotting "
                   "jaw, darkly cheerful and gossipy. Rural English accent.",
             line="Oh, another fresh one! Lovely. Don't you worry, dear, the first century is the hardest. After that "
                  "you stop noticing the maggots. Mostly. Here, have a biscuit. It's only a bit mouldy."),
        dict(label="duch ze studni",
             brief="Undead spirit woman in her fifties. A faint, hollow, sepulchral voice with a ghostly echo, as if "
                   "she speaks from the bottom of a well, rough with decay underneath; slow and eerie.",
             line="Down here nobody counts the years. Only the drops of water from the ceiling. One, two, three. Do you "
                  "hear them? I have heard every single one. Come down. Count them with me."),
    ],
})


# ---------------------------------------------------------------------- the assistant's own voice
# The user (8 Nov): "daj mu głos takiej sexy dziewczyny młodej, takiej charyzmatycznej".  She speaks
# Polish to you, so the preview lines are Polish and the briefs ask for a native Polish speaker -
# Voice Design (eleven_ttv_v3) then builds a voice that is natural in Polish, not an accent.
CANDIDATE_BRIEFS.update({
    ("Assistant", "Female"): [
        dict(label="aksamitna",
             brief="Polish woman in her mid twenties with a smooth, velvety, slightly husky mezzo voice; confident, "
                   "warm and alluring, with a teasing smile in every word. Native speaker of standard Polish, "
                   "intimate but perfectly clear, as if talking close to the microphone.",
             line="Mmm, wróciłeś. Już myślałam, że o mnie zapomniałeś. Czterech chętnych pisze inv, mam ich "
                  "zaprosić? Wystarczy, że wciśniesz Enter, a ja zajmę się resztą."),
        dict(label="zadziorna",
             brief="Polish woman around twenty-five with a bright, playful, sassy voice; quick, witty and flirtatious, "
                   "laughing easily, full of energy. Native speaker of standard Polish, natural modern delivery.",
             line="Serio? Znowu zginąłeś na tym samym bossie? Dobra, nie oceniam. Trzech chętnych pisze inv, wciśnij "
                  "Enter i zrobimy z nich drużynę marzeń."),
        dict(label="nocna prowadząca",
             brief="Polish woman in her late twenties with a low, warm, sultry voice like a late-night radio host; "
                   "slow, relaxed and seductive, every word deliberate. Native speaker of standard Polish.",
             line="Dobry wieczór, wojowniku. Na czacie spokojnie, w torbach dwanaście wolnych miejsc, a złota masz "
                  "akurat tyle, żeby postawić mi drinka w Goldshire. Co dalej?"),
        dict(label="streamerka",
             brief="Polish gamer girl in her mid twenties with an energetic, charismatic, friendly voice; expressive, "
                   "fun and a little cheeky, like a popular streamer. Native speaker of standard Polish, casual.",
             line="Dobra, słuchaj! Ktoś szepcze, że ma dla ciebie miecz za dwadzieścia złota. Odpisać mu, że bierzesz? "
                  "Mów szybko, zanim ktoś nas ubiegnie!"),
        dict(label="elegancka",
             brief="Polish woman in her late twenties with a refined, elegant, softly husky voice; calm confidence and "
                   "a hint of mischief, never in a hurry. Native speaker of standard Polish, clear diction.",
             line="Wszystko gotowe. Lider przekazany, ready check czeka na twój Enter. A potem możemy porozmawiać o "
                  "tym, jak dobrze ci dzisiaj w tej zbroi."),
        dict(label="ciepła i słodka",
             brief="Polish woman around twenty-five with a sweet, warm, gentle voice; affectionate and charming, "
                   "smiling while she talks, a little shy and playful. Native speaker of standard Polish.",
             line="Hej, jestem tutaj. Nikt do ciebie nie pisał, więc mam cię tylko dla siebie. Powiedz, czego "
                  "potrzebujesz, a zaraz się tym zajmę."),
    ],
})


# ---------------------------------------------------------------------- orc women, round 6
# Unlike the Alliance women, the orc women the user kept are rough: a raspy warrior, a gravelly
# war-captain, a cracked old matron, an old shaman like grinding stones.  The one rejected was the
# "husky, growling, sly and dangerous" huntress - so: rough, strong and direct, never sly or growling.
CANDIDATE_BRIEFS.update({
    ("Orc", "Female"): [
        dict(label="jeźdźczyni wilków",
             brief="Orc wolf rider woman in her thirties. A strong, rough, lively alto voice, loud and confident, "
                   "with a laugh like a bark; free and wild, every word clear.",
             line="Hold on tight! Snowfang hates strangers, but she loves speed even more. We ride to the Crossroads "
                  "before nightfall. If you fall off, I am not stopping!"),
        dict(label="karczmarka z Orgrimmaru",
             brief="Orc innkeeper woman in her forties. A deep, gravelly, warm voice, loud and bossy but friendly, "
                   "with a hearty laugh.",
             line="Sit, eat, drink! The boar is fresh and the grog is strong. Break a table and you pay for two. And "
                  "stop staring at the troll in the corner, he bites."),
        dict(label="kowalka",
             brief="Orc blacksmith woman in her thirties. A low, rough, steady voice, blunt and practical, few words, "
                   "each one like a hammer on an anvil.",
             line="Your axe is dull. Your armor is cracked. You fight like a peon. Leave them here. Tomorrow you will "
                  "have steel worthy of the Horde. Do not be late."),
        dict(label="zabójczyni",
             brief="Orc assassin woman in her thirties. A low, rough, quiet voice, cold and controlled, calmly "
                   "menacing; slow, clear words.",
             line="Quiet. The target sleeps in the second tent. Two guards, both drunk. I go first, you watch the "
                  "path. If anyone screams tonight, it will not be me."),
        dict(label="łowczyni z Durotaru",
             brief="Orc huntress in her late twenties. A strong, earthy, clear alto with a rough edge, calm and "
                   "patient, proud of her prey.",
             line="Three days I tracked this kodo across the Barrens. Three days! Tonight the whole camp eats well, and "
                  "tomorrow I hunt something bigger. Come with me, if you can keep up."),
    ],
})
