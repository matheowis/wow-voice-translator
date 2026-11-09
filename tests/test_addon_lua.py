"""Runs the real addon Lua (Protocol.lua + Core.lua) in Lua 5.1 with a mocked WoW API.

The textures the addon colours are turned back into an image and decoded with the
Python decoder, so this proves the in-game encoder and the desktop decoder agree.
"""

import pytest

from tests.conftest import ADDON_DIR
from wvt import protocol as P

lupa = pytest.importorskip("lupa")
try:
    from lupa.lua51 import LuaRuntime  # WoW uses Lua 5.1
except ImportError:  # pragma: no cover
    from lupa import LuaRuntime

MOCK = r"""
local mock = { time = 100, prints = {}, secret = {}, mouseLine = 2, frames = {},
               units = { player = "Player-1-ME" }, players = {}, dist2 = {}, interact = {}, languages = {} }
_G.WVT_MOCK = mock

function GetTime() return mock.time end
function GetPhysicalScreenSize() return 2560, 1440 end
function issecretvalue(v) return mock.secret[v] == true end
function issecrettable(v) return false end
strmatch = string.match
strlower = string.lower
strfind = string.find
strupper = string.upper
SlashCmdList = {}

-- unit / player info used by the voice relay
function UnitGUID(unit) return mock.units[unit] end
function GetPlayerInfoByGUID(guid)
  local p = mock.players[guid]
  if p then return "Wojownik", "WARRIOR", "LocRace", p.race, p.sex, p.name, "Forever" end
end
function UnitDistanceSquared(unit)
  local d = mock.dist2[unit]
  if d then return d, true end
  return 0, false
end
function UnitPosition(unit) return nil end
function CheckInteractDistance(unit, idx)
  local t = mock.interact[unit]
  return t ~= nil and t[idx] == true
end
C_ChatInfo = { CanPlayerSpeakLanguage = function(id) return mock.languages[id] ~= false end }
function IsShiftKeyDown() return false end

-- any frame/region method the tests do not care about is a no-op
local noop = function() end
local fallback = { __index = function() return noop end }

local function Region()
  local r = setmetatable({ shown = true, points = {}, w = 0, h = 0, scale = 1 }, fallback)
  function r:SetPoint(...) self.points[#self.points + 1] = { ... } end
  function r:ClearAllPoints() self.points = {} end
  function r:SetSize(w, h) self.w, self.h = w, h end
  function r:Show() self.shown = true end
  function r:Hide() self.shown = false end
  function r:IsShown() return self.shown end
  function r:IsVisible() return self.shown end
  function r:GetEffectiveScale() return self.scale end
  return r
end

function CreateFrame(kind, name, parent)
  local f = Region()
  f.scripts, f.events, f.textures = {}, {}, {}
  function f:SetFrameStrata() end
  function f:SetFrameLevel() end
  function f:SetToplevel() end
  function f:SetIgnoreParentScale() end
  function f:SetIgnoreParentAlpha() end
  function f:EnableMouse() end
  function f:SetScript(ev, fn) self.scripts[ev] = fn end
  function f:RegisterEvent(ev) self.events[ev] = true end
  function f:CreateTexture()
    local t = Region()
    function t:SetColorTexture(r, g, b) self.color = { r, g, b } end
    self.textures[#self.textures + 1] = t
    return t
  end
  function f:CreateFontString()
    local fs = Region()
    function fs:SetText(t) self.text = t end
    return fs
  end
  f.kind = kind
  if name then _G[name] = f end
  mock.frames[#mock.frames + 1] = f
  return f
end

UIParent = Region()
WorldFrame = Region()
GameTooltip = Region()

local function Line(i, msg, body, event, sender, secretBody)
  local l = Region()
  l.scale = 0.9
  local args = { n = 2, body, sender }
  if secretBody then mock.secret[body] = true; mock.secret[msg] = true end
  l.messageInfo = { message = msg, r = 0.67, g = 0.67, b = 1.0, timestamp = 1,
                    extraData = { n = 6, 1, nil, nil, event, args, nil } }
  function l:IsMouseOver() return mock.mouseLine == i end
  function l:GetLeft() return 35 end
  function l:GetBottom() return 140 + (i - 1) * 16 end
  function l:GetWidth() return 430 end
  function l:GetHeight() return 15 end
  function l:GetFont() return "Fonts\\ARIALN.TTF", 14, "" end
  return l
end

ChatFrame1 = Region()
ChatFrame1.visibleLines = {
  Line(1, "|Hchannel:PARTY|h[Party]|h |Hplayer:Anna-Realm:1:PARTY|h[|cffc69b6dAnna|r]|h: ok", "ok", "CHAT_MSG_PARTY", "Anna-Realm"),
  Line(2, "|Hchannel:PARTY|h[Party]|h |Hplayer:Bob-Realm:2:PARTY|h[|cff3fc7ebBob|r]|h: WTS |cff1eff00|Hitem:2589::::::::1:::::::|h[Linen Cloth]|h|r x20, pst", "WTS |cff1eff00|Hitem:2589::::::::1:::::::|h[Linen Cloth]|h|r x20, pst", "CHAT_MSG_PARTY", "Bob-Realm"),
  Line(3, "|Hchannel:PARTY|h[Party]|h [Boss]: secret stuff", "secret stuff", "CHAT_MSG_PARTY", "Boss", true),
}
function ChatFrame1:IsMouseOver() return mock.mouseLine ~= nil end
CHAT_FRAMES = { "ChatFrame1" }
function GetMouseFoci() return { ChatFrame1 } end
DEFAULT_CHAT_FRAME = { AddMessage = function(_, m) mock.prints[#mock.prints + 1] = m end }
"""


@pytest.fixture()
def lua():
    rt = LuaRuntime(unpack_returned_tuples=True)
    rt.execute(MOCK)
    ns = rt.table()
    for fname in ("Protocol.lua", "Core.lua"):
        code = (ADDON_DIR / fname).read_text(encoding="utf-8")
        chunk = rt.eval("function(code, name) return loadstring(code, name) end")(code, fname)
        assert chunk is not None, f"{fname} failed to compile"
        chunk("WoWVoiceTranslator", ns)
    g = rt.globals()
    mock = g.WVT_MOCK
    # fire ADDON_LOADED + PLAYER_LOGIN on the addon's event frame
    for f in mock.frames.values():
        if f.events["ADDON_LOADED"]:
            f.scripts["OnEvent"](f, "ADDON_LOADED", "WoWVoiceTranslator")
            f.scripts["OnEvent"](f, "PLAYER_LOGIN")
    return rt, g, mock


def driver_tick(mock, seconds=0.06):
    mock.time = mock.time + seconds
    for f in mock.frames.values():
        upd = f.scripts["OnUpdate"]
        if upd:
            upd(f, seconds)


def grab_cells(g, frame_name="WoWVoiceTranslatorSignal"):
    signal = g[frame_name]
    if not signal.shown:
        return None
    cells = []
    for t in signal.textures.values():
        if not t.shown:
            break
        r, gg, b = (int(round(c * 3)) for c in t.color.values())
        cells.append(r * 16 + gg * 4 + b)
    return cells


def test_addon_compiles_and_prints_login(lua):
    _, g, mock = lua
    assert any("aktywny" in m for m in mock.prints.values())


def test_hover_produces_decodable_packet(lua):
    _, g, mock = lua
    mock.mouseLine = 2
    for _ in range(6):
        driver_tick(mock)
    cells = grab_cells(g)
    assert cells, "signal grid not shown"
    pkt = P.decode_image(P.render_cells(cells))
    assert pkt.type == P.TYPE_HOVER
    assert pkt.text == "WTS [Linen Cloth] x20, pst"
    assert pkt.prefix == "[Party] [Bob]: "
    assert pkt.chat_kind_name == "PARTY"
    assert pkt.color == (171, 171, 255)
    assert pkt.left == pytest.approx(35 * 0.9, abs=0.05)
    assert pkt.bottom == pytest.approx((140 + 16) * 0.9, abs=0.05)
    assert pkt.font_height == pytest.approx(14 * 0.9, abs=0.05)
    assert (pkt.phys_w, pkt.phys_h) == (2560, 1440)


def test_keepalive_and_leave(lua):
    _, g, mock = lua
    mock.mouseLine = 1
    for _ in range(4):
        driver_tick(mock)
    first = P.decode_image(P.render_cells(grab_cells(g)))
    assert first.text == "ok"
    for _ in range(14):  # > FULL_PACKET_SECONDS
        driver_tick(mock)
    keep = P.decode_image(P.render_cells(grab_cells(g)))
    assert keep.type == P.TYPE_KEEP and keep.seq == first.seq
    assert len(grab_cells(g)) == 16  # tiny marker only
    mock.mouseLine = None
    driver_tick(mock)
    assert grab_cells(g) is None


def test_secret_message_is_not_read(lua):
    _, g, mock = lua
    mock.mouseLine = 3
    for _ in range(4):
        driver_tick(mock)
    pkt = P.decode_image(P.render_cells(grab_cells(g)))
    assert pkt.is_secret
    assert "secret" not in pkt.text


def test_slash_test_packet(lua):
    _, g, mock = lua
    g.SlashCmdList["WOWVOICETRANSLATOR"]("test")
    pkt = P.decode_image(P.render_cells(grab_cells(g)))
    assert pkt.type == P.TYPE_TEST and "connection works" in pkt.text


def _lua_protocol(rt):
    code = (ADDON_DIR / "Protocol.lua").read_text(encoding="utf-8")
    ns = rt.table()
    rt.eval("function(code) return loadstring(code) end")(code)("x", ns)
    return ns.Protocol


def test_lua_encoder_matches_python_encoder(lua):
    rt, _, _ = lua
    lp = _lua_protocol(rt)
    fields = dict(type=1, seq=9, flags=0, left=10.25, bottom=20.05, width=300, height=14.95)
    p = rt.table_from({**fields, "fontHeight": 12.6, "r": 255, "g": 128, "b": 0, "physW": 1920, "physH": 1080,
                       "kind": 10, "prefix": "[Whisper] Zoë: ", "text": "gtg, gn all – żółw"})
    lua_bytes = bytes(int(v) for v in lp.EncodePacket(p).values())
    pkt = P.Packet(**fields, font_height=12.6, color=(255, 128, 0), phys_w=1920, phys_h=1080, chat_kind=10,
                   prefix="[Whisper] Zoë: ", text="gtg, gn all – żółw")
    assert lua_bytes == P.encode_packet(pkt)
    lua_cells = [int(v) for v in lp.BytesToCells(lp.EncodePacket(p)).values()]
    assert lua_cells == P.packet_cells(pkt)
    # truncation flag and UTF-8 boundary handling must match too
    long = rt.table_from({**fields, "text": "ż" * 300, "prefix": ""})
    assert bytes(int(v) for v in lp.EncodePacket(long).values()) == P.encode_packet(P.Packet(**fields, text="ż" * 300))


def test_strip_markup_and_split(lua):
    rt, _, _ = lua
    lp = _lua_protocol(rt)
    line = "|cff40c040[Guild]|r |Hplayer:Kai:3:GUILD|h[Kai]|h: grats on |cffa335ee|Hitem:19019|h[Thunderfury]|h|r {skull} |TInterface\\Icons\\x:0|t||"
    stripped = lp.StripMarkup(line)
    assert stripped == "[Guild] [Kai]: grats on [Thunderfury] {skull} |"
    prefix, body = lp.SplitPrefix(stripped, "grats on [Thunderfury] {skull} |")
    assert prefix == "[Guild] [Kai]: " and body == "grats on [Thunderfury] {skull} |"
    assert lp.Utf8Truncate("żż", 3) == "ż"



# --------------------------------------------------------------------------- voice relay

def grid_packet(g):
    """Whatever the voice grid shows right now (spoken line, assistant-only line or state)."""
    cells = grab_cells(g, "WoWVoiceTranslatorVoiceSignal")
    return None if cells is None else P.decode_image(P.render_cells(cells))


def voice_packet(g):
    """The line on the voice grid if it is meant to be spoken (the assistant's packets ignored)."""
    pkt = grid_packet(g)
    if pkt is None or pkt.type != P.TYPE_VOICE:
        return None
    return pkt


def fire(mock, event, *args):
    for f in mock.frames.values():
        if f.events[event]:
            f.scripts["OnEvent"](f, event, *args)


def say_args(text, sender, guid, language_id=7):
    # CHAT_MSG_SAY payload: text, playerName, languageName, channelName, playerName2, specialFlags,
    # zoneChannelID, channelIndex, channelBaseName, languageID, lineID, guid
    return (text, sender, "Common", "", "", "", 0, 0, "", language_id, 123, guid)


@pytest.fixture()
def voice(lua):
    rt, g, mock = lua

    def add_player(guid, name, race, sex):
        mock.players[guid] = rt.table_from({"race": race, "sex": sex, "name": name})

    def slash(cmd):
        g.SlashCmdList["WOWVOICETRANSLATOR"](cmd)

    # the assistant's first state packet goes out right after login; let it pass (mouse off the chat)
    mock.mouseLine = None
    for _ in range(3):
        driver_tick(mock, 0.25)
    assert grid_packet(g) is None
    return rt, g, mock, add_player, slash


def test_voice_is_off_by_default(voice):
    _, g, mock, add_player, _ = voice
    add_player("Player-1-ORC", "Thrall", "Orc", 2)
    fire(mock, "CHAT_MSG_SAY", *say_args("lok tar", "Thrall", "Player-1-ORC"))
    driver_tick(mock)
    assert voice_packet(g) is None  # nothing sent, nothing spent


def test_say_packet_with_race_sex_and_exact_distance(voice):
    _, g, mock, add_player, slash = voice
    slash("say on")
    add_player("Player-1-ORC", "Thrall", "Orc", 2)
    mock.units["target"] = "Player-1-ORC"
    mock.dist2["target"] = 144  # 12 yards
    fire(mock, "CHAT_MSG_SAY",
         *say_args("Ready? |cff1eff00|Hitem:2589|h[Linen Cloth]|h|r for sale", "Thrall-Forever", "Player-1-ORC"))
    driver_tick(mock)
    pkt = voice_packet(g)
    assert pkt.type == P.TYPE_VOICE and pkt.chat_kind_name == "SAY"
    assert (pkt.sender, pkt.race, pkt.sex) == ("Thrall-Forever", "Orc", 2)
    assert pkt.distance == pytest.approx(12.0) and pkt.dist_method == P.DIST_EXACT
    assert pkt.text == "Ready? [Linen Cloth] for sale"
    assert not pkt.is_self and not pkt.lang_pl
    assert grab_cells(g) is None  # the hover grid is untouched by voice packets


def test_say_distance_falls_back_to_ranges_and_nameplates(voice):
    rt, g, mock, add_player, slash = voice
    slash("say on")
    add_player("Player-1-GNOME", "Fizz", "Gnome", 3)
    mock.units["nameplate3"] = "Player-1-GNOME"
    fire(mock, "CHAT_MSG_SAY", *say_args("far away", "Fizz", "Player-1-GNOME"))
    driver_tick(mock)
    pkt = voice_packet(g)
    assert pkt.distance == pytest.approx(35.0) and pkt.dist_method == P.DIST_NAMEPLATE
    driver_tick(mock, 0.3)
    mock.interact["nameplate3"] = rt.table_from({4: True})  # within follow range (~28 yd)
    fire(mock, "CHAT_MSG_SAY", *say_args("closer", "Fizz", "Player-1-GNOME"))
    driver_tick(mock)
    pkt = voice_packet(g)
    assert pkt.distance == pytest.approx(20.0) and pkt.dist_method == P.DIST_RANGE
    driver_tick(mock, 0.3)
    fire(mock, "CHAT_MSG_SAY", *say_args("who am I?", "Ghost", "Player-1-UNSEEN"))
    driver_tick(mock)
    pkt = voice_packet(g)
    assert pkt.distance is None and pkt.dist_method == P.DIST_NONE


def test_party_in_polish_mode_has_no_distance(voice):
    _, g, mock, add_player, slash = voice
    slash("party on")
    slash("lang pl")
    add_player("Player-1-DWARF", "Brann", "Dwarf", 2)
    mock.units["party2"] = "Player-1-DWARF"
    mock.dist2["party2"] = 100
    fire(mock, "CHAT_MSG_PARTY_LEADER", *say_args("mb, then pull", "Brann", "Player-1-DWARF"))
    driver_tick(mock)
    pkt = voice_packet(g)
    assert pkt.chat_kind_name == "PARTY_LEADER" and pkt.lang_pl
    assert pkt.distance is None and pkt.dist_method == P.DIST_NONE


def test_own_message_is_spoken_in_english_mode_only(voice):
    _, g, mock, add_player, slash = voice
    slash("say on")
    add_player("Player-1-ME", "Me", "NightElf", 3)
    fire(mock, "CHAT_MSG_SAY", *say_args("Hello everyone!", "Me", "Player-1-ME"))
    driver_tick(mock)
    pkt = voice_packet(g)
    assert pkt.is_self and pkt.distance == 0 and pkt.dist_method == P.DIST_SELF and pkt.race == "NightElf"
    driver_tick(mock, 0.3)  # packet expires, queue empty -> grid hidden
    assert voice_packet(g) is None
    slash("lang pl")
    fire(mock, "CHAT_MSG_SAY", *say_args("Hello again!", "Me", "Player-1-ME"))
    driver_tick(mock)
    assert voice_packet(g) is None


def test_other_faction_language_and_secret_text_are_skipped(voice):
    _, g, mock, _, slash = voice
    slash("say on")
    mock.languages[1] = False  # Orcish, not understood -> garbled text
    fire(mock, "CHAT_MSG_SAY", *say_args("Kek zug lok", "Grunt", "Player-1-ORC", language_id=1))
    mock.secret["boss secret"] = True
    fire(mock, "CHAT_MSG_SAY", *say_args("boss secret", "Grunt", "Player-1-ORC"))
    driver_tick(mock)
    assert voice_packet(g) is None


def test_voice_queue_shows_packets_one_after_another(voice):
    _, g, mock, _, slash = voice
    slash("party on")
    fire(mock, "CHAT_MSG_PARTY", *say_args("first", "Anna", "Player-1-A"))
    fire(mock, "CHAT_MSG_PARTY", *say_args("second", "Bob", "Player-1-B"))
    driver_tick(mock, 0.05)
    first = voice_packet(g)
    driver_tick(mock, 0.1)
    assert voice_packet(g).text == "first"  # still shown (0.2 s per packet)
    driver_tick(mock, 0.12)
    second = voice_packet(g)
    assert (first.text, second.text) == ("first", "second") and second.seq == first.seq + 1
    assert first.race == "" and first.sex == 0  # unknown player: the app uses the "unknown" voices
    driver_tick(mock, 0.25)
    assert voice_packet(g) is None


def test_panel_buttons_toggle_the_saved_options(voice):
    _, g, mock, _, _ = voice
    db = g.WoWVoiceTranslatorDB
    assert db.voiceSay is False and db.voiceParty is False and db.voiceLang == "en"
    buttons = [f for f in mock.frames.values() if f.kind == "Button" and f.scripts["OnClick"]]
    assert len(buttons) == 3
    for b in buttons:
        b.scripts["OnClick"](b)
    assert db.voiceSay is True and db.voiceParty is True and db.voiceLang == "pl"


def test_lua_voice_encoder_matches_python(lua):
    rt, _, _ = lua
    lp = _lua_protocol(rt)
    for dist in (17.25, None, 0):
        fields = {"type": 4, "seq": 300, "flags": 8 + 4, "kind": 3, "sex": 2, "distMethod": 1,
                  "race": "Scourge", "sender": "Zo\u00eb-Forever", "text": "r u ready? \u017c" * 40}
        if dist is not None:
            fields["distance"] = dist
        lua_bytes = bytes(int(v) for v in lp.EncodePacket(rt.table_from(fields)).values())
        py = P.Packet(type=4, seq=300, flags=12, chat_kind=3, sex=2, distance=dist, dist_method=1,
                      race="Scourge", sender="Zo\u00eb-Forever", text="r u ready? \u017c" * 40)
        assert lua_bytes == P.encode_packet(py)


def test_lua_state_encoder_matches_python(lua):
    rt, _, _ = lua
    lp = _lua_protocol(rt)
    members = rt.table_from(["Arthas", "Jaina-Kazzak", "Zoë", "Thrall", "Fifth-Ignored"])
    fields = {"type": 5, "seq": 41, "flags": 1 + 4 + 8, "level": 23, "xp": 12345, "xpMax": 400000, "gold": 70000,
              "silverCopper": 4567, "bagFree": 11, "bagSlots": 64, "groupSize": 5, "name": "Mateusz",
              "zone": "Elwynn Forest", "subzone": "Goldshire " + "ż" * 40, "members": members}
    lua_bytes = bytes(int(v) for v in lp.EncodePacket(rt.table_from(fields)).values())
    st = P.GameState(flags=13, level=23, xp=12345, xp_max=400000, gold=70000, silver_copper=4567, bag_free=11,
                     bag_slots=64, group_size=5, name="Mateusz", zone="Elwynn Forest",
                     subzone="Goldshire " + "ż" * 40, members=["Arthas", "Jaina-Kazzak", "Zoë", "Thrall"])
    assert lua_bytes == P.encode_packet(P.Packet(type=5, seq=41, state=st))
    decoded = P.decode_bytes(lua_bytes) if hasattr(P, "decode_bytes") else None
    if decoded is not None:
        assert decoded.state.members[-1] == "Thrall" and decoded.state.subzone.startswith("Goldshire")


# --------------------------------------------------------------------------- voice assistant

GROUP_MOCK = r"""
local mock = WVT_MOCK
mock.calls = {}
mock.group = { size = 0, raid = false, leader = false, restricted = false }
local function record(name) return function(...) mock.calls[#mock.calls + 1] = { name, ... } end end
C_PartyInfo = { InviteUnit = record("InviteUnit"), UninviteUnit = record("UninviteUnit"),
                PromoteToLeader = record("PromoteToLeader"), ConvertToRaid = record("ConvertToRaid"),
                LeaveParty = record("LeaveParty"), DoReadyCheck = record("DoReadyCheck") }
Enum = { AddOnRestrictionType = { Combat = 0, Encounter = 1, ChallengeMode = 2, PvPMatch = 3 } }
C_RestrictedActions = { IsAddOnRestrictionActive = function(kind) return mock.group.restricted and kind == 0 end }
function InCombatLockdown() return mock.group.restricted end
function IsInGroup() return mock.group.size > 0 end
function IsInRaid() return mock.group.raid end
function UnitIsGroupLeader(unit) return unit == "player" and mock.group.leader end
function GetNumGroupMembers() return mock.group.size end
function UnitName(unit)
  local names = { player = "Mateusz", party1 = "Arthas", party2 = "Jaina" }
  if unit == "party2" then return "Jaina", "Kazzak" end
  return names[unit]
end
function UnitLevel() return 23 end
function UnitXP() return 12345 end
function UnitXPMax() return 40000 end
function GetMoney() return 1234567 end
function GetZoneText() return "Elwynn Forest" end
function GetSubZoneText() return "Goldshire" end
C_Container = { GetContainerNumFreeSlots = function(bag) return 3 end, GetContainerNumSlots = function(bag) return 16 end }
"""


@pytest.fixture()
def assistant(voice):
    rt, g, mock, add_player, slash = voice
    rt.execute(GROUP_MOCK)
    return rt, g, mock, add_player, slash


def _calls(mock):
    return [tuple(v for v in c.values()) for c in mock.calls.values()]


def test_unspoken_chat_reaches_the_assistant_marked_never_speak(assistant):
    _, g, mock, add_player, _ = assistant
    fire(mock, "CHAT_MSG_WHISPER", *say_args("inv pls", "Bob-Kazzak", "Player-1-BOB", 0))
    driver_tick(mock)
    pkt = grid_packet(g)
    assert pkt.type == P.TYPE_CHAT and pkt.no_speak and pkt.chat_kind_name == "WHISPER"
    assert (pkt.sender, pkt.text) == ("Bob-Kazzak", "inv pls")
    assert voice_packet(g) is None  # nothing for the voices: costs no credits


def test_assistant_can_be_switched_off(assistant):
    _, g, mock, _, slash = assistant
    slash("asystent off")
    fire(mock, "CHAT_MSG_SAY", *say_args("inv me", "Bob", "Player-1-BOB"))
    for _ in range(3):
        driver_tick(mock, 0.25)
        assert grid_packet(g) is None


def test_state_packet_describes_the_player_and_group(assistant):
    _, g, mock, _, _ = assistant
    mock.group.size, mock.group.leader = 3, True
    fire(mock, "GROUP_ROSTER_UPDATE")
    seen = None
    for _ in range(6):
        driver_tick(mock, 0.25)
        pkt = grid_packet(g)
        if pkt is not None and pkt.type == P.TYPE_STATE:
            seen = pkt.state
            break
    assert seen is not None
    assert (seen.level, seen.xp, seen.xp_max, seen.gold, seen.silver_copper) == (23, 12345, 40000, 123, 4567)
    assert (seen.bag_free, seen.bag_slots, seen.zone, seen.subzone) == (15, 80, "Elwynn Forest", "Goldshire")
    assert seen.in_group and seen.is_leader and not seen.restricted and seen.group_size == 3
    assert seen.name == "Mateusz" and seen.members == ["Arthas", "Jaina-Kazzak"]


def test_invite_command_fills_the_party_and_names_the_rest(assistant):
    _, g, mock, _, slash = assistant
    slash("inv Ann Bob Cid Dan Eve")
    assert [c[1] for c in _calls(mock)] == ["Ann", "Bob", "Cid", "Dan"]  # solo: 4 free party slots
    assert any("Eve" in str(m) for m in mock.prints.values())


def test_kick_lead_raid_and_ready_respect_restrictions_and_leadership(assistant):
    _, g, mock, _, slash = assistant
    mock.group.size, mock.group.leader, mock.group.restricted = 3, True, True
    slash("kick Arthas")
    assert _calls(mock) == []  # in combat: refused, and said so
    assert "walka" in str(list(mock.prints.values())[-1])
    mock.group.restricted = False
    slash("kick Arthas")
    slash("lead Jaina-Kazzak")
    slash("raid")
    slash("ready")
    assert [c[0] for c in _calls(mock)] == ["UninviteUnit", "PromoteToLeader", "ConvertToRaid", "DoReadyCheck"]
    mock.group.leader = False
    slash("kick Jaina")
    assert len(_calls(mock)) == 4  # not the leader any more
    slash("leave")
    assert _calls(mock)[-1][0] == "LeaveParty"


def test_lua_quest_and_position_encoders_match_python(lua):
    rt, _, _ = lua
    lp = _lua_protocol(rt)
    objectives = rt.table_from([rt.table_from({"done": False, "text": "3/8 Kobold Candle"}),
                                rt.table_from({"done": True, "text": "Explore ż" * 20})])
    fields = {"type": 7, "seq": 12, "sync": 3, "index": 2, "total": 7, "questID": 60123, "level": 8, "flags": 12,
              "group": 0, "poiMap": 37, "poiX": 0.4123, "poiY": 0.8261, "distance": 230.6,
              "title": "The Fargodeep Mine", "zone": "Elwynn Forest", "objectives": objectives}
    lua_bytes = bytes(int(v) for v in lp.EncodePacket(rt.table_from(fields)).values())
    q = P.QuestEntry(sync=3, index=2, total=7, quest_id=60123, level=8, flags=12, group=0, poi_map=37, poi_x=0.4123,
                     poi_y=0.8261, distance_yd=231, title="The Fargodeep Mine", zone="Elwynn Forest",
                     objectives=[(False, "3/8 Kobold Candle"), (True, "Explore ż" * 20)])
    assert lua_bytes == P.encode_packet(P.Packet(type=7, seq=12, quest=q))
    no_poi = rt.table_from({"type": 7, "seq": 1, "title": "x"})  # no distance, no objectives
    assert bytes(int(v) for v in lp.EncodePacket(no_poi).values()) == P.encode_packet(
        P.Packet(type=7, seq=1, quest=P.QuestEntry(title="x")))
    pos = rt.table_from({"type": 8, "seq": 9, "mapID": 1429, "x": 0.4, "y": 0.8, "width": 3470.8, "height": 2314.6})
    assert bytes(int(v) for v in lp.EncodePacket(pos).values()) == P.encode_packet(
        P.Packet(type=8, seq=9, pos=P.PlayerPos(1429, 0.4, 0.8, 3470.8, 2314.6)))


QUEST_MOCK = r"""
local mock = WVT_MOCK
mock.map = { id = 37, x = 0.40, y = 0.70 }
mock.log = {
  { header = true, title = "Elwynn Forest" },
  { questID = 101, title = "The Fargodeep Mine", level = 8, done = false, have = 3, need = 8 },
  { questID = 102, title = "Bounty on Murlocs", level = 9, done = true },
}
local function entry(id) for _, q in ipairs(mock.log) do if q.questID == id then return q end end end
C_QuestLog = {
  GetNumQuestLogEntries = function() return #mock.log end,
  GetInfo = function(i)
    local q = mock.log[i]
    return { title = q.title, questID = q.questID or 0, isHeader = q.header or false, level = q.level,
             difficultyLevel = q.level, suggestedGroup = 0, isHidden = false }
  end,
  ReadyForTurnIn = function(id) return entry(id).done end,
  IsComplete = function(id) return entry(id).done end,
  IsFailed = function() return false end,
  GetQuestWatchType = function(id) if id == 101 then return 0 end return nil end,
  GetQuestsOnMap = function(mapID) return { { questID = 101, x = 0.55, y = 0.50 } } end,
  GetNextWaypoint = function(id) if id == 102 then return 52, 0.5, 0.25 end end,
  GetDistanceSqToQuest = function(id) if id == 101 then return 230 * 230 end end,
  GetQuestObjectives = function(id)
    local q = entry(id)
    if q.need then return { { text = q.have .. "/" .. q.need .. " Kobold Candle", finished = q.have >= q.need } } end
    return {}
  end,
}
C_Map = {
  GetBestMapForUnit = function() return mock.map.id end,
  GetPlayerMapPosition = function() return { x = mock.map.x, y = mock.map.y } end,
  GetMapWorldSize = function() return 3470, 2314 end,
  GetMapInfo = function(id) return { name = id == 37 and "Elwynn Forest" or "Westfall" } end,
}
"""


def _collect(g, mock, seconds):
    seen, last = [], None
    for _ in range(int(seconds / 0.1)):
        driver_tick(mock, 0.1)
        pkt = grid_packet(g)
        if pkt is not None and pkt != last:
            seen.append(pkt)
        last = pkt
    return seen


def test_quest_log_and_position_reach_the_app(voice):
    rt, g, mock, _, slash = voice
    rt.execute(QUEST_MOCK)
    fire(mock, "QUEST_LOG_UPDATE")
    seen = _collect(g, mock, 3)
    quests = {p.quest.quest_id: p.quest for p in seen if p.type == P.TYPE_QUEST}
    pos = [p.pos for p in seen if p.type == P.TYPE_POS]
    assert pos and pos[0].map_id == 37 and abs(pos[0].x - 0.40) < 1e-3 and pos[0].width == 3470
    mine = quests[101]
    assert (mine.title, mine.zone, mine.level, mine.total) == ("The Fargodeep Mine", "Elwynn Forest", 8, 2)
    assert mine.objectives == [(False, "3/8 Kobold Candle")] and mine.tracked and mine.has_poi
    assert abs(mine.poi_x - 0.55) < 1e-3 and mine.distance_yd == 230
    other = quests[102]
    assert other.complete and other.zone == "Westfall" and other.flags & P.QFLAG_POI_OTHER_MAP
    # progress: only the changed quest goes again, in the same sync
    mock.log[2]["have"] = 4  # Lua tables are 1-based; [2] is quest 101
    fire(mock, "QUEST_LOG_UPDATE")
    again = [p.quest for p in _collect(g, mock, 3) if p.type == P.TYPE_QUEST]
    assert [(q.quest_id, q.objectives[0][1], q.sync) for q in again] == [(101, "4/8 Kobold Candle", mine.sync)]
    # a new quest: a new sync with every quest
    mock.log[4] = rt.table_from({"questID": 103, "title": "Report to Gryan", "level": 10, "done": False})
    fire(mock, "QUEST_ACCEPTED")
    fresh = [p.quest for p in _collect(g, mock, 3) if p.type == P.TYPE_QUEST]
    assert sorted(q.quest_id for q in fresh) == [101, 102, 103] and {q.sync for q in fresh} == {mine.sync + 1}
    slash("questy")
    assert "3 questow, 2 ze znacznikiem" in str(list(mock.prints.values())[-1])



# --------------------------------------------------------------------------- player, completed quests, waypoints, loot

def test_lua_player_done_and_loot_encoders_match_python(lua):
    rt, _, _ = lua
    lp = _lua_protocol(rt)
    pl = rt.table_from({"type": 9, "seq": 4, "classID": 8, "raceID": 95, "faction": 1, "sex": 3})
    assert bytes(int(v) for v in lp.EncodePacket(pl).values()) == P.encode_packet(
        P.Packet(type=9, seq=4, player=P.PlayerInfo(8, 95, 1, 3)))
    ids = list(range(1000, 1300))  # more than one packet holds: the encoder keeps the first 154
    done = rt.table_from({"type": 10, "seq": 5, "sync": 2, "part": 1, "parts": 2, "ids": rt.table_from(ids)})
    assert bytes(int(v) for v in lp.EncodePacket(done).values()) == P.encode_packet(
        P.Packet(type=10, seq=5, done=P.DoneChunk(2, 1, 2, ids[:P.MAX_DONE_IDS])))
    stats = rt.table_from([rt.table_from([2, 6]), rt.table_from([3, -4]), rt.table_from([7, 127])])
    worn = rt.table_from([rt.table_from({"itemLevel": 15, "subclassID": 2, "name": "Rough Leather Gloves",
                                         "stats": rt.table_from([rt.table_from([6, 40])])})])
    loot = rt.table_from({"type": 11, "seq": 6, "rollID": 77, "flags": 6, "quality": 2, "itemLevel": 24,
                          "minLevel": 19, "classID": 4, "subclassID": 2, "equipLoc": 11, "count": 1,
                          "name": "Gloves of the Monkey", "stats": stats, "equipped": worn})
    lua_bytes = bytes(int(v) for v in lp.EncodePacket(loot).values())
    item = P.LootItem(77, 6, 2, 24, 19, 4, 2, "INVTYPE_HAND", 1, "Gloves of the Monkey",
                      {"agi": 6, "sta": -4, "dps": 12.7}, [P.WornItem(15, 2, "Rough Leather Gloves", {"armor": 40})])
    assert lua_bytes == P.encode_packet(P.Packet(type=11, seq=6, loot=item))
    assert P.decode_image(P.render_cells(P.packet_cells(P.Packet(type=11, seq=6, loot=item)))).loot == item


WORLD_MOCK = r"""
local mock = WVT_MOCK
mock.calls = mock.calls or {}
local function record(name) return function(...) mock.calls[#mock.calls + 1] = { name, ... } end end
function UnitClass() return "Mage", "MAGE", 8 end
function UnitRace() return "Skyborne", "Skyborne", 95 end
function UnitFactionGroup() return "Alliance", "Alliance" end
function UnitSex() return 3 end
mock.completed = {}
for i = 1, 200 do mock.completed[i] = 1600 - i * 3 end
C_QuestLog = C_QuestLog or {}
C_QuestLog.GetAllCompletedQuestIDs = function() return mock.completed end
C_QuestLog.GetLogIndexForQuestID = function(id) if id == 101 then return 2 end end
C_QuestLog.GetQuestWatchType = function(id) return nil end
C_QuestLog.AddQuestWatch = record("AddQuestWatch")
C_QuestLog.GetTitleForQuestID = function(id) return "The Fargodeep Mine" end
C_Map = C_Map or {}
C_Map.SetUserWaypoint = record("SetUserWaypoint")
C_Map.ClearUserWaypoint = record("ClearUserWaypoint")
C_Map.CanSetUserWaypointOnMap = function(id) return id ~= 999 end
C_Map.GetMapInfo = function(id) return { name = "Westfall" } end
UiMapPoint = { CreateFromCoordinates = function(m, x, y) return { uiMapID = m, x = x, y = y } end }
C_SuperTrack = { SetSuperTrackedUserWaypoint = record("SetSuperTrackedUserWaypoint"),
                 SetSuperTrackedQuestID = record("SetSuperTrackedQuestID") }
"""


@pytest.fixture()
def world_addon(voice):
    rt, g, mock, add_player, slash = voice
    rt.execute(WORLD_MOCK)
    return rt, g, mock, slash


def test_player_info_and_completed_quests_reach_the_app(world_addon):
    rt, g, mock, _ = world_addon
    seen = _collect(g, mock, 8)
    players = [p.player for p in seen if p.type == P.TYPE_PLAYER]
    assert players and players[0] == P.PlayerInfo(class_id=8, race_id=95, faction=P.FACTION_ALLIANCE, sex=3)
    parts = [p.done for p in seen if p.type == P.TYPE_DONE]
    assert [(d.part, d.parts, len(d.ids)) for d in parts] == [(1, 2, 154), (2, 2, 46)]
    ids = [i for d in parts for i in d.ids]
    assert ids == sorted(1600 - i * 3 for i in range(1, 201))  # sorted, all of them
    fire(mock, "QUEST_TURNED_IN", 4242, 100, 0)
    added = [p.done for p in _collect(g, mock, 2) if p.type == P.TYPE_DONE]
    assert added and added[0].parts == 0 and added[0].ids == [4242]


def test_waypoint_and_track_commands(world_addon):
    rt, g, mock, slash = world_addon
    slash("way 1436 56.3 47.5 Gryan Stoutmantle")
    calls = _calls(mock)
    assert calls[-2][0] == "SetUserWaypoint" and calls[-1] == ("SetSuperTrackedUserWaypoint", True)
    point = calls[-2][1]
    assert (point["uiMapID"], round(point["x"], 3), round(point["y"], 3)) == (1436, 0.563, 0.475)
    assert "znacznik: Gryan Stoutmantle (Westfall 56.3, 47.5)" in str(list(mock.prints.values())[-1])
    slash("way 999 10 10 Nowhere")
    assert "nie pozwala" in str(list(mock.prints.values())[-1]) and _calls(mock)[-1][0] != "SetUserWaypoint"
    slash("way clear")
    assert _calls(mock)[-1][0] == "ClearUserWaypoint"
    slash("track 101")
    assert _calls(mock)[-2] == ("AddQuestWatch", 101) and _calls(mock)[-1] == ("SetSuperTrackedQuestID", 101)
    slash("track 5")
    assert "nie ma w dzienniku" in str(list(mock.prints.values())[-1])


LOOT_MOCK = r"""
local mock = WVT_MOCK
function GetLootRollItemLink(id) return "|cff1eff00|Hitem:15000::::::::|h[Gloves of the Monkey]|h|r" end
function GetLootRollItemInfo(id) return 1337, "Gloves of the Monkey", 1, 2, false, true, true, false end
C_Item = {
  GetItemInfoInstant = function(link)
    if link:find("15000") then return 15000, "Armor", "Leather", "INVTYPE_HAND", 1, 4, 2 end
    return 2000, "Armor", "Leather", "INVTYPE_HAND", 1, 4, 2
  end,
  GetItemInfo = function(link)
    if link:find("15000") then return "Gloves of the Monkey", link, 2, 24, 19 end
    return "Rough Leather Gloves", link, 1, 15, 10
  end,
  GetItemStats = function(link)
    if link:find("15000") then return { ITEM_MOD_AGILITY_SHORT = 6, ITEM_MOD_STAMINA_SHORT = 4, RESISTANCE0_NAME = 62 } end
    return { RESISTANCE0_NAME = 40, ITEM_MOD_AGILITY_SHORT = 1, EMPTY_SOCKET_RED = 1 }
  end,
}
function GetInventoryItemLink(unit, slot) if slot == 10 then return "|Hitem:2000|h[Rough Leather Gloves]|h" end end
"""


def test_a_loot_roll_is_described_with_the_worn_item(world_addon):
    rt, g, mock, _ = world_addon
    rt.execute(LOOT_MOCK)
    fire(mock, "START_LOOT_ROLL", 77, 60000)
    loot = [p.loot for p in _collect(g, mock, 2) if p.type == P.TYPE_LOOT]
    assert len(loot) == 1
    it = loot[0]
    assert (it.roll_id, it.name, it.equip_loc, it.class_id, it.subclass_id, it.quality) == (
        77, "Gloves of the Monkey", "INVTYPE_HAND", 4, 2, 2)
    assert it.can_need and it.can_greed and not it.bop and (it.item_level, it.min_level) == (24, 19)
    assert it.stats == {"agi": 6.0, "sta": 4.0, "armor": 62.0}
    assert it.equipped == [P.WornItem(15, 2, "Rough Leather Gloves", {"agi": 1.0, "armor": 40.0})]
