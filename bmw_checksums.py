POLY_SAE_J1850 = 0x1D


def build_crc8_table(poly: int = POLY_SAE_J1850) -> list[int]:
    table = []
    for b in range(256):
        crc = b
        for _ in range(8):
            crc = ((crc << 1) ^ poly) if (crc & 0x80) else (crc << 1)
            crc &= 0xFF
        table.append(crc)
    return table


CRC8_TABLE = build_crc8_table()


def crc8_sae_j1850(data: bytes, init: int = 0xFF, xor_out: int = 0x00) -> int:
    crc = init & 0xFF
    for b in data:
        crc = CRC8_TABLE[(crc ^ b) & 0xFF]
    return (crc ^ xor_out) & 0xFF


def xor8(data: bytes, seed: int = 0x00) -> int:
    out = seed & 0xFF
    for b in data:
        out ^= (b & 0xFF)
    return out & 0xFF


def sum8(data: bytes, seed: int = 0x00, invert: bool = False) -> int:
    out = (sum(data) + seed) & 0xFF
    return ((~out) & 0xFF) if invert else out


def apply_counter(base: int, counter: int, mode: str = 'low4') -> int:
    base &= 0xFF
    counter &= 0xFF
    if mode == 'low4':
        return (base & 0xF0) | (counter & 0x0F)
    if mode == 'high4':
        return ((counter << 4) & 0xF0) | (base & 0x0F)
    return counter


BMW_COMMON_PATTERNS = {
    '03C_terminal': {'kind': 'crc8', 'init': 0xFF, 'xor_out': 0x91, 'range': 'b[1..7]', 'counter': 'byte1 low4'},
    '0F3_rpm': {'kind': 'crc8', 'init': 0x00, 'xor_out': 0x2C, 'range': 'b[0..7]', 'counter': 'byte1 low4'},
    '1A1_speed': {'kind': 'crc8', 'init': 0x00, 'xor_out': 0x2C, 'range': 'b[0..4]', 'counter': 'byte1 low4'},
    '0AB_airbag': {'kind': 'crc8', 'init': 0xFF, 'xor_out': 0x55, 'range': 'b[1..7]', 'counter': 'byte1 full'},
    '2A7_steering': {'kind': 'crc8', 'init': 0xFF, 'xor_out': 0x9E, 'range': 'b[1..4]', 'counter': 'byte1 low4'},
    '369_online': {'kind': 'crc8', 'init': 0xFF, 'xor_out': 0xC5, 'range': 'b[1..4]', 'counter': 'byte1 low4'},
    '36F_abs': {'kind': 'crc8', 'init': 0xFF, 'xor_out': 0x17, 'range': 'b[1..4]', 'counter': 'byte1 low4'},
    '3D8_drive_mode': {'kind': 'crc8', 'init': 0xFF, 'xor_out': 0xD8, 'range': 'b[1..7]'},
    '3FD_gear': {'kind': 'crc8', 'init': 0xFF, 'xor_out': 0xD6, 'range': 'b[1..7]', 'counter': 'byte1 low4'},
}
