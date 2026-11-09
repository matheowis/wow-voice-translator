-- WoW Voice Translator - pixel signal encoder.
-- Mirrors wvt/protocol.py (the Python side is the reference; tests/test_protocol.py
-- executes this file with lupa and checks that both encoders produce identical cells).
-- Pure Lua 5.1, no bit library, no WoW API - safe to unit test outside the game.

local _, ns = ...
ns = ns or {}

local floor = math.floor
local strbyte, strsub, strgsub, strfind = string.byte, string.sub, string.gsub, string.find

local P = {}
ns.Protocol = P

P.VERSION = 1
P.COLS = 32
P.MAX_ROWS = 20
P.CELL_PX = 4
P.CALIB = 4
P.MAX_CELLS = P.COLS * P.MAX_ROWS
P.MAX_BYTES = floor((P.MAX_CELLS - P.CALIB) / 4) * 3 -- 477
P.HOVER_FIXED_BYTES = 26
P.MAX_PREFIX_BYTES = 80

P.TYPE_HOVER = 1
P.TYPE_KEEP = 2
P.TYPE_TEST = 3
P.TYPE_VOICE = 4
P.TYPE_STATE = 5 -- player state for the voice assistant
P.TYPE_CHAT = 6 -- a chat line only for the assistant, never spoken (same layout as TYPE_VOICE)
P.TYPE_QUEST = 7 -- one quest of the quest log (assistant)
P.TYPE_POS = 8 -- the player's map position (assistant)

P.FLAG_SECRET = 1
P.FLAG_TRUNCATED = 2
P.VFLAG_SELF = 4
P.VFLAG_LANG_PL = 8

P.SFLAG_GROUP = 1
P.SFLAG_RAID = 2
P.SFLAG_LEADER = 4
P.SFLAG_RESTRICTED = 8 -- combat / boss / M+ / PvP match: no kick, lead, raid or ready check
P.SFLAG_COMBAT = 16
P.MAX_NAME_BYTES = 48
P.MAX_ZONE_BYTES = 64
P.MAX_MEMBERS = 4

P.QFLAG_COMPLETE = 1 -- ready to turn in
P.QFLAG_FAILED = 2
P.QFLAG_TRACKED = 4
P.QFLAG_POI = 8 -- the game shows a map marker for the next objective
P.QFLAG_POI_OTHER_MAP = 16 -- ...on another map than the player's
P.MAX_QUEST_TITLE_BYTES = 64
P.MAX_QUEST_ZONE_BYTES = 40
P.MAX_OBJECTIVES = 5
P.MAX_OBJECTIVE_BYTES = 60

-- voice grid: same geometry, COLS + 2 cells to the right of the hover grid
P.VOICE_GRID_X_CELLS = P.COLS + 2
P.MAX_RACE_BYTES = 24
P.MAX_SENDER_BYTES = 48
P.VOICE_FIXED_BYTES = 13

P.DIST_UNKNOWN = 65535
P.DIST_NONE = 0
P.DIST_EXACT = 1
P.DIST_RANGE = 2
P.DIST_NAMEPLATE = 3
P.DIST_SELF = 4

P.LEVELS = { 0, 1 / 3, 2 / 3, 1 }

local function clampU16(v)
	v = floor(v)
	if v < 0 then return 0 end
	if v > 65535 then return 65535 end
	return v
end

local function pushU8(t, v)
	t[#t + 1] = floor(v) % 256
end

local function pushU16(t, v)
	v = clampU16(v)
	t[#t + 1] = floor(v / 256)
	t[#t + 1] = v % 256
end

local function pushU32(t, v)
	v = floor(v or 0)
	if v < 0 then
		v = 0
	elseif v > 4294967295 then
		v = 4294967295
	end
	t[#t + 1] = floor(v / 16777216) % 256
	t[#t + 1] = floor(v / 65536) % 256
	t[#t + 1] = floor(v / 256) % 256
	t[#t + 1] = v % 256
end

local function pushByte(t, v)
	v = floor(v or 0)
	if v < 0 then
		v = 0
	elseif v > 255 then
		v = 255
	end
	t[#t + 1] = v
end

local function pushNorm16(t, v) -- 0..1 -> 0..65535
	local n = floor((v or 0) * 65535 + 0.5)
	if n < 0 then
		n = 0
	elseif n > 65535 then
		n = 65535
	end
	pushU16(t, n)
end

local function pushFixed10(t, v)
	pushU16(t, floor((v or 0) * 10 + 0.5))
end

local function pushBytes(t, s)
	for i = 1, #s do
		t[#t + 1] = strbyte(s, i)
	end
end

-- Cut a string to at most `limit` bytes without splitting a UTF-8 sequence.
function P.Utf8Truncate(s, limit)
	if #s <= limit then
		return s
	end
	local cut = limit
	while cut > 0 do
		local b = strbyte(s, cut + 1)
		if b and b >= 128 and b < 192 then
			cut = cut - 1
		else
			break
		end
	end
	return strsub(s, 1, cut)
end

function P.Fletcher16(bytes, first, last)
	local s1, s2 = 0, 0
	for i = first, last do
		s1 = (s1 + bytes[i]) % 255
		s2 = (s2 + s1) % 255
	end
	return s2 * 256 + s1
end

-- Remove WoW UI escape sequences so only the readable text is transmitted.
function P.StripMarkup(s)
	if not s then
		return ""
	end
	s = strgsub(s, "|c%x%x%x%x%x%x%x%x", "")
	s = strgsub(s, "|cn[^:|]+:", "")
	s = strgsub(s, "|r", "")
	s = strgsub(s, "|H.-|h(.-)|h", "%1")
	s = strgsub(s, "|T.-|t", "")
	s = strgsub(s, "|A.-|a", "")
	s = strgsub(s, "|K.-|k", "?")
	s = strgsub(s, "|n", " ")
	s = strgsub(s, "||", "|")
	return s
end

function P.Trim(s)
	return (strgsub(s or "", "^%s*(.-)%s*$", "%1"))
end

-- Split the displayed (stripped) chat line into "prefix" (timestamp, channel, sender)
-- and the message body, using the raw message text from the chat event.
function P.SplitPrefix(strippedLine, strippedBody)
	if not strippedBody or strippedBody == "" then
		return "", strippedLine
	end
	local n = #strippedBody
	if #strippedLine >= n and strsub(strippedLine, -n) == strippedBody then
		return strsub(strippedLine, 1, #strippedLine - n), strippedBody
	end
	local i = strfind(strippedLine, strippedBody, 1, true)
	if i then
		return strsub(strippedLine, 1, i - 1), strippedBody
	end
	return "", strippedLine
end

-- p = { type, seq, flags, left, bottom, width, height, fontHeight, r, g, b,
--       physW, physH, kind, prefix, text }   (r, g, b in 0..255)
-- voice packets: { type = TYPE_VOICE, seq, flags, kind, sex, distance (yards or nil),
--                  distMethod, race, sender, text }
-- state packets: { type = TYPE_STATE, seq, flags, level, xp, xpMax, gold, silverCopper,
--                  bagFree, bagSlots, groupSize, name, zone, subzone, members = { names } }
-- quest packets: { type = TYPE_QUEST, seq, sync, index, total, questID, level, flags, group,
--                  poiMap, poiX, poiY (0..1), distance (yards or nil), title, zone,
--                  objectives = { { done = bool, text = "3/8 Kobold Candle" }, ... } }
-- position packets: { type = TYPE_POS, seq, mapID, x, y (0..1), width, height (yards) }
function P.EncodePacket(p)
	local b = { 87, 86 } -- "WV"
	pushU8(b, P.VERSION)
	pushU8(b, p.type)
	pushU16(b, p.seq or 0)
	if p.type == P.TYPE_HOVER or p.type == P.TYPE_TEST then
		local prefix = P.Utf8Truncate(p.prefix or "", P.MAX_PREFIX_BYTES)
		local room = P.MAX_BYTES - P.HOVER_FIXED_BYTES - #prefix - 2 - 2
		local rawText = p.text or ""
		local text = P.Utf8Truncate(rawText, room)
		local flags = p.flags or 0
		if #text < #rawText and floor(flags / P.FLAG_TRUNCATED) % 2 == 0 then
			flags = flags + P.FLAG_TRUNCATED
		end
		pushU8(b, flags)
		pushFixed10(b, p.left)
		pushFixed10(b, p.bottom)
		pushFixed10(b, p.width)
		pushFixed10(b, p.height)
		pushFixed10(b, p.fontHeight)
		pushU8(b, p.r or 255)
		pushU8(b, p.g or 255)
		pushU8(b, p.b or 255)
		pushU16(b, p.physW or 0)
		pushU16(b, p.physH or 0)
		pushU8(b, p.kind or 0)
		pushU8(b, #prefix)
		pushBytes(b, prefix)
		pushU16(b, #text)
		pushBytes(b, text)
	elseif p.type == P.TYPE_VOICE or p.type == P.TYPE_CHAT then
		local race = P.Utf8Truncate(p.race or "", P.MAX_RACE_BYTES)
		local sender = P.Utf8Truncate(p.sender or "", P.MAX_SENDER_BYTES)
		local room = P.MAX_BYTES - P.VOICE_FIXED_BYTES - #race - 1 - #sender - 2 - 2
		local rawText = p.text or ""
		local text = P.Utf8Truncate(rawText, room)
		local flags = p.flags or 0
		if #text < #rawText and floor(flags / P.FLAG_TRUNCATED) % 2 == 0 then
			flags = flags + P.FLAG_TRUNCATED
		end
		pushU8(b, flags)
		pushU8(b, p.kind or 0)
		pushU8(b, p.sex or 0)
		if p.distance == nil then
			pushU16(b, P.DIST_UNKNOWN)
		else
			local d = floor(p.distance * 10 + 0.5)
			if d < 0 then
				d = 0
			elseif d > P.DIST_UNKNOWN - 1 then
				d = P.DIST_UNKNOWN - 1
			end
			pushU16(b, d)
		end
		pushU8(b, p.distMethod or 0)
		pushU8(b, #race)
		pushBytes(b, race)
		pushU8(b, #sender)
		pushBytes(b, sender)
		pushU16(b, #text)
		pushBytes(b, text)
	elseif p.type == P.TYPE_STATE then
		pushU8(b, p.flags or 0)
		pushByte(b, p.level)
		pushU32(b, p.xp)
		pushU32(b, p.xpMax)
		pushU32(b, p.gold)
		pushU16(b, p.silverCopper or 0)
		pushByte(b, p.bagFree)
		pushByte(b, p.bagSlots)
		pushByte(b, p.groupSize)
		for _, field in ipairs({ { p.name, P.MAX_NAME_BYTES }, { p.zone, P.MAX_ZONE_BYTES }, { p.subzone, P.MAX_ZONE_BYTES } }) do
			local s = P.Utf8Truncate(field[1] or "", field[2])
			pushU8(b, #s)
			pushBytes(b, s)
		end
		local members = p.members or {}
		local count = #members
		if count > P.MAX_MEMBERS then
			count = P.MAX_MEMBERS
		end
		pushU8(b, count)
		for i = 1, count do
			local s = P.Utf8Truncate(members[i] or "", P.MAX_NAME_BYTES)
			pushU8(b, #s)
			pushBytes(b, s)
		end
	elseif p.type == P.TYPE_QUEST then
		pushU16(b, p.sync or 0)
		pushByte(b, p.index)
		pushByte(b, p.total)
		pushU32(b, p.questID)
		pushByte(b, p.level)
		pushU8(b, p.flags or 0)
		pushByte(b, p.group)
		pushU32(b, p.poiMap)
		pushNorm16(b, p.poiX)
		pushNorm16(b, p.poiY)
		if p.distance == nil then
			pushU16(b, P.DIST_UNKNOWN)
		else
			local d = floor(p.distance + 0.5)
			if d > P.DIST_UNKNOWN - 1 then
				d = P.DIST_UNKNOWN - 1
			end
			pushU16(b, d)
		end
		for _, field in ipairs({ { p.title, P.MAX_QUEST_TITLE_BYTES }, { p.zone, P.MAX_QUEST_ZONE_BYTES } }) do
			local s = P.Utf8Truncate(field[1] or "", field[2])
			pushU8(b, #s)
			pushBytes(b, s)
		end
		local objectives = p.objectives or {}
		local count = #objectives
		if count > P.MAX_OBJECTIVES then
			count = P.MAX_OBJECTIVES
		end
		pushU8(b, count)
		for i = 1, count do
			local o = objectives[i]
			local s = P.Utf8Truncate(o.text or "", P.MAX_OBJECTIVE_BYTES)
			pushU8(b, o.done and 1 or 0)
			pushU8(b, #s)
			pushBytes(b, s)
		end
	elseif p.type == P.TYPE_POS then
		pushU32(b, p.mapID)
		pushNorm16(b, p.x)
		pushNorm16(b, p.y)
		pushU16(b, floor((p.width or 0) + 0.5))
		pushU16(b, floor((p.height or 0) + 0.5))
	end
	pushU16(b, P.Fletcher16(b, 3, #b))
	return b
end

-- bytes -> list of 6-bit cell values, calibration cells first
function P.BytesToCells(bytes)
	local cells = {}
	for k = 0, P.CALIB - 1 do
		cells[#cells + 1] = k * 21
	end
	local n = #bytes
	local i = 1
	while i <= n do
		local v = (bytes[i] or 0) * 65536 + (bytes[i + 1] or 0) * 256 + (bytes[i + 2] or 0)
		cells[#cells + 1] = floor(v / 262144) % 64
		cells[#cells + 1] = floor(v / 4096) % 64
		cells[#cells + 1] = floor(v / 64) % 64
		cells[#cells + 1] = v % 64
		i = i + 3
	end
	return cells
end

function P.CellColor(v)
	local L = P.LEVELS
	return L[floor(v / 16) % 4 + 1], L[floor(v / 4) % 4 + 1], L[v % 4 + 1]
end

return P
