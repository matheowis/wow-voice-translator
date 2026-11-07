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

P.FLAG_SECRET = 1
P.FLAG_TRUNCATED = 2

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
