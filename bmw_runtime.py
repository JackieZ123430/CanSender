TERMINAL_PAYLOADS = {
    # 用户提供的 0x03C 终端状态样本：
    # ACC: 46 55 00 12 11 00 E5 FF
    # IGN: 15 5C 06 12 22 00 2A FF
    # 注意：运行时发送器会刷新 CRC(byte0) 和 Counter(byte1低4位)，
    # 这里保存的是 byte[1..7] 的基础模板。
    'ACC': bytes.fromhex('5500121100E5FF'),
    'IGN': bytes.fromhex('5C061222002AFF'),
}


def _terminal_payload_equal(cur: bytes, ref: bytes) -> bool:
    if len(cur) != len(ref):
        return False
    # 03C 的 byte1 低4位会被当作 counter 刷新，只比较高4位和后续字节
    if (cur[0] & 0xF0) != (ref[0] & 0xF0):
        return False
    return cur[1:] == ref[1:]


def detect_terminal_state(frame_base_data: dict) -> str:
    cur = bytes(frame_base_data[0x03C][1:8])
    for name, payload in TERMINAL_PAYLOADS.items():
        if _terminal_payload_equal(cur, payload):
            return name
    return 'CUSTOM'


def apply_terminal_state(name: str, frame_base_data: dict, frame_lock, crc8_func) -> str:
    name = str(name).upper()
    if name not in TERMINAL_PAYLOADS:
        return detect_terminal_state(frame_base_data)
    with frame_lock:
        d = bytearray(frame_base_data[0x03C])
        d[1:8] = TERMINAL_PAYLOADS[name]
        d[0] = crc8_func(bytes(d[1:8]), 0xFF, 0x91)
        frame_base_data[0x03C] = d
    return name


def cycle_terminal_state(frame_base_data: dict, frame_lock, crc8_func) -> str:
    current = detect_terminal_state(frame_base_data)
    target = 'IGN' if current == 'ACC' else 'ACC'
    return apply_terminal_state(target, frame_base_data, frame_lock, crc8_func)


def set_frame_enabled(frame_enabled: dict, frame_enabled_lock, arb_id: int, enabled: bool) -> bool:
    with frame_enabled_lock:
        frame_enabled[arb_id] = bool(enabled)
        return frame_enabled[arb_id]


def toggle_frame_enabled(frame_enabled: dict, frame_enabled_lock, arb_id: int) -> bool:
    with frame_enabled_lock:
        frame_enabled[arb_id] = not bool(frame_enabled.get(arb_id, True))
        return frame_enabled[arb_id]
