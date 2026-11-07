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
