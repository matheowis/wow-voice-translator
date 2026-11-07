"""Pixel-signal protocol between the WoW addon and the desktop app.

WoW addons cannot open files, sockets or the clipboard, so the addon draws a small
grid of solid-colour cells in the top-left corner of the game's client area while the
mouse rests on a chat line.  The app captures that region (plain GDI screen capture,
no memory access) and decodes it.

Geometry (all in *physical* pixels, relative to the WoW client area origin):
  * the grid is ``COLS`` cells wide, at most ``MAX_ROWS`` rows high,
  * every cell is ``CELL_PX`` x ``CELL_PX`` pixels, cell ``k`` sits at
    column ``k % COLS``, row ``k // COLS``.

Cell colours carry 6 bits: each RGB channel uses one of 4 levels (0, 85, 170, 255),
value = r*16 + g*4 + b.  Cells 0..3 are calibration cells with level 0..3 on every
channel, so the decoder tolerates gamma/brightness post-processing.

Byte stream (big endian) packed 3 bytes -> 4 cells:
  magic 'W','V' | version u8 | type u8 | seq u16 | ... type specific ... | fletcher16 u16
The checksum covers every byte after the magic and before the checksum itself.

A second grid of the same geometry sits ``VOICE_GRID_X_CELLS`` cells to the right of the
first one.  It carries TYPE_VOICE packets: new /say and /party messages the player chose
(with the in-game toggles) to hear spoken.  The addon shows each one for ~0.2 s, one after
another, so hover packets and voice packets never compete for the same pixels.

This module is the single source of truth; ``addon/WoWVoiceTranslator/Protocol.lua``
implements the same encoder and ``tests/test_protocol.py`` runs the Lua code through
``lupa`` to prove that both sides agree.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

MAGIC = b"WV"
VERSION = 1
COLS = 32
MAX_ROWS = 20
CELL_PX = 4
CALIB_CELLS = 4
MAX_CELLS = COLS * MAX_ROWS
MAX_BYTES = ((MAX_CELLS - CALIB_CELLS) // 4) * 3  # 477
REGION_W = COLS * CELL_PX
REGION_H = MAX_ROWS * CELL_PX

TYPE_HOVER = 1  # full packet: geometry + text of the hovered chat line
TYPE_KEEP = 2  # compact "still hovering the same line" packet
TYPE_TEST = 3  # /wvt test – connection check, same layout as HOVER
TYPE_VOICE = 4  # new /say or /party message to read aloud (second grid)

FLAG_SECRET = 0x01  # message text is a Midnight "secret value" – unreadable for addons
FLAG_TRUNCATED = 0x02  # text had to be shortened to fit the grid
VFLAG_SELF = 0x04  # voice packet: the player wrote this message
VFLAG_LANG_PL = 0x08  # voice packet: in-game toggle says "speak Polish translations"

# voice grid position: right of the hover grid, same top edge
VOICE_GRID_X_CELLS = COLS + 2
VOICE_ORIGIN_X = VOICE_GRID_X_CELLS * CELL_PX
CAPTURE_W = VOICE_ORIGIN_X + REGION_W  # one capture covers both grids

# how the addon measured the distance to the speaker
DIST_UNKNOWN = 0xFFFF  # encoded "no distance"
DIST_NONE = 0  # no unit token for the sender (not target/group/nameplate)
DIST_EXACT = 1  # UnitDistanceSquared / UnitPosition, in yards
DIST_RANGE = 2  # CheckInteractDistance bucket (estimate)
DIST_NAMEPLATE = 3  # only "has a visible nameplate", i.e. further than the interact ranges
DIST_SELF = 4  # the player
DIST_METHODS = {0: "none", 1: "exact", 2: "range", 3: "nameplate", 4: "self"}

MAX_RACE_BYTES = 24
MAX_SENDER_BYTES = 48
# magic 2 + version/type 2 + seq 2 + flags 1 + kind 1 + sex 1 + distance 2 + method 1 + race_len 1
VOICE_FIXED_BYTES = 13

LEVEL_VALUES = (0, 85, 170, 255)

# chat kind ids (must match KIND table in Core.lua)
CHAT_KINDS = {
    0: "OTHER",
    1: "SAY",
    2: "YELL",
    3: "PARTY",
    4: "PARTY_LEADER",
    5: "RAID",
    6: "RAID_LEADER",
    7: "RAID_WARNING",
    8: "GUILD",
    9: "OFFICER",
    10: "WHISPER",
    11: "WHISPER_INFORM",
    12: "BN_WHISPER",
    13: "BN_WHISPER_INFORM",
    14: "CHANNEL",
    15: "INSTANCE_CHAT",
    16: "INSTANCE_CHAT_LEADER",
    17: "EMOTE",
    18: "TEXT_EMOTE",
    19: "SYSTEM",
    20: "MONSTER_SAY",
    21: "MONSTER_YELL",
    22: "MONSTER_WHISPER",
    23: "COMMUNITIES_CHANNEL",
}


@dataclass
class Packet:
    type: int
    seq: int
    flags: int = 0
    # rectangle of the hovered chat line in WoW "base" UI units (screen is 768 units high,
    # origin bottom-left) – the app converts it to client pixels.
    left: float = 0.0
    bottom: float = 0.0
    width: float = 0.0
    height: float = 0.0
    font_height: float = 0.0
    color: tuple[int, int, int] = (255, 255, 255)
    phys_w: int = 0
    phys_h: int = 0
    chat_kind: int = 0
    prefix: str = ""
    text: str = ""
    # TYPE_VOICE only
    sender: str = ""
    race: str = ""  # englishRace token from GetPlayerInfoByGUID ("NightElf", "Scourge"...), "" = unknown
    sex: int = 0  # WoW convention: 2 male, 3 female, 0/1 unknown
    distance: Optional[float] = None  # yards, None = unknown
    dist_method: int = DIST_NONE
    extra: dict = field(default_factory=dict)

    @property
    def is_secret(self) -> bool:
        return bool(self.flags & FLAG_SECRET)

    @property
    def is_self(self) -> bool:
        return bool(self.flags & VFLAG_SELF)

    @property
    def lang_pl(self) -> bool:
        return bool(self.flags & VFLAG_LANG_PL)

    @property
    def chat_kind_name(self) -> str:
        return CHAT_KINDS.get(self.chat_kind, "OTHER")

    def identity(self) -> tuple:
        """Value used by the app to decide whether the hovered line changed."""
        return (self.seq, self.prefix, self.text, round(self.left, 1), round(self.bottom, 1))


# --------------------------------------------------------------------------- helpers

def fletcher16(data: bytes) -> int:
    s1 = 0
    s2 = 0
    for b in data:
        s1 = (s1 + b) % 255
        s2 = (s2 + s1) % 255
    return (s2 << 8) | s1


def utf8_truncate(raw: bytes, limit: int) -> bytes:
    """Cut ``raw`` to at most ``limit`` bytes without splitting a UTF-8 sequence."""
    if len(raw) <= limit:
        return raw
    cut = limit
    while cut > 0 and (raw[cut] & 0xC0) == 0x80:
        cut -= 1
    return raw[:cut]


def _u16(v: float) -> int:
    v = int(v)
    return 0 if v < 0 else 65535 if v > 65535 else v


def _fixed10(v: float) -> int:
    """Fixed point with one decimal, rounded half-up exactly like Core.lua."""
    return _u16(math.floor(v * 10 + 0.5))


def _dist_u16(v: Optional[float]) -> int:
    if v is None:
        return DIST_UNKNOWN
    return max(0, min(DIST_UNKNOWN - 1, int(math.floor(v * 10 + 0.5))))


# bytes before the prefix: magic 2 + version/type 2 + seq 2 + flags 1 + 5*u16 10
# + rgb 3 + phys 4 + kind 1 + prefix_len 1
HOVER_FIXED_BYTES = 26
MAX_PREFIX_BYTES = 80


# --------------------------------------------------------------------------- encoder

def encode_packet(p: Packet) -> bytes:
    """Encode a packet to bytes (reference implementation mirrored by Core.lua)."""
    body = bytearray()
    body += bytes([VERSION, p.type & 0xFF])
    body += _u16(p.seq).to_bytes(2, "big")
    if p.type in (TYPE_HOVER, TYPE_TEST):
        prefix = utf8_truncate(p.prefix.encode("utf-8"), MAX_PREFIX_BYTES)
        room = MAX_BYTES - HOVER_FIXED_BYTES - len(prefix) - 2 - 2
        raw_text = p.text.encode("utf-8")
        text = utf8_truncate(raw_text, room)
        flags = p.flags | (FLAG_TRUNCATED if len(text) < len(raw_text) else 0)
        body.append(flags & 0xFF)
        for v in (p.left, p.bottom, p.width, p.height, p.font_height):
            body += _fixed10(v).to_bytes(2, "big")
        body += bytes(max(0, min(255, int(c))) for c in p.color)
        body += _u16(p.phys_w).to_bytes(2, "big")
        body += _u16(p.phys_h).to_bytes(2, "big")
        body.append(p.chat_kind & 0xFF)
        body.append(len(prefix))
        body += prefix
        body += len(text).to_bytes(2, "big")
        body += text
    elif p.type == TYPE_VOICE:
        race = utf8_truncate(p.race.encode("utf-8"), MAX_RACE_BYTES)
        sender = utf8_truncate(p.sender.encode("utf-8"), MAX_SENDER_BYTES)
        room = MAX_BYTES - VOICE_FIXED_BYTES - len(race) - 1 - len(sender) - 2 - 2
        raw_text = p.text.encode("utf-8")
        text = utf8_truncate(raw_text, room)
        flags = p.flags | (FLAG_TRUNCATED if len(text) < len(raw_text) else 0)
        body.append(flags & 0xFF)
        body.append(p.chat_kind & 0xFF)
        body.append(p.sex & 0xFF)
        body += _dist_u16(p.distance).to_bytes(2, "big")
        body.append(p.dist_method & 0xFF)
        body.append(len(race))
        body += race
        body.append(len(sender))
        body += sender
        body += len(text).to_bytes(2, "big")
        body += text
    out = bytearray(MAGIC) + body
    out += fletcher16(bytes(body)).to_bytes(2, "big")
    if len(out) > MAX_BYTES:
        raise ValueError("packet too large")
    return bytes(out)


def bytes_to_cells(data: bytes) -> list[int]:
    data = bytes(data) + b"\x00" * ((-len(data)) % 3)
    cells: list[int] = []
    for i in range(0, len(data), 3):
        n = (data[i] << 16) | (data[i + 1] << 8) | data[i + 2]
        cells += [(n >> 18) & 63, (n >> 12) & 63, (n >> 6) & 63, n & 63]
    return cells


def packet_cells(p: Packet) -> list[int]:
    calib = [k * 21 for k in range(CALIB_CELLS)]  # level k on r, g and b
    return calib + bytes_to_cells(encode_packet(p))


def cell_rgb(v: int) -> tuple[int, int, int]:
    return (LEVEL_VALUES[(v >> 4) & 3], LEVEL_VALUES[(v >> 2) & 3], LEVEL_VALUES[v & 3])


def render_cells(cells: list[int], background: Optional[np.ndarray] = None) -> np.ndarray:
    """Render cells to an RGB image of size REGION_H x REGION_W (used by tests/simulator)."""
    if background is None:
        img = np.zeros((REGION_H, REGION_W, 3), dtype=np.uint8)
    else:
        img = background.copy()
    for k, v in enumerate(cells):
        row, col = divmod(k, COLS)
        y, x = row * CELL_PX, col * CELL_PX
        img[y : y + CELL_PX, x : x + CELL_PX] = cell_rgb(v)
    return img


# --------------------------------------------------------------------------- decoder

class DecodeError(Exception):
    pass


def _sample_cells(img: np.ndarray, count: int, ox: int = 0, oy: int = 0) -> np.ndarray:
    """Return (count, 3) mean colour of the 2x2 centre of each cell."""
    ks = np.arange(count)
    rows, cols = np.divmod(ks, COLS)
    ys = oy + rows * CELL_PX + CELL_PX // 2 - 1
    xs = ox + cols * CELL_PX + CELL_PX // 2 - 1
    h, w = img.shape[:2]
    if ys.max(initial=0) + 1 >= h or xs.max(initial=0) + 1 >= w:
        raise DecodeError("region too small")
    a = img[ys, xs].astype(np.int32)
    b = img[ys + 1, xs].astype(np.int32)
    c = img[ys, xs + 1].astype(np.int32)
    d = img[ys + 1, xs + 1].astype(np.int32)
    return (a + b + c + d) // 4


def _calibrate(calib: np.ndarray) -> np.ndarray:
    """Return per-channel thresholds (3 x 3) from the 4 calibration cells."""
    thresholds = np.zeros((3, 3), dtype=np.int32)
    for ch in range(3):
        levels = calib[:, ch]
        if np.any(np.diff(levels) < 20):  # not monotonic or too compressed
            raise DecodeError("calibration failed")
        thresholds[ch] = (levels[:-1] + levels[1:]) // 2
    return thresholds


def _quantize(samples: np.ndarray, thresholds: np.ndarray) -> np.ndarray:
    lv = np.zeros(samples.shape, dtype=np.int32)
    for ch in range(3):
        lv[:, ch] = np.searchsorted(thresholds[ch], samples[:, ch], side="right")
    return lv[:, 0] * 16 + lv[:, 1] * 4 + lv[:, 2]


def _cells_to_bytes(values: np.ndarray) -> bytes:
    out = bytearray()
    for i in range(0, len(values) - 3, 4):
        n = (int(values[i]) << 18) | (int(values[i + 1]) << 12) | (int(values[i + 2]) << 6) | int(values[i + 3])
        out += bytes([(n >> 16) & 255, (n >> 8) & 255, n & 255])
    return bytes(out)


def decode_image(img: np.ndarray, ox: int = 0, oy: int = 0) -> Packet:
    """Decode a packet from an RGB image whose (ox, oy) is the client-area origin."""
    calib = _sample_cells(img, CALIB_CELLS, ox, oy)
    thresholds = _calibrate(calib)

    def read_bytes(n_bytes: int) -> bytes:
        n_cells = ((n_bytes + 2) // 3) * 4
        samples = _sample_cells(img, CALIB_CELLS + n_cells, ox, oy)[CALIB_CELLS:]
        return _cells_to_bytes(_quantize(samples, thresholds))[:n_bytes]

    head = read_bytes(6)
    if head[:2] != MAGIC:
        raise DecodeError("no magic")
    if head[2] != VERSION:
        raise DecodeError(f"unsupported version {head[2]}")
    ptype = head[3]
    if ptype == TYPE_KEEP:
        data = read_bytes(8)
    elif ptype in (TYPE_HOVER, TYPE_TEST):
        fixed = 2 + 4 + 1 + 10 + 3 + 4 + 1 + 1  # up to and including prefix_len
        data = read_bytes(fixed)
        prefix_len = data[-1]
        data = read_bytes(fixed + prefix_len + 2)
        text_len = int.from_bytes(data[-2:], "big")
        total = fixed + prefix_len + 2 + text_len + 2
        if total > MAX_BYTES:
            raise DecodeError("length out of range")
        data = read_bytes(total)
    elif ptype == TYPE_VOICE:
        fixed = VOICE_FIXED_BYTES  # up to and including race_len
        data = read_bytes(fixed)
        race_len = data[-1]
        if race_len > MAX_RACE_BYTES:
            raise DecodeError("race length out of range")
        data = read_bytes(fixed + race_len + 1)
        sender_len = data[-1]
        if sender_len > MAX_SENDER_BYTES:
            raise DecodeError("sender length out of range")
        head_len = fixed + race_len + 1 + sender_len + 2
        data = read_bytes(head_len)
        text_len = int.from_bytes(data[-2:], "big")
        total = head_len + text_len + 2
        if total > MAX_BYTES:
            raise DecodeError("length out of range")
        data = read_bytes(total)
    else:
        raise DecodeError(f"unknown type {ptype}")

    body, checksum = data[2:-2], int.from_bytes(data[-2:], "big")
    if fletcher16(body) != checksum:
        raise DecodeError("checksum mismatch")

    seq = int.from_bytes(body[2:4], "big")
    if ptype == TYPE_KEEP:
        return Packet(type=ptype, seq=seq)
    if ptype == TYPE_VOICE:
        return _parse_voice(body, seq)

    pos = 4
    flags = body[pos]
    pos += 1
    nums = [int.from_bytes(body[pos + 2 * i : pos + 2 * i + 2], "big") / 10.0 for i in range(5)]
    pos += 10
    color = (body[pos], body[pos + 1], body[pos + 2])
    pos += 3
    phys_w = int.from_bytes(body[pos : pos + 2], "big")
    phys_h = int.from_bytes(body[pos + 2 : pos + 4], "big")
    pos += 4
    chat_kind = body[pos]
    pos += 1
    plen = body[pos]
    pos += 1
    prefix = body[pos : pos + plen].decode("utf-8", errors="replace")
    pos += plen
    tlen = int.from_bytes(body[pos : pos + 2], "big")
    pos += 2
    text = body[pos : pos + tlen].decode("utf-8", errors="replace")
    return Packet(
        type=ptype,
        seq=seq,
        flags=flags,
        left=nums[0],
        bottom=nums[1],
        width=nums[2],
        height=nums[3],
        font_height=nums[4],
        color=color,
        phys_w=phys_w,
        phys_h=phys_h,
        chat_kind=chat_kind,
        prefix=prefix,
        text=text,
    )


def _parse_voice(body: bytes, seq: int) -> Packet:
    pos = 4
    flags, kind, sex = body[pos], body[pos + 1], body[pos + 2]
    pos += 3
    dist_raw = int.from_bytes(body[pos : pos + 2], "big")
    pos += 2
    method = body[pos]
    pos += 1
    rlen = body[pos]
    pos += 1
    race = body[pos : pos + rlen].decode("utf-8", errors="replace")
    pos += rlen
    slen = body[pos]
    pos += 1
    sender = body[pos : pos + slen].decode("utf-8", errors="replace")
    pos += slen
    tlen = int.from_bytes(body[pos : pos + 2], "big")
    pos += 2
    text = body[pos : pos + tlen].decode("utf-8", errors="replace")
    return Packet(
        type=TYPE_VOICE,
        seq=seq,
        flags=flags,
        chat_kind=kind,
        sex=sex,
        distance=None if dist_raw == DIST_UNKNOWN else dist_raw / 10.0,
        dist_method=method,
        race=race,
        sender=sender,
        text=text,
    )


def try_decode(img: np.ndarray, search: int = 3) -> Optional[Packet]:
    """Decode, tolerating a few pixels of misalignment of the grid origin."""
    for oy in range(search + 1):
        for ox in range(search + 1):
            try:
                return decode_image(img, ox, oy)
            except DecodeError:
                continue
    return None


def try_decode_at(img: np.ndarray, x0: int, search: int = 3, back: int = 2) -> Optional[Packet]:
    """Decode a grid whose origin should be near (x0, 0); rounding may move it a few px either way."""
    for oy in range(search + 1):
        for ox in range(max(0, x0 - back), x0 + search + 1):
            try:
                return decode_image(img, ox, oy)
            except DecodeError:
                continue
    return None


# --------------------------------------------------------------------------- geometry

def base_rect_to_client_px(p: Packet, client_w: int, client_h: int) -> tuple[int, int, int, int]:
    """Convert the packet's base-unit rectangle to (x, y, w, h) client pixels (y from top)."""
    if client_h <= 0:
        return (0, 0, 0, 0)
    scale = client_h / 768.0  # WoW base coordinate system is 768 units high
    x = round(p.left * scale)
    w = round(p.width * scale)
    h = round(p.height * scale)
    y = round(client_h - (p.bottom + p.height) * scale)
    return (x, y, w, h)


def font_px(p: Packet, client_h: int) -> float:
    return p.font_height * client_h / 768.0
