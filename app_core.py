import can
import time
import random
import threading
import msvcrt
import os
import sys
import subprocess
import ctypes
from collections import deque

from persistent_settings import load_settings
from settings_center import edit_settings_interactive
from bmw_runtime import (
    apply_terminal_state,
    cycle_terminal_state,
    detect_terminal_state,
    toggle_frame_enabled,
)
from keepalive_service import MenuKeepaliveService
from bmw_checksums import crc8_sae_j1850 as crc8

try:
    import tkinter as tk
    from tkinter import ttk, messagebox, scrolledtext, simpledialog
    HAS_TK = True
except Exception:
    tk = None; ttk = None; messagebox = None; scrolledtext = None
    HAS_TK = False

# ──────────────────────────────────────────────
#  配置
# ──────────────────────────────────────────────
CHANNEL        = "PCAN_USBBUS1"
BITRATE        = 500_000
BUS_TYPE       = "pcan"
RANDOM_CYCLE_S = 0.100
LOG_FILE       = os.path.join(os.path.dirname(os.path.abspath(__file__)), "can_log.txt")
SETTINGS_FILE  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "user_settings.json")
USER_SETTINGS  = load_settings(SETTINGS_FILE)

RESERVED_IDS = {
    0x03C, 0x0F3, 0x1A1, 0x0AB, 0x0DF, 0x2A7, 0x2EC,
    0x294, 0x2C4, 0x349, 0x369, 0x36E, 0x36F, 0x30B, 0x3A0,
    0x3D8, 0x3FD, 0x510
}

# ──────────────────────────────────────────────
#  ANSI
# ──────────────────────────────────────────────
os.system("")
RESET   = "\033[0m";  BOLD    = "\033[1m"
CYAN    = "\033[96m"; GREEN   = "\033[92m"
YELLOW  = "\033[93m"; RED     = "\033[91m"
GRAY    = "\033[90m"; WHITE   = "\033[97m"
MAGENTA = "\033[95m"; BLUE    = "\033[94m"
ORANGE  = "\033[33m"

# ──────────────────────────────────────────────
#  帧元信息  +  可编辑的基础数据（在线帧编辑窗口使用）
# ──────────────────────────────────────────────
FRAME_META = {
    0x03C: ("点火/Terminal",  "CRC8(b[1..7]) init=FF xor=91 | byte[1]低4位=CTR(0~14)"),
    0x0F3: ("RPM",            "CRC8(b[0..7]) init=00 xor=2C | byte[1]低4位=CTR(0~14)"),
    0x1A1: ("Vehicle Speed",  "CRC8(b[0..4]) init=00 xor=2C | byte[1]低4位=CTR(0~14)"),
    0x0AB: ("ACSM/Airbag",   "CRC8(b[1..7]) init=FF xor=55 | byte[1]=CTR FC~FF→C0~FB"),
    0x0DF: ("换挡提示",        "单字节状态帧 | 高4位=闪烁/静止状态 | 低4位=等级0~8"),
    0x2A7: ("Power Steering", "CRC8(b[1..4]) init=FF xor=9E | byte[1]低4位=CTR(0~15)"),
    0x2EC: ("Navi/Guidance",  "CRC8(b[1..7]) init=FF xor=00 | byte[1]低4位=CTR(0~14)"),
    0x294: ("转向助力",        "3帧轮播: 73C7FEFF14→3CCAFEFF14→55CCFEFF14"),
    0x2C4: ("水温/油温表",      "固定样本帧 | 目前按原始字节透传"),
    0x349: ("燃油",            "固定样本帧 | 目前按原始字节透传"),
    0x30B: ("DME keepalive",  "无CRC | byte[1]=sync_ctr 0x50~0x5F循环"),
    0x369: ("ECU online",     "CRC8(b[1..4]) init=FF xor=C5 | byte[1]低4位=CTR(0~15)"),
    0x36E: ("ABS",             "固定样本帧 | 目前按原始字节透传"),
    0x36F: ("ABS keepalive",  "CRC8(b[1..4]) init=FF xor=17 | byte[1]低4位=CTR(0~15)"),
    0x3A0: ("Unknown",        "无CRC | byte[7]自增 0x00~0xFF"),
    0x3D8: ("Drive Mode",     "CRC8(b[1..7]) init=FF xor=D8 | 无CTR"),
    0x3FD: ("Gear Selector",  "CRC8(b[1..7]) init=FF xor=D6 | byte[1]低4位=CTR(0~14)"),
    0x510: ("在线帧",          "固定数据: 40 10 00 02 02 12 11 00"),
}

# 固定帧默认基础数据（可被在线帧编辑窗口覆盖）
# 格式: {arb_id: bytearray}  — 这是编辑层，builder每次都从这里读
FRAME_BASE_DATA = {
    0x03C: bytearray.fromhex("155C061222002AFF"),
    0x0F3: bytearray.fromhex("F300C0F044FFFF00"),
    0x1A1: bytearray.fromhex("00F0000081"),
    0x0AB: bytearray.fromhex("00FC4055FDFFFFFF"),
    0x0DF: bytearray.fromhex("57"),
    0x2A7: bytearray.fromhex("00F0FEFF14"),
    0x2EC: bytearray.fromhex("0000000000000000"),
    0x294: bytearray.fromhex("73C7FEFF14"),
    0x2C4: bytearray.fromhex("8BFF62CD5D37CD00"),
    0x349: bytearray.fromhex("27275F01FF"),
    0x30B: bytearray.fromhex("0F500FC8FFFFFFFF"),
    0x369: bytearray.fromhex("00F0A0A0A0"),
    0x36E: bytearray.fromhex("A905FEFFFF"),
    0x36F: bytearray.fromhex("00F0380015"),
    0x3A0: bytearray.fromhex("00000000000000A5"),
    0x3D8: bytearray.fromhex("0006000000000000"),
    0x3FD: bytearray.fromhex("5A0020FC00000000"),
    0x510: bytearray.fromhex("4010000202121100"),
}
FRAME_BASE_LOCK = threading.Lock()

# 帧启用开关
FRAME_ENABLED = {aid: True for aid in RESERVED_IDS}
FRAME_ENABLED_LOCK = threading.Lock()

DISPLAY_ORDER = [
    0x03C, 0x0F3, 0x1A1, 0x0AB, 0x0DF, 0x2A7, 0x2EC,
    0x294, 0x2C4, 0x349, 0x30B, 0x369, 0x36E, 0x36F, 0x3A0,
    0x3D8, 0x3FD, 0x510
]

# ──────────────────────────────────────────────
#  全局会话
# ──────────────────────────────────────────────
def make_fresh_session():
    return {
        'APP_MODE':         'A',
        'FUZZ_FORMAT':      1,
        'FUZZ_TARGET_IDS':  [],
        'fuzz_lock':        threading.Lock(),
        'slot_states':      [],
        'slot_lock':        threading.Lock(),
        'next_slot':        [0],
        'target_paused':    False,
        'bytes_frozen':     False,
        'frozen_send_mode': 1,
        'gui_open':         False,
        'gui_thread':       None,
        'gui_lock':         threading.Lock(),
        'frame_state':      {},
        'frame_lock':       threading.Lock(),
        'marks':            deque(maxlen=500),
        'marks_lock':       threading.Lock(),
        'mark_counter':     [0],
        'tx_history':       deque(maxlen=50000),
        'tx_lock':          threading.Lock(),
        'running':          threading.Event(),
        'mode_c_fixed_profiles': [],
        'mode_c_fixed_lock': threading.Lock(),
        'speed_config_enabled': False,
        'speed_value': 0.0,
        'speed_jitter': 0.0,
        'rpm_config_enabled': False,
        'rpm_value': 0,
        'rpm_jitter': 50,
        'mode_b_fixed_random_ids': [],
        'mode_d': {
            'target_ids':     [0x000],
            'current_target_idx': 0,
            'length':         8,
            'current_byte':   1,
            'current_val':    0x00,
            'locked':         {},
            'hits':           [],
            'frozen_payload': None,
            'hit_frozen':     False,
            'auto_mode':      False,
            'auto_interval':  0.15,
            'auto_paused':    False,
            'scan_strategy':  1,
            'group_phase':    'high',
            'group_high_val': 0x00,
            'dual_byte_b':    0x00,
            'status':         '准备开始...',
            'done':           False,
            'waiting_key':    False,
            '_action':        'n',
            'lock':           threading.Lock(),
            'event':          threading.Event(),
        },
    }

S = make_fresh_session()
MENU_KEEPALIVE = None


def apply_runtime_defaults_to_session():
    S['speed_config_enabled'] = bool(USER_SETTINGS.get('default_speed_enabled', False))
    S['speed_value'] = float(USER_SETTINGS.get('default_speed_value', 0.0))
    S['speed_jitter'] = float(USER_SETTINGS.get('default_speed_jitter', 0.0))
    S['rpm_config_enabled'] = bool(USER_SETTINGS.get('default_rpm_enabled', False))
    S['rpm_value'] = int(USER_SETTINGS.get('default_rpm_value', 0))
    S['rpm_jitter'] = int(USER_SETTINGS.get('default_rpm_jitter', 50))
    with FRAME_ENABLED_LOCK:
        FRAME_ENABLED[0x3A0] = bool(USER_SETTINGS.get('default_3a0_enabled', True))
        for aid in RESERVED_IDS:
            key = f"{aid:03X}"
            if key in USER_SETTINGS.get('frame_enabled_defaults', {}):
                FRAME_ENABLED[aid] = bool(USER_SETTINGS['frame_enabled_defaults'][key])


def apply_startup_terminal_from_settings(which='menu_keepalive_terminal_state'):
    state = str(USER_SETTINGS.get(which, 'CUSTOM')).upper()
    if state in ('ACC', 'IGN'):
        apply_terminal_state(state, FRAME_BASE_DATA, FRAME_BASE_LOCK, crc8)
    return detect_terminal_state(FRAME_BASE_DATA)


def _status_line(msg):
    print(f"\n{CYAN}[状态]{RESET} {msg}")


apply_runtime_defaults_to_session()
apply_startup_terminal_from_settings('menu_keepalive_terminal_state')

# ──────────────────────────────────────────────
#  日志队列（全局，不随会话重置）
# ──────────────────────────────────────────────
_log_queue      = deque(maxlen=10000)
_log_queue_lock = threading.Lock()
_log_seq        = [0]   # 单调递增序号，用于增量读取

def _push_log(ts, arb_id, data):
    with _log_queue_lock:
        _log_seq[0] += 1
        _log_queue.append((_log_seq[0], ts, arb_id, bytes(data)))

# ──────────────────────────────────────────────
#  tkinter 单主线程架构
#  所有窗口都运行在同一个后台线程（_tk_thread）中
#  通过 _tk_root.after() 投递任务，避免多Tk()冲突
# ──────────────────────────────────────────────
_tk_root   = None          # 唯一的 tk.Tk() 实例（隐藏）
_tk_thread = None
_tk_ready  = threading.Event()
_tk_lock   = threading.Lock()

# 各窗口的 Toplevel 引用
_WIN = {
    'log':     None,
    'editor':  None,
    'history': None,
    'pcan':    None,
    'mode_c':  None,
    'control': None,
}
_WIN_LOCK = threading.Lock()

def _tk_main():
    """后台守护线程：持有唯一的 tk.Tk()，循环处理事件"""
    global _tk_root
    _tk_root = tk.Tk()
    _tk_root.withdraw()          # 隐藏主窗口
    _tk_root.title('CAN Sender daemon')
    _tk_ready.set()
    _tk_root.mainloop()

def _ensure_tk():
    """确保后台Tk线程已启动"""
    global _tk_thread
    if not HAS_TK:
        return False
    with _tk_lock:
        if _tk_thread is None or not _tk_thread.is_alive():
            _tk_thread = threading.Thread(target=_tk_main, daemon=True, name='TkDaemon')
            _tk_thread.start()
            _tk_ready.wait(timeout=3)
    return _tk_ready.is_set()

def _tk_call(fn):
    """在Tk主线程中安全调用fn（使用after(0,...)投递）"""
    if _tk_root:
        _tk_root.after(0, fn)

def _close_win(name):
    """关闭指定窗口"""
    def _do():
        with _WIN_LOCK:
            w = _WIN.get(name)
            if w:
                try: w.destroy()
                except: pass
                _WIN[name] = None
    _tk_call(_do)

def _close_all_session_windows():
    """退出模式时关闭所有会话级窗口（除PCAN窗口）"""
    for name in ['log', 'editor', 'history', 'mode_c', 'control']:
        _close_win(name)

# ──────────────────────────────────────────────
#  CRC8
# ──────────────────────────────────────────────
def _build_crc_table():
    t = []
    for b in range(256):
        crc = b
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1D) if (crc & 0x80) else (crc << 1)
            crc &= 0xFF
        t.append(crc)
    return t

_CRC_T = _build_crc_table()

def crc8(data: bytes, init: int, xor_out: int) -> int:
    crc = init
    for b in data: crc = _CRC_T[crc ^ b]
    return crc ^ xor_out

def make_counter(start=0, max_val=14, wrap=None):
    val  = [start]
    lock = threading.Lock()
    w    = wrap if wrap is not None else start
    def nxt():
        with lock:
            v = val[0]
            val[0] = w if val[0] >= max_val else val[0] + 1
            return v
    return nxt



def build_fuzz_payload(fmt, ctr_value, length=8):
    d = bytearray(length)
    ctr = ctr_value & 0xFF
    if fmt == 1:
        d[0] = ctr
        for i in range(1, length):
            d[i] = random.randint(0, 0xFF)
    elif fmt == 2:
        if length > 1:
            d[1] = ctr
        for i in range(2, length):
            d[i] = random.randint(0, 0xFF)
        d[0] = crc8(bytes(d[1:length]), 0xFF, 0x00)
    elif fmt == 4:
        if length > 1:
            d[1] = ctr
        for i in range(2, max(2, length - 1)):
            d[i] = random.randint(0, 0xFF)
        if length > 7:
            d[7] = ctr
        elif length >= 2:
            d[length - 1] = ctr
        d[0] = crc8(bytes(d[1:length]), 0xFF, 0x00)
    else:
        for i in range(length):
            d[i] = random.randint(0, 0xFF)
    return bytes(d)

_D_CTR = make_counter(0, 255, wrap=0)

# ──────────────────────────────────────────────
#  会话工具
# ──────────────────────────────────────────────
def record_tx(arb_id, data, cycle_ms):
    ts   = time.time()
    meta = FRAME_META.get(arb_id, (f"0x{arb_id:03X}", "探测帧"))
    with S['frame_lock']:
        if arb_id not in S['frame_state']:
            S['frame_state'][arb_id] = {
                "name": meta[0], "logic": meta[1],
                "cycle": cycle_ms, "count": 0,
                "data": data, "ts": ts,
            }
        st = S['frame_state'][arb_id]
        st["data"] = data; st["ts"] = ts; st["count"] += 1
    with S['tx_lock']:
        S['tx_history'].append({"ts": ts, "id": arb_id, "data": data})
    _push_log(ts, arb_id, data)

def do_mark():
    S['mark_counter'][0] += 1
    idx = S['mark_counter'][0]; ts = time.time()
    with S['frame_lock']:
        fixed = {k: dict(v) for k, v in S['frame_state'].items() if k in RESERVED_IDS}
        rnd   = {k: dict(v) for k, v in S['frame_state'].items() if k not in RESERVED_IDS}
    with S['marks_lock']:
        S['marks'].append({"ts": ts, "idx": idx, "fixed": fixed, "random": rnd})

def fmt_bytes_indexed(data):
    return "  ".join(f"{GRAY}[{i}]{RESET}{b:02X}" for i, b in enumerate(data))

def fmt_bytes_plain(data):
    return " ".join(f"{b:02X}" for b in data)

def get_fuzz_ids():
    with S['fuzz_lock']: return list(S['FUZZ_TARGET_IDS'])

def parse_hex_id_list(text):
    ids = []
    for part in text.replace('，',',').replace(';',',').replace('；',',').split(','):
        p = part.strip().upper()
        if not p: continue
        if p.startswith('0X'): p = p[2:]
        if not p: continue
        aid = int(p, 16)
        if not (0 <= aid <= 0x7FF): raise ValueError(f'ID {part.strip()} 超出范围')
        ids.append(aid)
    return list(dict.fromkeys(ids))


def parse_hex_id(text):
    p = text.strip().upper()
    if p.startswith('0X'):
        p = p[2:]
    if not p:
        raise ValueError('ID 不能为空')
    aid = int(p, 16)
    if not (0 <= aid <= 0x7FF):
        raise ValueError('ID 超出范围 (000~7FF)')
    return aid


def parse_int_list(text):
    vals = []
    for part in text.replace('，', ',').split(','):
        p = part.strip()
        if not p:
            continue
        vals.append(int(p))
    return vals


def parse_hex_bytes(text, expected_len=None):
    raw = text.replace(',', ' ').replace('，', ' ').replace(';', ' ').replace('；', ' ')
    raw = ' '.join(raw.split())
    if not raw:
        if expected_len is None:
            return bytearray()
        return bytearray([0x00] * expected_len)
    if ' ' in raw:
        parts = raw.split(' ')
    else:
        raw = raw.replace('0X', '').replace('0x', '')
        if len(raw) % 2 != 0:
            raise ValueError('十六进制字节数量不正确')
        parts = [raw[i:i+2] for i in range(0, len(raw), 2)]
    out = bytearray()
    for part in parts:
        p = part.strip()
        if not p:
            continue
        p = p.replace('0X', '').replace('0x', '')
        if len(p) > 2:
            raise ValueError(f'字节 {part} 超出 00~FF')
        v = int(p, 16)
        if not (0 <= v <= 0xFF):
            raise ValueError(f'字节 {part} 超出 00~FF')
        out.append(v)
    if expected_len is not None:
        if len(out) < expected_len:
            out.extend([0x00] * (expected_len - len(out)))
        elif len(out) > expected_len:
            raise ValueError(f'字节数应为 {expected_len}，实际 {len(out)}')
    return out


def ask_yes_no(prompt, default=True):
    suffix = 'Y/n' if default else 'y/N'
    while True:
        v = input(f"{prompt} ({suffix}): ").strip().lower()
        if not v:
            return default
        if v in ('y', 'yes', '1'):
            return True
        if v in ('n', 'no', '0'):
            return False
        print(f"{RED}请输入 y 或 n{RESET}")


def ask_int(prompt, default=None, min_v=None, max_v=None):
    while True:
        raw = input(f"{prompt}{'' if default is None else f' (默认{default})'}: ").strip()
        if not raw and default is not None:
            return default
        try:
            val = int(raw)
            if min_v is not None and val < min_v:
                raise ValueError()
            if max_v is not None and val > max_v:
                raise ValueError()
            return val
        except ValueError:
            if min_v is not None and max_v is not None:
                print(f"{RED}请输入 {min_v}~{max_v}{RESET}")
            else:
                print(f"{RED}请输入有效整数{RESET}")


def ask_float(prompt, default=None, min_v=None, max_v=None):
    while True:
        raw = input(f"{prompt}{'' if default is None else f' (默认{default})'}: ").strip()
        if not raw and default is not None:
            return float(default)
        try:
            val = float(raw)
            if min_v is not None and val < min_v:
                raise ValueError()
            if max_v is not None and val > max_v:
                raise ValueError()
            return val
        except ValueError:
            print(f"{RED}请输入有效数字{RESET}")


def configure_speed_value_interactive():
    enabled = ask_yes_no('要不要设置车速值（作用于内置1A1，默认0）', default=False)
    S['speed_config_enabled'] = enabled
    if enabled:
        S['speed_value'] = ask_float('车速 km/h', float(S.get('speed_value', 0.0)), 0.0, 300.0)
        S['speed_jitter'] = ask_float('车速抖动 km/h', float(S.get('speed_jitter', 0.0)), 0.0, 20.0)
    else:
        S['speed_value'] = 0.0
        S['speed_jitter'] = 0.0


def configure_rpm_value_interactive():
    enabled = ask_yes_no('要不要设置转速值（作用于内置0F3，默认0）', default=False)
    S['rpm_config_enabled'] = enabled
    if enabled:
        S['rpm_value'] = ask_int('基础转速 rpm', int(S.get('rpm_value', 0)), 0, 7500)
        S['rpm_jitter'] = ask_int('上下浮动 rpm', int(S.get('rpm_jitter', 50)), 0, 500)
    else:
        S['rpm_value'] = 0
        S['rpm_jitter'] = 50


def configure_3a0_switch_interactive():
    current = True
    with FRAME_ENABLED_LOCK:
        current = FRAME_ENABLED.get(0x3A0, True)
    enabled = ask_yes_no('要不要开启内置固定帧 3A0', default=current)
    with FRAME_ENABLED_LOCK:
        FRAME_ENABLED[0x3A0] = enabled
    return enabled


def configure_mode_b_fixed_random_ids_interactive():
    S['mode_b_fixed_random_ids'] = []
    if not ask_yes_no('模式B要不要额外增加固定的随机帧', default=False):
        return
    while True:
        raw = input('固定随机帧ID（可多个，逗号分隔，如 21D,22E）: ').strip()
        try:
            aids = parse_hex_id_list(raw)
            if not aids:
                print(f"{RED}请至少输入一个ID{RESET}")
                continue
            bad = [aid for aid in aids if aid in RESERVED_IDS]
            if bad:
                print(f"{RED}这些ID是内置保留ID，不能作为模式B固定随机帧: {', '.join(f'0x{x:03X}' for x in bad)}{RESET}")
                continue
            dup = [aid for aid in aids if aid in S['FUZZ_TARGET_IDS']]
            if dup:
                print(f"{RED}这些ID已经在模式B随机目标里，不要重复: {', '.join(f'0x{x:03X}' for x in dup)}{RESET}")
                continue
            S['mode_b_fixed_random_ids'] = aids
            print(f"{GREEN}模式B固定随机帧: {', '.join(f'0x{i:03X}' for i in aids)}{RESET}")
            break
        except ValueError as e:
            print(f"{RED}{e}{RESET}")


def ask_hex_id(prompt, allow_reserved=False):
    while True:
        raw = input(f"{prompt}: ").strip()
        try:
            aid = parse_hex_id(raw)
            if not allow_reserved and aid in RESERVED_IDS:
                print(f"{RED}0x{aid:03X} 是内置保留ID，请换一个{RESET}")
                continue
            return aid
        except ValueError as e:
            print(f"{RED}{e}{RESET}")


def ask_optional_index(prompt, length, default=-1):
    while True:
        raw = input(f"{prompt} (-1=无，默认{default}): ").strip()
        if not raw:
            return default
        try:
            val = int(raw)
            if val == -1 or 0 <= val < length:
                return val
        except ValueError:
            pass
        print(f"{RED}请输入 -1 或 0~{length-1}{RESET}")


def ask_nibble_mode(prompt='Counter写入方式', default='full'):
    opts = {'1': 'full', '2': 'low4', '3': 'high4'}
    while True:
        print(f"{prompt}: 1=整字节  2=低4位  3=高4位")
        raw = input(f"选择 (默认{[k for k,v in opts.items() if v==default][0]}): ").strip() or [k for k,v in opts.items() if v==default][0]
        if raw in opts:
            return opts[raw]
        print(f"{RED}请输入 1 / 2 / 3{RESET}")


def mode_c_profile_mode_name(mode):
    return {
        'random': '全随机',
        'fixed': '固定',
        'manual': '手动CRC/CTR',
        'speed': '车速算法',
        'rpm': '转速算法',
    }.get(mode, mode)


def mode_c_profile_logic_desc(profile):
    mode = profile.get('mode', 'manual')
    parts = [mode_c_profile_mode_name(mode)]
    if mode == 'speed':
        parts.append(f"speed*64 -> b[{profile.get('speed_lo_pos', 1)}]/b[{profile.get('speed_hi_pos', 2)}]")
    elif mode == 'rpm':
        parts.append(f"rpm*1.557 -> b[{profile.get('rpm_pack_pos', 1)}]/b[{profile.get('rpm_hi_pos', 2)}]")
        parts.append(f"抖动±{profile.get('rpm_jitter', 50)}rpm")
    if profile.get('counter_pos', -1) >= 0:
        parts.append(f"CTR@b[{profile['counter_pos']}] {profile.get('counter_mode', 'full')}")
    if profile.get('crc_pos', -1) >= 0:
        parts.append(f"CRC@b[{profile['crc_pos']}] init={profile.get('crc_init', 0xFF):02X} xor={profile.get('crc_xor', 0x00):02X}")
    return ' | '.join(parts)


def mode_c_get_fixed_profiles():
    with S['mode_c_fixed_lock']:
        return list(S['mode_c_fixed_profiles'])


def mode_c_get_active_fuzz_slots():
    with S['slot_lock']:
        return [st for st in S['slot_states'] if st.get('active', True)]


def mode_c_all_used_ids(include_reserved=True):
    ids = set(RESERVED_IDS if include_reserved else [])
    with S['fuzz_lock']:
        ids.update(S['FUZZ_TARGET_IDS'])
    with S['mode_c_fixed_lock']:
        ids.update(p['id'] for p in S['mode_c_fixed_profiles'] if p.get('active', True))
    return ids


def _fmt_id_list(ids):
    return ', '.join(f"0x{x:03X}" for x in ids)


def _disable_builtin_ids(ids):
    ids = [aid for aid in ids if aid in RESERVED_IDS]
    if not ids:
        return []
    with FRAME_ENABLED_LOCK:
        for aid in ids:
            FRAME_ENABLED[aid] = False
    return ids


def _restore_builtin_ids(ids):
    ids = [aid for aid in ids if aid in RESERVED_IDS]
    if not ids:
        return []
    with FRAME_ENABLED_LOCK:
        for aid in ids:
            default_enabled = bool(USER_SETTINGS.get('frame_enabled_defaults', {}).get(f"{aid:03X}", True))
            if aid == 0x3A0:
                default_enabled = bool(USER_SETTINGS.get('default_3a0_enabled', default_enabled))
            FRAME_ENABLED[aid] = default_enabled
    return ids


def confirm_reserved_ids_for_mode_c_cli(ids):
    reserved = [aid for aid in ids if aid in RESERVED_IDS]
    if not reserved:
        return True, []
    print(f"{YELLOW}检测到这些ID本来是内置固定帧: {_fmt_id_list(reserved)}{RESET}")
    print(f"{YELLOW}如果继续，程序会自动禁用这些内置固定帧，然后把它们作为模式C的随机/Fuzz目标发送。{RESET}")
    print(f"{YELLOW}注意：像 0x03C / 0x510 这种在线/点火相关ID，改成随机后可能影响仪表当前状态。{RESET}")
    if not ask_yes_no('确认继续并禁用这些内置固定帧', default=False):
        return False, reserved
    _disable_builtin_ids(reserved)
    print(f"{GREEN}已禁用内置固定帧: {_fmt_id_list(reserved)}{RESET}")
    return True, reserved


def confirm_reserved_id_for_mode_c_gui(aid, parent=None):
    if aid not in RESERVED_IDS:
        return True, []
    msg = (
        f'0x{aid:03X} 当前属于内置固定帧。\n\n'
        '如果继续，程序会自动禁用这个内置固定帧，\n'
        '然后把它作为模式C的 Fuzz 目标发送。\n\n'
        '注意：如果这是 03C / 510 这类在线或点火相关ID，\n'
        '随机发送可能影响当前显示或在线状态。\n\n'
        '确认继续吗？'
    )
    try:
        ok = bool(messagebox.askyesno('确认接管内置ID', msg, parent=parent)) if messagebox else False
    except Exception:
        ok = False
    if not ok:
        return False, [aid]
    _disable_builtin_ids([aid])
    return True, [aid]


def mode_c_refresh_meta():
    for st in mode_c_get_active_fuzz_slots():
        FRAME_META[st['id']] = (st.get('name', f"Fuzz 0x{st['id']:03X}"), f"模式C定向Fuzz | slot={st.get('slot', 0)} | {_FMT_NAMES.get(S['FUZZ_FORMAT'], '未知')}")
    for p in mode_c_get_fixed_profiles():
        if p.get('active', True):
            FRAME_META[p['id']] = (p.get('name', f"Fixed 0x{p['id']:03X}"), mode_c_profile_logic_desc(p))


def _counter_apply(base, counter, mode):
    base &= 0xFF
    counter &= 0xFF
    if mode == 'low4':
        return (base & 0xF0) | (counter & 0x0F)
    if mode == 'high4':
        return ((counter << 4) & 0xF0) | (base & 0x0F)
    return counter


def _counter_next(state, profile):
    val = state.get('counter_value', profile.get('counter_start', 0))
    mn = profile.get('counter_min', 0)
    mx = profile.get('counter_max', 255)
    wrap = profile.get('counter_wrap', mn)
    state['counter_value'] = wrap if val >= mx else val + 1
    return val


def _compute_profile_crc(data, crc_pos, init_v, xor_v):
    raw = bytes(b for i, b in enumerate(data) if i != crc_pos)
    return crc8(raw, init_v & 0xFF, xor_v & 0xFF)


def _profile_base_bytes(profile):
    data = bytearray(profile.get('data', []))
    length = int(profile.get('length', len(data) or 8))
    if len(data) < length:
        data.extend([0x00] * (length - len(data)))
    else:
        data = data[:length]
    return data


def _profile_random_bytes(length, frozen_box=None):
    if frozen_box is None:
        return bytes(random.randint(0, 0xFF) for _ in range(length))
    if S['bytes_frozen'] and frozen_box[0] is not None:
        return frozen_box[0]
    rnd = bytes(random.randint(0, 0xFF) for _ in range(length))
    frozen_box[0] = rnd
    return rnd


def build_mode_c_profile_payload(profile, state, frozen_box=None):
    mode = profile.get('mode', 'manual')
    length = int(profile.get('length', 8))
    d = _profile_base_bytes(profile)

    if mode == 'random':
        d = bytearray(_profile_random_bytes(length, frozen_box))
    elif mode == 'speed':
        speed = float(profile.get('speed_value', 60.0))
        jitter = float(profile.get('speed_jitter', 0.0))
        if jitter > 0:
            speed += random.uniform(-jitter, jitter)
        speed = max(0.0, min(300.0, speed))
        scaled = int(round(speed * 64.0))
        lo = int(profile.get('speed_lo_pos', 1))
        hi = int(profile.get('speed_hi_pos', 2))
        if 0 <= lo < length: d[lo] = scaled & 0xFF
        if 0 <= hi < length: d[hi] = (scaled >> 8) & 0xFF
    elif mode == 'rpm':
        rpm = int(profile.get('rpm_value', 1200))
        jitter = int(profile.get('rpm_jitter', 50))
        if jitter > 0:
            rpm += random.randint(-jitter, jitter)
        rpm = max(0, min(7500, rpm))
        scaled = int(rpm * 1.557)
        pack_pos = int(profile.get('rpm_pack_pos', 1))
        hi_pos = int(profile.get('rpm_hi_pos', 2))
        if 0 <= hi_pos < length:
            d[hi_pos] = (scaled >> 8) & 0xFF
        state['rpm_last'] = rpm
        state['rpm_scaled'] = scaled
        state['rpm_pack_pending'] = (pack_pos, scaled)
        if profile.get('rpm_status_pos', -1) >= 0:
            pos = int(profile['rpm_status_pos'])
            if 0 <= pos < length:
                d[pos] = int(profile.get('rpm_status_value', 0xF0)) & 0xFF

    cpos = int(profile.get('counter_pos', -1))
    counter_used = False
    if mode == 'rpm' and 'rpm_pack_pending' in state:
        pack_pos, scaled = state.pop('rpm_pack_pending')
        if cpos == pack_pos and 0 <= pack_pos < length:
            ctr = _counter_next(state, profile)
            d[pack_pos] = ((scaled & 0xF0) | (ctr & 0x0F)) & 0xFF
            counter_used = True
        elif 0 <= pack_pos < length:
            d[pack_pos] = scaled & 0xF0

    if cpos >= 0 and not counter_used and 0 <= cpos < length:
        ctr = _counter_next(state, profile)
        d[cpos] = _counter_apply(d[cpos], ctr, profile.get('counter_mode', 'full'))

    crc_pos = int(profile.get('crc_pos', -1))
    if 0 <= crc_pos < length:
        d[crc_pos] = _compute_profile_crc(d, crc_pos, int(profile.get('crc_init', 0xFF)), int(profile.get('crc_xor', 0x00)))

    return bytes(d)


def make_mode_c_profile_builder(profile):
    state = {'counter_value': int(profile.get('counter_start', 0))}
    frozen_box = [None]
    def builder():
        if not profile.get('active', True):
            return 0x000, b'\x00' * max(1, int(profile.get('length', 8)))
        if not profile.get('enabled', True):
            return 0x000, b'\x00' * max(1, int(profile.get('length', 8)))
        if S['target_paused']:
            return 0x000, b'\x00' * max(1, int(profile.get('length', 8)))
        if S['bytes_frozen'] and S['frozen_send_mode'] == 2 and profile.get('mode') in ('random',):
            return 0x000, b'\x00' * max(1, int(profile.get('length', 8)))
        data = build_mode_c_profile_payload(profile, state, frozen_box)
        return int(profile['id']), data
    return builder


def _mode_c_new_slot_id():
    with S['slot_lock']:
        idx = S['next_slot'][0]
        S['next_slot'][0] += 1
        return idx


def mode_c_add_target(bus, threads, target_id, parent=None):
    used = mode_c_all_used_ids(include_reserved=False)
    if target_id in used:
        return False, f'0x{target_id:03X} 已存在'
    if target_id in RESERVED_IDS:
        ok, _ = confirm_reserved_id_for_mode_c_gui(target_id, parent=parent)
        if not ok:
            return False, f'已取消接管内置ID 0x{target_id:03X}'
    with S['fuzz_lock']:
        S['FUZZ_TARGET_IDS'].append(target_id)
    idx = _mode_c_new_slot_id()
    slot = {'slot': idx, 'id': target_id, 'active': True, 'enabled': True, 'name': f'Fuzz 0x{target_id:03X}'}
    with S['slot_lock']:
        S['slot_states'].append(slot)
    FRAME_META[target_id] = (slot['name'], f'模式C定向Fuzz | slot={idx} | {_FMT_NAMES.get(S["FUZZ_FORMAT"], "未知")}')
    t = CyclicSender(bus, -1, make_slot_builder(slot), RANDOM_CYCLE_S, f'FZC_{idx}')
    t.start(); threads.append(t)
    if target_id in RESERVED_IDS:
        return True, f'已禁用内置固定帧并添加 Fuzz 0x{target_id:03X}'
    return True, f'已添加 Fuzz 0x{target_id:03X}'


def mode_c_remove_target(target_id=None, slot_idx=None):
    removed = None
    with S['slot_lock']:
        for st in S['slot_states']:
            if not st.get('active', True):
                continue
            if (target_id is not None and st['id'] == target_id) or (slot_idx is not None and st['slot'] == slot_idx):
                st['active'] = False
                st['enabled'] = False
                removed = st
                break
    if not removed:
        return False, '未找到要删除的Fuzz ID'
    with S['fuzz_lock']:
        S['FUZZ_TARGET_IDS'] = [x for x in S['FUZZ_TARGET_IDS'] if x != removed['id']]
    if removed['id'] in RESERVED_IDS:
        _restore_builtin_ids([removed['id']])
        return True, f"已删除 Fuzz 0x{removed['id']:03X}，并恢复对应内置固定帧"
    return True, f"已删除 Fuzz 0x{removed['id']:03X}"


def mode_c_set_fuzz_enabled(slot_idx, enabled):
    with S['slot_lock']:
        for st in S['slot_states']:
            if st.get('slot') == slot_idx and st.get('active', True):
                st['enabled'] = bool(enabled)
                return True, f"Fuzz 0x{st['id']:03X} -> {'启用' if enabled else '禁用'}"
    return False, '未找到对应Fuzz项目'


def mode_c_replace_fuzz_id(slot_idx, new_id, parent=None):
    used = mode_c_all_used_ids(include_reserved=False)
    with S['slot_lock']:
        slot = next((st for st in S['slot_states'] if st.get('slot') == slot_idx and st.get('active', True)), None)
    if not slot:
        return False, '未找到Fuzz项目'
    if new_id != slot['id'] and new_id in used:
        return False, f'0x{new_id:03X} 已存在'
    old_id = slot['id']
    if new_id in RESERVED_IDS and new_id != old_id:
        ok, _ = confirm_reserved_id_for_mode_c_gui(new_id, parent=parent)
        if not ok:
            return False, f'已取消接管内置ID 0x{new_id:03X}'
    slot['id'] = new_id
    slot['name'] = f'Fuzz 0x{new_id:03X}'
    with S['fuzz_lock']:
        S['FUZZ_TARGET_IDS'] = [new_id if x == old_id else x for x in S['FUZZ_TARGET_IDS']]
    if old_id in RESERVED_IDS and old_id != new_id:
        _restore_builtin_ids([old_id])
    FRAME_META[new_id] = (slot['name'], f'模式C定向Fuzz | slot={slot_idx} | {_FMT_NAMES.get(S["FUZZ_FORMAT"], "未知")}')
    if new_id in RESERVED_IDS and new_id != old_id:
        return True, f'已恢复 0x{old_id:03X} 内置固定帧，并接管 0x{new_id:03X}'
    return True, f'已修改 Fuzz 0x{old_id:03X} -> 0x{new_id:03X}'


def mode_c_add_fixed_profile(bus, threads, profile):
    aid = int(profile['id'])
    if aid in RESERVED_IDS:
        return False, f'0x{aid:03X} 是内置保留ID'
    used = mode_c_all_used_ids(include_reserved=False)
    if aid in used:
        return False, f'0x{aid:03X} 已存在'
    profile = dict(profile)
    profile.setdefault('name', f"自定义 0x{aid:03X}")
    profile.setdefault('enabled', True)
    profile.setdefault('active', True)
    profile.setdefault('length', 8)
    profile.setdefault('cycle_ms', 100)
    profile.setdefault('data', bytearray([0x00] * int(profile['length'])))
    profile.setdefault('counter_pos', -1)
    profile.setdefault('crc_pos', -1)
    profile.setdefault('counter_mode', 'full')
    profile.setdefault('counter_start', 0)
    profile.setdefault('counter_min', 0)
    profile.setdefault('counter_max', 255)
    profile.setdefault('counter_wrap', 0)
    profile.setdefault('crc_init', 0xFF)
    profile.setdefault('crc_xor', 0x00)
    profile['slot'] = _mode_c_new_slot_id()
    with S['mode_c_fixed_lock']:
        S['mode_c_fixed_profiles'].append(profile)
    FRAME_META[aid] = (profile['name'], mode_c_profile_logic_desc(profile))
    t = CyclicSender(bus, -1, make_mode_c_profile_builder(profile), max(1, int(profile['cycle_ms'])) / 1000.0, f"FXC_{profile['slot']}")
    t.start(); threads.append(t)
    return True, f"已添加固定ID 0x{aid:03X} ({mode_c_profile_mode_name(profile.get('mode', 'manual'))})"


def mode_c_update_fixed_profile(slot_idx, new_profile):
    aid = int(new_profile['id'])
    with S['mode_c_fixed_lock']:
        target = next((p for p in S['mode_c_fixed_profiles'] if p.get('slot') == slot_idx and p.get('active', True)), None)
        if not target:
            return False, '未找到固定ID项目'
        used = {p['id'] for p in S['mode_c_fixed_profiles'] if p.get('active', True) and p.get('slot') != slot_idx}
    with S['fuzz_lock']:
        used.update(S['FUZZ_TARGET_IDS'])
    if aid in RESERVED_IDS:
        return False, f'0x{aid:03X} 是内置保留ID'
    if aid in used:
        return False, f'0x{aid:03X} 已存在'
    old_id = target['id']
    for k, v in dict(new_profile).items():
        target[k] = v
    target['slot'] = slot_idx
    target['active'] = True
    FRAME_META[aid] = (target.get('name', f"自定义 0x{aid:03X}"), mode_c_profile_logic_desc(target))
    return True, f'已更新固定ID 0x{old_id:03X} -> 0x{aid:03X}'


def mode_c_remove_fixed_profile(slot_idx):
    with S['mode_c_fixed_lock']:
        for p in S['mode_c_fixed_profiles']:
            if p.get('slot') == slot_idx and p.get('active', True):
                p['active'] = False
                p['enabled'] = False
                return True, f"已删除固定ID 0x{p['id']:03X}"
    return False, '未找到固定ID项目'


def mode_c_set_fixed_enabled(slot_idx, enabled):
    with S['mode_c_fixed_lock']:
        for p in S['mode_c_fixed_profiles']:
            if p.get('slot') == slot_idx and p.get('active', True):
                p['enabled'] = bool(enabled)
                return True, f"固定ID 0x{p['id']:03X} -> {'启用' if enabled else '禁用'}"
    return False, '未找到固定ID项目'


def mode_c_set_all_enabled(enabled):
    msgs = []
    with S['slot_lock']:
        for st in S['slot_states']:
            if st.get('active', True):
                st['enabled'] = bool(enabled)
    with S['mode_c_fixed_lock']:
        for p in S['mode_c_fixed_profiles']:
            if p.get('active', True):
                p['enabled'] = bool(enabled)
    return True, '全部启用' if enabled else '全部禁用'


def mode_c_solo(kind, slot_idx):
    with S['slot_lock']:
        for st in S['slot_states']:
            if st.get('active', True):
                st['enabled'] = (kind == 'fuzz' and st.get('slot') == slot_idx)
    with S['mode_c_fixed_lock']:
        for p in S['mode_c_fixed_profiles']:
            if p.get('active', True):
                p['enabled'] = (kind == 'fixed' and p.get('slot') == slot_idx)
    return True, '已切换为 Solo 模式'


def mode_c_collect_entries():
    entries = []
    with S['slot_lock']:
        for st in S['slot_states']:
            if not st.get('active', True):
                continue
            entries.append({
                'kind': 'fuzz',
                'slot': st['slot'],
                'id': st['id'],
                'enabled': st.get('enabled', True),
                'name': st.get('name', f"Fuzz 0x{st['id']:03X}"),
                'mode': 'Fuzz',
                'length': 8,
                'cycle_ms': int(RANDOM_CYCLE_S * 1000),
                'logic': f'模式C定向Fuzz | {_FMT_NAMES.get(S["FUZZ_FORMAT"], "未知")}',
            })
    with S['mode_c_fixed_lock']:
        for p in S['mode_c_fixed_profiles']:
            if not p.get('active', True):
                continue
            entries.append({
                'kind': 'fixed',
                'slot': p['slot'],
                'id': p['id'],
                'enabled': p.get('enabled', True),
                'name': p.get('name', f"自定义 0x{p['id']:03X}"),
                'mode': mode_c_profile_mode_name(p.get('mode', 'manual')),
                'length': p.get('length', 8),
                'cycle_ms': p.get('cycle_ms', 100),
                'logic': mode_c_profile_logic_desc(p),
                'profile': p,
            })
    entries.sort(key=lambda x: (0 if x['enabled'] else 1, x['kind'], x['id']))
    return entries


def mode_c_get_selected_entry(slot_idx, kind):
    for item in mode_c_collect_entries():
        if item['kind'] == kind and item['slot'] == slot_idx:
            return item
    return None


def mode_c_build_quick_profile(kind='speed'):
    kind = 'rpm' if str(kind).lower() == 'rpm' else 'speed'
    is_speed = kind == 'speed'
    label = '车速' if is_speed else '转速'
    print(f"\n{BOLD}{CYAN}配置{label}固定ID{RESET}")
    aid = ask_hex_id(f'{label}固定ID（例如 555）')
    length = ask_int('帧长度', 8, 1, 8)
    cycle_ms = ask_int('发送周期ms', 50 if is_speed else 75, 10, 5000)
    name = input('显示名称（可空）: ').strip() or (f'{label} 0x{aid:03X}')
    raw = input(f'基础数据（{length}字节，留空全00，可写 11 22 33 ...）: ').strip()
    data = parse_hex_bytes(raw, expected_len=length) if raw else parse_hex_bytes('', expected_len=length)
    profile = {
        'id': aid,
        'name': name,
        'length': length,
        'cycle_ms': cycle_ms,
        'mode': kind,
        'data': data,
        'enabled': True,
        'active': True,
        'counter_pos': -1,
        'crc_pos': -1,
        'counter_mode': 'low4' if not is_speed else 'full',
        'counter_start': 0,
        'counter_min': 0,
        'counter_max': 15 if not is_speed else 255,
        'counter_wrap': 0,
        'crc_init': 0xFF,
        'crc_xor': 0x00,
    }
    if ask_yes_no(f'要不要设置{label}值', default=True):
        if is_speed:
            profile['speed_value'] = ask_float('车速 km/h', 0.0, 0.0, 300.0)
            profile['speed_jitter'] = ask_float('车速抖动 km/h', 0.0, 0.0, 20.0)
            profile['speed_lo_pos'] = ask_int('车速低字节位置', 1, 0, length - 1)
            profile['speed_hi_pos'] = ask_int('车速高字节位置', 2 if length > 2 else length - 1, 0, length - 1)
        else:
            profile['rpm_value'] = ask_int('基础转速 rpm', 0, 0, 7500)
            profile['rpm_jitter'] = ask_int('上下浮动 rpm', 50, 0, 500)
            profile['rpm_pack_pos'] = ask_int('rpm打包字节位置（低4位可留给counter）', 1, 0, length - 1)
            profile['rpm_hi_pos'] = ask_int('rpm高字节位置', 2 if length > 2 else length - 1, 0, length - 1)
            profile['rpm_status_pos'] = ask_optional_index('状态字节位置（可选）', length, -1)
            if profile['rpm_status_pos'] >= 0:
                profile['rpm_status_value'] = ask_int('状态字节固定值', 0xF0, 0, 255)
    else:
        if is_speed:
            profile['speed_value'] = 0.0
            profile['speed_jitter'] = 0.0
            profile['speed_lo_pos'] = 1 if length > 1 else 0
            profile['speed_hi_pos'] = 2 if length > 2 else max(0, length - 1)
        else:
            profile['rpm_value'] = 0
            profile['rpm_jitter'] = 50
            profile['rpm_pack_pos'] = 1 if length > 1 else 0
            profile['rpm_hi_pos'] = 2 if length > 2 else max(0, length - 1)
            profile['rpm_status_pos'] = -1
    if ask_yes_no('要不要设置Counter位置', default=not is_speed):
        profile['counter_pos'] = ask_optional_index('Counter字节位置', length, 1 if length > 1 else 0)
        if profile['counter_pos'] >= 0:
            profile['counter_mode'] = ask_nibble_mode(default='low4' if not is_speed else 'full')
            profile['counter_start'] = ask_int('Counter起始值', 0, 0, 255)
            profile['counter_min'] = ask_int('Counter最小值', 0, 0, 255)
            profile['counter_max'] = ask_int('Counter最大值', 15 if not is_speed else 255, 0, 255)
            profile['counter_wrap'] = ask_int('Counter回卷值', 0, 0, 255)
    if ask_yes_no('要不要设置CRC位置', default=False):
        profile['crc_pos'] = ask_optional_index('CRC字节位置', length, 0)
        if profile['crc_pos'] >= 0:
            profile['crc_init'] = ask_int('CRC init', 0xFF, 0, 255)
            profile['crc_xor'] = ask_int('CRC xor_out', 0x00, 0, 255)
    return profile


def mode_c_build_terminal_profile():
    print(f"\n{BOLD}{CYAN}配置固定自定义ID{RESET}")
    aid = ask_hex_id('自定义固定ID（例如 555）')
    length = ask_int('帧长度', 8, 1, 8)
    cycle_ms = ask_int('发送周期ms', 100, 10, 5000)
    name = input('显示名称（可空）: ').strip() or f'自定义 0x{aid:03X}'
    print('类型: 1=全部随机  2=固定数据  3=手动CRC/COUNTER  4=车速算法  5=转速算法')
    while True:
        t = input('选择类型 (1/2/3/4/5): ').strip()
        if t in ('1', '2', '3', '4', '5'):
            break
        print(f"{RED}请输入 1~5{RESET}")
    mode = {'1': 'random', '2': 'fixed', '3': 'manual', '4': 'speed', '5': 'rpm'}[t]
    data = parse_hex_bytes('', expected_len=length)
    if mode in ('fixed', 'manual', 'speed', 'rpm'):
        raw = input(f'基础数据（{length}字节，留空全00，可写 11 22 33 ...）: ').strip()
        data = parse_hex_bytes(raw, expected_len=length) if raw else parse_hex_bytes('', expected_len=length)

    profile = {
        'id': aid,
        'name': name,
        'length': length,
        'cycle_ms': cycle_ms,
        'mode': mode,
        'data': data,
        'enabled': True,
        'active': True,
        'counter_pos': -1,
        'crc_pos': -1,
        'counter_mode': 'full',
        'counter_start': 0,
        'counter_min': 0,
        'counter_max': 255,
        'counter_wrap': 0,
        'crc_init': 0xFF,
        'crc_xor': 0x00,
    }

    if mode in ('manual', 'speed', 'rpm'):
        profile['counter_pos'] = ask_optional_index('Counter字节位置', length, -1)
        if profile['counter_pos'] >= 0:
            profile['counter_mode'] = ask_nibble_mode(default='full' if mode != 'rpm' else 'low4')
            profile['counter_start'] = ask_int('Counter起始值', 0, 0, 255)
            profile['counter_min'] = ask_int('Counter最小值', 0, 0, 255)
            profile['counter_max'] = ask_int('Counter最大值', 255, 0, 255)
            profile['counter_wrap'] = ask_int('Counter回卷值', profile['counter_min'], 0, 255)
        profile['crc_pos'] = ask_optional_index('CRC字节位置', length, -1)
        if profile['crc_pos'] >= 0:
            profile['crc_init'] = ask_int('CRC init', 0xFF, 0, 255)
            profile['crc_xor'] = ask_int('CRC xor_out', 0x00, 0, 255)

    if mode == 'speed':
        profile['speed_value'] = ask_float('车速 km/h', 0.0, 0.0, 300.0)
        profile['speed_jitter'] = ask_float('车速抖动 km/h', 0.0, 0.0, 20.0)
        profile['speed_lo_pos'] = ask_int('车速低字节位置', 1, 0, length - 1)
        profile['speed_hi_pos'] = ask_int('车速高字节位置', 2 if length > 2 else length - 1, 0, length - 1)
    elif mode == 'rpm':
        profile['rpm_value'] = ask_int('基础转速 rpm', 0, 0, 7500)
        profile['rpm_jitter'] = ask_int('上下浮动 rpm', 50, 0, 500)
        profile['rpm_pack_pos'] = ask_int('rpm打包字节位置（低4位可留给counter）', 1, 0, length - 1)
        profile['rpm_hi_pos'] = ask_int('rpm高字节位置', 2 if length > 2 else length - 1, 0, length - 1)
        profile['rpm_status_pos'] = ask_optional_index('状态字节位置（可选）', length, -1)
        if profile['rpm_status_pos'] >= 0:
            profile['rpm_status_value'] = ask_int('状态字节固定值', 0xF0, 0, 255)
        if profile['counter_pos'] == -1:
            print(f"{YELLOW}提示: rpm算法通常建议配合 low4 counter 使用。当前未设置Counter。{RESET}")

    return profile

# ──────────────────────────────────────────────
#  固定帧构建（从 FRAME_BASE_DATA 读取，支持在线编辑）
# ──────────────────────────────────────────────
def _get_base(aid):
    with FRAME_BASE_LOCK:
        return bytearray(FRAME_BASE_DATA[aid])

def _make_fixed_builders():
    c03C = make_counter(0,14)
    def b03C():
        with FRAME_ENABLED_LOCK:
            if not FRAME_ENABLED.get(0x03C, True): return b''
        d = _get_base(0x03C)
        d[1]=(d[1]&0xF0)|(c03C()&0x0F)
        d[0]=crc8(bytes(d[1:8]),0xFF,0x91); return bytes(d)

    c0F3 = make_counter(0,14)
    def b0F3():
        with FRAME_ENABLED_LOCK:
            if not FRAME_ENABLED.get(0x0F3, True): return b''
        d = _get_base(0x0F3)
        ctr = c0F3() & 0x0F
        rpm = int(S.get('rpm_value', 0)) if S.get('rpm_config_enabled') else 0
        jitter = int(S.get('rpm_jitter', 50)) if S.get('rpm_config_enabled') else 0
        if jitter > 0:
            rpm += random.randint(-jitter, jitter)
        rpm = max(0, min(7500, rpm))
        scaled = int(rpm * 1.557)
        d[1] = ((scaled & 0xF0) | ctr) & 0xFF
        d[2] = (scaled >> 8) & 0xFF
        d[0]=crc8(bytes(d[0:8]),0x00,0x2C); return bytes(d)

    c1A1 = make_counter(0,14)
    def b1A1():
        with FRAME_ENABLED_LOCK:
            if not FRAME_ENABLED.get(0x1A1, True): return b''
        d = _get_base(0x1A1)
        ctr = c1A1() & 0x0F
        speed = float(S.get('speed_value', 0.0)) if S.get('speed_config_enabled') else 0.0
        jitter = float(S.get('speed_jitter', 0.0)) if S.get('speed_config_enabled') else 0.0
        if jitter > 0:
            speed += random.uniform(-jitter, jitter)
        speed = max(0.0, min(300.0, speed))

        # 按用户给的 1A1 样本重写：
        # 65km  -> .. F7 00 10 81
        # 129km -> .. F6 00 20 81
        # 257km -> .. F6 00 40 81
        # 说明速度主体更接近 (speed * 64) 的高字节落在 byte[3]，而不是旧版写到 byte[1]/byte[2]。
        scaled = int(round(speed * 64.0))
        hi = (scaled >> 8) & 0xFF
        lo = scaled & 0xFF

        d[1] = (d[1] & 0xF0) | ctr
        d[2] = 0x30 if lo >= 0x80 else 0x00
        d[3] = hi
        d[4] = 0x41 if lo >= 0x80 else 0x81
        d[0] = crc8(bytes(d[0:5]), 0x00, 0x2C)
        return bytes(d)

    cAB=[0xFC]; lAB=threading.Lock()
    def _nAB():
        with lAB:
            v=cAB[0]
            if v==0xFF: cAB[0]=0xC0
            elif v==0xFB: cAB[0]=0xFC
            else: cAB[0]+=1
            return v
    def b0AB():
        with FRAME_ENABLED_LOCK:
            if not FRAME_ENABLED.get(0x0AB, True): return b''
        d = _get_base(0x0AB); d[1]=_nAB()
        d[0]=crc8(bytes(d[1:8]),0xFF,0x55); return bytes(d)

    c2A7 = make_counter(0,15,wrap=0)
    def b2A7():
        with FRAME_ENABLED_LOCK:
            if not FRAME_ENABLED.get(0x2A7, True): return b''
        d = _get_base(0x2A7)
        d[1]=(d[1]&0xF0)|(c2A7()&0x0F)
        d[0]=crc8(bytes(d[1:5]),0xFF,0x9E); return bytes(d)

    c2EC = make_counter(0,14)
    def b2EC():
        with FRAME_ENABLED_LOCK:
            if not FRAME_ENABLED.get(0x2EC, True): return b''
        d = _get_base(0x2EC)
        d[1]=(d[1]&0xF0)|(c2EC()&0x0F)
        d[0]=crc8(bytes(d[1:8]),0xFF,0x00); return bytes(d)


    SEQ294=[bytes.fromhex("73C7FEFF14"),bytes.fromhex("3CCAFEFF14"),bytes.fromhex("55CCFEFF14")]
    p294=[0]; lp294=threading.Lock()
    def b294():
        with FRAME_ENABLED_LOCK:
            if not FRAME_ENABLED.get(0x294, True): return b''
        with lp294: d=SEQ294[p294[0]]; p294[0]=(p294[0]+1)%3; return d

    c30B=[0x50]; l30B=threading.Lock()
    def b30B():
        with FRAME_ENABLED_LOCK:
            if not FRAME_ENABLED.get(0x30B, True): return b''
        d = _get_base(0x30B)
        with l30B: d[1]=c30B[0]; c30B[0]=0x50 if c30B[0]>=0x5F else c30B[0]+1
        return bytes(d)

    c369 = make_counter(0,15,wrap=0)
    def b369():
        with FRAME_ENABLED_LOCK:
            if not FRAME_ENABLED.get(0x369, True): return b''
        d = _get_base(0x369)
        d[1]=(d[1]&0xF0)|(c369()&0x0F)
        d[0]=crc8(bytes(d[1:5]),0xFF,0xC5); return bytes(d)

    c36F = make_counter(0,15,wrap=0)
    def b36F():
        with FRAME_ENABLED_LOCK:
            if not FRAME_ENABLED.get(0x36F, True): return b''
        d = _get_base(0x36F)
        d[1]=(d[1]&0xF0)|(c36F()&0x0F)
        d[0]=crc8(bytes(d[1:5]),0xFF,0x17); return bytes(d)

    c3A0=[0xA5]; l3A0=threading.Lock()
    def b3A0():
        with FRAME_ENABLED_LOCK:
            if not FRAME_ENABLED.get(0x3A0, True): return b''
        d = _get_base(0x3A0)
        with l3A0: d[7]=c3A0[0]; c3A0[0]=(c3A0[0]+1)&0xFF
        return bytes(d)

    def b3D8():
        with FRAME_ENABLED_LOCK:
            if not FRAME_ENABLED.get(0x3D8, True): return b''
        d = _get_base(0x3D8)
        d[0]=crc8(bytes(d[1:8]),0xFF,0xD8); return bytes(d)

    c3FD = make_counter(0,14)
    def b3FD():
        with FRAME_ENABLED_LOCK:
            if not FRAME_ENABLED.get(0x3FD, True): return b''
        d = _get_base(0x3FD)
        d[1]=(d[1]&0xF0)|(c3FD()&0x0F)
        d[0]=crc8(bytes(d[1:8]),0xFF,0xD6); return bytes(d)

    def b0DF():
        with FRAME_ENABLED_LOCK:
            if not FRAME_ENABLED.get(0x0DF, True): return b''
        return bytes(_get_base(0x0DF))

    def b2C4():
        with FRAME_ENABLED_LOCK:
            if not FRAME_ENABLED.get(0x2C4, True): return b''
        return bytes(_get_base(0x2C4))

    def b349():
        with FRAME_ENABLED_LOCK:
            if not FRAME_ENABLED.get(0x349, True): return b''
        return bytes(_get_base(0x349))

    def b36E():
        with FRAME_ENABLED_LOCK:
            if not FRAME_ENABLED.get(0x36E, True): return b''
        return bytes(_get_base(0x36E))

    def b510():
        with FRAME_ENABLED_LOCK:
            if not FRAME_ENABLED.get(0x510, True): return b''
        return bytes(_get_base(0x510))

    c_rnda = make_counter(0, 255, wrap=0)
    def b_rnd_a():
        while True:
            aid = random.randint(0x001, 0x7FF)
            if aid not in RESERVED_IDS: break
        return aid, build_fuzz_payload(S['FUZZ_FORMAT'], c_rnda(), 8)

    return {
        0x03C:b03C, 0x0F3:b0F3, 0x1A1:b1A1, 0x0AB:b0AB, 0x0DF:b0DF,
        0x2A7:b2A7, 0x2EC:b2EC, 0x294:b294, 0x2C4:b2C4, 0x349:b349,
        0x30B:b30B, 0x369:b369, 0x36E:b36E, 0x36F:b36F, 0x3A0:b3A0,
        0x3D8:b3D8, 0x3FD:b3FD, 0x510:b510, 'rnd_a':b_rnd_a,
    }

# ──────────────────────────────────────────────
#  Fuzz帧构建（模式C）
# ──────────────────────────────────────────────
def make_slot_builder(slot_state):
    ctr = make_counter(0, 255, wrap=0)
    frozen_rnd = [None]
    def builder():
        with S['slot_lock']:
            active = slot_state.get('active', True)
            enabled = slot_state.get('enabled', True)
            target_id = slot_state['id']
        if not active or not enabled:
            return 0x000, b'\x00' * 8
        if S['target_paused']:
            return 0x000, b'\x00' * 8
        fmt = S['FUZZ_FORMAT']; bfrozen = S['bytes_frozen']; fmode = S['frozen_send_mode']
        if bfrozen and fmode == 2:
            return 0x000, b'\x00' * 8
        if bfrozen:
            if frozen_rnd[0] is None:
                frozen_rnd[0] = bytes(random.randint(0, 0xFF) for _ in range(7))
            rnd = frozen_rnd[0]
        else:
            rnd = bytes(random.randint(0, 0xFF) for _ in range(7))
            frozen_rnd[0] = rnd
        c = ctr()
        if fmt == 1:
            d = bytearray(8)
            d[0] = c
            d[1:8] = rnd
            return target_id, bytes(d)
        elif fmt == 2:
            d = bytearray(8)
            d[1] = c
            d[2:8] = rnd[:6]
            d[0] = crc8(bytes(d[1:8]), 0xFF, 0x00)
            return target_id, bytes(d)
        elif fmt == 4:
            d = bytearray(8)
            d[1] = c
            d[2:7] = rnd[:5]
            d[7] = c
            d[0] = crc8(bytes(d[1:8]), 0xFF, 0x00)
            return target_id, bytes(d)
        else:
            return target_id, build_fuzz_payload(fmt, c, 8)
    return builder

# ──────────────────────────────────────────────
#  发送线程
# ──────────────────────────────────────────────
class CyclicSender(threading.Thread):
    def __init__(self,bus,arb_id,builder,cycle_s,label):
        super().__init__(daemon=True,name=label)
        self.bus=bus; self.arb_id=arb_id; self.builder=builder; self.cycle_s=cycle_s

    def run(self):
        next_t=time.perf_counter()
        while S['running'].is_set():
            if self.arb_id==-1:
                result=self.builder()
                if isinstance(result,tuple): aid,data=result
                else: next_t+=self.cycle_s; continue
            else:
                data=self.builder()
                if not data:
                    next_t+=self.cycle_s
                    s=next_t-time.perf_counter()
                    if s>0: time.sleep(s)
                    else: next_t=time.perf_counter()
                    continue
                aid=self.arb_id
            if aid!=0x000 and data:
                try:
                    self.bus.send(can.Message(arbitration_id=aid,data=data,is_extended_id=False))
                    record_tx(aid,data,self.cycle_s*1000)
                except can.CanError: pass
            next_t+=self.cycle_s
            s=next_t-time.perf_counter()
            if s>0: time.sleep(s)
            else: next_t=time.perf_counter()

# ──────────────────────────────────────────────
#  模式D：帧构建（修复byte[0]显示问题）
# ──────────────────────────────────────────────
def mode_d_build_frame():
    D=S['mode_d']
    with D['lock']:
        length=D['length']; locked=dict(D['locked'])
        cur_byte=D['current_byte']; cur_val=D['current_val']
        hit_frozen=D['hit_frozen']; frozen_pay=D['frozen_payload']
        strategy=D['scan_strategy']; dual_b=D['dual_byte_b']
        fmt=S['FUZZ_FORMAT']

    d=bytearray(length)
    # 填锁定值
    for pos,val in locked.items():
        if pos<length: d[pos]=val
    # 填当前穷举byte
    if cur_byte<length: d[cur_byte]=cur_val
    # 联扫第二byte
    if strategy==3 and cur_byte+1<length: d[cur_byte+1]=dual_b
    # 填其余随机byte（跳过0，跳过已处理的）
    for i in range(1,length):
        if i in locked: continue
        if i==cur_byte: continue
        if strategy==3 and i==cur_byte+1: continue
        if hit_frozen and frozen_pay and (i-1)<len(frozen_pay):
            d[i]=frozen_pay[i-1]
        else:
            d[i]=random.randint(0,0xFF)

    # byte[0]按格式处理 —— 关键修复：先计算再写入，保证显示一致
    c=_D_CTR()
    if fmt==1:
        d[0]=c
    elif fmt==2:
        if 1 not in locked and cur_byte!=1:
            d[1]=c
        d[0]=crc8(bytes(d[1:length]),0xFF,0x00)
    elif fmt==4:
        if length > 1 and 1 not in locked and cur_byte!=1:
            d[1]=c
        if length > 7 and 7 not in locked and cur_byte!=7:
            d[7]=c
        elif 1 < length and (length-1) not in locked and cur_byte!=(length-1):
            d[length-1]=c
        d[0]=crc8(bytes(d[1:length]),0xFF,0x00)
    else:
        d[0]=random.randint(0,0xFF)

    return bytes(d)

def mode_d_get_current_target():
    D=S['mode_d']
    with D['lock']:
        ids=D.get('target_ids') or [0x000]
        idx=D.get('current_target_idx',0)
        if idx<0: idx=0
        if idx>=len(ids): idx=0
        return ids[idx], idx, list(ids)

def mode_d_switch_target(step):
    D=S['mode_d']
    with D['lock']:
        ids=D.get('target_ids') or [0x000]
        if not ids:
            ids=[0x000]
            D['target_ids']=ids
        D['current_target_idx']=(D.get('current_target_idx',0)+step) % len(ids)
        aid=ids[D['current_target_idx']]
        D['status']=f"已切换目标ID -> 0x{aid:03X}"
    D['event'].set()

# ──────────────────────────────────────────────
#  模式D：辅助
# ──────────────────────────────────────────────
def _d_send_and_wait(bus, target_id=None):
    D=S['mode_d']
    if target_id is None:
        target_id,_,_=mode_d_get_current_target()
    data=mode_d_build_frame()
    try:
        bus.send(can.Message(arbitration_id=target_id,data=data,is_extended_id=False))
        record_tx(target_id,data,0)
    except can.CanError: pass

    auto=D['auto_mode']; interval=D['auto_interval']; strategy=D['scan_strategy']
    if not auto or strategy==4:
        with D['lock']: D['waiting_key']=True
        D['event'].clear(); D['event'].wait(timeout=60)
        with D['lock']: D['waiting_key']=False; action=D['_action']; D['_action']='n'
    else:
        D['event'].clear(); D['event'].wait(timeout=interval)
        with D['lock']: action=D['_action']; D['_action']='n'
        while D['auto_paused'] and S['running'].is_set(): time.sleep(0.05)
    return action

def _d_handle_hit(byte_pos, val):
    D=S['mode_d']
    with D['lock']:
        D['hits'].append((byte_pos,val,f"0x{val:02X}"))
        D['hit_frozen']=True
        D['frozen_payload']=bytes(random.randint(0,0xFF) for _ in range(D['length']-1))
        D['status']=(f"★ 命中 byte[{byte_pos}]=0x{val:02X}！随机bytes已冻结，Counter/CRC继续刷新。按[解冻  L锁定")

def _d_handle_lock(byte_pos, val):
    D=S['mode_d']
    with D['lock']:
        D['locked'][byte_pos]=val
        D['hits'].append((byte_pos,val,f"0x{val:02X} [锁定]"))
        D['hit_frozen']=False; D['frozen_payload']=None
        D['status']=f"✓ 锁定 byte[{byte_pos}]=0x{val:02X}，进入 byte[{byte_pos+1}]"

def _d_handle_back(byte_pos):
    D=S['mode_d']
    if byte_pos>1:
        prev=byte_pos-1
        with D['lock']:
            D['locked'].pop(prev,None); D['hit_frozen']=False; D['frozen_payload']=None
            D['status']=f"↩ 回退到 byte[{prev}]"
        return prev
    return byte_pos

# ──────────────────────────────────────────────
#  模式D：策略
# ──────────────────────────────────────────────
def _strat_standard(bus, byte_pos, target_id, length):
    D=S['mode_d']; val=0x00
    while val<=0xFF and S['running'].is_set():
        with D['lock']:
            D['current_byte']=byte_pos; D['current_val']=val
            strat=D['scan_strategy']
        if strat not in (1,4): return byte_pos,'switch'
        auto=D['auto_mode']; interval=D['auto_interval']
        with D['lock']:
            D['status']=(
                f"[{'自动' if auto else '手动'}|策略{'4' if strat==4 else '1'}] "
                f"byte[{byte_pos}]=0x{val:02X}  "
                f"{'间隔:'+str(int(interval*1000))+'ms  ' if auto and strat!=4 else ''}"
                f"Y=有反应  N=无反应  L=锁定  B=回退"
            )
        action=_d_send_and_wait(bus,target_id)
        if action=='q': return byte_pos,'quit'
        elif action=='y': _d_handle_hit(byte_pos,val); val+=1
        elif action=='l': _d_handle_lock(byte_pos,val); return byte_pos+1,'next'
        elif action=='b': return _d_handle_back(byte_pos),'back'
        else:
            with D['lock']: D['hit_frozen']=False
            val+=1
    return byte_pos+1,'next'

def _strat_group(bus, byte_pos, target_id, length):
    D=S['mode_d']
    with D['lock']:
        D['group_phase']='high'
        D['status']=f"[分组扫] byte[{byte_pos}] 阶段1: 扫高nibble"
    found_high=None
    for hi in range(16):
        val=hi<<4
        with D['lock']:
            D['current_byte']=byte_pos; D['current_val']=val
            if D['scan_strategy']!=2: return byte_pos,'switch'
        with D['lock']:
            D['status']=f"[分组扫-高nibble] byte[{byte_pos}]=0x{val:02X}  Y=命中  N=继续  L=锁定  B=回退"
        action=_d_send_and_wait(bus,target_id)
        if action=='q': return byte_pos,'quit'
        elif action=='b': return _d_handle_back(byte_pos),'back'
        elif action in ('y','l'):
            found_high=hi; _d_handle_hit(byte_pos,val)
            if action=='l': _d_handle_lock(byte_pos,val); return byte_pos+1,'next'
            break
    if found_high is None: return byte_pos+1,'next'
    with D['lock']:
        D['group_phase']='low'; D['group_high_val']=found_high
        D['status']=f"[分组扫] byte[{byte_pos}] 阶段2: 精确低nibble"
    for lo in range(16):
        val=(found_high<<4)|lo
        with D['lock']:
            D['current_byte']=byte_pos; D['current_val']=val
            if D['scan_strategy']!=2: return byte_pos,'switch'
        with D['lock']:
            D['status']=f"[分组扫-低nibble] byte[{byte_pos}]=0x{val:02X}  Y=命中  L=锁定  N=继续"
        action=_d_send_and_wait(bus,target_id)
        if action=='q': return byte_pos,'quit'
        elif action=='l': _d_handle_lock(byte_pos,val); return byte_pos+1,'next'
        elif action=='y': _d_handle_hit(byte_pos,val)
        elif action=='b': return _d_handle_back(byte_pos),'back'
    return byte_pos+1,'next'

def _strat_dual(bus, byte_pos, target_id, length):
    D=S['mode_d']
    if byte_pos+1>=length: return byte_pos+1,'next'
    with D['lock']:
        D['status']=f"[联扫] byte[{byte_pos}]+byte[{byte_pos+1}] 同步递增"
    for val in range(256):
        with D['lock']:
            D['current_byte']=byte_pos; D['current_val']=val; D['dual_byte_b']=val
            if D['scan_strategy']!=3: return byte_pos,'switch'
        with D['lock']:
            D['status']=f"[联扫] b[{byte_pos}]=b[{byte_pos+1}]=0x{val:02X}  Y/L=命中锁定  N=继续  B=回退"
        action=_d_send_and_wait(bus,target_id)
        if action=='q': return byte_pos,'quit'
        elif action in ('y','l'):
            _d_handle_hit(byte_pos,val); _d_handle_hit(byte_pos+1,val)
            if action=='l':
                with D['lock']: D['locked'][byte_pos]=val; D['locked'][byte_pos+1]=val
            return byte_pos+2,'next'
        elif action=='b': return _d_handle_back(byte_pos),'back'
    return byte_pos+2,'next'

def mode_d_worker(bus):
    D=S['mode_d']
    with D['lock']: length=D['length']
    byte_pos=1
    while byte_pos<length and S['running'].is_set():
        with D['lock']: D['current_byte']=byte_pos; strat=D['scan_strategy']
        target_id,_,_=mode_d_get_current_target()
        if strat==2: byte_pos,result=_strat_group(bus,byte_pos,target_id,length)
        elif strat==3: byte_pos,result=_strat_dual(bus,byte_pos,target_id,length)
        else: byte_pos,result=_strat_standard(bus,byte_pos,target_id,length)
        if result=='quit': break
        if result=='switch': continue
    with D['lock']:
        D['done']=True; D['status']='穷举完成！按 S 保存并回主菜单。'

# ──────────────────────────────────────────────
#  显示（终端）
# ──────────────────────────────────────────────
_FMT_NAMES={1:"CTR+随机",2:"CRC+CTR+随机",3:"纯随机",4:"CRC+双CTR+随机"}
_SCAN_NAMES={1:"标准逐byte",2:"分组nibble",3:"双byte联扫",4:"加速手动"}

def draw_normal():
    now=time.time(); mode=S['APP_MODE']; fmt=S['FUZZ_FORMAT']
    out=["[H[J"]
    hk=f"{YELLOW}M=标记  S=回菜单  Q=保存退出  ALT+Q=不保存退出  W=重开日志窗"
    if mode=='B': hk+=f"  {MAGENTA}N=换ID"
    if mode=='C':
        pl=f"{RED}P=恢复目标帧" if S['target_paused'] else f"{GREEN}P=暂停目标帧"
        fl=f"{BLUE}[=解冻随机" if S['bytes_frozen'] else f"{CYAN}[=冻结随机"
        fm="停发" if S['frozen_send_mode']==2 else "继续发"
        hk+=f"  {pl}  {fl}  {GRAY}9=冻结({fm})  {CYAN}A=管理中心GUI"
    out.append(f"{BOLD}{CYAN}{'═'*112}{RESET}")
    out.append(f"{BOLD}{WHITE}  CAN监控  {time.strftime('%H:%M:%S')}  [模式{mode}] {GRAY}格式:{_FMT_NAMES[fmt]}{RESET}  {hk}{RESET}")
    out.append(f"{BOLD}{CYAN}{'═'*112}{RESET}\n")
    if mode=='C':
        ps=f"{RED}● 目标帧已暂停{RESET}" if S['target_paused'] else f"{GREEN}● 目标帧发送中{RESET}"
        fs=f"{BLUE}● 随机bytes冻结{RESET}" if S['bytes_frozen'] else f"{GREEN}● 随机bytes正常{RESET}"
        fuzz_n=len(mode_c_get_active_fuzz_slots())
        fix_n=len([p for p in mode_c_get_fixed_profiles() if p.get('active', True)])
        out.append(f"  {ps}    {fs}    {CYAN}Fuzz={fuzz_n}  固定ID={fix_n}{RESET}\n")
    with S['frame_lock']: snap=dict(S['frame_state'])
    out.append(f"{BOLD}{YELLOW}  {'ID':<8}{'名称':<22}{'周期':>7}  {'次数':>9}  数据{RESET}")
    out.append(f"  {GRAY}{'─'*108}{RESET}")
    for aid in DISPLAY_ORDER:
        if aid not in snap: continue
        st=snap[aid]; age=now-st['ts']; col=GRAY if age>0.5 else GREEN
        with FRAME_ENABLED_LOCK: en=FRAME_ENABLED.get(aid,True)
        en_str=f"{GREEN}●{RESET}" if en else f"{RED}○{RESET}"
        out.append(
            f"  {en_str} {CYAN}0x{aid:03X}{RESET}  {WHITE}{st['name']:<22}{RESET}"
            f"{GRAY}{st['cycle']:>6.0f}ms{RESET}  {col}{st['count']:>9}{RESET}  "
            f"{fmt_bytes_indexed(st['data'])}"
        )
        out.append(f"  {GRAY}{'':12}{st['logic']}{RESET}\n")

    if mode in ('B','C'):
        fuzz_entries = [e for e in mode_c_collect_entries() if e['kind']=='fuzz'] if mode=='C' else []
        if mode=='B':
            ids_str = ", ".join(f"0x{i:03X}" for i in get_fuzz_ids()) or "(空)"
            out.append(f"{BOLD}{MAGENTA}  ▶ 定向Fuzz: {ids_str} ◀{RESET}")
            out.append(f"  {GRAY}{'─'*108}{RESET}")
            for aid in sorted(get_fuzz_ids()):
                if aid not in snap: continue
                st=snap[aid]; age=now-st['ts']; col=GRAY if age>0.5 else MAGENTA
                out.append(
                    f"    {col}0x{aid:03X}{RESET}  {GRAY}{'定向Fuzz':<22}{RESET}"
                    f"{GRAY}{st['cycle']:>6.0f}ms{RESET}  {col}{st['count']:>9}{RESET}  {fmt_bytes_indexed(st['data'])}"
                )
            out.append("")
        elif fuzz_entries:
            ids_str = ", ".join(f"0x{e['id']:03X}{'' if e['enabled'] else '(OFF)'}" for e in fuzz_entries) or "(空)"
            out.append(f"{BOLD}{MAGENTA}  ▶ 模式C Fuzz目标: {ids_str} ◀{RESET}")
            out.append(f"  {GRAY}{'─'*108}{RESET}")
            for e in fuzz_entries:
                aid=e['id']
                if aid not in snap: continue
                st=snap[aid]; age=now-st['ts']; col=GRAY if age>0.5 else MAGENTA
                state=f"{GREEN}ON{RESET}" if e['enabled'] else f"{RED}OFF{RESET}"
                out.append(
                    f"    {col}0x{aid:03X}{RESET}  {GRAY}{e['name']:<22}{RESET}{state}  "
                    f"{GRAY}{st['cycle']:>6.0f}ms{RESET}  {col}{st['count']:>9}{RESET}  {fmt_bytes_indexed(st['data'])}"
                )
            out.append("")

    if mode=='C':
        fixed_entries = [e for e in mode_c_collect_entries() if e['kind']=='fixed']
        if fixed_entries:
            out.append(f"{BOLD}{CYAN}  ▶ 自定义固定ID ◀{RESET}")
            out.append(f"  {GRAY}{'─'*108}{RESET}")
            for e in fixed_entries:
                aid=e['id']
                st=snap.get(aid)
                state=f"{GREEN}ON{RESET}" if e['enabled'] else f"{RED}OFF{RESET}"
                data_s=fmt_bytes_indexed(st['data']) if st else f"{GRAY}(暂无发送){RESET}"
                cnt_s=f"{st['count']:>9}" if st else '        0'
                cyc = st['cycle'] if st else e['cycle_ms']
                col=CYAN if st and now-st['ts']<=0.5 else GRAY
                out.append(
                    f"    {col}0x{aid:03X}{RESET}  {WHITE}{e['name']:<22}{RESET}{state}  "
                    f"{GRAY}{cyc:>6.0f}ms{RESET}  {col}{cnt_s}{RESET}  {data_s}"
                )
                out.append(f"    {GRAY}{e['logic']}{RESET}")
            out.append("")

    with S['marks_lock']: recent=list(S['marks'])[-3:]
    if recent:
        out.append(f"{BOLD}{RED}  最近标记{RESET}")
        for m in recent:
            out.append(f"  {RED}▶ 标记#{m['idx']} [{time.strftime('%H:%M:%S',time.localtime(m['ts']))}]{RESET}")
    with S['marks_lock']: nm=len(S['marks'])
    out.append(f"\n  {GRAY}监控ID={len(snap)}  标记={nm}  历史帧={len(S['tx_history'])}{RESET}")
    sys.stdout.write("\n".join(out)); sys.stdout.flush()

def draw_mode_d():
    D=S['mode_d']
    with D['lock']:
        ids=list(D.get('target_ids') or [0x000]); cur_idx=D.get('current_target_idx',0); length=D['length']
        if cur_idx<0 or cur_idx>=len(ids): cur_idx=0
        target_id=ids[cur_idx]
        cur_byte=D['current_byte']; cur_val=D['current_val']
        locked=dict(D['locked']); hits=list(D['hits'])
        status=D['status']; done=D['done']
        auto_mode=D['auto_mode']; interval=D['auto_interval']
        strategy=D['scan_strategy']; hit_frz=D['hit_frozen']
        auto_pause=D['auto_paused']; dual_b=D['dual_byte_b']
        gphase=D['group_phase']; ghi=D['group_high_val']
        waiting=D['waiting_key']
    d=bytearray(length)
    for pos,val in locked.items():
        if pos<length: d[pos]=val
    if cur_byte<length: d[cur_byte]=cur_val
    if strategy==3 and cur_byte+1<length: d[cur_byte+1]=dual_b
    out=["\033[H\033[J"]
    out.append(f"{BOLD}{CYAN}{'═'*96}{RESET}")
    auto_str=(f"{RED}自动[已暂停]" if auto_pause else
              f"{GREEN}自动[{interval*1000:.0f}ms]" if auto_mode else f"{YELLOW}手动")
    out.append(f"{BOLD}{WHITE}  模式D穷举  {time.strftime('%H:%M:%S')}  {CYAN}{_SCAN_NAMES[strategy]}{RESET}  {auto_str}{RESET}")
    out.append(f"  {GRAY}T=手动/自动  +/-=间隔  P=暂停  ↑/↓=切换目标ID  1/2/3/4=策略  W=重开日志{RESET}")
    out.append(f"  {GRAY}Y=有反应  N/空格=无反应  L=锁定进下一byte  B=回退  [=解冻  S=保存回菜单  ALT+Q=不保存退出{RESET}")
    out.append(f"{BOLD}{CYAN}{'═'*96}{RESET}\n")
    out.append(f"  目标: {CYAN}0x{target_id:03X}{RESET}  ({cur_idx+1}/{len(ids)})   帧长: {length}   格式: {_FMT_NAMES[S['FUZZ_FORMAT']]}\n")
    out.append(f"  {GRAY}全部目标ID: {'  '.join((CYAN if i==cur_idx else GRAY)+f'0x{x:03X}'+RESET for i,x in enumerate(ids))}{RESET}\n")
    fs="  "
    for i,b in enumerate(d):
        if i==0: c=YELLOW if S['FUZZ_FORMAT']==1 else GRAY
        elif i in locked: c=GREEN
        elif i==cur_byte: c=YELLOW
        elif strategy==3 and i==cur_byte+1: c=ORANGE
        else: c=GRAY
        fs+=f"  {c}[{i}]{b:02X}{RESET}"
    out.append(fs)
    out.append(f"  {GRAY}[0]{YELLOW}黄=CTR/fmt控  {GREEN}绿=锁定  {YELLOW}黄=穷举中  {ORANGE}橙=联扫辅助{RESET}")
    if strategy==2:
        ph="阶段1:高nibble" if gphase=='high' else f"阶段2:低nibble(高nibble=0x{ghi:X})"
        out.append(f"\n  {CYAN}[分组扫] {ph}{RESET}")
    out.append("")
    if strategy==2:
        step=(cur_val>>4) if gphase=='high' else (cur_val&0xF)
        filled=int(40*step/15)
        out.append(f"  byte[{cur_byte}]({gphase}nibble): {YELLOW}{'█'*filled}{GRAY}{'░'*(40-filled)}{RESET} {YELLOW}0x{cur_val:02X}{RESET}")
    elif strategy==3:
        filled=int(40*cur_val/255)
        out.append(f"  b[{cur_byte}]+b[{cur_byte+1}]联扫: {YELLOW}{'█'*filled}{GRAY}{'░'*(40-filled)}{RESET} {YELLOW}0x{cur_val:02X}/0x{dual_b:02X}{RESET}")
    else:
        filled=int(40*cur_val/255)
        out.append(f"  byte[{cur_byte}]: {YELLOW}{'█'*filled}{GRAY}{'░'*(40-filled)}{RESET} {YELLOW}{cur_val}/255 (0x{cur_val:02X}){RESET}")
    out.append("")
    if locked:
        out.append(f"  {GREEN}已锁定: "+"  ".join(f"b[{p}]=0x{v:02X}" for p,v in sorted(locked.items()))+RESET)
    if hits:
        out.append(f"  {RED}命中: "+"  ".join(f"b[{bp}]={note}" for bp,_,note in hits[-10:])+RESET)
    if hit_frz:
        out.append(f"  {MAGENTA}★ 命中冻结中：随机bytes固定，Counter/CRC继续刷新  按[解冻{RESET}")
    scol=GREEN if done else (RED if hit_frz else (YELLOW if waiting else CYAN))
    out.append(f"\n  {scol}{BOLD}{status}{RESET}")
    out.append(f"\n  {GRAY}后台固定帧持续发送中{RESET}")
    sys.stdout.write("\n".join(out)); sys.stdout.flush()

def monitor_loop():
    while S['running'].is_set():
        if S['APP_MODE']=='D': draw_mode_d()
        else: draw_normal()
        time.sleep(0.15)

# ──────────────────────────────────────────────
#  保存日志
# ──────────────────────────────────────────────
def save_log():
    with S['marks_lock']: all_marks=list(S['marks'])
    with S['tx_lock']:    all_tx=list(S['tx_history'])
    with S['frame_lock']: final_st=dict(S['frame_state'])
    with open(LOG_FILE,"a",encoding="utf-8") as f:
        f.write("\n"+"="*90+"\n")
        f.write(f"保存: {time.strftime('%Y-%m-%d %H:%M:%S')}  模式{S['APP_MODE']}  格式{S['FUZZ_FORMAT']}\n")
        f.write("="*90+"\n\n")
        if S['APP_MODE']=='D':
            D=S['mode_d']
            with D['lock']: d_ids=list(D.get('target_ids') or [0x000]); d_idx=D.get('current_target_idx',0); d_hits=list(D['hits']); d_locked=dict(D['locked'])
            d_id=d_ids[d_idx if 0<=d_idx<len(d_ids) else 0]
            f.write(f"【模式D穷举结果】目标列表: {', '.join(f'0x{x:03X}' for x in d_ids)} | 当前: 0x{d_id:03X}\n")
            for pos,val in sorted(d_locked.items()): f.write(f"  锁定 byte[{pos}]=0x{val:02X}\n")
            for bp,bv,note in d_hits: f.write(f"  命中 byte[{bp}]={note}\n")
            f.write("\n")
        f.write("【帧状态】\n")
        for aid in DISPLAY_ORDER:
            if aid in final_st:
                st=final_st[aid]
                f.write(f"  0x{aid:03X}  {st['name']:<22}  {fmt_bytes_plain(st['data'])}  x{st['count']}\n")
        if all_marks:
            f.write(f"\n【标记】{len(all_marks)}个\n")
            for m in all_marks:
                ts_s=time.strftime("%Y-%m-%d %H:%M:%S",time.localtime(m["ts"]))
                f.write(f"\n  标记#{m['idx']} [{ts_s}]\n")
                for aid in DISPLAY_ORDER:
                    if aid in m["fixed"]:
                        st=m["fixed"][aid]; f.write(f"    0x{aid:03X}  {fmt_bytes_plain(st['data'])}\n")
        f.write(f"\n【发送历史】{len(all_tx)}条\n")
        from collections import defaultdict
        by_id=defaultdict(list)
        for rec in all_tx: by_id[rec["id"]].append(rec)
        for aid in DISPLAY_ORDER:
            if aid not in by_id: continue
            f.write(f"\n  0x{aid:03X} ({len(by_id[aid])}条)\n")
            for rec in by_id[aid]:
                f.write(f"    {time.strftime('%H:%M:%S',time.localtime(rec['ts']))}  {fmt_bytes_plain(rec['data'])}\n")
        rr=[(k,v) for k,vs in by_id.items() if k not in RESERVED_IDS for v in vs]
        if rr:
            f.write(f"\n  探测帧({len(rr)}条)\n")
            for aid,rec in sorted(rr,key=lambda x:x[1]["ts"]):
                f.write(f"    {time.strftime('%H:%M:%S',time.localtime(rec['ts']))}  0x{aid:03X}  {fmt_bytes_plain(rec['data'])}\n")
    print(f"\n{GREEN}✓ 已追加保存: {LOG_FILE}{RESET}\n")

# ══════════════════════════════════════════════
#  GUI 窗口 0：会话控制中心（新增，不替代原菜单/按键）
# ══════════════════════════════════════════════
def open_control_window(bus=None, threads=None):
    if not _ensure_tk(): return
    def _build():
        with _WIN_LOCK:
            if _WIN.get('control') and _WIN['control'].winfo_exists():
                _WIN['control'].lift(); return
        win = tk.Toplevel(_tk_root)
        with _WIN_LOCK: _WIN['control'] = win
        win.title('🧭 会话控制中心  —  CAN Sender v3.3')
        win.geometry('1180x760')
        win.configure(bg='#0b1020')

        top = tk.Frame(win, bg='#0b1020', padx=12, pady=10)
        top.pack(fill='x')
        tk.Label(top, text='BMW CAN Session Control Center', bg='#0b1020', fg='#9ad7ff',
                 font=('Segoe UI', 16, 'bold')).pack(anchor='w')
        tk.Label(top, text='这是新增的控制中心，不替代原来的开机画面、主菜单、模式选择、按键和文本。所有原功能仍保留。',
                 bg='#0b1020', fg='#7f8ea3', font=('Segoe UI', 9)).pack(anchor='w', pady=(4, 0))

        body = tk.Frame(win, bg='#0b1020')
        body.pack(fill='both', expand=True, padx=12, pady=(0, 12))

        left = tk.Frame(body, bg='#0b1020')
        left.pack(side='left', fill='both', expand=True)
        right = tk.Frame(body, bg='#0b1020', width=360)
        right.pack(side='right', fill='y', padx=(12, 0))
        right.pack_propagate(False)

        def card(parent, title, subtitle=''):
            outer = tk.Frame(parent, bg='#131b2f', bd=0, highlightthickness=1, highlightbackground='#22304f')
            outer.pack(fill='x', pady=6)
            hdr = tk.Frame(outer, bg='#131b2f')
            hdr.pack(fill='x', padx=12, pady=(10, 6))
            tk.Label(hdr, text=title, bg='#131b2f', fg='#eef5ff', font=('Segoe UI', 11, 'bold')).pack(anchor='w')
            if subtitle:
                tk.Label(hdr, text=subtitle, bg='#131b2f', fg='#73839d', font=('Segoe UI', 8)).pack(anchor='w', pady=(2, 0))
            inner = tk.Frame(outer, bg='#131b2f')
            inner.pack(fill='x', padx=12, pady=(0, 12))
            return outer, inner

        summary_card, summary = card(left, '会话概览', '原终端显示继续保留，这里只做更顺手的集中控制。')
        summary_text = tk.StringVar(value='加载中...')
        tk.Label(summary, textvariable=summary_text, justify='left', anchor='w', bg='#131b2f', fg='#cfd8e6',
                 font=('Consolas', 10)).pack(fill='x')

        term_card, term = card(left, '终端 / 点火 / 固定在线帧')
        term_state_var = tk.StringVar(value='CUSTOM')
        frame3a0_var = tk.StringVar(value='未知')
        tk.Label(term, text='当前 Terminal 状态', bg='#131b2f', fg='#7f8ea3', font=('Segoe UI', 8)).grid(row=0, column=0, sticky='w')
        tk.Label(term, textvariable=term_state_var, bg='#131b2f', fg='#9ad7ff', font=('Consolas', 12, 'bold')).grid(row=1, column=0, sticky='w', pady=(0, 8))
        tk.Label(term, text='3A0 固定帧', bg='#131b2f', fg='#7f8ea3', font=('Segoe UI', 8)).grid(row=0, column=1, sticky='w', padx=(18, 0))
        tk.Label(term, textvariable=frame3a0_var, bg='#131b2f', fg='#9ad7ff', font=('Consolas', 12, 'bold')).grid(row=1, column=1, sticky='w', padx=(18, 0), pady=(0, 8))

        def _btn(master, text, cmd, bg='#22304f', fg='#eef5ff', w=12):
            return tk.Button(master, text=text, command=cmd, bg=bg, fg=fg, activebackground=bg, activeforeground=fg,
                             relief='flat', font=('Segoe UI', 9, 'bold'), width=w, padx=8, pady=6, cursor='hand2')

        actions = tk.Frame(term, bg='#131b2f')
        actions.grid(row=2, column=0, columnspan=2, sticky='w')
        _btn(actions, 'ACC', lambda: apply_terminal_state('ACC', FRAME_BASE_DATA, FRAME_BASE_LOCK, crc8), bg='#1f6feb').pack(side='left', padx=(0, 6))
        _btn(actions, 'IGN', lambda: apply_terminal_state('IGN', FRAME_BASE_DATA, FRAME_BASE_LOCK, crc8), bg='#238636').pack(side='left', padx=6)
        _btn(actions, '切换', lambda: cycle_terminal_state(FRAME_BASE_DATA, FRAME_BASE_LOCK, crc8), bg='#6f42c1').pack(side='left', padx=6)
        _btn(actions, '3A0 开关', lambda: toggle_frame_enabled(FRAME_ENABLED, FRAME_ENABLED_LOCK, 0x3A0), bg='#d29922').pack(side='left', padx=6)

        config_card, config = card(left, '速度 / 转速设置', '这两个就是你原来在模式 B/C 里手动配置的项，这里只是补一个更符合人习惯的面板。')
        speed_en = tk.BooleanVar(value=bool(S.get('speed_config_enabled', False)))
        speed_val = tk.StringVar(value=f"{float(S.get('speed_value', 0.0)):.1f}")
        speed_jit = tk.StringVar(value=f"{float(S.get('speed_jitter', 0.0)):.1f}")
        rpm_en = tk.BooleanVar(value=bool(S.get('rpm_config_enabled', False)))
        rpm_val = tk.StringVar(value=str(int(S.get('rpm_value', 0))))
        rpm_jit = tk.StringVar(value=str(int(S.get('rpm_jitter', 50))))

        def _apply_speed():
            try:
                S['speed_config_enabled'] = bool(speed_en.get())
                S['speed_value'] = max(0.0, min(300.0, float(speed_val.get().strip() or '0')))
                S['speed_jitter'] = max(0.0, min(20.0, float(speed_jit.get().strip() or '0')))
            except Exception:
                pass

        def _apply_rpm():
            try:
                S['rpm_config_enabled'] = bool(rpm_en.get())
                S['rpm_value'] = max(0, min(9000, int(rpm_val.get().strip() or '0')))
                S['rpm_jitter'] = max(0, min(1000, int(rpm_jit.get().strip() or '0')))
            except Exception:
                pass

        def _wire_commit(var, fn):
            var.trace_add('write', lambda *_: fn())

        tk.Checkbutton(config, text='启用默认车速', variable=speed_en, command=_apply_speed, bg='#131b2f', fg='#cfd8e6',
                       selectcolor='#1d2942', activebackground='#131b2f', font=('Segoe UI', 9)).grid(row=0, column=0, sticky='w')
        tk.Label(config, text='车速 km/h', bg='#131b2f', fg='#7f8ea3', font=('Segoe UI', 8)).grid(row=1, column=0, sticky='w', pady=(8, 0))
        tk.Entry(config, textvariable=speed_val, width=10, bg='#0f1726', fg='#9ee2a8', insertbackground='white', relief='flat', font=('Consolas', 10)).grid(row=2, column=0, sticky='w')
        tk.Label(config, text='抖动 ±', bg='#131b2f', fg='#7f8ea3', font=('Segoe UI', 8)).grid(row=1, column=1, sticky='w', padx=(16,0), pady=(8, 0))
        tk.Entry(config, textvariable=speed_jit, width=10, bg='#0f1726', fg='#9ee2a8', insertbackground='white', relief='flat', font=('Consolas', 10)).grid(row=2, column=1, sticky='w', padx=(16,0))

        tk.Checkbutton(config, text='启用默认转速', variable=rpm_en, command=_apply_rpm, bg='#131b2f', fg='#cfd8e6',
                       selectcolor='#1d2942', activebackground='#131b2f', font=('Segoe UI', 9)).grid(row=3, column=0, sticky='w', pady=(14,0))
        tk.Label(config, text='转速 rpm', bg='#131b2f', fg='#7f8ea3', font=('Segoe UI', 8)).grid(row=4, column=0, sticky='w', pady=(8, 0))
        tk.Entry(config, textvariable=rpm_val, width=10, bg='#0f1726', fg='#9ee2a8', insertbackground='white', relief='flat', font=('Consolas', 10)).grid(row=5, column=0, sticky='w')
        tk.Label(config, text='抖动 ±', bg='#131b2f', fg='#7f8ea3', font=('Segoe UI', 8)).grid(row=4, column=1, sticky='w', padx=(16,0), pady=(8, 0))
        tk.Entry(config, textvariable=rpm_jit, width=10, bg='#0f1726', fg='#9ee2a8', insertbackground='white', relief='flat', font=('Consolas', 10)).grid(row=5, column=1, sticky='w', padx=(16,0))
        _wire_commit(speed_val, _apply_speed); _wire_commit(speed_jit, _apply_speed); _wire_commit(rpm_val, _apply_rpm); _wire_commit(rpm_jit, _apply_rpm)

        mode_card, mode_box = card(left, '模式专属控制', '模式 B / C / D 的原按键依然有效，这里只是补一个图形入口。')
        mode_info = tk.StringVar(value='')
        tk.Label(mode_box, textvariable=mode_info, justify='left', anchor='w', bg='#131b2f', fg='#cfd8e6', font=('Consolas', 10)).pack(fill='x')
        btns = tk.Frame(mode_box, bg='#131b2f')
        btns.pack(fill='x', pady=(8, 0))

        def _regen_mode_b():
            cur = list(get_fuzz_ids())
            new = []
            while len(new) < len(cur):
                aid = random.randint(0x001, 0x7FF)
                if aid not in RESERVED_IDS and aid not in new:
                    new.append(aid)
            with S['fuzz_lock']:
                S['FUZZ_TARGET_IDS'][:] = new

        _btn(btns, '模式B换ID', _regen_mode_b, bg='#0d6efd', w=12).pack(side='left', padx=(0,6))
        _btn(btns, '模式C管理', lambda: open_mode_c_gui(bus, threads or []), bg='#8957e5', w=12).pack(side='left', padx=6)
        _btn(btns, '日志窗口', open_log_window, bg='#1f6feb', w=12).pack(side='left', padx=6)
        _btn(btns, '编辑器', open_editor_window, bg='#2da44e', w=12).pack(side='left', padx=6)
        _btn(btns, '历史筛选', open_history_window, bg='#d29922', fg='#1b1f24', w=12).pack(side='left', padx=6)

        right_card, right_inner = card(right, '快捷面板 / 在线帧扩展', '这里补进了你要求加设置的几个 ID，不动原来的功能。')
        extra_ids = [0x0DF, 0x2C4, 0x349, 0x36E]
        row_vars = {}
        for ridx, aid in enumerate(extra_ids):
            name, logic = FRAME_META.get(aid, ('?', '?'))
            tk.Label(right_inner, text=f'0x{aid:03X}', bg='#131b2f', fg='#9ad7ff', font=('Consolas', 10, 'bold')).grid(row=ridx*2, column=0, sticky='w', pady=(2,0))
            tk.Label(right_inner, text=name, bg='#131b2f', fg='#cfd8e6', font=('Segoe UI', 9)).grid(row=ridx*2, column=1, sticky='w', pady=(2,0))
            data_var = tk.StringVar(value=' '.join(f'{b:02X}' for b in FRAME_BASE_DATA[aid]))
            row_vars[aid] = data_var
            ent = tk.Entry(right_inner, textvariable=data_var, bg='#0f1726', fg='#9ee2a8', insertbackground='white', relief='flat', font=('Consolas', 10), width=26)
            ent.grid(row=ridx*2+1, column=0, columnspan=2, sticky='we', pady=(0,4))
            def _apply_extra(fid=aid, var=data_var):
                raw = var.get().strip().replace(',', ' ').replace('0x', '').replace('0X', '')
                try:
                    parts = [p for p in raw.split() if p]
                    data = bytes(int(p, 16) & 0xFF for p in parts)
                    with FRAME_BASE_LOCK:
                        base = bytearray(FRAME_BASE_DATA[fid])
                        if len(data) == len(base):
                            FRAME_BASE_DATA[fid][:] = data
                except Exception:
                    pass
            data_var.trace_add('write', lambda *_args, fid=aid, var=data_var: _apply_extra(fid, var))
            tk.Label(right_inner, text=logic, bg='#131b2f', fg='#6f7f96', font=('Segoe UI', 7)).grid(row=ridx*2+1, column=2, sticky='w', padx=(8,0))
        right_inner.grid_columnconfigure(1, weight=1)

        hotkey_card, hotkeys = card(right, '原始按键仍保留', '这个窗口只是把常用操作集中，不会删掉终端原按键。')
        hotkey_text = 'M 标记\nS 保存回菜单\nQ 保存退出\nALT+Q 不保存退出\nW 重开日志/编辑/历史\nU 打开新增控制中心\nO=ACC  I=IGN  K=切换  ]=3A0\nB模式 N=换ID\nC模式 A=管理中心GUI  P=暂停  [=冻结  9=冻结模式\nD模式 ↑↓ T +/- P 1~4 Y/N/L/B/['
        tk.Label(hotkeys, text=hotkey_text,
                 justify='left', anchor='w', bg='#131b2f', fg='#cfd8e6', font=('Consolas', 10)).pack(fill='x')

        def refresh():
            if not win.winfo_exists():
                return
            mode = S.get('APP_MODE', '?')
            terminal = detect_terminal_state(FRAME_BASE_DATA)
            term_state_var.set(terminal)
            with FRAME_ENABLED_LOCK:
                frame3a0_var.set('开启' if FRAME_ENABLED.get(0x3A0, True) else '关闭')
            with S['marks_lock']:
                marks = len(S['marks'])
            with S['frame_lock']:
                snap = dict(S['frame_state'])
            fixed_on = sum(1 for aid in DISPLAY_ORDER if aid in snap)
            summary_text.set(
                f'模式: {mode}\n'
                f'格式: {_FMT_NAMES.get(S.get("FUZZ_FORMAT"), "未知")}\n'
                f'Terminal: {terminal}\n'
                f'固定在线帧可见数: {fixed_on}\n'
                f'历史发送: {len(S.get("tx_history", []))}\n'
                f'标记数: {marks}\n'
                f'车速设置: {"开" if S.get("speed_config_enabled") else "关"} / {S.get("speed_value", 0.0):.1f} / ±{S.get("speed_jitter", 0.0):.1f}\n'
                f'转速设置: {"开" if S.get("rpm_config_enabled") else "关"} / {S.get("rpm_value", 0)} / ±{S.get("rpm_jitter", 0)}'
            )
            if mode == 'B':
                ids = ', '.join(f'0x{x:03X}' for x in get_fuzz_ids()) or '(空)'
                extra = ', '.join(f'0x{x:03X}' for x in S.get('mode_b_fixed_random_ids', [])) or '(空)'
                mode_info.set(f'模式B 定向Fuzz目标\n{ids}\n\n模式B固定随机帧\n{extra}')
            elif mode == 'C':
                ids = ', '.join(f'0x{x:03X}' for x in S.get('FUZZ_TARGET_IDS', [])) or '(空)'
                fixed = len([p for p in S.get('mode_c_fixed_profiles', []) if p.get('active', True)])
                mode_info.set(f'模式C Fuzz目标\n{ids}\n\n固定/自定义ID数量: {fixed}\n目标暂停: {"是" if S.get("target_paused") else "否"}\n随机冻结: {"是" if S.get("bytes_frozen") else "否"}')
            elif mode == 'D':
                D = S.get('mode_d', {})
                ids = ', '.join(f'0x{x:03X}' for x in D.get('target_ids', [])) or '(空)'
                mode_info.set(f'模式D 穷举目标\n{ids}\n\n当前状态: {D.get("status", "-")}\n自动模式: {"是" if D.get("auto_mode") else "否"}\n策略: {_SCAN_NAMES.get(D.get("scan_strategy"), "-")}')
            else:
                mode_info.set('模式A 全局随机ID\n使用原逻辑发送，不做删改。')
            win.after(250, refresh)

        def on_close():
            with _WIN_LOCK:
                _WIN['control'] = None
            win.destroy()

        win.protocol('WM_DELETE_WINDOW', on_close)
        refresh()
    _tk_call(_build)

# ══════════════════════════════════════════════
#  GUI 窗口 1：实时发送日志
# ══════════════════════════════════════════════
def open_log_window():
    if not _ensure_tk(): return
    def _build():
        with _WIN_LOCK:
            if _WIN.get('log') and _WIN['log'].winfo_exists():
                _WIN['log'].lift(); return
        win=tk.Toplevel(_tk_root)
        with _WIN_LOCK: _WIN['log']=win
        win.title('📡 实时发送日志  —  CAN Sender v3.3')
        win.geometry('1000x500'); win.configure(bg='#0d0d0d')

        top=tk.Frame(win,bg='#0d0d0d'); top.pack(fill='x',padx=6,pady=(4,2))
        tk.Label(top,text='显示模式:',bg='#0d0d0d',fg='#888',font=('Consolas',9)).pack(side='left')
        mode_var=tk.StringVar(value='1 - 全部帧')
        ttk.Combobox(top,textvariable=mode_var,width=18,state='readonly',
                     values=['1 - 全部帧','2 - 仅指定ID']).pack(side='left',padx=(2,10))
        tk.Label(top,text='过滤ID:',bg='#0d0d0d',fg='#888',font=('Consolas',9)).pack(side='left')
        fv=tk.StringVar()
        tk.Entry(top,textvariable=fv,width=20,bg='#1a1a1a',fg='#0f0',
                 insertbackground='white',font=('Consolas',9),relief='flat').pack(side='left',padx=(2,10))
        auto_v=tk.BooleanVar(value=True)
        tk.Checkbutton(top,text='自动滚动',variable=auto_v,bg='#0d0d0d',fg='#aaa',
                       selectcolor='#1a1a1a',activebackground='#0d0d0d',font=('Consolas',9)).pack(side='left')
        pause_v=tk.BooleanVar(value=False)
        tk.Checkbutton(top,text='暂停',variable=pause_v,bg='#0d0d0d',fg='#fa0',
                       selectcolor='#1a1a1a',activebackground='#0d0d0d',font=('Consolas',9)).pack(side='left',padx=4)
        cnt_v=tk.StringVar(value='0 帧')
        tk.Label(top,textvariable=cnt_v,bg='#0d0d0d',fg='#444',font=('Consolas',9)).pack(side='right',padx=6)

        fr=tk.Frame(win,bg='#0d0d0d'); fr.pack(fill='both',expand=True,padx=6,pady=(2,6))
        sb=tk.Scrollbar(fr); sb.pack(side='right',fill='y')
        txt=tk.Text(fr,bg='#0d0d0d',fg='#0f0',font=('Consolas',10),state='disabled',
                    wrap='none',yscrollcommand=sb.set,relief='flat',selectbackground='#003322')
        txt.pack(fill='both',expand=True); sb.config(command=txt.yview)
        txt.tag_config('ts',foreground='#444'); txt.tag_config('fix',foreground='#0cf')
        txt.tag_config('fuzz',foreground='#f8f'); txt.tag_config('d',foreground='#fc0')
        txt.tag_config('data',foreground='#0f0'); txt.tag_config('nm',foreground='#666')

        def do_clear():
            txt.configure(state='normal'); txt.delete('1.0',tk.END)
            txt.configure(state='disabled'); cnt_v.set('0 帧'); last_seq[0]=0; shown[0]=0
        tk.Button(top,text='清空',command=do_clear,bg='#2a0000',fg='#f44',
                  relief='flat',font=('Consolas',9),padx=6).pack(side='right')

        last_seq=[0]; shown=[0]

        def _poll():
            if not win.winfo_exists(): return
            if pause_v.get(): win.after(200,_poll); return
            use_filter='2' in mode_var.get()
            fids=None
            if use_filter:
                try: fids=set(parse_hex_id_list(fv.get()))
                except: pass
            with _log_queue_lock: snap=list(_log_queue)
            new=[e for e in snap if e[0]>last_seq[0]]
            if new:
                last_seq[0]=new[-1][0]
                txt.configure(state='normal')
                for seq,ts,aid,data in new:
                    if fids is not None and aid not in fids: continue
                    ts_s=time.strftime('%H:%M:%S',time.localtime(ts))+f'.{int((ts%1)*1000):03d}'
                    id_tag='fix' if aid in RESERVED_IDS else ('d' if (S['APP_MODE']=='D' and aid in (S['mode_d'].get('target_ids') or [])) else 'fuzz')
                    txt.insert(tk.END,ts_s+'  ','ts')
                    txt.insert(tk.END,f'0x{aid:03X}  ',id_tag)
                    txt.insert(tk.END,' '.join(f'{b:02X}' for b in data)+'  ','data')
                    txt.insert(tk.END,FRAME_META.get(aid,('?',))[0]+'\n','nm')
                    shown[0]+=1
                txt.configure(state='disabled')
                cnt_v.set(f'{shown[0]} 帧')
                if auto_v.get(): txt.see(tk.END)
                lines=int(txt.index('end-1c').split('.')[0])
                if lines>4000:
                    txt.configure(state='normal')
                    txt.delete('1.0',f'{lines-3000}.0')
                    txt.configure(state='disabled')
            win.after(80,_poll)

        def on_close():
            with _WIN_LOCK: _WIN['log']=None
            win.destroy()
        win.protocol('WM_DELETE_WINDOW',on_close)
        win.after(200,_poll)
    _tk_call(_build)

# ══════════════════════════════════════════════
#  GUI 窗口 2：在线帧编辑器
# ══════════════════════════════════════════════
def open_editor_window():
    if not _ensure_tk(): return
    def _build():
        with _WIN_LOCK:
            if _WIN.get('editor') and _WIN['editor'].winfo_exists():
                _WIN['editor'].lift(); return
        win=tk.Toplevel(_tk_root)
        with _WIN_LOCK: _WIN['editor']=win
        win.title('🔧 在线帧编辑器  —  CAN Sender v3.3')
        win.geometry('1060x520'); win.configure(bg='#0d0d0d')

        tk.Label(win,text='在线帧编辑器  —  修改后实时生效到发送线程',
                 bg='#0d0d0d',fg='#0cf',font=('Consolas',11,'bold')).pack(pady=(8,4))
        tk.Label(win,text='修改数据格子（16进制）；启用开关立即生效',
                 bg='#0d0d0d',fg='#666',font=('Consolas',9)).pack()

        container=tk.Frame(win,bg='#0d0d0d'); container.pack(fill='both',expand=True,padx=6,pady=6)
        canvas=tk.Canvas(container,bg='#0d0d0d',highlightthickness=0)
        vsb=tk.Scrollbar(container,orient='vertical',command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side='right',fill='y'); canvas.pack(side='left',fill='both',expand=True)
        inner=tk.Frame(canvas,bg='#0d0d0d'); canvas.create_window((0,0),window=inner,anchor='nw')
        inner.bind('<Configure>',lambda e: canvas.configure(scrollregion=canvas.bbox('all')))

        for c,(col,w) in enumerate(zip(['备注/名称','ID','长度','实时发送数据','启用','计算方法'],
                                        [20,7,4,52,5,30])):
            tk.Label(inner,text=col,bg='#111',fg='#0cf',font=('Consolas',9,'bold'),
                     width=w,relief='flat',bd=1).grid(row=0,column=c,padx=1,pady=1,sticky='ew')

        row_refs=[]
        for r,aid in enumerate(DISPLAY_ORDER,start=1):
            meta=FRAME_META.get(aid,('?','?'))
            with FRAME_BASE_LOCK: bd=bytearray(FRAME_BASE_DATA.get(aid,b'\x00'*8))
            with FRAME_ENABLED_LOCK: en=FRAME_ENABLED.get(aid,True)
            tk.Label(inner,text=meta[0],bg='#0d0d0d',fg='#0cf',font=('Consolas',9),
                     anchor='w',width=20).grid(row=r,column=0,padx=1,pady=1,sticky='ew')
            tk.Label(inner,text=f'0x{aid:03X}',bg='#0d0d0d',fg='#fa0',font=('Consolas',9),
                     width=7).grid(row=r,column=1,padx=1)
            tk.Label(inner,text=str(len(bd)),bg='#0d0d0d',fg='#888',font=('Consolas',9),
                     width=4).grid(row=r,column=2,padx=1)
            df=tk.Frame(inner,bg='#0d0d0d'); df.grid(row=r,column=3,padx=1,sticky='w')
            bvars=[]
            for i,b in enumerate(bd):
                bv=tk.StringVar(value=f'{b:02X}')
                tk.Entry(df,textvariable=bv,width=3,bg='#1a1a1a',fg='#0f0',
                         insertbackground='white',font=('Consolas',9),relief='flat',
                         justify='center').grid(row=0,column=i,padx=1)
                bvars.append(bv)
                def _chg(var=bv,idx=i,fid=aid):
                    try:
                        v=var.get().strip()
                        if v.upper().startswith('0X'): v=v[2:]
                        val=int(v,16); val=max(0,min(255,val))
                        with FRAME_BASE_LOCK:
                            if idx<len(FRAME_BASE_DATA[fid]):
                                FRAME_BASE_DATA[fid][idx]=val
                    except: pass
                bv.trace_add('write',lambda *a,f=_chg: f())
            live_v=tk.StringVar(value='—')
            tk.Label(inner,textvariable=live_v,bg='#0d0d0d',fg='#050',font=('Consolas',8),
                     anchor='w').grid(row=r,column=3,padx=(len(bd)*26+6,0),sticky='w')
            en_v=tk.BooleanVar(value=en)
            def _tog(var=en_v,fid=aid):
                with FRAME_ENABLED_LOCK: FRAME_ENABLED[fid]=var.get()
            tk.Checkbutton(inner,variable=en_v,command=_tog,bg='#0d0d0d',
                           selectcolor='#003',activebackground='#0d0d0d').grid(row=r,column=4)
            short_logic=meta[1][:32]+'…' if len(meta[1])>32 else meta[1]
            tk.Label(inner,text=short_logic,bg='#0d0d0d',fg='#555',font=('Consolas',8),
                     anchor='w',width=32).grid(row=r,column=5,padx=2,sticky='w')
            row_refs.append((aid,bvars,live_v,en_v))

        def _refresh():
            if not win.winfo_exists(): return
            with S['frame_lock']: snap=dict(S['frame_state'])
            for aid,_,lv,_ in row_refs:
                if aid in snap:
                    lv.set(' '.join(f'{b:02X}' for b in snap[aid]['data']))
            win.after(100,_refresh)

        bot=tk.Frame(win,bg='#0d0d0d'); bot.pack(fill='x',padx=6,pady=4)
        tk.Button(bot,text='全部启用',bg='#003300',fg='#0f0',relief='flat',font=('Consolas',9),
                  padx=8,command=lambda:[FRAME_ENABLED.update({a:True for a in RESERVED_IDS})]).pack(side='left',padx=4)
        tk.Button(bot,text='全部禁用',bg='#330000',fg='#f44',relief='flat',font=('Consolas',9),
                  padx=8,command=lambda:[FRAME_ENABLED.update({a:False for a in RESERVED_IDS})]).pack(side='left',padx=4)
        tk.Label(bot,text='⚠ 修改仅本次运行有效，关闭不保存',bg='#0d0d0d',fg='#555',
                 font=('Consolas',8)).pack(side='right',padx=8)

        def on_close():
            if messagebox.askyesno('关闭编辑器','修改仅在本次运行有效。确认关闭？',parent=win):
                with _WIN_LOCK: _WIN['editor']=None
                win.destroy()
        win.protocol('WM_DELETE_WINDOW',on_close)
        win.after(200,_refresh)
    _tk_call(_build)

# ══════════════════════════════════════════════
#  GUI 窗口 3：历史筛选
# ══════════════════════════════════════════════
def open_history_window():
    if not _ensure_tk(): return
    def _build():
        with _WIN_LOCK:
            if _WIN.get('history') and _WIN['history'].winfo_exists():
                _WIN['history'].lift(); return
        win=tk.Toplevel(_tk_root)
        with _WIN_LOCK: _WIN['history']=win
        win.title('🔍 历史帧筛选  —  CAN Sender v3.3')
        win.geometry('900x600'); win.configure(bg='#0d0d0d')

        sf=tk.LabelFrame(win,text='筛选条件',bg='#0d0d0d',fg='#0cf',
                         font=('Consolas',10),relief='flat'); sf.pack(fill='x',padx=8,pady=(6,2))
        r1=tk.Frame(sf,bg='#0d0d0d'); r1.pack(fill='x',padx=4,pady=2)
        tk.Label(r1,text='ID(逗号分隔，空=全部):',bg='#0d0d0d',fg='#aaa',font=('Consolas',9)).pack(side='left')
        id_v=tk.StringVar()
        tk.Entry(r1,textvariable=id_v,width=28,bg='#1a1a1a',fg='#fa0',
                 insertbackground='white',font=('Consolas',9),relief='flat').pack(side='left',padx=4)
        tk.Label(r1,text='数据包含(HEX):',bg='#0d0d0d',fg='#aaa',font=('Consolas',9)).pack(side='left',padx=(8,0))
        data_v=tk.StringVar()
        tk.Entry(r1,textvariable=data_v,width=22,bg='#1a1a1a',fg='#0f0',
                 insertbackground='white',font=('Consolas',9),relief='flat').pack(side='left',padx=4)
        r2=tk.Frame(sf,bg='#0d0d0d'); r2.pack(fill='x',padx=4,pady=2)
        tk.Label(r2,text='最近N秒(0=全部):',bg='#0d0d0d',fg='#aaa',font=('Consolas',9)).pack(side='left')
        time_v=tk.StringVar(value='0')
        tk.Entry(r2,textvariable=time_v,width=7,bg='#1a1a1a',fg='#aaa',
                 font=('Consolas',9),relief='flat').pack(side='left',padx=4)
        of=tk.BooleanVar(value=False); oz=tk.BooleanVar(value=False)
        tk.Checkbutton(r2,text='仅固定帧',variable=of,bg='#0d0d0d',fg='#0cf',
                       selectcolor='#1a1a1a',activebackground='#0d0d0d',font=('Consolas',9)).pack(side='left',padx=6)
        tk.Checkbutton(r2,text='仅Fuzz帧',variable=oz,bg='#0d0d0d',fg='#f8f',
                       selectcolor='#1a1a1a',activebackground='#0d0d0d',font=('Consolas',9)).pack(side='left',padx=4)
        res_v=tk.StringVar(value='')
        tk.Label(r2,textvariable=res_v,bg='#0d0d0d',fg='#888',font=('Consolas',9)).pack(side='right',padx=8)

        rf=tk.Frame(win,bg='#0d0d0d'); rf.pack(fill='both',expand=True,padx=8,pady=(2,8))
        sb=tk.Scrollbar(rf); sb.pack(side='right',fill='y')
        txt=tk.Text(rf,bg='#0d0d0d',fg='#0f0',font=('Consolas',10),state='disabled',
                    wrap='none',yscrollcommand=sb.set,relief='flat')
        txt.pack(fill='both',expand=True); sb.config(command=txt.yview)
        txt.tag_config('ts',foreground='#444'); txt.tag_config('fix',foreground='#0cf')
        txt.tag_config('fuzz',foreground='#f8f'); txt.tag_config('data',foreground='#0f0')
        txt.tag_config('nm',foreground='#555')

        def do_search():
            txt.configure(state='normal'); txt.delete('1.0',tk.END)
            with S['tx_lock']: history=list(S['tx_history'])
            fids=None
            try:
                raw=id_v.get().strip()
                if raw: fids=set(parse_hex_id_list(raw))
            except: pass
            data_filter=None
            try:
                dv=data_v.get().strip().replace(' ','')
                if dv: data_filter=bytes.fromhex(dv)
            except: pass
            now=time.time()
            try: tsec=float(time_v.get()); tsec=tsec if tsec>0 else None
            except: tsec=None
            count=0
            for rec in reversed(history):
                aid=rec['id']; ts=rec['ts']; data=rec['data']
                if fids and aid not in fids: continue
                if of.get() and aid not in RESERVED_IDS: continue
                if oz.get() and aid in RESERVED_IDS: continue
                if tsec and (now-ts)>tsec: continue
                if data_filter and data_filter not in data: continue
                ts_s=time.strftime('%H:%M:%S',time.localtime(ts))+f'.{int((ts%1)*1000):03d}'
                id_tag='fix' if aid in RESERVED_IDS else 'fuzz'
                txt.insert(tk.END,ts_s+'  ','ts')
                txt.insert(tk.END,f'0x{aid:03X}  ',id_tag)
                txt.insert(tk.END,' '.join(f'{b:02X}' for b in data)+'  ','data')
                txt.insert(tk.END,FRAME_META.get(aid,('?',))[0]+'\n','nm')
                count+=1
                if count>=2000: break
            txt.configure(state='disabled')
            res_v.set(f'共 {count} 条（最多2000，倒序）')

        tk.Button(r2,text='搜索',command=do_search,bg='#003344',fg='#0cf',
                  relief='flat',font=('Consolas',9,'bold'),padx=10).pack(side='left',padx=(16,0))
        tk.Button(r2,text='清空',bg='#1a1a1a',fg='#888',relief='flat',font=('Consolas',9),
                  command=lambda:[txt.configure(state='normal'),txt.delete('1.0',tk.END),
                                  txt.configure(state='disabled')]).pack(side='left',padx=4)

        def on_close():
            with _WIN_LOCK: _WIN['history']=None
            win.destroy()
        win.protocol('WM_DELETE_WINDOW',on_close)
    _tk_call(_build)

# ══════════════════════════════════════════════
#  GUI 窗口 4：PCAN占用查看
# ══════════════════════════════════════════════
def get_pcan_users():
    results=[]
    try:
        out=subprocess.check_output(
            ['powershell','-Command',
             'Get-Process | Where-Object {$_.Modules.FileName -like "*PCAN*"} | '
             'Select-Object Id,ProcessName,MainWindowTitle | Format-Table -AutoSize'],
            creationflags=0x08000000,timeout=6,stderr=subprocess.DEVNULL
        ).decode('gbk','ignore')
        for line in out.splitlines():
            line=line.strip()
            if line and not line.startswith('Id') and not line.startswith('-'): results.append(line)
    except: pass
    try:
        out2=subprocess.check_output(
            ['powershell','-Command',
             'Get-WmiObject Win32_Process | Where-Object {$_.Name -like "*pcan*"} | '
             'Select-Object ProcessId,Name | Format-Table -AutoSize'],
            creationflags=0x08000000,timeout=6,stderr=subprocess.DEVNULL
        ).decode('gbk','ignore')
        for line in out2.splitlines():
            line=line.strip()
            if line and not line.startswith('Process') and not line.startswith('-') and line not in results:
                results.append(line)
    except: pass
    return results if results else ['未检测到其他程序占用PCAN（或需要管理员权限）']

def open_pcan_info_window():
    if not _ensure_tk(): return
    def _build():
        with _WIN_LOCK:
            if _WIN.get('pcan') and _WIN['pcan'].winfo_exists():
                _WIN['pcan'].lift(); return
        win=tk.Toplevel(_tk_root)
        with _WIN_LOCK: _WIN['pcan']=win
        win.title('🔌 PCAN 占用查看  —  CAN Sender v3.3')
        win.geometry('760x420'); win.configure(bg='#0d0d0d')

        tk.Label(win,text='PCAN 设备占用情况',bg='#0d0d0d',fg='#0cf',
                 font=('Consolas',12,'bold')).pack(pady=(10,4))
        tk.Label(win,text='显示当前 Windows 中哪些程序正在使用 PCAN 相关驱动/DLL',
                 bg='#0d0d0d',fg='#555',font=('Consolas',9)).pack()

        fr=tk.Frame(win,bg='#0d0d0d'); fr.pack(fill='both',expand=True,padx=10,pady=8)
        sb=tk.Scrollbar(fr); sb.pack(side='right',fill='y')
        txt=tk.Text(fr,bg='#0d0d0d',fg='#0f0',font=('Consolas',10),
                    yscrollcommand=sb.set,relief='flat',state='disabled')
        txt.pack(fill='both',expand=True); sb.config(command=txt.yview)
        txt.tag_config('hd',foreground='#0cf',font=('Consolas',10,'bold'))
        txt.tag_config('ok',foreground='#0f0'); txt.tag_config('warn',foreground='#fa0')
        txt.tag_config('gray',foreground='#555')

        status_v=tk.StringVar(value='点击"刷新"扫描...')
        tk.Label(win,textvariable=status_v,bg='#0d0d0d',fg='#888',font=('Consolas',9)).pack()
        bot=tk.Frame(win,bg='#0d0d0d'); bot.pack(fill='x',padx=10,pady=(0,8))

        auto_refresh=[None]

        def do_refresh():
            status_v.set('扫描中...'); win.update_idletasks()
            lines=get_pcan_users()
            txt.configure(state='normal'); txt.delete('1.0',tk.END)
            txt.insert(tk.END,f'扫描时间: {time.strftime("%Y-%m-%d %H:%M:%S")}\n','hd')
            txt.insert(tk.END,'─'*80+'\n','gray')
            for line in lines:
                tag='warn' if any(kw in line.lower() for kw in ['pcan','explorer','python','peak']) else 'ok'
                txt.insert(tk.END,line+'\n',tag)
            txt.insert(tk.END,'─'*80+'\n','gray')
            txt.insert(tk.END,'提示: PCAN Explorer、Python(本程序)均会出现\n','gray')
            txt.configure(state='disabled')
            status_v.set(f'扫描完成，共 {len(lines)} 条')

        def toggle_auto():
            if auto_refresh[0]:
                win.after_cancel(auto_refresh[0]); auto_refresh[0]=None
                auto_btn.configure(text='自动刷新(5s)',bg='#1a1a1a')
            else:
                def _loop():
                    do_refresh()
                    if win.winfo_exists(): auto_refresh[0]=win.after(5000,_loop)
                auto_btn.configure(text='停止自动刷新',bg='#003322')
                _loop()

        tk.Button(bot,text='刷新',command=do_refresh,bg='#003344',fg='#0cf',
                  relief='flat',font=('Consolas',10,'bold'),padx=12).pack(side='left')
        auto_btn=tk.Button(bot,text='自动刷新(5s)',command=toggle_auto,bg='#1a1a1a',fg='#888',
                           relief='flat',font=('Consolas',9),padx=8)
        auto_btn.pack(side='left',padx=6)

        def on_close():
            if auto_refresh[0]: win.after_cancel(auto_refresh[0])
            with _WIN_LOCK: _WIN['pcan']=None
            win.destroy()
        win.protocol('WM_DELETE_WINDOW',on_close)
        win.after(300,do_refresh)
    _tk_call(_build)

# ══════════════════════════════════════════════
#  模式C GUI
# ══════════════════════════════════════════════
def _mode_c_profile_dialog(parent, title, initial=None):
    initial = dict(initial or {})
    result = {'value': None}
    win = tk.Toplevel(parent)
    win.title(title)
    win.geometry('760x620')
    win.configure(bg='#111')
    win.resizable(False, False)
    frm = tk.Frame(win, bg='#111', padx=12, pady=12)
    frm.pack(fill='both', expand=True)

    vars_ = {
        'id': tk.StringVar(value=f"{int(initial.get('id', 0x555)):03X}" if initial.get('id') is not None else '555'),
        'name': tk.StringVar(value=initial.get('name', '')),
        'length': tk.StringVar(value=str(initial.get('length', 8))),
        'cycle_ms': tk.StringVar(value=str(initial.get('cycle_ms', 100))),
        'mode': tk.StringVar(value=initial.get('mode', 'manual')),
        'data': tk.StringVar(value=fmt_bytes_plain(initial.get('data', bytearray([0]*int(initial.get('length', 8)))))) if initial.get('data') is not None else tk.StringVar(value=''),
        'counter_pos': tk.StringVar(value=str(initial.get('counter_pos', -1))),
        'counter_mode': tk.StringVar(value=initial.get('counter_mode', 'full')),
        'counter_start': tk.StringVar(value=str(initial.get('counter_start', 0))),
        'counter_min': tk.StringVar(value=str(initial.get('counter_min', 0))),
        'counter_max': tk.StringVar(value=str(initial.get('counter_max', 255))),
        'counter_wrap': tk.StringVar(value=str(initial.get('counter_wrap', 0))),
        'crc_pos': tk.StringVar(value=str(initial.get('crc_pos', -1))),
        'crc_init': tk.StringVar(value=str(initial.get('crc_init', 255))),
        'crc_xor': tk.StringVar(value=str(initial.get('crc_xor', 0))),
        'speed_value': tk.StringVar(value=str(initial.get('speed_value', 0.0))),
        'speed_jitter': tk.StringVar(value=str(initial.get('speed_jitter', 0.0))),
        'speed_lo_pos': tk.StringVar(value=str(initial.get('speed_lo_pos', 1))),
        'speed_hi_pos': tk.StringVar(value=str(initial.get('speed_hi_pos', 2))),
        'rpm_value': tk.StringVar(value=str(initial.get('rpm_value', 0))),
        'rpm_jitter': tk.StringVar(value=str(initial.get('rpm_jitter', 50))),
        'rpm_pack_pos': tk.StringVar(value=str(initial.get('rpm_pack_pos', 1))),
        'rpm_hi_pos': tk.StringVar(value=str(initial.get('rpm_hi_pos', 2))),
        'rpm_status_pos': tk.StringVar(value=str(initial.get('rpm_status_pos', -1))),
        'rpm_status_value': tk.StringVar(value=str(initial.get('rpm_status_value', 240))),
    }

    row = 0
    def add_entry(label, key, width=16):
        nonlocal row
        tk.Label(frm, text=label, bg='#111', fg='#ddd', font=('Consolas', 10)).grid(row=row, column=0, sticky='w', pady=3)
        ent = tk.Entry(frm, textvariable=vars_[key], bg='#1a1a1a', fg='#0f0', insertbackground='white', width=width, font=('Consolas', 10), relief='flat')
        ent.grid(row=row, column=1, sticky='w', pady=3, padx=(8, 18))
        row += 1
        return ent

    def add_combo(label, key, values):
        nonlocal row
        tk.Label(frm, text=label, bg='#111', fg='#ddd', font=('Consolas', 10)).grid(row=row, column=0, sticky='w', pady=3)
        cb = ttk.Combobox(frm, textvariable=vars_[key], values=values, state='readonly', width=18)
        cb.grid(row=row, column=1, sticky='w', pady=3, padx=(8, 18))
        row += 1
        return cb

    tk.Label(frm, text='模式C 固定/自定义ID配置', bg='#111', fg='#0cf', font=('Consolas', 12, 'bold')).grid(row=row, column=0, columnspan=4, sticky='w', pady=(0, 10)); row += 1
    add_entry('ID (hex)', 'id')
    add_entry('名称', 'name', 28)
    add_entry('长度', 'length')
    add_entry('周期ms', 'cycle_ms')
    add_combo('类型', 'mode', ['random', 'fixed', 'manual', 'speed', 'rpm'])
    add_entry('基础数据(hex)', 'data', 40)
    add_entry('Counter位置', 'counter_pos')
    add_combo('Counter写法', 'counter_mode', ['full', 'low4', 'high4'])
    add_entry('Counter起始', 'counter_start')
    add_entry('Counter最小', 'counter_min')
    add_entry('Counter最大', 'counter_max')
    add_entry('Counter回卷', 'counter_wrap')
    add_entry('CRC位置', 'crc_pos')
    add_entry('CRC init', 'crc_init')
    add_entry('CRC xor', 'crc_xor')

    sec_speed = tk.LabelFrame(frm, text='车速算法', bg='#111', fg='#0cf', font=('Consolas', 10, 'bold'), padx=8, pady=8)
    sec_speed.grid(row=1, column=2, rowspan=6, sticky='nw', padx=(24, 0), pady=(0, 8))
    for i, (lab, key) in enumerate([('车速km/h', 'speed_value'), ('抖动km/h', 'speed_jitter'), ('低字节位置', 'speed_lo_pos'), ('高字节位置', 'speed_hi_pos')]):
        tk.Label(sec_speed, text=lab, bg='#111', fg='#ddd', font=('Consolas', 10)).grid(row=i, column=0, sticky='w', pady=2)
        tk.Entry(sec_speed, textvariable=vars_[key], bg='#1a1a1a', fg='#0f0', insertbackground='white', width=14, font=('Consolas', 10), relief='flat').grid(row=i, column=1, sticky='w', padx=(8, 0), pady=2)

    sec_rpm = tk.LabelFrame(frm, text='转速算法', bg='#111', fg='#0cf', font=('Consolas', 10, 'bold'), padx=8, pady=8)
    sec_rpm.grid(row=7, column=2, rowspan=8, sticky='nw', padx=(24, 0), pady=(0, 8))
    rpm_fields = [('基础rpm', 'rpm_value'), ('抖动rpm', 'rpm_jitter'), ('打包位置', 'rpm_pack_pos'), ('高字节位置', 'rpm_hi_pos'), ('状态位置', 'rpm_status_pos'), ('状态值', 'rpm_status_value')]
    for i, (lab, key) in enumerate(rpm_fields):
        tk.Label(sec_rpm, text=lab, bg='#111', fg='#ddd', font=('Consolas', 10)).grid(row=i, column=0, sticky='w', pady=2)
        tk.Entry(sec_rpm, textvariable=vars_[key], bg='#1a1a1a', fg='#0f0', insertbackground='white', width=14, font=('Consolas', 10), relief='flat').grid(row=i, column=1, sticky='w', padx=(8, 0), pady=2)

    tips = tk.StringVar(value='说明: speed=速度*64，rpm=rpm*1.557；rpm默认建议 counter 放在同一字节低4位。')
    tk.Label(frm, textvariable=tips, bg='#111', fg='#999', font=('Consolas', 9), wraplength=720, justify='left').grid(row=20, column=0, columnspan=4, sticky='w', pady=(10, 8))

    def refresh_visibility(*_):
        mode = vars_['mode'].get()
        if mode == 'speed':
            sec_speed.grid(); sec_rpm.grid_remove()
        elif mode == 'rpm':
            sec_rpm.grid(); sec_speed.grid_remove()
            vars_['counter_mode'].set('low4') if vars_['counter_pos'].get() != '-1' else None
        else:
            sec_speed.grid_remove(); sec_rpm.grid_remove()
    vars_['mode'].trace_add('write', refresh_visibility)
    refresh_visibility()

    def save():
        try:
            length = int(vars_['length'].get())
            if not (1 <= length <= 8):
                raise ValueError('长度必须 1~8')
            prof = {
                'id': parse_hex_id(vars_['id'].get()),
                'name': vars_['name'].get().strip() or f"自定义 0x{parse_hex_id(vars_['id'].get()):03X}",
                'length': length,
                'cycle_ms': max(10, int(vars_['cycle_ms'].get())),
                'mode': vars_['mode'].get(),
                'data': parse_hex_bytes(vars_['data'].get(), expected_len=length),
                'counter_pos': int(vars_['counter_pos'].get()),
                'counter_mode': vars_['counter_mode'].get(),
                'counter_start': int(vars_['counter_start'].get()),
                'counter_min': int(vars_['counter_min'].get()),
                'counter_max': int(vars_['counter_max'].get()),
                'counter_wrap': int(vars_['counter_wrap'].get()),
                'crc_pos': int(vars_['crc_pos'].get()),
                'crc_init': int(vars_['crc_init'].get()),
                'crc_xor': int(vars_['crc_xor'].get()),
                'enabled': bool(initial.get('enabled', True)),
                'active': True,
            }
            if prof['mode'] == 'speed':
                prof.update({
                    'speed_value': float(vars_['speed_value'].get()),
                    'speed_jitter': float(vars_['speed_jitter'].get()),
                    'speed_lo_pos': int(vars_['speed_lo_pos'].get()),
                    'speed_hi_pos': int(vars_['speed_hi_pos'].get()),
                })
            elif prof['mode'] == 'rpm':
                prof.update({
                    'rpm_value': int(vars_['rpm_value'].get()),
                    'rpm_jitter': int(vars_['rpm_jitter'].get()),
                    'rpm_pack_pos': int(vars_['rpm_pack_pos'].get()),
                    'rpm_hi_pos': int(vars_['rpm_hi_pos'].get()),
                    'rpm_status_pos': int(vars_['rpm_status_pos'].get()),
                    'rpm_status_value': int(vars_['rpm_status_value'].get()),
                })
            result['value'] = prof
            win.destroy()
        except Exception as ex:
            messagebox.showerror('输入错误', str(ex), parent=win)

    btns = tk.Frame(frm, bg='#111'); btns.grid(row=21, column=0, columnspan=4, sticky='we', pady=(8, 0))
    tk.Button(btns, text='保存', command=save, bg='#173117', fg='#0f0', relief='flat', font=('Consolas', 10)).pack(side='left', padx=(0, 8))
    tk.Button(btns, text='取消', command=win.destroy, bg='#2a1a1a', fg='#f66', relief='flat', font=('Consolas', 10)).pack(side='left')

    win.transient(parent)
    win.grab_set()
    parent.wait_window(win)
    return result['value']



def open_mode_c_gui(bus, threads):
    if not _ensure_tk():
        return
    with S['gui_lock']:
        if S['gui_open']:
            return
        S['gui_open'] = True

    def _build():
        with _WIN_LOCK:
            if _WIN.get('mode_c') and _WIN['mode_c'].winfo_exists():
                _WIN['mode_c'].lift(); return
        win = tk.Toplevel(_tk_root)
        with _WIN_LOCK:
            _WIN['mode_c'] = win
        win.title('模式C - ID管理')
        win.geometry('980x620')
        win.configure(bg='#0d0d0d')
        win.resizable(False, False)

        sv = tk.StringVar(value='↑↓选择  +新增ID  -删除ID  Space启用/禁用  Enter编辑  S=Solo。弹窗期间发送线程不会暂停，现有帧会继续同时发送。')
        filter_var = tk.StringVar(value='')

        F = tk.Frame(win, bg='#0d0d0d', padx=12, pady=12)
        F.pack(fill='both', expand=True)
        tk.Label(F, text='模式C：排除无关ID / 保留关键ID / 固定帧与Fuzz帧都持续发送', bg='#0d0d0d', fg='#0cf', font=('Consolas', 12, 'bold')).pack(anchor='w', pady=(0, 4))
        speed_rpm_var = tk.StringVar(value='')
        tk.Label(F, textvariable=speed_rpm_var, bg='#0d0d0d', fg='#9ad', font=('Consolas', 10)).pack(anchor='w', pady=(0, 8))

        top = tk.Frame(F, bg='#0d0d0d'); top.pack(fill='x', pady=(0, 6))
        tk.Label(top, text='筛选:', bg='#0d0d0d', fg='#aaa', font=('Consolas', 10)).pack(side='left')
        ent_filter = tk.Entry(top, textvariable=filter_var, bg='#1a1a1a', fg='#0f0', insertbackground='white', width=24, font=('Consolas', 10), relief='flat')
        ent_filter.pack(side='left', padx=(6, 10))
        tk.Label(top, text='键盘: ↑↓ 选择   + 新增   - 删除   Space 开关   Enter 编辑', bg='#0d0d0d', fg='#888', font=('Consolas', 9)).pack(side='left')

        cols = ('kind', 'state', 'id', 'name', 'mode', 'len', 'cycle', 'last')
        tree = ttk.Treeview(F, columns=cols, show='headings', height=20)
        for col, txt, w in [
            ('kind', '类别', 70), ('state', '状态', 70), ('id', 'ID', 80), ('name', '名称', 170),
            ('mode', '模式', 120), ('len', '长度', 60), ('cycle', '周期ms', 70), ('last', '最近数据', 300)
        ]:
            tree.heading(col, text=txt)
            tree.column(col, width=w, anchor='center' if col not in ('name', 'last') else 'w')
        tree.pack(fill='both', expand=True, pady=(0, 8))

        detail = tk.Text(F, bg='#111', fg='#ddd', height=8, font=('Consolas', 10), relief='flat')
        detail.pack(fill='x', pady=(0, 8))

        btn = tk.Frame(F, bg='#0d0d0d'); btn.pack(fill='x')
        tk.Button(btn, text='设置车速', command=lambda: set_speed_value(), bg='#173044', fg='#8df', relief='flat', font=('Consolas', 10)).pack(side='left', padx=(0, 6))
        tk.Button(btn, text='设置转速', command=lambda: set_rpm_value(), bg='#20304a', fg='#9df', relief='flat', font=('Consolas', 10)).pack(side='left', padx=(0, 6))
        tk.Button(btn, text='全部启用', command=lambda: set_all(True), bg='#173117', fg='#0f0', relief='flat', font=('Consolas', 10)).pack(side='left', padx=(0, 6))
        tk.Button(btn, text='全部禁用', command=lambda: set_all(False), bg='#311717', fg='#f66', relief='flat', font=('Consolas', 10)).pack(side='left', padx=(0, 6))
        tk.Button(btn, text='关闭', command=lambda: on_close(), bg='#1a1a1a', fg='#ccc', relief='flat', font=('Consolas', 10)).pack(side='right')

        def current_selection():
            sel = tree.selection()
            if not sel:
                return None
            iid = sel[0]
            kind, slot = iid.split(':', 1)
            return kind, int(slot)

        def ensure_selection():
            kids = tree.get_children()
            if not kids:
                return None
            sel = tree.selection()
            if sel and sel[0] in kids:
                return sel[0]
            tree.selection_set(kids[0])
            tree.focus(kids[0])
            tree.see(kids[0])
            return kids[0]

        def move_selection(delta):
            kids = list(tree.get_children())
            if not kids:
                return 'break'
            cur = tree.selection()
            if not cur or cur[0] not in kids:
                idx = 0
            else:
                idx = kids.index(cur[0]) + delta
                if idx < 0: idx = 0
                if idx >= len(kids): idx = len(kids) - 1
            iid = kids[idx]
            tree.selection_set(iid)
            tree.focus(iid)
            tree.see(iid)
            on_tree_select()
            return 'break'

        def fill_detail(entry):
            detail.configure(state='normal')
            detail.delete('1.0', tk.END)
            if not entry:
                detail.insert(tk.END, '未选择项目。\n\n+ 新增ID，- 删除当前ID。\n弹窗只影响输入，不会暂停发送线程。')
            else:
                detail.insert(tk.END, f"类别: {entry['kind']}\n")
                detail.insert(tk.END, f"ID: 0x{entry['id']:03X}\n")
                detail.insert(tk.END, f"名称: {entry['name']}\n")
                detail.insert(tk.END, f"模式: {entry['mode']}\n")
                detail.insert(tk.END, f"长度: {entry['length']}  周期: {entry['cycle_ms']}ms\n")
                detail.insert(tk.END, f"状态: {'启用' if entry['enabled'] else '禁用'}\n")
                detail.insert(tk.END, f"逻辑: {entry['logic']}\n")
                with S['frame_lock']:
                    st = S['frame_state'].get(entry['id'])
                if st:
                    detail.insert(tk.END, f"最近数据: {fmt_bytes_plain(st['data'])}\n")
                    detail.insert(tk.END, f"发送次数: {st['count']}\n")
                detail.insert(tk.END, '\n操作: Space=启停  Enter=编辑  S=Solo  -=删除')
            detail.configure(state='disabled')

        def refresh(*_):
            keep = tree.selection()[0] if tree.selection() else None
            keyword = filter_var.get().strip().lower()
            tree.delete(*tree.get_children())
            entries = mode_c_collect_entries()
            for e in entries:
                text_mix = f"{e['kind']} 0x{e['id']:03X} {e['name']} {e['mode']} {e['logic']}".lower()
                if keyword and keyword not in text_mix:
                    continue
                with S['frame_lock']:
                    st = S['frame_state'].get(e['id'])
                last = fmt_bytes_plain(st['data']) if st else ''
                iid = f"{e['kind']}:{e['slot']}"
                tree.insert('', 'end', iid=iid, values=(
                    'Fuzz' if e['kind']=='fuzz' else '固定',
                    'ON' if e['enabled'] else 'OFF',
                    f"0x{e['id']:03X}",
                    e['name'],
                    e['mode'],
                    e['length'],
                    e['cycle_ms'],
                    last,
                ))
            kids = tree.get_children()
            if kids:
                if keep in kids:
                    tree.selection_set(keep); tree.focus(keep); tree.see(keep)
                else:
                    ensure_selection()
                sel = current_selection()
                fill_detail(mode_c_get_selected_entry(sel[1], sel[0]) if sel else None)
            else:
                fill_detail(None)
            speed_rpm_var.set(
                f"内置1A1车速: {float(S.get('speed_value', 0.0)):.1f} km/h ±{float(S.get('speed_jitter', 0.0)):.1f}    "
                f"内置0F3转速: {int(S.get('rpm_value', 0))} rpm ±{int(S.get('rpm_jitter', 50))}"
            )
            if win.winfo_exists() and S['running'].is_set():
                win.after(400, refresh)
            elif win.winfo_exists() and not S['running'].is_set():
                on_close()

        def add_fuzz():
            raw = simpledialog.askstring('新增ID', '输入要新增的 Fuzz ID。\n可一次输入多个，逗号分隔，例如：21D,1F3\n\n注意：弹窗期间其他固定帧和已启用帧仍会继续发送。', parent=win)
            if raw is None:
                return 'break'
            msgs = []
            try:
                for aid in parse_hex_id_list(raw):
                    ok, msg = mode_c_add_target(bus, threads, aid, parent=win)
                    msgs.append(('✓ ' if ok else '· ') + msg)
            except Exception as ex:
                messagebox.showerror('输入错误', str(ex), parent=win)
                return 'break'
            mode_c_refresh_meta()
            sv.set(' | '.join(msgs) if msgs else '无改动')
            refresh()
            return 'break'

        def add_fixed():
            prof = _mode_c_profile_dialog(win, '新增固定自定义ID')
            if not prof:
                return 'break'
            ok, msg = mode_c_add_fixed_profile(bus, threads, prof)
            sv.set(('✓ ' if ok else '· ') + msg)
            if not ok:
                messagebox.showerror('添加失败', msg, parent=win)
            mode_c_refresh_meta(); refresh()
            return 'break'

        def add_id_popup(event=None):
            choice = simpledialog.askstring('新增ID', '输入新增类型：\nF = Fuzz ID\nX = 固定自定义ID\n\n直接输入 F 或 X', parent=win)
            if choice is None:
                return 'break'
            c = choice.strip().upper()
            if c == 'F':
                return add_fuzz()
            if c == 'X':
                return add_fixed()
            messagebox.showinfo('提示', '请输入 F 或 X。', parent=win)
            return 'break'

        def set_speed_value():
            enabled = messagebox.askyesno('车速设置', '是否设置内置 0x1A1 车速值？\n\n是 = 输入车速\n否 = 恢复为 0\n\n注意：操作期间其余帧仍继续发送。', parent=win) if messagebox else True
            S['speed_config_enabled'] = enabled
            if enabled:
                val = simpledialog.askfloat('车速', '输入车速 km/h（0~300）', initialvalue=float(S.get('speed_value', 0.0)), minvalue=0.0, maxvalue=300.0, parent=win) if simpledialog else None
                if val is None:
                    return
                jit = simpledialog.askfloat('车速抖动', '输入车速抖动 km/h（0~20）', initialvalue=float(S.get('speed_jitter', 0.0)), minvalue=0.0, maxvalue=20.0, parent=win) if simpledialog else 0.0
                S['speed_value'] = float(val)
                S['speed_jitter'] = float(jit or 0.0)
            else:
                S['speed_value'] = 0.0
                S['speed_jitter'] = 0.0
            sv.set(f"✓ 内置0x1A1车速 = {S['speed_value']:.1f} km/h ±{S['speed_jitter']:.1f}")
            refresh()

        def set_rpm_value():
            enabled = messagebox.askyesno('转速设置', '是否设置内置 0x0F3 转速值？\n\n是 = 输入转速\n否 = 恢复为 0\n\n注意：操作期间其余帧仍继续发送。', parent=win) if messagebox else True
            S['rpm_config_enabled'] = enabled
            if enabled:
                val = simpledialog.askinteger('转速', '输入基础转速 rpm（0~7500）', initialvalue=int(S.get('rpm_value', 0)), minvalue=0, maxvalue=7500, parent=win) if simpledialog else None
                if val is None:
                    return
                jit = simpledialog.askinteger('转速浮动', '输入上下浮动 rpm（0~500）', initialvalue=int(S.get('rpm_jitter', 50)), minvalue=0, maxvalue=500, parent=win) if simpledialog else 50
                S['rpm_value'] = int(val)
                S['rpm_jitter'] = int(jit or 0)
            else:
                S['rpm_value'] = 0
                S['rpm_jitter'] = 50
            sv.set(f"✓ 内置0x0F3转速 = {S['rpm_value']} rpm ±{S['rpm_jitter']}")
            refresh()

        def edit_selected(event=None):
            sel = current_selection()
            if not sel:
                return 'break'
            kind, slot = sel
            entry = mode_c_get_selected_entry(slot, kind)
            if not entry:
                return 'break'
            if kind == 'fuzz':
                raw = simpledialog.askstring('编辑Fuzz ID', f'把 0x{entry["id"]:03X} 改成:', initialvalue=f"{entry['id']:03X}", parent=win)
                if raw is None:
                    return 'break'
                try:
                    ok, msg = mode_c_replace_fuzz_id(slot, parse_hex_id(raw), parent=win)
                except Exception as ex:
                    messagebox.showerror('输入错误', str(ex), parent=win); return 'break'
                sv.set(('✓ ' if ok else '· ') + msg)
                if not ok:
                    messagebox.showerror('修改失败', msg, parent=win)
            else:
                prof = _mode_c_profile_dialog(win, '编辑固定自定义ID', entry.get('profile', {}))
                if not prof:
                    return 'break'
                ok, msg = mode_c_update_fixed_profile(slot, prof)
                sv.set(('✓ ' if ok else '· ') + msg)
                if not ok:
                    messagebox.showerror('修改失败', msg, parent=win)
            mode_c_refresh_meta(); refresh()
            return 'break'

        def toggle_selected(event=None):
            sel = current_selection()
            if not sel:
                return 'break'
            kind, slot = sel
            entry = mode_c_get_selected_entry(slot, kind)
            if not entry:
                return 'break'
            enable = not entry['enabled']
            if kind == 'fuzz':
                ok, msg = mode_c_set_fuzz_enabled(slot, enable)
            else:
                ok, msg = mode_c_set_fixed_enabled(slot, enable)
            sv.set(('✓ ' if ok else '· ') + msg)
            refresh()
            return 'break'

        def delete_selected(event=None):
            sel = current_selection()
            if not sel:
                return 'break'
            kind, slot = sel
            entry = mode_c_get_selected_entry(slot, kind)
            if not entry:
                return 'break'
            if not messagebox.askyesno('确认删除', f"删除 0x{entry['id']:03X} / {entry['name']} ?\n\n注意：删除当前项时，其他帧仍会继续同时发送。", parent=win):
                return 'break'
            if kind == 'fuzz':
                ok, msg = mode_c_remove_target(slot_idx=slot)
            else:
                ok, msg = mode_c_remove_fixed_profile(slot)
            sv.set(('✓ ' if ok else '· ') + msg)
            refresh()
            return 'break'

        def solo_selected(event=None):
            sel = current_selection()
            if not sel:
                return 'break'
            kind, slot = sel
            ok, msg = mode_c_solo(kind, slot)
            sv.set(('✓ ' if ok else '· ') + msg)
            refresh()
            return 'break'

        def set_all(enabled):
            ok, msg = mode_c_set_all_enabled(enabled)
            sv.set(('✓ ' if ok else '· ') + msg)
            refresh()

        def on_tree_select(event=None):
            sel = current_selection()
            fill_detail(mode_c_get_selected_entry(sel[1], sel[0]) if sel else None)

        def on_close():
            with S['gui_lock']:
                S['gui_open'] = False
                S['gui_thread'] = None
            with _WIN_LOCK:
                _WIN['mode_c'] = None
            try:
                win.destroy()
            except Exception:
                pass

        filter_var.trace_add('write', refresh)
        tree.bind('<<TreeviewSelect>>', on_tree_select)
        tree.bind('<space>', toggle_selected)
        tree.bind('<Delete>', delete_selected)
        tree.bind('<Return>', edit_selected)
        tree.bind('s', solo_selected)
        tree.bind('S', solo_selected)
        tree.bind('<Up>', lambda e: move_selection(-1))
        tree.bind('<Down>', lambda e: move_selection(1))
        tree.bind('+', add_id_popup)
        tree.bind('=', add_id_popup)
        tree.bind('<KP_Add>', add_id_popup)
        tree.bind('-', delete_selected)
        tree.bind('<KP_Subtract>', delete_selected)
        win.bind('<Up>', lambda e: move_selection(-1))
        win.bind('<Down>', lambda e: move_selection(1))
        win.bind('+', add_id_popup)
        win.bind('=', add_id_popup)
        win.bind('<KP_Add>', add_id_popup)
        win.bind('-', delete_selected)
        win.bind('<KP_Subtract>', delete_selected)
        win.bind('<space>', toggle_selected)
        win.bind('<Return>', edit_selected)
        win.bind('s', solo_selected)
        win.bind('S', solo_selected)
        win.protocol('WM_DELETE_WINDOW', on_close)
        ensure_selection()
        tree.focus_set()
        refresh()

    _tk_call(_build)

def build_fixed_sender_specs(builders):
    return [
        (0x03C, builders[0x03C], 0.075, "03C"),
        (0x0F3, builders[0x0F3], 0.075, "0F3"),
        (0x1A1, builders[0x1A1], 0.020, "1A1"),
        (0x0AB, builders[0x0AB], 0.040, "0AB"),
        (0x0DF, builders[0x0DF], 0.100, "0DF"),
        (0x2A7, builders[0x2A7], 0.075, "2A7"),
        (0x2EC, builders[0x2EC], 0.075, "2EC"),
        (0x294, builders[0x294], 0.075, "294"),
        (0x2C4, builders[0x2C4], 0.075, "2C4"),
        (0x349, builders[0x349], 0.075, "349"),
        (0x30B, builders[0x30B], 0.075, "30B"),
        (0x369, builders[0x369], 0.075, "369"),
        (0x36E, builders[0x36E], 0.075, "36E"),
        (0x36F, builders[0x36F], 0.075, "36F"),
        (0x3A0, builders[0x3A0], 0.075, "3A0"),
        (0x3D8, builders[0x3D8], 0.075, "3D8"),
        (0x3FD, builders[0x3FD], 0.075, "3FD"),
        (0x510, builders[0x510], 0.075, "510"),
    ]


def stop_menu_keepalive():
    global MENU_KEEPALIVE
    if MENU_KEEPALIVE is not None:
        MENU_KEEPALIVE.stop()


def start_menu_keepalive(bus):
    global MENU_KEEPALIVE
    if not USER_SETTINGS.get('menu_keepalive_enabled', True):
        return
    apply_runtime_defaults_to_session()
    apply_startup_terminal_from_settings('menu_keepalive_terminal_state')
    builders = _make_fixed_builders()
    if MENU_KEEPALIVE is None:
        MENU_KEEPALIVE = MenuKeepaliveService(bus)
    MENU_KEEPALIVE.start(build_fixed_sender_specs(builders))


# ──────────────────────────────────────────────
#  开机画面
# ──────────────────────────────────────────────
def show_splash():
    os.system("cls")
    print(f"{CYAN}╔══════════════════════════════════════════════════════════════════════╗{RESET}")
    print(f"{CYAN}║{RESET}  {BOLD}{WHITE} ██████╗ █████╗ ███╗  ██╗    ███████╗███╗  ██╗██████╗  {RESET}        {CYAN}║{RESET}")
    print(f"{CYAN}║{RESET}  {BOLD}{CYAN}██╔════╝██╔══██╗████╗ ██║    ██╔════╝████╗ ██║██╔══██╗ {RESET}        {CYAN}║{RESET}")
    print(f"{CYAN}║{RESET}  {BOLD}{CYAN}██║     ███████║██╔██╗██║    ███████╗██╔██╗██║██║  ██║ {RESET}        {CYAN}║{RESET}")
    print(f"{CYAN}║{RESET}  {BOLD}{BLUE}██║     ██╔══██║██║╚████║    ╚════██║██║╚████║██║  ██║ {RESET}        {CYAN}║{RESET}")
    print(f"{CYAN}║{RESET}  {BOLD}{BLUE}╚██████╗██║  ██║██║ ╚███║    ███████║██║ ╚███║██████╔╝ {RESET}        {CYAN}║{RESET}")
    print(f"{CYAN}║{RESET}  {BOLD}{BLUE} ╚═════╝╚═╝  ╚═╝╚═╝  ╚══╝    ╚══════╝╚═╝  ╚══╝╚═════╝  {RESET}       {CYAN}║{RESET}")
    print(f"{CYAN}║{RESET}       {YELLOW}CAN Bus Fuzzing & Analysis Tool  v3.1{RESET}                      {CYAN}║{RESET}")
    print(f"{CYAN}║{RESET}       {GRAY}GitHub: JackieZ123430  |  BMW F/G Series  |  SAE-J1850{RESET}      {CYAN}║{RESET}")
    print(f"{CYAN}╠══════════════════════════════════════════════════════════════════════╣{RESET}")
    print(f"{CYAN}║{RESET}  {BOLD}{YELLOW}全局按键{RESET}                                                          {CYAN}║{RESET}")
    print(f"{CYAN}║{RESET}   {GREEN}M{RESET} 打标记   {GREEN}S{RESET} 保存回菜单   {GREEN}Q{RESET} 保存退出   {RED}ALT+Q{RESET} 不保存退出       {CYAN}║{RESET}")
    print(f"{CYAN}║{RESET}   {GREEN}W{RESET} 重开日志窗口  {GREEN}O{RESET}=ACC  {GREEN}I{RESET}=IGN  {GREEN}K{RESET}=切换  {GREEN}]{RESET}=3A0开关   {CYAN}║{RESET}")
    print(f"{CYAN}║{RESET}   {GREEN}U{RESET} 打开新增会话控制中心（不替代原菜单/窗口，只是更顺手）                {CYAN}║{RESET}")
    print(f"{CYAN}║{RESET}                                                                      {CYAN}║{RESET}")
    print(f"{CYAN}║{RESET}  {BOLD}{YELLOW}4个GUI窗口（进入模式后自动弹出）{RESET}                                  {CYAN}║{RESET}")
    print(f"{CYAN}║{RESET}   {CYAN}📡 实时发送日志{RESET}  终端滚动，下拉筛选全部/指定ID，支持过滤        {CYAN}║{RESET}")
    print(f"{CYAN}║{RESET}   {CYAN}🔧 在线帧编辑器{RESET}  修改固定帧数据/启用开关，实时生效到发送        {CYAN}║{RESET}")
    print(f"{CYAN}║{RESET}   {CYAN}🧭 会话控制中心{RESET}  新增集中控制窗口，保留原功能并补齐更符合人习惯的面板  {CYAN}║{RESET}")
    print(f"{CYAN}║{RESET}   {CYAN}🔍 历史帧筛选  {RESET}  按ID/数据/时间筛选历史发送记录               {CYAN}║{RESET}")
    print(f"{CYAN}║{RESET}   {CYAN}🔌 PCAN占用查看{RESET}  {RED}开机画面后立即弹出{RESET}，查看哪些程序在用PCAN      {CYAN}║{RESET}")
    print(f"{CYAN}║{RESET}                                                                      {CYAN}║{RESET}")
    print(f"{CYAN}║{RESET}  {BOLD}{YELLOW}模式C: {GREEN}P{RESET}暂停帧 {GREEN}[{RESET}冻结随机 {GREEN}9{RESET}冻结模式 {GREEN}A{RESET}ID管理GUI  {BOLD}{YELLOW}模式B: {GREEN}N{RESET}换ID   {CYAN}║{RESET}")
    print(f"{CYAN}║{RESET}  {BOLD}{YELLOW}模式D: {GREEN}↑↓{RESET}切换ID {GREEN}T{RESET}自动/手动 {GREEN}+/-{RESET}速度 {GREEN}P{RESET}暂停 {GREEN}1~4{RESET}策略 {GREEN}Y/N/L/B/[{RESET}操作     {CYAN}║{RESET}")
    print(f"{CYAN}╚══════════════════════════════════════════════════════════════════════╝{RESET}")
    print()

    # 弹出PCAN占用窗口（开机后立即）
    if USER_SETTINGS.get('auto_open_pcan_window', True):
        open_pcan_info_window()

    input(f"  {GRAY}按回车键进入主菜单...{RESET}")


def _show_modern_start_panel():
    remembered = USER_SETTINGS.get('remember_last_choice', True)
    fmt = int(USER_SETTINGS.get('last_fuzz_format', 1))
    mode = str(USER_SETTINGS.get('last_app_mode', 'A')).upper()
    print(f"{BOLD}{BLUE}{'─'*70}{RESET}")
    print(f"{BOLD}{WHITE}  现代化快速入口（新增，不替代原来的格式/模式选择）{RESET}")
    print(f"{BOLD}{BLUE}{'─'*70}{RESET}")
    print(f"  {CYAN}•{RESET} 快速命令: {YELLOW}FAST{RESET}=使用上次配置  {YELLOW}SET{RESET}=设置中心  {YELLOW}HELP{RESET}=显示说明")
    print(f"  {CYAN}•{RESET} 当前记忆: 格式={fmt}  模式={mode}  记忆开关={'开' if remembered else '关'}")
    print(f"{GRAY}  说明：原始菜单、原始文本、原始模式和原始按键全部保留；这里只是新增一个更顺手的入口。{RESET}\n")


def _print_start_help():
    print(f"{BOLD}{CYAN}帮助：{RESET}")
    print("  1) 你仍然可以像以前一样输入 1/2/3/4 和 A/B/C/D。")
    print("  2) FAST 会直接套用上次成功启动会话时记录的格式和模式。")
    print("  3) SET 会进入设置中心，原有所有选项继续可用。")
    print("  4) 本次升级不删除任何功能，只新增便捷入口与记忆能力。\n")


def _launch_terminal_startup_selector():
    """纯终端启动向导：无GUI依赖，稳定可回退。"""
    print(f"\n{BOLD}{CYAN}┌─ 终端启动向导（TUI） ─────────────────────────────────────┐{RESET}")
    print(f"{CYAN}│{RESET} 保留全部原功能：这里只是把分散输入改成一次性分组输入。          {CYAN}│{RESET}")
    print(f"{CYAN}└──────────────────────────────────────────────────────────┘{RESET}")
    while True:
        mode = (input("模式 [A/B/C/D] (回车=A, Q=退出向导): ").strip().upper() or 'A')
        if mode == 'Q':
            return {'action': 'cancel'}
        if mode in ('A', 'B', 'C', 'D'):
            break
        print(f"{RED}请输入 A/B/C/D 或 Q{RESET}")

    fmt = ask_int("发送格式 [1~4]", default=int(USER_SETTINGS.get('last_fuzz_format', 1)), min_v=1, max_v=4)
    keep_3a0 = ask_yes_no("启用内置3A0固定帧", default=bool(FRAME_ENABLED.get(0x3A0, True)))
    speed_enabled = ask_yes_no("启用车速配置", default=bool(S.get('speed_config_enabled', False)))
    speed_value, speed_jitter = 0.0, 0.0
    if speed_enabled:
        speed_value = ask_float("车速 km/h", default=float(S.get('speed_value', 0.0)), min_v=0.0, max_v=300.0)
        speed_jitter = ask_float("车速抖动 ±km/h", default=float(S.get('speed_jitter', 0.0)), min_v=0.0, max_v=20.0)
    rpm_enabled = ask_yes_no("启用转速配置", default=bool(S.get('rpm_config_enabled', False)))
    rpm_value, rpm_jitter = 0, 50
    if rpm_enabled:
        rpm_value = ask_int("转速 rpm", default=int(S.get('rpm_value', 0)), min_v=0, max_v=9000)
        rpm_jitter = ask_int("转速抖动 ±rpm", default=int(S.get('rpm_jitter', 50)), min_v=0, max_v=1000)

    payload = {
        'fmt': fmt,
        'mode': mode,
        'enable_3a0': keep_3a0,
        'speed_enabled': speed_enabled,
        'speed_value': speed_value,
        'speed_jitter': speed_jitter,
        'rpm_enabled': rpm_enabled,
        'rpm_value': rpm_value,
        'rpm_jitter': rpm_jitter,
        'mode_b_count': int(USER_SETTINGS.get('last_mode_b_count', 3)),
        'mode_b_fixed_ids_text': '',
        'mode_c_ids_text': '',
        'mode_d_ids_text': '',
        'mode_d_length': int(USER_SETTINGS.get('last_mode_d_length', 8)),
    }

    if mode == 'B':
        payload['mode_b_count'] = ask_int("模式B随机ID数量", default=int(USER_SETTINGS.get('last_mode_b_count', 3)), min_v=1, max_v=2048)
        if ask_yes_no("模式B增加固定随机帧ID", default=False):
            payload['mode_b_fixed_ids_text'] = input("固定随机帧ID（如 21D,22E）: ").strip()
    elif mode == 'C':
        default_c = str(USER_SETTINGS.get('last_mode_c_ids', '')).strip()
        while True:
            v = input(f"模式C目标Fuzz ID（如 21D,1F3，回车={default_c or '无'}）: ").strip()
            if not v and default_c:
                v = default_c
            try:
                if not parse_hex_id_list(v):
                    raise ValueError("至少输入一个ID")
                payload['mode_c_ids_text'] = v
                break
            except Exception as ex:
                print(f"{RED}输入有误: {ex}{RESET}")
    elif mode == 'D':
        default_d = str(USER_SETTINGS.get('last_mode_d_ids', '')).strip()
        while True:
            v = input(f"模式D穷举ID（如 21D,22E，回车={default_d or '无'}）: ").strip()
            if not v and default_d:
                v = default_d
            try:
                if not parse_hex_id_list(v):
                    raise ValueError("至少输入一个ID")
                payload['mode_d_ids_text'] = v
                break
            except Exception as ex:
                print(f"{RED}输入有误: {ex}{RESET}")
        payload['mode_d_length'] = ask_int("模式D帧长度", default=int(USER_SETTINGS.get('last_mode_d_length', 8)), min_v=1, max_v=8)

    return {'action': 'apply', 'payload': payload}


def _launch_modern_startup_selector():
    if not HAS_TK or not USER_SETTINGS.get('modern_startup_gui_enabled', True):
        return None
    if not _ensure_tk():
        return None

    done = threading.Event()
    result = {'action': 'cancel'}

    def _build():
        top = tk.Toplevel(_tk_root)
        top.title('PCAN Sender 现代启动中心')
        top.geometry('860x670')
        top.configure(bg='#111')
        top.resizable(True, True)

        style = ttk.Style(top)
        try:
            style.theme_use('clam')
        except Exception:
            pass

        fmt_var = tk.StringVar(value=str(int(USER_SETTINGS.get('last_fuzz_format', 1))))
        mode_var = tk.StringVar(value=str(USER_SETTINGS.get('last_app_mode', 'A')).upper())
        keep_3a0_var = tk.BooleanVar(value=bool(FRAME_ENABLED.get(0x3A0, True)))
        speed_enable_var = tk.BooleanVar(value=bool(S.get('speed_config_enabled', False)))
        speed_val_var = tk.StringVar(value=f"{float(S.get('speed_value', 0.0)):.1f}")
        speed_jitter_var = tk.StringVar(value=f"{float(S.get('speed_jitter', 0.0)):.1f}")
        rpm_enable_var = tk.BooleanVar(value=bool(S.get('rpm_config_enabled', False)))
        rpm_val_var = tk.StringVar(value=str(int(S.get('rpm_value', 0))))
        rpm_jitter_var = tk.StringVar(value=str(int(S.get('rpm_jitter', 50))))
        b_count_var = tk.StringVar(value=str(int(USER_SETTINGS.get('last_mode_b_count', 3))))
        b_fixed_ids_var = tk.StringVar(value='')
        c_ids_var = tk.StringVar(value=str(USER_SETTINGS.get('last_mode_c_ids', '')).strip())
        d_ids_var = tk.StringVar(value=str(USER_SETTINGS.get('last_mode_d_ids', '')).strip())
        d_len_var = tk.StringVar(value=str(int(USER_SETTINGS.get('last_mode_d_length', 8))))

        root = tk.Frame(top, bg='#111')
        root.pack(fill='both', expand=True, padx=14, pady=12)

        tk.Label(root, text='现代化启动中心（保留原有全部功能）', bg='#111', fg='#8ec5ff',
                 font=('Microsoft YaHei UI', 15, 'bold')).pack(anchor='w')
        tk.Label(root, text='这里是新增图形入口；原开机页、原菜单、原模式输入与原按键均可继续使用。', bg='#111',
                 fg='#aaa', font=('Microsoft YaHei UI', 10)).pack(anchor='w', pady=(4, 10))

        core = tk.LabelFrame(root, text='基础启动参数', bg='#161616', fg='#8ee7ff',
                             font=('Microsoft YaHei UI', 10, 'bold'), bd=1, relief='solid')
        core.pack(fill='x', padx=2, pady=(0, 8))
        row1 = tk.Frame(core, bg='#161616'); row1.pack(fill='x', padx=10, pady=10)
        tk.Label(row1, text='发送格式', bg='#161616', fg='#ddd').pack(side='left')
        ttk.Combobox(row1, textvariable=fmt_var, values=['1', '2', '3', '4'], state='readonly', width=8).pack(side='left', padx=(8, 20))
        tk.Label(row1, text='工作模式', bg='#161616', fg='#ddd').pack(side='left')
        ttk.Combobox(row1, textvariable=mode_var, values=['A', 'B', 'C', 'D'], state='readonly', width=8).pack(side='left', padx=(8, 18))
        tk.Checkbutton(row1, text='启用3A0固定帧', variable=keep_3a0_var, bg='#161616', fg='#ddd',
                       activebackground='#161616', selectcolor='#222').pack(side='left')

        perf = tk.LabelFrame(root, text='车速 / 转速（与原逻辑一致）', bg='#161616', fg='#8ee7ff',
                             font=('Microsoft YaHei UI', 10, 'bold'), bd=1, relief='solid')
        perf.pack(fill='x', padx=2, pady=(0, 8))
        speed_row = tk.Frame(perf, bg='#161616'); speed_row.pack(fill='x', padx=10, pady=(8, 4))
        tk.Checkbutton(speed_row, text='启用车速', variable=speed_enable_var, bg='#161616', fg='#ddd',
                       activebackground='#161616', selectcolor='#222').pack(side='left')
        tk.Label(speed_row, text='值(km/h)', bg='#161616', fg='#aaa').pack(side='left', padx=(10, 4))
        tk.Entry(speed_row, textvariable=speed_val_var, width=8).pack(side='left')
        tk.Label(speed_row, text='抖动±', bg='#161616', fg='#aaa').pack(side='left', padx=(10, 4))
        tk.Entry(speed_row, textvariable=speed_jitter_var, width=8).pack(side='left')

        rpm_row = tk.Frame(perf, bg='#161616'); rpm_row.pack(fill='x', padx=10, pady=(4, 10))
        tk.Checkbutton(rpm_row, text='启用转速', variable=rpm_enable_var, bg='#161616', fg='#ddd',
                       activebackground='#161616', selectcolor='#222').pack(side='left')
        tk.Label(rpm_row, text='值(rpm)', bg='#161616', fg='#aaa').pack(side='left', padx=(10, 4))
        tk.Entry(rpm_row, textvariable=rpm_val_var, width=8).pack(side='left')
        tk.Label(rpm_row, text='浮动±', bg='#161616', fg='#aaa').pack(side='left', padx=(10, 4))
        tk.Entry(rpm_row, textvariable=rpm_jitter_var, width=8).pack(side='left')

        mode_box = tk.LabelFrame(root, text='模式专属参数', bg='#161616', fg='#8ee7ff',
                                 font=('Microsoft YaHei UI', 10, 'bold'), bd=1, relief='solid')
        mode_box.pack(fill='both', expand=True, padx=2, pady=(0, 8))

        hint = tk.Label(mode_box, text='提示：会根据上方模式自动显示输入区域。', bg='#161616', fg='#888')
        hint.pack(anchor='w', padx=10, pady=(8, 6))

        mode_panel = tk.Frame(mode_box, bg='#161616')
        mode_panel.pack(fill='both', expand=True, padx=10, pady=(0, 10))

        msg_var = tk.StringVar(value='')
        tk.Label(root, textvariable=msg_var, bg='#111', fg='#ffb36b').pack(anchor='w')

        def _clear_mode_panel():
            for w in mode_panel.winfo_children():
                w.destroy()

        def _draw_mode_panel(*_):
            _clear_mode_panel()
            m = mode_var.get().upper()
            if m == 'A':
                tk.Label(mode_panel, text='模式A不需要额外参数。', bg='#161616', fg='#bbb').pack(anchor='w')
            elif m == 'B':
                tk.Label(mode_panel, text='随机ID数量（>=1）', bg='#161616', fg='#bbb').grid(row=0, column=0, sticky='w')
                tk.Entry(mode_panel, textvariable=b_count_var, width=10).grid(row=0, column=1, sticky='w', padx=(8, 0))
                tk.Label(mode_panel, text='额外固定随机帧ID（可空，如 21D,22E）', bg='#161616', fg='#bbb').grid(row=1, column=0, sticky='w', pady=(8, 0))
                tk.Entry(mode_panel, textvariable=b_fixed_ids_var, width=36).grid(row=1, column=1, sticky='w', padx=(8, 0), pady=(8, 0))
            elif m == 'C':
                tk.Label(mode_panel, text='目标Fuzz ID（必填，如 21D,1F3）', bg='#161616', fg='#bbb').grid(row=0, column=0, sticky='w')
                tk.Entry(mode_panel, textvariable=c_ids_var, width=40).grid(row=0, column=1, sticky='w', padx=(8, 0))
            elif m == 'D':
                tk.Label(mode_panel, text='穷举目标ID（必填，如 21D,22E）', bg='#161616', fg='#bbb').grid(row=0, column=0, sticky='w')
                tk.Entry(mode_panel, textvariable=d_ids_var, width=40).grid(row=0, column=1, sticky='w', padx=(8, 0))
                tk.Label(mode_panel, text='帧长度（1~8）', bg='#161616', fg='#bbb').grid(row=1, column=0, sticky='w', pady=(8, 0))
                tk.Entry(mode_panel, textvariable=d_len_var, width=10).grid(row=1, column=1, sticky='w', padx=(8, 0), pady=(8, 0))

        mode_var.trace_add('write', _draw_mode_panel)
        _draw_mode_panel()

        btns = tk.Frame(root, bg='#111')
        btns.pack(fill='x', pady=(2, 0))

        def _close(action='cancel'):
            result['action'] = action
            try:
                top.grab_release()
            except Exception:
                pass
            try:
                top.destroy()
            except Exception:
                pass
            done.set()

        def _apply():
            try:
                fmt = int(fmt_var.get())
                if fmt not in (1, 2, 3, 4):
                    raise ValueError('发送格式仅支持 1~4')
                mode = mode_var.get().strip().upper()
                if mode not in ('A', 'B', 'C', 'D'):
                    raise ValueError('模式仅支持 A/B/C/D')
                speed_v = float(speed_val_var.get().strip() or '0')
                speed_j = float(speed_jitter_var.get().strip() or '0')
                rpm_v = int(rpm_val_var.get().strip() or '0')
                rpm_j = int(rpm_jitter_var.get().strip() or '50')
                if not (0 <= speed_v <= 300 and 0 <= speed_j <= 20):
                    raise ValueError('车速范围: 0~300，抖动: 0~20')
                if not (0 <= rpm_v <= 9000 and 0 <= rpm_j <= 1000):
                    raise ValueError('转速范围: 0~9000，浮动: 0~1000')

                payload = {
                    'fmt': fmt,
                    'mode': mode,
                    'enable_3a0': bool(keep_3a0_var.get()),
                    'speed_enabled': bool(speed_enable_var.get()),
                    'speed_value': speed_v,
                    'speed_jitter': speed_j,
                    'rpm_enabled': bool(rpm_enable_var.get()),
                    'rpm_value': rpm_v,
                    'rpm_jitter': rpm_j,
                    'mode_b_count': int(b_count_var.get().strip() or '3'),
                    'mode_b_fixed_ids_text': b_fixed_ids_var.get().strip(),
                    'mode_c_ids_text': c_ids_var.get().strip(),
                    'mode_d_ids_text': d_ids_var.get().strip(),
                    'mode_d_length': int(d_len_var.get().strip() or '8'),
                }
                if payload['mode_b_count'] < 1:
                    raise ValueError('模式B随机ID数量必须 >= 1')
                if not (1 <= payload['mode_d_length'] <= 8):
                    raise ValueError('模式D帧长度必须在 1~8')
                if mode == 'C' and not payload['mode_c_ids_text']:
                    raise ValueError('模式C必须填写目标Fuzz ID')
                if mode == 'D' and not payload['mode_d_ids_text']:
                    raise ValueError('模式D必须填写穷举目标ID')

                result['action'] = 'apply'
                result['payload'] = payload
                _close('apply')
            except Exception as ex:
                msg_var.set(f'参数有误：{ex}')

        tk.Button(btns, text='应用并进入会话', command=_apply, width=18, bg='#198754', fg='white',
                  activebackground='#157347', relief='flat').pack(side='left')
        tk.Button(btns, text='打开设置中心', command=lambda: _close('settings'), width=14, bg='#6f42c1', fg='white',
                  activebackground='#5c35a5', relief='flat').pack(side='left', padx=8)
        tk.Button(btns, text='回到原CLI菜单', command=lambda: _close('cancel'), width=14, bg='#495057', fg='white',
                  activebackground='#3d4348', relief='flat').pack(side='left')

        top.bind('<Escape>', lambda _e: _close('cancel'))
        top.protocol('WM_DELETE_WINDOW', lambda: _close('cancel'))
        top.grab_set()
        top.focus_set()

    _tk_call(_build)
    done.wait()
    return result


def _remember_last_selection():
    if not USER_SETTINGS.get('remember_last_choice', True):
        return
    USER_SETTINGS['last_fuzz_format'] = int(S['FUZZ_FORMAT'])
    USER_SETTINGS['last_app_mode'] = str(S['APP_MODE']).upper()
    if S['APP_MODE'] == 'B':
        USER_SETTINGS['last_mode_b_count'] = int(max(1, len(S['FUZZ_TARGET_IDS'])))
    elif S['APP_MODE'] == 'C':
        USER_SETTINGS['last_mode_c_ids'] = ','.join(f'{x:03X}' for x in S['FUZZ_TARGET_IDS'])
    elif S['APP_MODE'] == 'D':
        D = S.get('mode_d', {})
        USER_SETTINGS['last_mode_d_ids'] = ','.join(f'{x:03X}' for x in D.get('target_ids', []))
        USER_SETTINGS['last_mode_d_length'] = int(D.get('length', 8))
    try:
        from persistent_settings import save_settings
        save_settings(SETTINGS_FILE, USER_SETTINGS)
    except Exception:
        pass

# ──────────────────────────────────────────────
#  菜单
# ──────────────────────────────────────────────
def select_mode():
    global S, USER_SETTINGS
    S = make_fresh_session()
    USER_SETTINGS = load_settings(SETTINGS_FILE)
    apply_runtime_defaults_to_session()
    apply_startup_terminal_from_settings('menu_keepalive_terminal_state')
    os.system("cls")
    print(f"{BOLD}{CYAN}{'═'*70}{RESET}")
    print(f"{BOLD}{WHITE}  PCAN Sender v3.3  ·  GitHub: JackieZ123430{RESET}")
    print(f"{BOLD}{CYAN}{'═'*70}{RESET}\n")
    gui_boot = None
    if USER_SETTINGS.get('modern_menu_enabled', True):
        _show_modern_start_panel()
        while True:
            boot_mode = (input("启动入口 (TUI=终端向导, GUI=图形向导, CLI=经典流程, SET=设置中心, 回车=TUI): ").strip().upper() or 'TUI')
            if boot_mode == 'SET':
                USER_SETTINGS = edit_settings_interactive(USER_SETTINGS, SETTINGS_FILE)
                apply_runtime_defaults_to_session()
                apply_startup_terminal_from_settings('menu_keepalive_terminal_state')
                continue
            if boot_mode == 'GUI':
                gui_boot = _launch_modern_startup_selector()
                if gui_boot and gui_boot.get('action') == 'settings':
                    USER_SETTINGS = edit_settings_interactive(USER_SETTINGS, SETTINGS_FILE)
                    apply_runtime_defaults_to_session()
                    apply_startup_terminal_from_settings('menu_keepalive_terminal_state')
                    continue
                break
            if boot_mode == 'TUI':
                gui_boot = _launch_terminal_startup_selector()
                break
            if boot_mode == 'CLI':
                gui_boot = None
                break
            print(f"{RED}请输入 TUI / GUI / CLI / SET{RESET}")
    if USER_SETTINGS.get('modern_menu_enabled', True):
        print(f"{GRAY}提示：如果你更习惯老流程，直接继续在命令行输入 1/2/3/4 + A/B/C/D 即可。{RESET}")
    print(f"{BOLD}【发送格式】{RESET}")
    print(f"  {YELLOW}1{RESET}  byte[0]=Counter  byte[1~7]=随机  {GRAY}(默认){RESET}")
    print(f"  {YELLOW}2{RESET}  byte[0]=CRC  byte[1]=Counter  byte[2~7]=随机")
    print(f"  {YELLOW}3{RESET}  全部纯随机")
    print(f"  {YELLOW}4{RESET}  byte[0]=CRC  byte[1]=Counter  byte[7]=Counter  byte[2~6]=随机\n")
    prefilled = None
    if gui_boot and gui_boot.get('action') == 'apply':
        prefilled = gui_boot.get('payload', {})
        S['FUZZ_FORMAT'] = int(prefilled.get('fmt', 1))
        S['APP_MODE'] = str(prefilled.get('mode', 'A')).upper()
        with FRAME_ENABLED_LOCK:
            FRAME_ENABLED[0x3A0] = bool(prefilled.get('enable_3a0', True))
        S['speed_config_enabled'] = bool(prefilled.get('speed_enabled', False))
        S['speed_value'] = float(prefilled.get('speed_value', 0.0))
        S['speed_jitter'] = float(prefilled.get('speed_jitter', 0.0))
        S['rpm_config_enabled'] = bool(prefilled.get('rpm_enabled', False))
        S['rpm_value'] = int(prefilled.get('rpm_value', 0))
        S['rpm_jitter'] = int(prefilled.get('rpm_jitter', 50))
        print(f"{GREEN}已从现代GUI加载: 格式{S['FUZZ_FORMAT']} / 模式{S['APP_MODE']}{RESET}")
    else:
        while True:
            v = input("发送格式 (1/2/3/4，SET=设置中心，HELP=帮助，FAST=上次，回车=1): ").strip().upper() or '1'
            if v == 'SET':
                USER_SETTINGS = edit_settings_interactive(USER_SETTINGS, SETTINGS_FILE)
                apply_runtime_defaults_to_session()
                apply_startup_terminal_from_settings('menu_keepalive_terminal_state')
                continue
            if v == 'HELP':
                _print_start_help()
                continue
            if v == 'FAST':
                S['FUZZ_FORMAT'] = int(USER_SETTINGS.get('last_fuzz_format', 1))
                S['APP_MODE'] = str(USER_SETTINGS.get('last_app_mode', 'A')).upper()
                print(f"{GREEN}已套用上次配置: 格式{S['FUZZ_FORMAT']} / 模式{S['APP_MODE']}{RESET}")
                break
            if v in ('1', '2', '3', '4'):
                S['FUZZ_FORMAT'] = int(v)
                break
            print(f"{RED}请输入 1、2、3、4、SET、HELP 或 FAST{RESET}")

    print(f"\n{BOLD}【工作模式】{RESET}")
    print(f"  {YELLOW}A{RESET}  全局随机ID")
    print(f"  {YELLOW}B{RESET}  随机N个ID定向  {GRAY}[N]换ID{RESET}")
    print(f"  {YELLOW}C{RESET}  手动指定ID定向 + 固定/自定义ID  {GRAY}[P]暂停 [[]冻结 [9]冻结模式 [A]管理中心GUI{RESET}")
    print(f"  {YELLOW}D{RESET}  逐Byte穷举  {GRAY}支持标准/分组/联扫/加速手动，自动/手动递增{RESET}\n")
    enabled_3a0 = configure_3a0_switch_interactive()
    print(f"{GREEN}当前3A0固定帧: {'开启' if enabled_3a0 else '关闭'}{RESET}")
    print(f"{GREEN}Terminal默认状态: {detect_terminal_state(FRAME_BASE_DATA)}  | 菜单保活: {'开启' if USER_SETTINGS.get('menu_keepalive_enabled', True) else '关闭'}{RESET}")

    if prefilled is None and not (v == 'FAST' and S.get('APP_MODE') in ('A', 'B', 'C', 'D')):
        while True:
            c = input("模式 (A/B/C/D): " ).strip().upper()
            if c in ('A', 'B', 'C', 'D'):
                S['APP_MODE'] = c
                break

    if S['APP_MODE'] == 'B':
        if prefilled is not None:
            count = int(prefilled.get('mode_b_count', USER_SETTINGS.get('last_mode_b_count', 3)))
            count = max(1, count)
        else:
            while True:
                try:
                    remembered_count = int(USER_SETTINGS.get('last_mode_b_count', 3))
                    n = input(f"\n随机ID数量 (默认{remembered_count}): ").strip()
                    count = int(n) if n else remembered_count
                    if count >= 1:
                        break
                except ValueError:
                    pass
                print(f"{RED}请输入 >=1 的整数{RESET}")
        while len(S['FUZZ_TARGET_IDS']) < count:
            aid = random.randint(0x001, 0x7FF)
            if aid not in RESERVED_IDS and aid not in S['FUZZ_TARGET_IDS']:
                S['FUZZ_TARGET_IDS'].append(aid)
        print(f"\n{GREEN}IDs: {', '.join(f'0x{i:03X}' for i in S['FUZZ_TARGET_IDS'])}{RESET}")

        if prefilled is None:
            configure_speed_value_interactive()
            configure_rpm_value_interactive()
            configure_mode_b_fixed_random_ids_interactive()
        else:
            raw_fixed = str(prefilled.get('mode_b_fixed_ids_text', '')).strip()
            if raw_fixed:
                try:
                    fixed_ids = parse_hex_id_list(raw_fixed)
                    bad = [aid for aid in fixed_ids if aid in RESERVED_IDS or aid in S['FUZZ_TARGET_IDS']]
                    S['mode_b_fixed_random_ids'] = [aid for aid in fixed_ids if aid not in bad]
                except Exception:
                    S['mode_b_fixed_random_ids'] = []
        print(f"{GREEN}当前车速设置: {'启用' if S['speed_config_enabled'] else '关闭'} / {S['speed_value']:.1f} km/h / 抖动±{S['speed_jitter']:.1f}{RESET}")
        print(f"{GREEN}当前转速设置: {'启用' if S['rpm_config_enabled'] else '关闭'} / {S['rpm_value']} rpm / 浮动±{S['rpm_jitter']}{RESET}")
        print(f"{GREEN}新增会话控制中心: {'自动打开' if USER_SETTINGS.get('auto_open_control_window', True) else '手动(U键)'}{RESET}")
        if S['mode_b_fixed_random_ids']:
            print(f"{GREEN}模式B固定随机帧: {', '.join(f'0x{i:03X}' for i in S['mode_b_fixed_random_ids'])}{RESET}")
        time.sleep(1.2)

    elif S['APP_MODE'] == 'C':
        while True:
            if prefilled is not None:
                val = str(prefilled.get('mode_c_ids_text', '')).strip()
            else:
                remembered_c = USER_SETTINGS.get('last_mode_c_ids', '')
                hint = f"；回车=上次({remembered_c})" if remembered_c else ""
                val = input(f"\n目标Fuzz ID（逗号分隔，如 21D,1F3{hint}）: ").strip()
                if not val and remembered_c:
                    val = remembered_c
            try:
                aids = parse_hex_id_list(val)
                if not aids:
                    print(f"{RED}请至少输入一个ID{RESET}")
                    if prefilled is not None:
                        prefilled = None
                    continue
                ok, reserved = confirm_reserved_ids_for_mode_c_cli(aids)
                if not ok:
                    print(f"{YELLOW}已取消。本次没有接管这些内置ID，请重新输入。{RESET}")
                    if prefilled is not None:
                        prefilled = None
                    continue
                S['FUZZ_TARGET_IDS'] = aids
                break
            except ValueError as e:
                print(f"{RED}{e}{RESET}")
                if prefilled is not None:
                    prefilled = None
        print(f"\n{GREEN}Fuzz目标: {_fmt_id_list(S['FUZZ_TARGET_IDS'])}{RESET}")

        if prefilled is None:
            configure_speed_value_interactive()
            configure_rpm_value_interactive()
        print(f"{GREEN}当前车速设置: {'启用' if S['speed_config_enabled'] else '关闭'} / {S['speed_value']:.1f} km/h / 抖动±{S['speed_jitter']:.1f}{RESET}")
        print(f"{GREEN}当前转速设置: {'启用' if S['rpm_config_enabled'] else '关闭'} / {S['rpm_value']} rpm / 浮动±{S['rpm_jitter']}{RESET}")

        if prefilled is None and ask_yes_no('要不要同时增加固定的自定义ID', default=True):
            while True:
                try:
                    prof = mode_c_build_terminal_profile()
                except Exception as ex:
                    print(f"{RED}配置失败: {ex}{RESET}")
                    if not ask_yes_no('重试这个固定ID配置', default=True):
                        break
                    continue
                tmp_used = mode_c_all_used_ids(include_reserved=False)
                if prof['id'] in tmp_used:
                    print(f"{RED}0x{prof['id']:03X} 已存在，请换一个{RESET}")
                else:
                    with S['mode_c_fixed_lock']:
                        prof['slot'] = len(S['mode_c_fixed_profiles'])
                        S['mode_c_fixed_profiles'].append(prof)
                    print(f"{GREEN}已加入固定ID 0x{prof['id']:03X} / {mode_c_profile_mode_name(prof['mode'])}{RESET}")
                if not ask_yes_no('继续增加固定自定义ID', default=False):
                    break
        mode_c_refresh_meta()
        time.sleep(1.2)

    elif S['APP_MODE'] == 'D':
        while True:
            if prefilled is not None:
                val = str(prefilled.get('mode_d_ids_text', '')).strip().upper()
            else:
                remembered_d = USER_SETTINGS.get('last_mode_d_ids', '')
                hint = f"，回车=上次({remembered_d})" if remembered_d else ""
                val = input(f"\n穷举目标ID（可多个，逗号分隔，如 21D,22E,2EC{hint}）: " ).strip().upper()
                if not val and remembered_d:
                    val = remembered_d
            try:
                ids = parse_hex_id_list(val)
                if not ids:
                    raise ValueError
                break
            except Exception:
                print(f"{RED}请输入有效16进制ID列表 (000~7FF){RESET}")
                if prefilled is not None:
                    prefilled = None
        if prefilled is not None:
            fl = int(prefilled.get('mode_d_length', USER_SETTINGS.get('last_mode_d_length', 8)))
            fl = max(1, min(8, fl))
        else:
            while True:
                try:
                    remembered_len = int(USER_SETTINGS.get('last_mode_d_length', 8))
                    n = input(f"帧长度 (默认{remembered_len}): " ).strip()
                    fl = int(n) if n else remembered_len
                    if 1 <= fl <= 8:
                        break
                except ValueError:
                    pass
                print(f"{RED}1~8{RESET}")
        D = S['mode_d']
        D['target_ids'] = ids; D['current_target_idx'] = 0; D['length'] = fl
        D['status'] = f"准备穷举 {', '.join(f'0x{x:03X}' for x in ids)}，当前=0x{ids[0]:03X}，帧长={fl}，从byte[1]开始"
        print(f"\n{GREEN}目标: {', '.join(f'0x{x:03X}' for x in ids)}  帧长: {fl}{RESET}")
        print(f"{YELLOW}进入后: ↑/↓切换目标ID  T=切换自动/手动  1/2/3/4=切换扫描策略  += 加速  -= 减速{RESET}")
        time.sleep(1.5)

    _remember_last_selection()

# ──────────────────────────────────────────────
#  ALT+Q 检测（Windows专用）
# ──────────────────────────────────────────────
def _check_alt_q():
    """检测ALT键是否按下（通过GetAsyncKeyState）"""
    try:
        VK_MENU=0x12   # ALT键
        state=ctypes.windll.user32.GetAsyncKeyState(VK_MENU)
        return bool(state & 0x8000)
    except:
        return False

# ──────────────────────────────────────────────
#  单次会话
# ──────────────────────────────────────────────
def run_session(bus):
    stop_menu_keepalive()
    apply_runtime_defaults_to_session()
    apply_startup_terminal_from_settings('session_start_terminal_state')
    S['running'].set()
    builders = _make_fixed_builders()
    threads = [CyclicSender(bus, arb_id, builder, cycle_s, label) for arb_id, builder, cycle_s, label in build_fixed_sender_specs(builders)]
    mode = S['APP_MODE']
    if mode == 'A':
        threads.append(CyclicSender(bus,-1,builders['rnd_a'],RANDOM_CYCLE_S,"RND"))
    elif mode == 'B':
        for i in range(len(S['FUZZ_TARGET_IDS'])):
            def _mk(idx):
                ctr = make_counter(0,255,wrap=0)
                def bld():
                    with S['fuzz_lock']:
                        tid = S['FUZZ_TARGET_IDS'][idx] if idx < len(S['FUZZ_TARGET_IDS']) else 0x000
                    if not tid:
                        return 0x000,b'\x00'*8
                    d = bytearray(8); fmt = S['FUZZ_FORMAT']
                    c = ctr()
                    if fmt == 1:
                        d[0] = c
                        [d.__setitem__(j, random.randint(0,0xFF)) for j in range(1,8)]
                        return tid, bytes(d)
                    elif fmt == 2:
                        d[1] = c
                        for j in range(2,8): d[j] = random.randint(0,0xFF)
                        d[0] = crc8(bytes(d[1:8]),0xFF,0x00)
                        return tid, bytes(d)
                    elif fmt == 4:
                        d[1] = c
                        for j in range(2,7): d[j] = random.randint(0,0xFF)
                        d[7] = c
                        d[0] = crc8(bytes(d[1:8]),0xFF,0x00)
                        return tid, bytes(d)
                    else:
                        return tid, build_fuzz_payload(fmt, c, 8)
                return bld
            threads.append(CyclicSender(bus,-1,_mk(i),RANDOM_CYCLE_S,f"FZ_{i}"))
        for i in range(len(S.get('mode_b_fixed_random_ids', []))):
            def _mk_fixed(idx):
                ctr = make_counter(0,255,wrap=0)
                def bld():
                    ids = S.get('mode_b_fixed_random_ids', [])
                    tid = ids[idx] if idx < len(ids) else 0x000
                    if not tid:
                        return 0x000, b'\x00'*8
                    d = bytearray(8); fmt = S['FUZZ_FORMAT']
                    c = ctr()
                    if fmt == 1:
                        d[0] = c
                        for j in range(1,8): d[j] = random.randint(0,0xFF)
                        return tid, bytes(d)
                    elif fmt == 2:
                        d[1] = c
                        for j in range(2,8): d[j] = random.randint(0,0xFF)
                        d[0] = crc8(bytes(d[1:8]),0xFF,0x00)
                        return tid, bytes(d)
                    elif fmt == 4:
                        d[1] = c
                        for j in range(2,7): d[j] = random.randint(0,0xFF)
                        d[7] = c
                        d[0] = crc8(bytes(d[1:8]),0xFF,0x00)
                        return tid, bytes(d)
                    else:
                        return tid, build_fuzz_payload(fmt, c, 8)
                return bld
            threads.append(CyclicSender(bus,-1,_mk_fixed(i),RANDOM_CYCLE_S,f"BFX_{i}"))
    elif mode == 'C':
        mode_c_refresh_meta()
        with S['slot_lock']:
            S['slot_states'].clear()
        for tid in S['FUZZ_TARGET_IDS']:
            idx = _mode_c_new_slot_id()
            slot = {'slot': idx, 'id': tid, 'active': True, 'enabled': True, 'name': f'Fuzz 0x{tid:03X}'}
            with S['slot_lock']:
                S['slot_states'].append(slot)
            threads.append(CyclicSender(bus,-1,make_slot_builder(slot),RANDOM_CYCLE_S,f"FZC_{idx}"))
        for p in mode_c_get_fixed_profiles():
            if not p.get('active', True):
                continue
            FRAME_META[p['id']] = (p.get('name', f"自定义 0x{p['id']:03X}"), mode_c_profile_logic_desc(p))
            threads.append(CyclicSender(bus,-1,make_mode_c_profile_builder(p),max(1, int(p.get('cycle_ms', 100)))/1000.0,f"FXC_{p.get('slot', 0)}"))
    elif mode == 'D':
        threading.Thread(target=mode_d_worker,args=(bus,),daemon=True,name='ModeD').start()

    for t in threads: t.start()
    threading.Thread(target=monitor_loop,daemon=True).start()

    if USER_SETTINGS.get('auto_open_log_window', True):
        open_log_window()
    if USER_SETTINGS.get('auto_open_editor_window', True):
        open_editor_window()
    if USER_SETTINGS.get('auto_open_history_window', True):
        open_history_window()
    if USER_SETTINGS.get('auto_open_control_window', True):
        open_control_window(bus, threads)

    result='menu'
    while True:
        if not msvcrt.kbhit():
            time.sleep(0.02)
            continue
        ch=msvcrt.getwch().upper()
        alt_held=_check_alt_q()

        if mode=='D':
            D=S['mode_d']
            if ch in ('\x00','\xe0'):
                ch2=msvcrt.getwch().upper()
                if ch2=='H': mode_d_switch_target(-1); continue
                elif ch2=='P': mode_d_switch_target(1); continue
            if   ch=='T': D['auto_mode']=not D['auto_mode']; D['event'].set()
            elif ch=='P': D['auto_paused']=not D['auto_paused']
            elif ch=='+': D['auto_interval']=max(0.03,D['auto_interval']-0.05); D['event'].set()
            elif ch=='-': D['auto_interval']=min(3.0,D['auto_interval']+0.05); D['event'].set()
            elif ch in ('1','2','3','4'): D['scan_strategy']=int(ch); D['event'].set()
            elif ch=='[':
                with D['lock']: D['hit_frozen']=False; D['frozen_payload']=None
                D['event'].set()
            elif ch in ('Y','N',' ','L','B'):
                D['_action']={'Y':'y','N':'n',' ':'n','L':'l','B':'b'}[ch]; D['event'].set()
            elif ch=='O':
                state = apply_terminal_state('ACC', FRAME_BASE_DATA, FRAME_BASE_LOCK, crc8)
                _status_line(f'Terminal -> {state}')
            elif ch=='I':
                state = apply_terminal_state('IGN', FRAME_BASE_DATA, FRAME_BASE_LOCK, crc8)
                _status_line(f'Terminal -> {state}')
            elif ch=='K':
                state = cycle_terminal_state(FRAME_BASE_DATA, FRAME_BASE_LOCK, crc8)
                _status_line(f'Terminal -> {state}')
            elif ch==']':
                flag = toggle_frame_enabled(FRAME_ENABLED, FRAME_ENABLED_LOCK, 0x3A0)
                _status_line(f"3A0固定帧 -> {'开启' if flag else '关闭'}")
            elif ch=='M': do_mark()
            elif ch=='W': open_log_window(); open_editor_window(); open_history_window()
            elif ch=='U': open_control_window(bus, threads)
            elif ch=='S':
                S['running'].clear(); time.sleep(0.3)
                _close_all_session_windows()
                os.system('cls'); save_log(); result='menu'; break
            elif ch=='Q':
                if alt_held:
                    S['running'].clear(); time.sleep(0.3)
                    _close_all_session_windows(); os.system('cls')
                    print(f"{YELLOW}已退出（未保存日志）{RESET}\n")
                    result='quit_nosave'; break
                else:
                    S['running'].clear(); time.sleep(0.3)
                    _close_all_session_windows(); os.system('cls'); save_log(); result='quit'; break
            continue

        if   ch=='O':
            state = apply_terminal_state('ACC', FRAME_BASE_DATA, FRAME_BASE_LOCK, crc8)
            _status_line(f'Terminal -> {state}')
        elif ch=='I':
            state = apply_terminal_state('IGN', FRAME_BASE_DATA, FRAME_BASE_LOCK, crc8)
            _status_line(f'Terminal -> {state}')
        elif ch=='K':
            state = cycle_terminal_state(FRAME_BASE_DATA, FRAME_BASE_LOCK, crc8)
            _status_line(f'Terminal -> {state}')
        elif ch==']':
            flag = toggle_frame_enabled(FRAME_ENABLED, FRAME_ENABLED_LOCK, 0x3A0)
            _status_line(f"3A0固定帧 -> {'开启' if flag else '关闭'}")
        elif ch=='M': do_mark()
        elif ch=='W': open_log_window(); open_editor_window(); open_history_window()
        elif ch=='U': open_control_window(bus, threads)
        elif ch=='S':
            S['running'].clear(); time.sleep(0.3)
            _close_all_session_windows(); os.system('cls'); save_log(); result='menu'; break
        elif ch=='Q':
            if alt_held:
                S['running'].clear(); time.sleep(0.3)
                _close_all_session_windows(); os.system('cls')
                print(f"{YELLOW}已退出（未保存日志）{RESET}\n")
                result='quit_nosave'; break
            else:
                S['running'].clear(); time.sleep(0.3)
                _close_all_session_windows(); os.system('cls'); save_log(); result='quit'; break
        elif ch=='N' and mode=='B':
            cur=get_fuzz_ids(); new=[]
            while len(new)<len(cur):
                aid=random.randint(0x001,0x7FF)
                if aid not in RESERVED_IDS and aid not in new: new.append(aid)
            with S['fuzz_lock']: S['FUZZ_TARGET_IDS'][:]=new
        elif mode=='C':
            if   ch=='A': open_mode_c_gui(bus,threads)
            elif ch=='P': S['target_paused']=not S['target_paused']
            elif ch=='[': S['bytes_frozen']=not S['bytes_frozen']
            elif ch=='9': S['frozen_send_mode']=2 if S['frozen_send_mode']==1 else 1

    for t in threads: t.join(timeout=0.5)
    if result == 'menu':
        start_menu_keepalive(bus)
    return result

# ──────────────────────────────────────────────
#  主入口
# ──────────────────────────────────────────────
def main():
    global USER_SETTINGS
    os.system("cls")
    print(f"{BOLD}{CYAN}PCAN Sender 连接中...{RESET}\n")
    try:
        bus=can.Bus(interface=BUS_TYPE,channel=CHANNEL,bitrate=BITRATE)
    except Exception as e:
        print(f"{RED}连接失败: {e}{RESET}")
        print("请检查: 1) 驱动已安装  2) 设备已连接  3) CHANNEL配置正确")
        input("按回车退出"); return
    print(f"{GREEN}已连接 {CHANNEL} @ {BITRATE//1000}kbps{RESET}\n")
    start_menu_keepalive(bus)
    time.sleep(0.3)
    if USER_SETTINGS.get('show_splash', True):
        show_splash()

    while True:
        select_mode()
        os.system("cls")
        result=run_session(bus)
        if result in ('quit','quit_nosave'): break

    stop_menu_keepalive()
    bus.shutdown()
    print(f"\n{CYAN}再见！  GitHub: JackieZ123430{RESET}\n")

if __name__=="__main__":
    main()
