"""EDID blocks: checksums and fixing them, and a short description of an EDID."""

from .common import EDID_BLOCK, EDID_HEADER, HDMI_OUI, ScreenpadError


def block_checksum(block):
    return (-sum(block[: EDID_BLOCK - 1])) & 0xFF


def edid_blocks(edid):
    """Validate the EDID framing and return its block count."""
    if len(edid) < EDID_BLOCK or edid[:8] != EDID_HEADER:
        raise ScreenpadError("not an EDID (bad header or shorter than 128 bytes)")
    count = 1 + edid[126]
    if len(edid) < count * EDID_BLOCK:
        raise ScreenpadError("EDID announces %d extension(s) but has only %d bytes" % (count - 1, len(edid)))
    return count


def fix_checksums(edid):
    """Return (fixed EDID trimmed to its blocks, [(block, stored, correct)] for each fix)."""
    count = edid_blocks(edid)
    fixed = bytearray(edid[: count * EDID_BLOCK])
    changes = []
    for n in range(count):
        start = n * EDID_BLOCK
        correct = block_checksum(fixed[start : start + EDID_BLOCK])
        stored = fixed[start + EDID_BLOCK - 1]
        if stored != correct:
            fixed[start + EDID_BLOCK - 1] = correct
            changes.append((n, stored, correct))
    return bytes(fixed), changes


def checksums_ok(edid):
    try:
        return not fix_checksums(edid)[1]
    except ScreenpadError:
        return False


def manufacturer(edid):
    word = (edid[8] << 8) | edid[9]
    return "".join(chr(ord("A") - 1 + ((word >> shift) & 0x1F)) for shift in (10, 5, 0))


def detailed_timing(edid):
    """(pixel clock MHz, hactive, vactive, refresh Hz) of the first detailed timing, or None."""
    d = edid[54:72]
    clock = d[0] | (d[1] << 8)
    if clock == 0:
        return None
    hactive = d[2] | ((d[4] >> 4) << 8)
    hblank = d[3] | ((d[4] & 0xF) << 8)
    vactive = d[5] | ((d[7] >> 4) << 8)
    vblank = d[6] | ((d[7] & 0xF) << 8)
    hz = clock * 10000 / ((hactive + hblank) * (vactive + vblank))
    return clock / 100, hactive, vactive, hz


def has_hdmi_vsdb(edid):
    for n in range(1, edid_blocks(edid)):
        block = edid[n * EDID_BLOCK : (n + 1) * EDID_BLOCK]
        if block[0] != 0x02:
            continue
        pos, end = 4, block[2]
        while pos < end and pos < EDID_BLOCK - 1:
            tag, length = block[pos] >> 5, block[pos] & 0x1F
            if tag == 3 and block[pos + 1 : pos + 4] == HDMI_OUI:
                return True
            pos += 1 + length
    return False


def describe_edid(edid):
    count = edid_blocks(edid)
    product = edid[10] | (edid[11] << 8)
    parts = ["%s %04x" % (manufacturer(edid), product), "%d block(s)" % count]
    timing = detailed_timing(edid)
    if timing:
        parts.append("%dx%d @ %.2f Hz, %.2f MHz" % (timing[1], timing[2], timing[3], timing[0]))
    parts.append("HDMI VSDB" if has_hdmi_vsdb(edid) else "no HDMI VSDB")
    parts.append("checksums ok" if checksums_ok(edid) else "BAD checksums")
    return ", ".join(parts)
