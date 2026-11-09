-- WoW Voice Translator - in-game part.
--
-- What it does:
--   * while the mouse rests on a chat line it draws a tiny grid of coloured cells in the
--     top-left corner of the screen (see Protocol.lua).  The desktop app reads those
--     pixels, translates the line EN->PL with a local model and paints the Polish text
--     on a click-through overlay exactly over that chat line.  Mouse leaves -> the grid
--     disappears -> the overlay disappears -> you see the English original again.
--   * after ~0.6 s the big grid shrinks to a 16-cell "still hovering" marker.
--   * voices (opt-in with the small panel above the chat or /wvt say|party): every new
--     /say or /party message is shown for ~0.2 s on a second grid, right of the first
--     one, with the sender's race, sex and - for /say - distance.  The app speaks it with
--     a voice that fits the character (ElevenLabs), in English or translated to Polish.
--     Channels that are switched off are never sent, so they cost nothing.
--   * voice assistant (desktop app, own push-to-talk key): /say, /yell, whispers and party
--     lines are passed on the same grid as "chat only" packets, plus a small state packet
--     (level, money, zone, bags, group).  She prepares commands such as "/wvt inv A B" in
--     the chat box; they run only when you press Enter: invite, kick, give lead, raid,
--     leave, ready check - the normal party API, refused politely during combat, boss
--     fights, M+ and PvP matches (Midnight addon restrictions).
--     She also gets your quest log (titles, objectives, progress, the game's own map marker
--     of the next objective) and where you stand, so she can say where to go.
--
-- What it does NOT do: no protected/secure calls, no chat sending, no automation.
-- Messages that the game marks as "secret values" (Midnight rules, e.g. during
-- encounters in instances) are never read - the app shows a lock icon instead.

local ADDON_NAME, ns = ...
local P = ns.Protocol

local floor, sqrt = math.floor, math.sqrt
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
local VOICE_PACKET_SECONDS = 0.2 -- each voice packet stays this long (app reads 25x/s)
local VOICE_QUEUE_MAX = 8

local defaults = {
	enabled = true,
	dwell = 0.12, -- mouse must rest this long on a line before we signal
	voiceSay = false, -- read /say aloud (costs ElevenLabs credits) - off until you switch it on
	voiceParty = false, -- read /party aloud
	voiceLang = "en", -- "en": hear the English original (your character repeats your line)
	--                    "pl": hear others translated to Polish (your own lines stay silent)
	panelShown = true,
	voiceDebug = false,
	assistant = true, -- pass chat lines and the player's state to the voice assistant (never spoken, free)
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

-- chat events that can be spoken, and which toggle controls them
local VOICE_EVENTS = {
	CHAT_MSG_SAY = "say",
	CHAT_MSG_PARTY = "party",
	CHAT_MSG_PARTY_LEADER = "party",
}

-- chat the voice assistant reads (who asked for an invite, whispers...): sent as TYPE_CHAT
-- (never spoken) unless the line is spoken anyway
local ASSISTANT_EVENTS = {
	CHAT_MSG_SAY = true,
	CHAT_MSG_YELL = true,
	CHAT_MSG_WHISPER = true,
	CHAT_MSG_WHISPER_INFORM = true,
	CHAT_MSG_PARTY = true,
	CHAT_MSG_PARTY_LEADER = true,
	CHAT_MSG_RAID = true,
	CHAT_MSG_RAID_LEADER = true,
}
-- anything that changes what the state packet says
local STATE_EVENTS = {
	"PLAYER_ENTERING_WORLD", "PLAYER_MONEY", "PLAYER_XP_UPDATE", "PLAYER_LEVEL_UP", "ZONE_CHANGED",
	"ZONE_CHANGED_NEW_AREA", "ZONE_CHANGED_INDOORS", "BAG_UPDATE_DELAYED", "GROUP_ROSTER_UPDATE",
	"PARTY_LEADER_CHANGED", "PLAYER_REGEN_DISABLED", "PLAYER_REGEN_ENABLED", "ADDON_RESTRICTION_STATE_CHANGED",
}
-- quest log changes: scan again (at most every QUEST_SCAN_SECONDS)
local QUEST_EVENTS = {
	"QUEST_LOG_UPDATE", "QUEST_ACCEPTED", "QUEST_REMOVED", "QUEST_TURNED_IN", "QUEST_WATCH_LIST_CHANGED",
	"UNIT_QUEST_LOG_CHANGED", "ZONE_CHANGED_NEW_AREA",
}
local QUEST_SET_EVENTS = { QUEST_ACCEPTED = true, QUEST_REMOVED = true, QUEST_TURNED_IN = true }
local QUEST_SCAN_SECONDS = 2
local QUEST_FULL_SECONDS = 60 -- every quest again once a minute, so a restarted app catches up
local POS_CHECK_SECONDS = 0.5
local POS_MIN_SECONDS = 2 -- at most one position packet per 2 s...
local POS_HEARTBEAT_SECONDS = 20 -- ...at least one per 20 s
local POS_MIN_YARDS = 4 -- ...and only after moving this far
local LOG_QUEUE_MAX = 12
local STATE_MIN_SECONDS = 1.0 -- at most one state packet per second...
local STATE_HEARTBEAT_SECONDS = 20 -- ...and at least one every 20 s, so a freshly started app knows
local PLAYER_HEARTBEAT_SECONDS = 60 -- class / race / faction: rarely needed, cheap
local DONE_FIRST_DELAY = 5 -- completed quests: the first list a few seconds after login...
local DONE_FULL_SECONDS = 300 -- ...then the whole list every 5 minutes (a restarted app catches up)
local LOOT_RETRIES = 4 -- item data of a roll can arrive a moment after START_LOOT_ROLL

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

-- call a WoW API that may be missing, restricted or erroring in this client
local function SafeCall(fn, ...)
	if type(fn) ~= "function" then
		return nil
	end
	local ok, a, b, c, d, e, f, g, h = pcall(fn, ...)
	if not ok then
		return nil
	end
	return a, b, c, d, e, f, g, h
end

---------------------------------------------------------------------------- signal grids

local function ComputeCellSize(frame)
	local _, physH = GetPhysicalScreenSize()
	if not physH or physH <= 0 then
		physH = 768
	end
	local scale = frame:GetEffectiveScale()
	if not scale or scale <= 0 then
		scale = 1
	end
	return P.CELL_PX * 768 / physH / scale
end

-- a grid of P.COLS x P.MAX_ROWS cells, `xCells` cells right of the top-left corner
local function NewGrid(name, xCells)
	local frame = CreateFrame("Frame", name, UIParent)
	frame:SetFrameStrata("TOOLTIP")
	frame:SetFrameLevel(9000)
	frame:SetToplevel(true)
	if frame.SetIgnoreParentScale then
		frame:SetIgnoreParentScale(true)
	end
	if frame.SetIgnoreParentAlpha then
		frame:SetIgnoreParentAlpha(true)
	end
	frame:EnableMouse(false)
	frame:SetPoint("TOPLEFT", UIParent, "TOPLEFT", 0, 0)
	frame:Hide()

	local grid = { frame = frame, textures = {}, shown = 0, cell = 2, xCells = xCells }

	function grid:Layout(k)
		local tex = self.textures[k]
		local idx = k - 1
		local col, row = idx % P.COLS, floor(idx / P.COLS)
		tex:ClearAllPoints()
		tex:SetPoint("TOPLEFT", self.frame, "TOPLEFT", col * self.cell, -row * self.cell)
		tex:SetSize(self.cell, self.cell)
	end

	function grid:UpdateGeometry()
		self.cell = ComputeCellSize(self.frame)
		self.frame:ClearAllPoints()
		self.frame:SetPoint("TOPLEFT", UIParent, "TOPLEFT", self.xCells * self.cell, 0)
		self.frame:SetSize(P.COLS * self.cell, P.MAX_ROWS * self.cell)
		for k = 1, #self.textures do
			self:Layout(k)
		end
	end

	function grid:Texture(k)
		local tex = self.textures[k]
		if not tex then
			tex = self.frame:CreateTexture(nil, "OVERLAY", nil, 7)
			self.textures[k] = tex
			self:Layout(k)
		end
		return tex
	end

	function grid:Hide()
		if self.shown > 0 or self.frame:IsShown() then
			for k = 1, self.shown do
				self.textures[k]:Hide()
			end
			self.shown = 0
			self.frame:Hide()
		end
	end

	function grid:ShowBytes(bytes)
		local cells = P.BytesToCells(bytes)
		for k = 1, #cells do
			local tex = self:Texture(k)
			tex:SetColorTexture(P.CellColor(cells[k]))
			tex:Show()
		end
		for k = #cells + 1, self.shown do
			self.textures[k]:Hide()
		end
		self.shown = #cells
		self.frame:Show()
	end

	return grid
end

local hoverGrid = NewGrid("WoWVoiceTranslatorSignal", 0)
local voiceGrid = NewGrid("WoWVoiceTranslatorVoiceSignal", P.VOICE_GRID_X_CELLS)

local function UpdateGeometry()
	hoverGrid:UpdateGeometry()
	voiceGrid:UpdateGeometry()
end

local function HideSignal()
	hoverGrid:Hide()
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
		hoverGrid:ShowBytes(P.EncodePacket(pkt))
		state.sentKey, state.sentAt, state.keepShown = key, now, false
	elseif not state.keepShown and now - state.sentAt >= FULL_PACKET_SECONDS then
		hoverGrid:ShowBytes(P.EncodePacket({ type = P.TYPE_KEEP, seq = seq }))
		state.keepShown = true
	end
end

---------------------------------------------------------------------------- voice relay

local voiceQueue = {}
local logQueue = {} -- assistant-only lines: shown when no spoken line is waiting
local voiceShownUntil = 0
local voiceSeq = 0
local voiceStats = { sent = 0, dropped = 0, last = nil }
local stateDirty, lastStateAt, stateSeq = true, -1000, 0
local BuildStatePacket -- defined with the assistant helpers below
local AssistantExtra -- position and quest packets, defined below

local function VoiceTick(now)
	if voiceShownUntil > 0 and now < voiceShownUntil then
		return
	end
	local nextBytes = table.remove(voiceQueue, 1) or table.remove(logQueue, 1)
	if not nextBytes and db.assistant and BuildStatePacket
		and ((stateDirty and now - lastStateAt >= STATE_MIN_SECONDS) or now - lastStateAt >= STATE_HEARTBEAT_SECONDS) then
		nextBytes = BuildStatePacket()
		stateDirty, lastStateAt = false, now
	end
	if not nextBytes and db.assistant and AssistantExtra then
		nextBytes = AssistantExtra(now)
	end
	if nextBytes then
		voiceGrid:ShowBytes(nextBytes)
		voiceShownUntil = now + VOICE_PACKET_SECONDS
	elseif voiceShownUntil > 0 then
		voiceGrid:Hide()
		voiceShownUntil = 0
	end
end

local function EnqueueVoice(bytes)
	if #voiceQueue >= VOICE_QUEUE_MAX then
		table.remove(voiceQueue, 1) -- the oldest line is stale anyway
		voiceStats.dropped = voiceStats.dropped + 1
	end
	voiceQueue[#voiceQueue + 1] = bytes
	voiceStats.sent = voiceStats.sent + 1
end

local function EnqueueLog(bytes)
	if #logQueue >= LOG_QUEUE_MAX then
		table.remove(logQueue, 1)
	end
	logQueue[#logQueue + 1] = bytes
end

local function GuidOf(unit)
	return Plain(SafeCall(UnitGUID, unit), "string")
end

-- a unit token for the sender, so we can measure the distance: target, mouseover,
-- group members or a visible nameplate. Strangers without a nameplate have none.
local function FindUnitForGUID(guid)
	for _, unit in ipairs({ "target", "mouseover", "focus", "softfriend", "softinteract" }) do
		if GuidOf(unit) == guid then
			return unit, false
		end
	end
	for i = 1, 4 do
		local unit = "party" .. i
		if GuidOf(unit) == guid then
			return unit, false
		end
	end
	for i = 1, 40 do
		local unit = "raid" .. i
		if GuidOf(unit) == guid then
			return unit, false
		end
	end
	for i = 1, 40 do
		local unit = "nameplate" .. i
		if GuidOf(unit) == guid then
			return unit, true
		end
	end
	return nil, false
end

-- distance in yards, best method first; every step may be unavailable in this client
local function DistanceTo(unit, viaNameplate)
	local d2, checked = SafeCall(UnitDistanceSquared, unit)
	d2 = Plain(d2, "number")
	if d2 and d2 >= 0 and IsTrue(checked) then
		return sqrt(d2), P.DIST_EXACT
	end
	local py, px, _, pInst = SafeCall(UnitPosition, "player")
	local uy, ux, _, uInst = SafeCall(UnitPosition, unit)
	px, py, ux, uy = Plain(px, "number"), Plain(py, "number"), Plain(ux, "number"), Plain(uy, "number")
	pInst, uInst = Plain(pInst, "number"), Plain(uInst, "number")
	if px and py and ux and uy and pInst and pInst == uInst then
		local dx, dy = px - ux, py - uy
		return sqrt(dx * dx + dy * dy), P.DIST_EXACT
	end
	-- interaction ranges: 3 = duel (~10 yd), 2 = trade (~11 yd), 4 = follow (~28 yd)
	if IsTrue(SafeCall(CheckInteractDistance, unit, 3)) then
		return 8, P.DIST_RANGE
	end
	if IsTrue(SafeCall(CheckInteractDistance, unit, 2)) then
		return 11, P.DIST_RANGE
	end
	if IsTrue(SafeCall(CheckInteractDistance, unit, 4)) then
		return 20, P.DIST_RANGE
	end
	if viaNameplate then
		return 35, P.DIST_NAMEPLATE
	end
	return nil, P.DIST_NONE
end

local function VoiceChannelEnabled(channel)
	if channel == "say" then
		return db.voiceSay
	elseif channel == "party" then
		return db.voiceParty
	end
	return false
end

local function OnVoiceChat(event, text, playerName, _, _, _, _, _, _, _, languageID, _, guid)
	local channel = VOICE_EVENTS[event]
	local speak = channel ~= nil and VoiceChannelEnabled(channel)
	local forAssistant = db.assistant and ASSISTANT_EVENTS[event]
	if not speak and not forAssistant then
		return
	end
	text = Plain(text, "string") -- secret during chat lockdown: never read
	if not text then
		return
	end
	text = P.Trim(P.StripMarkup(text))
	if text == "" then
		return
	end
	guid = Plain(guid, "string")
	local me = GuidOf("player")
	-- an outgoing whisper names its recipient as the "sender"; it is still your own line
	local isSelf = (guid ~= nil and guid == me) or event == "CHAT_MSG_WHISPER_INFORM"
	if speak and isSelf and db.voiceLang == "pl" then
		speak = false -- Polish mode: you already said it yourself
		if not forAssistant then
			return
		end
	end
	if not isSelf then
		local lid = Plain(languageID, "number")
		if lid and lid > 0 and C_ChatInfo and C_ChatInfo.CanPlayerSpeakLanguage then
			local can = SafeCall(C_ChatInfo.CanPlayerSpeakLanguage, lid)
			if can ~= nil and not issecretvalue(can) and not can then
				return -- the other faction's language arrives garbled - not worth a voice
			end
		end
	end

	local race, sex = "", 0
	if speak and guid then
		local _, _, _, englishRace, gender = SafeCall(GetPlayerInfoByGUID, guid)
		race = Plain(englishRace, "string") or ""
		sex = Plain(gender, "number") or 0
	end

	local distance, method = nil, P.DIST_NONE
	local unit
	if not speak then
		distance, method = nil, P.DIST_NONE -- the assistant does not need it
	elseif isSelf then
		distance, method = 0, P.DIST_SELF
	elseif channel == "say" and guid then
		local viaNameplate
		unit, viaNameplate = FindUnitForGUID(guid)
		if unit then
			distance, method = DistanceTo(unit, viaNameplate)
		end
	end

	local flags = 0
	if isSelf then
		flags = flags + P.VFLAG_SELF
	end
	if db.voiceLang == "pl" then
		flags = flags + P.VFLAG_LANG_PL
	end
	voiceSeq = (voiceSeq + 1) % 65536
	local sender = Plain(playerName, "string") or ""
	local bytes = P.EncodePacket({
		type = speak and P.TYPE_VOICE or P.TYPE_CHAT,
		seq = voiceSeq,
		flags = flags,
		kind = KIND[event] or 0,
		sex = sex,
		distance = distance,
		distMethod = method,
		race = race,
		sender = sender,
		text = text,
	})
	if not speak then
		EnqueueLog(bytes)
		return
	end
	EnqueueVoice(bytes)
	voiceStats.last = {
		sender = sender,
		race = race,
		sex = sex,
		distance = distance,
		method = method,
		unit = unit or "-",
	}
	if db.voiceDebug then
		Print(("glos: %s [%s, plec %d] %s, metoda %d (%s)"):format(
			sender,
			race ~= "" and race or "rasa ?",
			sex,
			distance and ("%.1f yd"):format(distance) or "odleglosc ?",
			method,
			unit or "-"
		))
	end
end

---------------------------------------------------------------------------- assistant: state + group commands

-- an addon restriction (Midnight) blocks kick / lead / raid / ready check right now
local function IsRestricted()
	local ra = C_RestrictedActions
	local kinds = Enum and Enum.AddOnRestrictionType
	if ra and ra.IsAddOnRestrictionActive and kinds then
		for _, key in ipairs({ "Combat", "Encounter", "ChallengeMode", "PvPMatch" }) do
			if kinds[key] ~= nil and IsTrue(SafeCall(ra.IsAddOnRestrictionActive, kinds[key])) then
				return true
			end
		end
		return false
	end
	return IsTrue(SafeCall(InCombatLockdown))
end

local function FullName(unit)
	local name, realm = SafeCall(UnitName, unit)
	name, realm = Plain(name, "string"), Plain(realm, "string")
	if not name or name == "" then
		return nil
	end
	if realm and realm ~= "" then
		return name .. "-" .. realm
	end
	return name
end

local function BagSpace()
	local c = C_Container or {}
	local freeFn = c.GetContainerNumFreeSlots or GetContainerNumFreeSlots
	local slotsFn = c.GetContainerNumSlots or GetContainerNumSlots
	local free, total = 0, 0
	for bag = 0, 4 do
		free = free + (Plain(SafeCall(freeFn, bag), "number") or 0)
		total = total + (Plain(SafeCall(slotsFn, bag), "number") or 0)
	end
	return free, total
end

BuildStatePacket = function()
	local inGroup = IsTrue(SafeCall(IsInGroup))
	local inRaid = IsTrue(SafeCall(IsInRaid))
	local flags = 0
	if inGroup then
		flags = flags + P.SFLAG_GROUP
	end
	if inRaid then
		flags = flags + P.SFLAG_RAID
	end
	if inGroup and IsTrue(SafeCall(UnitIsGroupLeader, "player")) then
		flags = flags + P.SFLAG_LEADER
	end
	if IsRestricted() then
		flags = flags + P.SFLAG_RESTRICTED
	end
	if IsTrue(SafeCall(InCombatLockdown)) then
		flags = flags + P.SFLAG_COMBAT
	end
	local members = {}
	if inGroup and not inRaid then
		for i = 1, 4 do
			local n = FullName("party" .. i)
			if n then
				members[#members + 1] = n
			end
		end
	end
	local money = Plain(SafeCall(GetMoney), "number") or 0
	local free, total = BagSpace()
	stateSeq = (stateSeq + 1) % 65536
	return P.EncodePacket({
		type = P.TYPE_STATE,
		seq = stateSeq,
		flags = flags,
		level = Plain(SafeCall(UnitLevel, "player"), "number") or 0,
		xp = Plain(SafeCall(UnitXP, "player"), "number") or 0,
		xpMax = Plain(SafeCall(UnitXPMax, "player"), "number") or 0,
		gold = floor(money / 10000),
		silverCopper = money % 10000,
		bagFree = free,
		bagSlots = total,
		groupSize = Plain(SafeCall(GetNumGroupMembers), "number") or 0,
		name = FullName("player") or "",
		zone = Plain(SafeCall(GetZoneText), "string") or "",
		subzone = Plain(SafeCall(GetSubZoneText), "string") or "",
		members = members,
	})
end

---------------------------------------------------------------------------- assistant: quests + position

local questQueue = {}
local questsDirty, questSetChanged = true, true
local questSync, questSeq, posSeq = 0, 0, 0
local lastQuestScan, lastFullQuestSend = -1000, -1000
local questSent = {} -- questID -> what the app got last (title, flags, objectives, marker)
local questStats = { quests = 0, withMarker = 0 }
local lastPosCheck, lastPosAt, lastPos = -1000, -1000, nil

local function PlayerMap()
	return Plain(SafeCall(C_Map and C_Map.GetBestMapForUnit, "player"), "number")
end

local function QuestMarkers(mapID)
	local out = {}
	local list = mapID and SafeCall(C_QuestLog and C_QuestLog.GetQuestsOnMap, mapID)
	if type(list) == "table" and not issecrettable(list) then
		for _, q in ipairs(list) do
			local id, x, y = Plain(q.questID, "number"), Plain(q.x, "number"), Plain(q.y, "number")
			if id and x and y then
				out[id] = { x, y }
			end
		end
	end
	return out
end

local function MapName(mapID)
	local info = SafeCall(C_Map and C_Map.GetMapInfo, mapID)
	if type(info) == "table" and not issecrettable(info) then
		return Plain(info.name, "string")
	end
end

local function ScanQuests()
	local ql = C_QuestLog
	local n = ql and Plain(SafeCall(ql.GetNumQuestLogEntries), "number") or 0
	local mapID = PlayerMap()
	local markers = QuestMarkers(mapID)
	local quests, header = {}, ""
	for i = 1, n do
		local info = SafeCall(ql.GetInfo, i)
		if type(info) == "table" and not issecrettable(info) then
			local title = Plain(info.title, "string") or ""
			local id = Plain(info.questID, "number")
			if IsTrue(info.isHeader) then
				header = title
			elseif id and id > 0 and not IsTrue(info.isHidden) then
				local flags = 0
				if IsTrue(SafeCall(ql.ReadyForTurnIn, id)) or IsTrue(SafeCall(ql.IsComplete, id)) then
					flags = flags + P.QFLAG_COMPLETE
				end
				if IsTrue(SafeCall(ql.IsFailed, id)) then
					flags = flags + P.QFLAG_FAILED
				end
				if SafeCall(ql.GetQuestWatchType, id) ~= nil then
					flags = flags + P.QFLAG_TRACKED
				end
				local poiMap, x, y, zone = 0, 0, 0, header
				local m = markers[id]
				if m and mapID then
					poiMap, x, y = mapID, m[1], m[2]
					flags = flags + P.QFLAG_POI
				else
					local wm, wx, wy = SafeCall(ql.GetNextWaypoint, id)
					wm, wx, wy = Plain(wm, "number"), Plain(wx, "number"), Plain(wy, "number")
					if wm and wx and wy then
						poiMap, x, y = wm, wx, wy
						flags = flags + P.QFLAG_POI
						if wm ~= mapID then
							flags = flags + P.QFLAG_POI_OTHER_MAP
						end
					end
				end
				if poiMap ~= 0 then
					zone = MapName(poiMap) or zone
				end
				local dist
				local d2 = Plain(SafeCall(ql.GetDistanceSqToQuest, id), "number")
				if d2 and d2 >= 0 then
					dist = sqrt(d2)
				end
				local objectives = {}
				local objs = SafeCall(ql.GetQuestObjectives, id)
				if type(objs) == "table" and not issecrettable(objs) then
					for _, o in ipairs(objs) do
						local text = Plain(o.text, "string")
						if text and text ~= "" then
							objectives[#objectives + 1] = { done = IsTrue(o.finished), text = text }
						end
					end
				end
				quests[#quests + 1] = {
					questID = id,
					level = Plain(info.difficultyLevel, "number") or Plain(info.level, "number") or 0,
					flags = flags,
					group = Plain(info.suggestedGroup, "number") or 0,
					poiMap = poiMap,
					poiX = x,
					poiY = y,
					distance = dist,
					title = title,
					zone = zone,
					objectives = objectives,
				}
			end
		end
	end
	return quests
end

local function QuestKey(q)
	local parts = { q.title, q.flags, q.poiMap, floor(q.poiX * 1000), floor(q.poiY * 1000) }
	for _, o in ipairs(q.objectives) do
		parts[#parts + 1] = (o.done and "+" or "-") .. o.text
	end
	return table.concat(parts, "|")
end

local function QueueQuests(now)
	local quests = ScanQuests()
	questStats.quests, questStats.withMarker = #quests, 0
	if questSetChanged then -- accepted / abandoned / turned in: the app replaces its whole list
		questSync, questSetChanged, questSent = (questSync + 1) % 65536, false, {}
	end
	local full = now - lastFullQuestSend >= QUEST_FULL_SECONDS
	if full then
		lastFullQuestSend, questSent = now, {}
	end
	questQueue = {}
	for i, q in ipairs(quests) do
		if q.flags % (P.QFLAG_POI * 2) >= P.QFLAG_POI then
			questStats.withMarker = questStats.withMarker + 1
		end
		local key = QuestKey(q)
		if questSent[q.questID] ~= key then
			questSent[q.questID] = key
			q.type, q.sync, q.index, q.total = P.TYPE_QUEST, questSync, i, #quests
			questQueue[#questQueue + 1] = q
		end
	end
	if #quests == 0 and (full or questSent.empty ~= questSync) then -- an empty log is news too
		questSent.empty = questSync
		questQueue[1] = { type = P.TYPE_QUEST, sync = questSync, index = 0, total = 0, questID = 0 }
	end
end

local function PositionBytes(now)
	if now - lastPosCheck < POS_CHECK_SECONDS then
		return nil
	end
	lastPosCheck = now
	local mapID = PlayerMap()
	if not mapID then
		return nil
	end
	local pos = SafeCall(C_Map.GetPlayerMapPosition, mapID, "player")
	if type(pos) ~= "table" or issecrettable(pos) then
		return nil
	end
	local x, y = Plain(pos.x, "number"), Plain(pos.y, "number")
	if not x or not y then
		return nil
	end
	local w, h = SafeCall(C_Map.GetMapWorldSize, mapID)
	w, h = Plain(w, "number") or 0, Plain(h, "number") or 0
	local moved = true
	if lastPos and lastPos.mapID == mapID then
		local dx, dy = (x - lastPos.x) * w, (y - lastPos.y) * h
		moved = sqrt(dx * dx + dy * dy) >= POS_MIN_YARDS
	end
	local due = (moved and now - lastPosAt >= POS_MIN_SECONDS) or now - lastPosAt >= POS_HEARTBEAT_SECONDS
	if not due then
		return nil
	end
	lastPos, lastPosAt = { mapID = mapID, x = x, y = y }, now
	posSeq = (posSeq + 1) % 65536
	return P.EncodePacket({ type = P.TYPE_POS, seq = posSeq, mapID = mapID, x = x, y = y, width = w, height = h })
end

---------------------------------------------------------------------------- assistant: player, completed quests, loot

local playerSeq, lastPlayerAt, lastPlayerTry = 0, -1000, -1000
local doneQueue, doneSync, doneSeq = {}, 0, 0
local loginAt, lastDoneFull = nil, nil
local doneStats = { count = 0, sent = 0 }
local lootQueue, lootSeq = {}, 0

local function PlayerBytes(now)
	if now - lastPlayerAt < PLAYER_HEARTBEAT_SECONDS or now - lastPlayerTry < 5 then
		return nil
	end
	lastPlayerTry = now
	local _, _, classID = SafeCall(UnitClass, "player")
	local _, _, raceID = SafeCall(UnitRace, "player")
	classID, raceID = Plain(classID, "number") or 0, Plain(raceID, "number") or 0
	if classID == 0 and raceID == 0 then
		return nil -- not known yet (loading screen): ask again in 5 s
	end
	lastPlayerAt = now
	local faction = Plain(SafeCall(UnitFactionGroup, "player"), "string")
	playerSeq = (playerSeq + 1) % 65536
	return P.EncodePacket({
		type = P.TYPE_PLAYER,
		seq = playerSeq,
		classID = classID,
		raceID = raceID,
		faction = faction == "Alliance" and P.FACTION_ALLIANCE or faction == "Horde" and P.FACTION_HORDE or P.FACTION_NONE,
		sex = Plain(SafeCall(UnitSex, "player"), "number") or 0,
	})
end

-- the whole completed-quest list, sorted, in parts of MAX_DONE_IDS ids
local function QueueDoneList()
	local list = SafeCall(C_QuestLog and C_QuestLog.GetAllCompletedQuestIDs)
	if type(list) ~= "table" or issecrettable(list) then
		return
	end
	local ids = {}
	for _, id in ipairs(list) do
		id = Plain(id, "number")
		if id and id > 0 then
			ids[#ids + 1] = id
		end
	end
	table.sort(ids)
	doneStats.count = #ids
	doneSync = (doneSync + 1) % 65536
	local parts = math.max(1, math.ceil(#ids / P.MAX_DONE_IDS))
	doneQueue = {}
	for part = 1, parts do
		local chunk = {}
		for i = (part - 1) * P.MAX_DONE_IDS + 1, math.min(#ids, part * P.MAX_DONE_IDS) do
			chunk[#chunk + 1] = ids[i]
		end
		doneQueue[#doneQueue + 1] = { type = P.TYPE_DONE, sync = doneSync, part = part, parts = parts, ids = chunk }
	end
end

local function QueueDoneAdd(questID)
	questID = Plain(questID, "number")
	if questID and questID > 0 then
		table.insert(doneQueue, 1, { type = P.TYPE_DONE, sync = doneSync, part = 0, parts = 0, ids = { questID } })
	end
end

local function DoneBytes(now)
	if loginAt and now - loginAt >= DONE_FIRST_DELAY and (not lastDoneFull or now - lastDoneFull >= DONE_FULL_SECONDS) then
		lastDoneFull = now
		QueueDoneList()
	end
	local d = table.remove(doneQueue, 1)
	if d then
		doneSeq = (doneSeq + 1) % 65536
		d.seq = doneSeq
		doneStats.sent = doneStats.sent + 1
		return P.EncodePacket(d)
	end
	return nil
end

-- item stats as { { statCode, value }, ... } (Protocol.lua STAT codes; DPS x10)
local STAT_PATTERNS = {
	{ "STRENGTH", 1 }, { "AGILITY", 2 }, { "STAMINA", 3 }, { "INTELLECT", 4 }, { "SPIRIT", 5 },
	{ "RESISTANCE0_NAME", 6 }, { "DAMAGE_PER_SECOND", 7 }, { "RANGED_ATTACK_POWER", 9 }, { "ATTACK_POWER", 8 },
	{ "HEALING", 11 }, { "SPELL_DAMAGE", 10 }, { "SPELL_POWER", 10 }, { "CRIT_SPELL", 14 }, { "SPELL_CRIT", 14 },
	{ "CRIT", 12 }, { "HIT_SPELL", 15 }, { "SPELL_HIT", 15 }, { "HIT", 13 }, { "DEFENSE", 16 },
	{ "MANA_REGENERATION", 17 }, { "POWER_REGEN0", 17 }, { "BLOCK", 18 }, { "DODGE", 19 }, { "PARRY", 20 },
}

local function ItemStats(link)
	local fn = (C_Item and C_Item.GetItemStats) or GetItemStats
	local stats = SafeCall(fn, link)
	local out = {}
	if type(stats) ~= "table" or issecrettable(stats) then
		return out
	end
	for key, value in pairs(stats) do
		key, value = Plain(key, "string"), Plain(value, "number")
		if key and value then
			for _, sp in ipairs(STAT_PATTERNS) do
				if strfind(key, sp[1], 1, true) then
					out[#out + 1] = { sp[2], sp[2] == 7 and floor(value * 10 + 0.5) or floor(value + 0.5) }
					break
				end
			end
		end
	end
	table.sort(out, function(a, b) return a[1] < b[1] end)
	return out
end

local function ItemBasics(link)
	local info = (C_Item and C_Item.GetItemInfoInstant) or GetItemInfoInstant
	local _, _, _, equipLoc, _, classID, subclassID = SafeCall(info, link)
	local getInfo = (C_Item and C_Item.GetItemInfo) or GetItemInfo
	local name, _, quality, itemLevel, minLevel = SafeCall(getInfo, link)
	return {
		name = Plain(name, "string"),
		quality = Plain(quality, "number") or 0,
		itemLevel = Plain(itemLevel, "number") or 0,
		minLevel = Plain(minLevel, "number") or 0,
		equipLoc = Plain(equipLoc, "string") or "",
		classID = Plain(classID, "number") or 0,
		subclassID = Plain(subclassID, "number") or 0,
	}
end

local function BuildLoot(rollID, attempt)
	local link = Plain(SafeCall(GetLootRollItemLink, rollID), "string")
	local _, rollName, count, quality, bop, canNeed, canGreed, canDE = SafeCall(GetLootRollItemInfo, rollID)
	local item = link and ItemBasics(link)
	if not item or not item.name then
		if attempt < LOOT_RETRIES and C_Timer and C_Timer.After then
			C_Timer.After(0.5, function() BuildLoot(rollID, attempt + 1) end)
		end
		return
	end
	local flags = 0
	if IsTrue(bop) then flags = flags + P.LFLAG_BOP end
	if IsTrue(canNeed) then flags = flags + P.LFLAG_NEED end
	if IsTrue(canGreed) then flags = flags + P.LFLAG_GREED end
	if IsTrue(canDE) then flags = flags + P.LFLAG_DE end
	local equipped = {}
	for _, slot in ipairs(P.SLOTS_FOR[item.equipLoc] or {}) do
		local worn = Plain(SafeCall(GetInventoryItemLink, "player", slot), "string")
		if worn then
			local w = ItemBasics(worn)
			equipped[#equipped + 1] = { itemLevel = w.itemLevel, subclassID = w.subclassID, name = w.name or "",
				stats = ItemStats(worn) }
		end
	end
	lootQueue[#lootQueue + 1] = {
		type = P.TYPE_LOOT, rollID = rollID, flags = flags,
		quality = Plain(quality, "number") or item.quality, itemLevel = item.itemLevel, minLevel = item.minLevel,
		classID = item.classID, subclassID = item.subclassID, equipLoc = P.EQUIP_LOC_INDEX[item.equipLoc] or 0,
		count = Plain(count, "number") or 1, name = item.name or Plain(rollName, "string") or "",
		stats = ItemStats(link), equipped = equipped,
	}
end

local function LootBytes()
	local l = table.remove(lootQueue, 1)
	if l then
		lootSeq = (lootSeq + 1) % 65536
		l.seq = lootSeq
		return P.EncodePacket(l)
	end
	return nil
end

AssistantExtra = function(now)
	loginAt = loginAt or now
	local bytes = LootBytes() or PositionBytes(now) -- a loot roll has a timer: first; position: time-sensitive, tiny
	if bytes then
		return bytes
	end
	if (questsDirty and now - lastQuestScan >= QUEST_SCAN_SECONDS) or now - lastFullQuestSend >= QUEST_FULL_SECONDS then
		lastQuestScan, questsDirty = now, false
		QueueQuests(now)
	end
	local q = table.remove(questQueue, 1)
	if q then
		questSeq = (questSeq + 1) % 65536
		q.seq = questSeq
		return P.EncodePacket(q)
	end
	return PlayerBytes(now) or DoneBytes(now)
end

---------------------------------------------------------------------------- assistant: map waypoints

-- /wvt way <uiMapID> <x> <y> [label]   (x, y in percent, like the coordinates under the map)
local function SetWaypoint(rest)
	local mapID, x, y, label = strmatch(rest or "", "^(%d+)%s+([%d%.]+)%s+([%d%.]+)%s*(.-)$")
	mapID, x, y = tonumber(mapID), tonumber(x), tonumber(y)
	if not (mapID and x and y) or x < 0 or x > 100 or y < 0 or y > 100 then
		Print("uzycie: /wvt way <mapa> <x> <y> [opis]  albo  /wvt way clear")
		return
	end
	if not (C_Map and C_Map.SetUserWaypoint) then
		Print("ta wersja gry nie ma znacznikow na mapie")
		return
	end
	if C_Map.CanSetUserWaypointOnMap and not IsTrue(SafeCall(C_Map.CanSetUserWaypointOnMap, mapID)) then
		Print("na tej mapie (" .. mapID .. ") gra nie pozwala postawic znacznika")
		return
	end
	local point
	if UiMapPoint and UiMapPoint.CreateFromCoordinates then
		point = UiMapPoint.CreateFromCoordinates(mapID, x / 100, y / 100)
	elseif CreateVector2D then
		point = { uiMapID = mapID, position = CreateVector2D(x / 100, y / 100) }
	end
	if not point or not pcall(C_Map.SetUserWaypoint, point) then
		Print("nie udalo sie postawic znacznika")
		return
	end
	if C_SuperTrack and C_SuperTrack.SetSuperTrackedUserWaypoint then
		pcall(C_SuperTrack.SetSuperTrackedUserWaypoint, true)
	end
	Print(("znacznik: %s (%s %.1f, %.1f)"):format(label ~= "" and label or "cel", MapName(mapID) or mapID, x, y))
end

local function ClearWaypoint()
	if C_Map and C_Map.ClearUserWaypoint then
		pcall(C_Map.ClearUserWaypoint)
	end
	Print("znacznik usuniety")
end

-- /wvt track <questID>: the game's own arrow and marker for a quest of the quest log
local function TrackQuest(rest)
	local id = tonumber(strmatch(rest or "", "(%d+)"))
	if not id then
		Print("uzycie: /wvt track <numer questa>")
		return
	end
	local ql = C_QuestLog
	if ql and ql.GetLogIndexForQuestID and not Plain(SafeCall(ql.GetLogIndexForQuestID, id), "number") then
		Print("tego questa nie ma w dzienniku (" .. id .. ")")
		return
	end
	if ql and ql.AddQuestWatch and SafeCall(ql.GetQuestWatchType, id) == nil then
		pcall(ql.AddQuestWatch, id)
	end
	if C_SuperTrack and C_SuperTrack.SetSuperTrackedQuestID then
		pcall(C_SuperTrack.SetSuperTrackedQuestID, id)
	end
	local title = ql and ql.GetTitleForQuestID and Plain(SafeCall(ql.GetTitleForQuestID, id), "string")
	Print("prowadze do: " .. (title or ("quest " .. id)))
end

-- C_PartyInfo first (WoW Forever), the old globals as a fallback; true when the call went through
local function PartyCall(name, ...)
	local fn = (C_PartyInfo and C_PartyInfo[name]) or _G[name]
	if type(fn) ~= "function" then
		return false
	end
	return (pcall(fn, ...))
end

local function GroupActionAllowed(needLeader)
	if IsRestricted() then
		Print("teraz nie moge tego zrobic (walka, boss, M+ albo PvP) - sprobuj po walce")
		return false
	end
	if needLeader and IsTrue(SafeCall(IsInGroup)) and not IsTrue(SafeCall(UnitIsGroupLeader, "player")) then
		Print("to moze zrobic tylko lider grupy")
		return false
	end
	return true
end

local function Words(rest)
	local out = {}
	for w in string.gmatch(rest or "", "%S+") do
		out[#out + 1] = w
	end
	return out
end

local function InviteNames(names)
	if #names == 0 then
		Print("uzycie: /wvt inv Imie1 Imie2 ...")
		return
	end
	local inRaid = IsTrue(SafeCall(IsInRaid))
	local size = Plain(SafeCall(GetNumGroupMembers), "number") or 0
	local room = inRaid and (40 - size) or (5 - math.max(1, size))
	local invited, skipped = {}, {}
	for _, name in ipairs(names) do
		if #invited < room and PartyCall("InviteUnit", name) then
			invited[#invited + 1] = name
		else
			skipped[#skipped + 1] = name
		end
	end
	if #invited > 0 then
		Print("zaproszenia wyslane: " .. table.concat(invited, ", "))
	end
	if #skipped > 0 then
		Print("bez zaproszenia (brak miejsca w party - /wvt raid): " .. table.concat(skipped, ", "))
	end
	stateDirty = true
end

local function GroupCommand(cmd, rest)
	local names = Words(rest)
	if cmd == "inv" then
		InviteNames(names)
	elseif cmd == "kick" then
		if names[1] and GroupActionAllowed(true) and PartyCall("UninviteUnit", names[1]) then
			Print("usuwam z grupy: " .. names[1])
		end
	elseif cmd == "lead" then
		if names[1] and GroupActionAllowed(true) and PartyCall("PromoteToLeader", names[1]) then
			Print("lider dla: " .. names[1])
		end
	elseif cmd == "raid" then
		if GroupActionAllowed(true) and PartyCall("ConvertToRaid") then
			Print("zmieniam grupe w raid")
		end
	elseif cmd == "leave" then
		if PartyCall("LeaveParty") then
			Print("opuszczam grupe")
		end
	elseif cmd == "ready" then
		if GroupActionAllowed(true) and PartyCall("DoReadyCheck") then
			Print("ready check")
		end
	end
	stateDirty = true
end

---------------------------------------------------------------------------- update driver

local driver = CreateFrame("Frame")
local acc = 0
local errorReported, voiceErrorReported = false, false
driver:SetScript("OnUpdate", function(_, elapsed)
	local ok, err = pcall(VoiceTick, GetTime())
	if not ok then
		voiceGrid:Hide()
		voiceQueue = {}
		voiceShownUntil = 0
		if not voiceErrorReported then
			voiceErrorReported = true
			Print("blad przekazywania glosu (zglaszam tylko raz): " .. tostring(err))
		end
	end
	acc = acc + elapsed
	if acc < POLL_INTERVAL then
		return
	end
	acc = 0
	ok, err = pcall(HoverTick)
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
	hoverGrid:ShowBytes(P.EncodePacket(pkt))
	state.testUntil = GetTime() + TEST_SECONDS
	Print("pakiet testowy wyswietlony na " .. TEST_SECONDS .. " s - aplikacja powinna pokazac tlumaczenie na srodku ekranu.")
end

---------------------------------------------------------------------------- voice panel

local panel, buttons = nil, {}

local function OnOff(v)
	return v and "WL" or "wyl"
end

local function UpdatePanel()
	if not panel then
		return
	end
	local function paint(btn, on, label)
		btn.label:SetText(label)
		if on then
			btn.bg:SetColorTexture(0.10, 0.45, 0.15, 0.85)
		else
			btn.bg:SetColorTexture(0.18, 0.18, 0.18, 0.85)
		end
	end
	paint(buttons.say, db.voiceSay, "Say: " .. OnOff(db.voiceSay))
	paint(buttons.party, db.voiceParty, "Party: " .. OnOff(db.voiceParty))
	paint(buttons.lang, db.voiceLang == "pl", db.voiceLang == "pl" and "Glos: PL" or "Glos: EN")
	if db.panelShown then
		panel:Show()
	else
		panel:Hide()
	end
end

local function SetVoiceOption(key, value)
	db[key] = value
	UpdatePanel()
end

local function MakeButton(parent, width, x, tooltipTitle, tooltipText, onClick)
	local btn = CreateFrame("Button", nil, parent)
	btn:SetSize(width, 18)
	btn:SetPoint("LEFT", parent, "LEFT", x, 0)
	btn.bg = btn:CreateTexture(nil, "BACKGROUND")
	btn.bg:SetAllPoints(btn)
	btn.label = btn:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
	btn.label:SetPoint("CENTER", btn, "CENTER", 0, 0)
	btn:RegisterForClicks("LeftButtonUp")
	btn:SetScript("OnClick", onClick)
	btn:SetScript("OnEnter", function(self)
		if GameTooltip then
			GameTooltip:SetOwner(self, "ANCHOR_TOP")
			GameTooltip:SetText(tooltipTitle)
			GameTooltip:AddLine(tooltipText, 1, 1, 1, true)
			GameTooltip:AddLine("Shift + przeciagnij: przesun panel. /wvt panel: ukryj.", 0.6, 0.6, 0.6, true)
			GameTooltip:Show()
		end
	end)
	btn:SetScript("OnLeave", function()
		if GameTooltip then
			GameTooltip:Hide()
		end
	end)
	return btn
end

local function CreatePanel()
	panel = CreateFrame("Frame", "WoWVoiceTranslatorPanel", UIParent)
	panel:SetSize(212, 22)
	panel:SetFrameStrata("MEDIUM")
	panel:SetClampedToScreen(true)
	panel:SetMovable(true)
	panel:EnableMouse(true)
	panel:RegisterForDrag("LeftButton")
	panel:SetScript("OnDragStart", function(self)
		if IsShiftKeyDown and IsShiftKeyDown() then
			self:StartMoving()
		end
	end)
	panel:SetScript("OnDragStop", function(self)
		self:StopMovingOrSizing()
		local point, _, relPoint, x, y = self:GetPoint(1)
		db.panelPoint = { point, relPoint, x, y }
	end)
	local bg = panel:CreateTexture(nil, "BACKGROUND")
	bg:SetAllPoints(panel)
	bg:SetColorTexture(0, 0, 0, 0.45)

	local title = panel:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
	title:SetPoint("LEFT", panel, "LEFT", 4, 0)
	title:SetText("WVT")

	buttons.say = MakeButton(panel, 56, 30, "Czytaj /say na glos",
		"Nowe wiadomosci /say w poblizu czyta glos pasujacy do rasy i plci postaci, ciszej im dalej. Zuzywa kredyty ElevenLabs tylko gdy wlaczone.",
		function()
			SetVoiceOption("voiceSay", not db.voiceSay)
		end)
	buttons.party = MakeButton(panel, 64, 88, "Czytaj /party na glos",
		"Wiadomosci druzyny, zawsze ta sama glosnosc. Zuzywa kredyty ElevenLabs tylko gdy wlaczone.",
		function()
			SetVoiceOption("voiceParty", not db.voiceParty)
		end)
	buttons.lang = MakeButton(panel, 56, 154, "Jezyk glosu",
		"EN: slyszysz oryginal po angielsku, a Twoja postac powtarza Twoja wiadomosc swoim glosem. PL: slyszysz innych po polsku (tlumaczenie lokalne), czat zostaje po angielsku.",
		function()
			SetVoiceOption("voiceLang", db.voiceLang == "pl" and "en" or "pl")
		end)

	panel:ClearAllPoints()
	local p = db.panelPoint
	if type(p) == "table" and p[1] then
		panel:SetPoint(p[1], UIParent, p[2], p[3], p[4])
	elseif ChatFrame1 then
		panel:SetPoint("BOTTOMLEFT", ChatFrame1, "TOPLEFT", 0, 28)
	else
		panel:SetPoint("CENTER", UIParent, "CENTER", 0, 0)
	end
	UpdatePanel()
end

---------------------------------------------------------------------------- events / slash

local events = CreateFrame("Frame")
events:RegisterEvent("ADDON_LOADED")
events:RegisterEvent("PLAYER_LOGIN")
events:RegisterEvent("DISPLAY_SIZE_CHANGED")
events:RegisterEvent("UI_SCALE_CHANGED")
for ev in pairs(VOICE_EVENTS) do
	events:RegisterEvent(ev)
end
for ev in pairs(ASSISTANT_EVENTS) do
	if not VOICE_EVENTS[ev] then
		pcall(events.RegisterEvent, events, ev)
	end
end
local STATE_EVENT_SET = {}
for _, ev in ipairs(STATE_EVENTS) do
	STATE_EVENT_SET[ev] = true
	pcall(events.RegisterEvent, events, ev) -- an event this client lacks must not break the addon
end
local QUEST_EVENT_SET = {}
for _, ev in ipairs(QUEST_EVENTS) do
	QUEST_EVENT_SET[ev] = true
	pcall(events.RegisterEvent, events, ev)
end
pcall(events.RegisterEvent, events, "START_LOOT_ROLL")
events:SetScript("OnEvent", function(_, event, ...)
	if event == "START_LOOT_ROLL" then
		if db.assistant then
			local rollID = Plain((...), "number")
			if rollID then
				BuildLoot(rollID, 1)
			end
		end
		return
	end
	if QUEST_EVENT_SET[event] then
		questsDirty = true
		if QUEST_SET_EVENTS[event] then
			questSetChanged = true
		end
		if event == "QUEST_TURNED_IN" then
			QueueDoneAdd((...))
		end
		if not STATE_EVENT_SET[event] then
			return
		end
	end
	if STATE_EVENT_SET[event] then
		stateDirty = true
		return
	end
	if VOICE_EVENTS[event] or ASSISTANT_EVENTS[event] then
		local ok, err = pcall(OnVoiceChat, event, ...)
		if not ok and not voiceErrorReported then
			voiceErrorReported = true
			Print("blad odczytu wiadomosci do glosu (zglaszam tylko raz): " .. tostring(err))
		end
		return
	end
	local arg1 = ...
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
		CreatePanel()
		Print("aktywny. Najedz myszka na linie czatu, aby zobaczyc tlumaczenie (/wvt - pomoc).")
	else
		UpdateGeometry()
	end
end)

local function ParseOnOff(rest, current)
	rest = strlower(rest or "")
	if rest == "on" or rest == "1" then
		return true
	elseif rest == "off" or rest == "0" then
		return false
	end
	return not current
end

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
	elseif cmd == "say" then
		SetVoiceOption("voiceSay", ParseOnOff(rest, db.voiceSay))
		Print("glos /say: " .. OnOff(db.voiceSay))
	elseif cmd == "party" then
		SetVoiceOption("voiceParty", ParseOnOff(rest, db.voiceParty))
		Print("glos /party: " .. OnOff(db.voiceParty))
	elseif cmd == "lang" then
		local v = strlower(rest or "")
		if v ~= "en" and v ~= "pl" then
			v = db.voiceLang == "pl" and "en" or "pl"
		end
		SetVoiceOption("voiceLang", v)
		Print("jezyk glosu: " .. strupper(v))
	elseif cmd == "panel" then
		SetVoiceOption("panelShown", not db.panelShown)
		Print("panel glosow: " .. (db.panelShown and "widoczny" or "ukryty"))
	elseif cmd == "inv" or cmd == "kick" or cmd == "lead" or cmd == "raid" or cmd == "leave" or cmd == "ready" then
		GroupCommand(cmd, rest)
	elseif cmd == "questy" or cmd == "quests" then
		Print(("dziennik dla asystentki: %d questow, %d ze znacznikiem na mapie, ukonczonych %d (wysylane: %s)"):format(
			questStats.quests, questStats.withMarker, doneStats.count, OnOff(db.assistant)))
	elseif cmd == "way" then
		if strlower(rest or "") == "clear" then
			ClearWaypoint()
		else
			SetWaypoint(rest)
		end
	elseif cmd == "track" then
		TrackQuest(rest)
	elseif cmd == "asystent" or cmd == "assistant" then
		db.assistant = ParseOnOff(rest, db.assistant)
		if not db.assistant then
			logQueue = {}
		end
		Print("asystentka (czat i stan dla aplikacji): " .. OnOff(db.assistant))
	elseif cmd == "voicedebug" then
		db.voiceDebug = ParseOnOff(rest, db.voiceDebug)
		Print("diagnostyka glosu: " .. OnOff(db.voiceDebug))
	elseif cmd == "voice" then
		Print(("glos: say %s | party %s | jezyk %s | wyslane %d, pominiete %d"):format(
			OnOff(db.voiceSay), OnOff(db.voiceParty), strupper(db.voiceLang or "en"), voiceStats.sent, voiceStats.dropped))
		local l = voiceStats.last
		if l then
			Print(("ostatni nadawca: %s [%s, plec %d] %s, metoda %d (%s)"):format(
				l.sender, l.race ~= "" and l.race or "rasa ?", l.sex,
				l.distance and ("%.1f yd"):format(l.distance) or "odleglosc ?", l.method, l.unit))
		end
	else
		local physW, physH = GetPhysicalScreenSize()
		Print(
			("status: %s | opoznienie %.2f s | ekran %sx%s | komorka %.2f j."):format(
				db.enabled and "wlaczone" or "wylaczone",
				db.dwell or defaults.dwell,
				tostring(physW),
				tostring(physH),
				ComputeCellSize(hoverGrid.frame)
			)
		)
		Print("/wvt on | off | test | dwell <s> | say [on|off] | party [on|off] | lang en|pl | panel | voice | voicedebug")
		Print("/wvt inv Imie1 Imie2 | kick Imie | lead Imie | raid | leave | ready | asystent [on|off] | questy")
		Print("/wvt way <mapa> <x> <y> [opis] | way clear | track <numer questa>")
	end
end
