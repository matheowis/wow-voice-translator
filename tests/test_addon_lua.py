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

def voice_packet(g):
    cells = grab_cells(g, "WoWVoiceTranslatorVoiceSignal")
    return None if cells is None else P.decode_image(P.render_cells(cells))


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
