-- WoW Voice Translator - in-game part.
--
-- What it does:
--   * while the mouse rests on a chat line it draws a tiny grid of coloured cells in the
--     top-left corner of the screen (see Protocol.lua).  The desktop app reads those
--     pixels, translates the line EN->PL with a local model and paints the Polish text
--     on a click-through overlay exactly over that chat line.  Mouse leaves -> the grid
--     disappears -> the overlay disappears -> you see the English original again.
--   * after ~0.6 s the big grid shrinks to a 16-cell "still hovering" marker.
--
-- What it does NOT do: no protected/secure calls, no chat sending, no automation.
-- Messages that the game marks as "secret values" (Midnight rules, e.g. during
-- encounters in instances) are never read - the app shows a lock icon instead.

local ADDON_NAME, ns = ...
local P = ns.Protocol

local floor = math.floor
local GetTime = GetTime
local issecretvalue = issecretvalue or function()
	return false
end
local issecrettable = issecrettable or function()
	return false
end

local POLL_INTERVAL = 0.05 -- seconds between hover checks
local FULL_PACKET_SECONDS = 0.6 -- how long the full packet stays before shrinking
local TEST_SECONDS = 4

local defaults = {
	enabled = true,
	dwell = 0.12, -- mouse must rest this long on a line before we signal
}

local KIND = {
	CHAT_MSG_SAY = 1,
	CHAT_MSG_YELL = 2,
	CHAT_MSG_PARTY = 3,
	CHAT_MSG_PARTY_LEADER = 4,
	CHAT_MSG_RAID = 5,
	CHAT_MSG_RAID_LEADER = 6,
	CHAT_MSG_RAID_WARNING = 7,
	CHAT_MSG_GUILD = 8,
	CHAT_MSG_OFFICER = 9,
	CHAT_MSG_WHISPER = 10,
	CHAT_MSG_WHISPER_INFORM = 11,
	CHAT_MSG_BN_WHISPER = 12,
	CHAT_MSG_BN_WHISPER_INFORM = 13,
	CHAT_MSG_CHANNEL = 14,
	CHAT_MSG_INSTANCE_CHAT = 15,
	CHAT_MSG_INSTANCE_CHAT_LEADER = 16,
	CHAT_MSG_EMOTE = 17,
	CHAT_MSG_TEXT_EMOTE = 18,
	CHAT_MSG_SYSTEM = 19,
	CHAT_MSG_MONSTER_SAY = 20,
	CHAT_MSG_MONSTER_YELL = 21,
	CHAT_MSG_MONSTER_WHISPER = 22,
	CHAT_MSG_COMMUNITIES_CHANNEL = 23,
}

local db = defaults

local function Print(msg)
	local frame = DEFAULT_CHAT_FRAME or ChatFrame1
	if frame then
		frame:AddMessage("|cff66ccffWoW Voice Translator:|r " .. msg)
	end
end

-- a value we may look at: not secret, right type
local function Plain(v, wantedType)
	if v == nil or issecretvalue(v) then
		return nil
	end
	if wantedType and type(v) ~= wantedType then
		return nil
	end
	return v
end

local function IsTrue(v)
	if v == nil or issecretvalue(v) then
		return false
	end
	return v and true or false
end

---------------------------------------------------------------------------- signal grid

local signal = CreateFrame("Frame", "WoWVoiceTranslatorSignal", UIParent)
signal:SetFrameStrata("TOOLTIP")
signal:SetFrameLevel(9000)
signal:SetToplevel(true)
if signal.SetIgnoreParentScale then
	signal:SetIgnoreParentScale(true)
end
if signal.SetIgnoreParentAlpha then
	signal:SetIgnoreParentAlpha(true)
end
signal:EnableMouse(false)
signal:SetPoint("TOPLEFT", UIParent, "TOPLEFT", 0, 0)
signal:Hide()

local textures = {}
local shownCells = 0
local cellSize = 2

local function ComputeCellSize()
	local _, physH = GetPhysicalScreenSize()
	if not physH or physH <= 0 then
		physH = 768
	end
	local scale = signal:GetEffectiveScale()
	if not scale or scale <= 0 then
		scale = 1
	end
	return P.CELL_PX * 768 / physH / scale
end

local function LayoutTexture(k)
	local tex = textures[k]
	local idx = k - 1
	local col, row = idx % P.COLS, floor(idx / P.COLS)
	tex:ClearAllPoints()
	tex:SetPoint("TOPLEFT", signal, "TOPLEFT", col * cellSize, -row * cellSize)
	tex:SetSize(cellSize, cellSize)
end

local function UpdateGeometry()
	cellSize = ComputeCellSize()
	signal:SetSize(P.COLS * cellSize, P.MAX_ROWS * cellSize)
	for k = 1, #textures do
		LayoutTexture(k)
	end
end

local function GetTexture(k)
	local tex = textures[k]
	if not tex then
		tex = signal:CreateTexture(nil, "OVERLAY", nil, 7)
		textures[k] = tex
		LayoutTexture(k)
	end
	return tex
end

local function HideSignal()
	if shownCells > 0 or signal:IsShown() then
		for k = 1, shownCells do
			textures[k]:Hide()
		end
		shownCells = 0
		signal:Hide()
	end
end

local function ShowBytes(bytes)
	local cells = P.BytesToCells(bytes)
	for k = 1, #cells do
		local tex = GetTexture(k)
		tex:SetColorTexture(P.CellColor(cells[k]))
		tex:Show()
	end
	for k = #cells + 1, shownCells do
		textures[k]:Hide()
	end
	shownCells = #cells
	signal:Show()
end

---------------------------------------------------------------------------- chat lines

local function ChatFrameList()
	local list = {}
	if type(CHAT_FRAMES) == "table" then
		for _, name in ipairs(CHAT_FRAMES) do
			list[#list + 1] = _G[name]
		end
	end
	if #list == 0 then
		for i = 1, (NUM_CHAT_WINDOWS or 10) do
			list[#list + 1] = _G["ChatFrame" .. i]
		end
	end
	return list
end

-- is the chat frame (or one of its children) the top-most frame under the cursor?
local function ChatFrameHasMouse(cf)
	if not GetMouseFoci then
		return true
	end
	local foci = GetMouseFoci()
	local top = foci and foci[1]
	if not top or top == WorldFrame then
		return true
	end
	local f = top
	for _ = 1, 6 do
		if f == cf then
			return true
		end
		f = f.GetParent and f:GetParent()
		if not f then
			break
		end
	end
	return false
end

local function FindHoveredLine()
	for _, cf in ipairs(ChatFrameList()) do
		if cf and type(cf.visibleLines) == "table" and cf:IsVisible() and IsTrue(cf:IsMouseOver()) then
			if not ChatFrameHasMouse(cf) then
				return nil
			end
			for _, line in ipairs(cf.visibleLines) do
				if line.messageInfo and line:IsShown() and IsTrue(line:IsMouseOver()) then
					return cf, line
				end
			end
		end
	end
	return nil
end

local function LineRect(line)
	local s = Plain(line:GetEffectiveScale(), "number")
	local left = Plain(line:GetLeft(), "number")
	local bottom = Plain(line:GetBottom(), "number")
	local width = Plain(line:GetWidth(), "number")
	local height = Plain(line:GetHeight(), "number")
	if not (s and left and bottom and width and height) then
		return nil
	end
	return left * s, bottom * s, width * s, height * s, s
end

local function ExtractText(info)
	local flags, kind = 0, 0
	local prefix, text = "", ""

	local ex = Plain(info.extraData, "table")
	if ex and issecrettable(ex) then
		ex = nil
	end
	local event = ex and Plain(ex[4], "string")
	if event then
		kind = KIND[event] or 0
	end
	local args = ex and Plain(ex[5], "table")
	if args and issecrettable(args) then
		args = nil
		flags = P.FLAG_SECRET
	end

	local rawBody
	if args then
		local a1 = args[1]
		if a1 ~= nil and issecretvalue(a1) then
			flags = P.FLAG_SECRET
		else
			rawBody = Plain(a1, "string")
		end
	end

	local rawLine = info.message
	if rawLine ~= nil and issecretvalue(rawLine) then
		flags = P.FLAG_SECRET
		rawLine = nil
	end
	rawLine = Plain(rawLine, "string")
	if flags == P.FLAG_SECRET then
		-- the game hides this message from addons: do not transmit any of its text
		return flags, kind, "", ""
	end

	if rawLine then
		local strippedLine = P.StripMarkup(rawLine)
		local strippedBody = rawBody and P.Trim(P.StripMarkup(rawBody)) or nil
		prefix, text = P.SplitPrefix(strippedLine, strippedBody)
		text = P.Trim(text)
	elseif rawBody then
		text = P.Trim(P.StripMarkup(rawBody))
	end
	return flags, kind, prefix, text
end

local seq = 0

local function BuildPacket(ptype, line)
	local info = line.messageInfo
	if type(info) ~= "table" or issecrettable(info) then
		return nil
	end
	local left, bottom, width, height, scale = LineRect(line)
	if not left then
		return nil
	end
	local _, fontHeight = line:GetFont()
	fontHeight = Plain(fontHeight, "number") or 14
	local physW, physH = GetPhysicalScreenSize()
	local flags, kind, prefix, text = ExtractText(info)
	local r = Plain(info.r, "number") or 1
	local g = Plain(info.g, "number") or 1
	local b = Plain(info.b, "number") or 1
	return {
		type = ptype,
		seq = seq,
		flags = flags,
		left = left,
		bottom = bottom,
		width = width,
		height = height,
		fontHeight = fontHeight * scale,
		r = floor(r * 255 + 0.5),
		g = floor(g * 255 + 0.5),
		b = floor(b * 255 + 0.5),
		physW = physW or 0,
		physH = physH or 0,
		kind = kind,
		prefix = prefix,
		text = text,
	}
end

---------------------------------------------------------------------------- hover loop

local state = {
	line = nil,
	info = nil,
	since = 0,
	sentKey = nil,
	sentAt = 0,
	keepShown = false,
	testUntil = 0,
}

local function ResetHover()
	state.line, state.info, state.sentKey, state.keepShown = nil, nil, nil, false
end

local function RectKey(line, info)
	local left, bottom, width, height = LineRect(line)
	if not left then
		return nil
	end
	return tostring(info) .. ":" .. floor(left) .. ":" .. floor(bottom) .. ":" .. floor(width) .. ":" .. floor(height)
end

local function HoverTick()
	local now = GetTime()
	if state.testUntil > now then
		return
	elseif state.testUntil > 0 then
		state.testUntil = 0
		HideSignal()
	end

	if not db.enabled then
		HideSignal()
		ResetHover()
		return
	end

	local _, line = FindHoveredLine()
	if not line then
		HideSignal()
		ResetHover()
		return
	end

	local info = line.messageInfo
	if line ~= state.line or info ~= state.info then
		state.line, state.info, state.since = line, info, now
		state.sentKey, state.keepShown = nil, false
		HideSignal()
		return
	end
	if now - state.since < (db.dwell or defaults.dwell) then
		return
	end

	local key = RectKey(line, info)
	if not key then
		HideSignal()
		return
	end
	if key ~= state.sentKey then
		seq = (seq + 1) % 65536
		local pkt = BuildPacket(P.TYPE_HOVER, line)
		if not pkt then
			HideSignal()
			return
		end
		ShowBytes(P.EncodePacket(pkt))
		state.sentKey, state.sentAt, state.keepShown = key, now, false
	elseif not state.keepShown and now - state.sentAt >= FULL_PACKET_SECONDS then
		ShowBytes(P.EncodePacket({ type = P.TYPE_KEEP, seq = seq }))
		state.keepShown = true
	end
end

local driver = CreateFrame("Frame")
local acc = 0
local errorReported = false
driver:SetScript("OnUpdate", function(_, elapsed)
	acc = acc + elapsed
	if acc < POLL_INTERVAL then
		return
	end
	acc = 0
	local ok, err = pcall(HoverTick)
	if not ok then
		HideSignal()
		ResetHover()
		if not errorReported then
			errorReported = true
			Print("blad w petli hover (zglaszam tylko raz): " .. tostring(err))
		end
	end
end)

---------------------------------------------------------------------------- test packet

local function ShowTestPacket()
	local physW, physH = GetPhysicalScreenSize()
	local baseH = 768
	local baseW = (physW and physH and physH > 0) and (768 * physW / physH) or 1024
	seq = (seq + 1) % 65536
	local pkt = {
		type = P.TYPE_TEST,
		seq = seq,
		flags = 0,
		left = baseW / 2 - 200,
		bottom = baseH / 2,
		width = 400,
		height = 16,
		fontHeight = 14,
		r = 255,
		g = 209,
		b = 0,
		physW = physW or 0,
		physH = physH or 0,
		kind = 0,
		prefix = "[Test] ",
		text = "Hello from WoW Voice Translator! If you can read this in Polish, the connection works.",
	}
	ShowBytes(P.EncodePacket(pkt))
	state.testUntil = GetTime() + TEST_SECONDS
	Print("pakiet testowy wyswietlony na " .. TEST_SECONDS .. " s - aplikacja powinna pokazac tlumaczenie na srodku ekranu.")
end

---------------------------------------------------------------------------- events / slash

local events = CreateFrame("Frame")
events:RegisterEvent("ADDON_LOADED")
events:RegisterEvent("PLAYER_LOGIN")
events:RegisterEvent("DISPLAY_SIZE_CHANGED")
events:RegisterEvent("UI_SCALE_CHANGED")
events:SetScript("OnEvent", function(_, event, arg1)
	if event == "ADDON_LOADED" and arg1 == ADDON_NAME then
		if type(WoWVoiceTranslatorDB) ~= "table" then
			WoWVoiceTranslatorDB = {}
		end
		for k, v in pairs(defaults) do
			if WoWVoiceTranslatorDB[k] == nil then
				WoWVoiceTranslatorDB[k] = v
			end
		end
		db = WoWVoiceTranslatorDB
	elseif event == "PLAYER_LOGIN" then
		UpdateGeometry()
		Print("aktywny. Najedz myszka na linie czatu, aby zobaczyc tlumaczenie (/wvt - pomoc).")
	else
		UpdateGeometry()
	end
end)

SLASH_WOWVOICETRANSLATOR1 = "/wvt"
SlashCmdList["WOWVOICETRANSLATOR"] = function(msg)
	local cmd, rest = strmatch(msg or "", "^%s*(%S*)%s*(.-)%s*$")
	cmd = strlower(cmd or "")
	if cmd == "on" then
		db.enabled = true
		Print("tlumaczenie po najechaniu: WLACZONE")
	elseif cmd == "off" then
		db.enabled = false
		HideSignal()
		Print("tlumaczenie po najechaniu: WYLACZONE")
	elseif cmd == "test" then
		UpdateGeometry()
		ShowTestPacket()
	elseif cmd == "dwell" then
		local v = tonumber(rest)
		if v and v >= 0 and v <= 2 then
			db.dwell = v
			Print("opoznienie najechania: " .. v .. " s")
		else
			Print("uzycie: /wvt dwell 0.12")
		end
	else
		local physW, physH = GetPhysicalScreenSize()
		Print(
			("status: %s | opoznienie %.2f s | ekran %sx%s | komorka %.2f j."):format(
				db.enabled and "wlaczone" or "wylaczone",
				db.dwell or defaults.dwell,
				tostring(physW),
				tostring(physH),
				ComputeCellSize()
			)
		)
		Print("/wvt on | off | test | dwell <sekundy>")
	end
end
