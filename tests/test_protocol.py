import numpy as np
import pytest

from wvt import protocol as P


def sample_packet(**kw):
    base = dict(
        type=P.TYPE_HOVER,
        seq=513,
        flags=0,
        left=34.5,
        bottom=120.2,
        width=430.0,
        height=15.3,
        font_height=13.3,
        color=(170, 170, 255),
        phys_w=2560,
        phys_h=1440,
        chat_kind=3,
        prefix="[Party] [Bob]: ",
        text="LFM Deadmines need tank and heals, zażółć gęślą jaźń",
    )
    base.update(kw)
    return P.Packet(**base)


def test_roundtrip_clean():
    pkt = sample_packet()
    img = P.render_cells(P.packet_cells(pkt))
    out = P.decode_image(img)
    assert out.type == P.TYPE_HOVER and out.seq == 513
    assert out.prefix == pkt.prefix and out.text == pkt.text
    assert out.color == pkt.color and out.chat_kind == 3 and out.chat_kind_name == "PARTY"
    assert (out.phys_w, out.phys_h) == (2560, 1440)
    assert out.left == pytest.approx(34.5) and out.height == pytest.approx(15.3)


def test_keep_packet_roundtrip():
    img = P.render_cells(P.packet_cells(P.Packet(type=P.TYPE_KEEP, seq=77)))
    out = P.decode_image(img)
    assert out.type == P.TYPE_KEEP and out.seq == 77


def test_long_text_is_truncated_on_utf8_boundary():
    pkt = sample_packet(text="ż" * 400)
    raw = P.encode_packet(pkt)
    assert len(raw) <= P.MAX_BYTES
    out = P.decode_image(P.render_cells(P.packet_cells(pkt)))
    assert out.flags & P.FLAG_TRUNCATED
    assert set(out.text) == {"ż"}  # no broken characters


def test_survives_gamma_and_brightness():
    pkt = sample_packet()
    img = P.render_cells(P.packet_cells(pkt)).astype(np.float64) / 255.0
    # gamma 1.4 + slight brightness/contrast change + noise, like a post-process filter
    img = np.clip(0.9 * img ** 1.4 + 0.04, 0, 1)
    rng = np.random.default_rng(1)
    img = np.clip(img * 255 + rng.normal(0, 2.5, img.shape), 0, 255).astype(np.uint8)
    out = P.decode_image(img)
    assert out.text == pkt.text


def test_misaligned_origin_is_found():
    pkt = sample_packet()
    cells = P.render_cells(P.packet_cells(pkt))
    big = np.full((P.REGION_H + 8, P.REGION_W + 8, 3), 37, dtype=np.uint8)
    big[2:2 + P.REGION_H, 1:1 + P.REGION_W] = cells
    out = P.try_decode(big, search=3)
    assert out is not None and out.text == pkt.text


def test_random_game_background_is_rejected():
    rng = np.random.default_rng(5)
    for _ in range(50):
        img = rng.integers(0, 256, (P.REGION_H + 4, P.REGION_W + 4, 3), dtype=np.uint8)
        assert P.try_decode(img) is None
    flat = np.full((P.REGION_H + 4, P.REGION_W + 4, 3), 128, dtype=np.uint8)
    assert P.try_decode(flat) is None


def test_corrupted_cell_fails_checksum():
    pkt = sample_packet()
    cells = P.packet_cells(pkt)
    cells[40] ^= 0b000001
    with pytest.raises(P.DecodeError):
        P.decode_image(P.render_cells(cells))


def test_geometry_conversion():
    pkt = sample_packet(left=0, bottom=0, width=768, height=384)
    # 768 base units == client height
    x, y, w, h = P.base_rect_to_client_px(pkt, 2560, 1440)
    assert (x, y, w, h) == (0, 720, 1440, 720)
    assert P.font_px(sample_packet(font_height=14), 1440) == pytest.approx(26.25)
