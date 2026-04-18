"""
pcan_sender.py  v4.0
GitHub: JackieZ123430
"""

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
try:
    import can.interfaces.pcan
except Exception:
    pass

try:
    import can.interfaces.slcan
except Exception:
    pass

try:
    import tkinter as tk
    from tkinter import ttk, messagebox, scrolledtext
    HAS_TK = True
except Exception:
    tk = None; ttk = None; messagebox = None; scrolledtext = None
    HAS_TK = False

# 兼容模式C里 E 键弹窗调用；当前版本仍沿用各窗口各自线程的实现。
# 这里只补上缺失的调度函数，避免按键后直接 NameError。
_tk_root = None

def _ensure_tk():
    return HAS_TK

def _tk_call(func, wait=False):
    try:
        return func()
    except Exception:
        return None

# ──────────────────────────────────────────────
#  配置
# ──────────────────────────────────────────────
ADAPTER_TYPE   = "pcan"        # pcan / slcan / socketcan
CHANNEL        = "PCAN_USBBUS1"
BITRATE        = 500_000
BUS_TYPE       = "pcan"
RANDOM_CYCLE_S = 0.100
# CMD日志窗口
_CMD_LOG_PROC  = None
_CMD_LOG_PIPE  = None
_CMD_LOG_LOCK  = threading.Lock()
LOG_FILE       = os.path.join(os.path.dirname(os.path.abspath(__file__)), "can_log.txt")

RESERVED_IDS = {
    0x03C, 0x0F3, 0x1A1, 0x0AB, 0x2A7, 0x2EC,
    0x289, 0x294, 0x30B, 0x369, 0x36F, 0x3A0,
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
    0x2A7: ("Power Steering", "CRC8(b[1..4]) init=FF xor=9E | byte[1]低4位=CTR(0~15)"),
    0x2EC: ("Navi/Guidance",  "CRC8(b[1..7]) init=FF xor=00 | byte[1]低4位=CTR(0~14)"),
    0x289: ("Cruise Control", "CRC8(b[1..7]) init=FF xor=82 | byte[1..7]全随机"),
    0x294: ("转向助力",        "3帧轮播: 73C7FEFF14→3CCAFEFF14→55CCFEFF14"),
    0x30B: ("DME keepalive",  "无CRC | byte[1]=sync_ctr 0x50~0x5F循环"),
    0x369: ("ECU online",     "CRC8(b[1..4]) init=FF xor=C5 | byte[1]低4位=CTR(0~15)"),
    0x36F: ("ABS keepalive",  "CRC8(b[1..4]) init=FF xor=17 | byte[1]低4位=CTR(0~15)"),
    0x3A0: ("Unknown",        "无CRC | byte[7]自增 0x00~0xFF"),
    0x3D8: ("Drive Mode",     "CRC8(b[1..7]) init=FF xor=D8 | 无CTR"),
    0x3FD: ("Gear Selector",  "CRC8(b[1..7]) init=FF xor=D6 | byte[1]低4位=CTR(0~14)"),
    0x510: ("在线帧",          "固定数据: 40 10 00 02 02 12 11 00"),
}

# 固定帧默认基础数据（可被在线帧编辑窗口覆盖）
# 格式: {arb_id: bytearray}  — 这是编辑层，builder每次都从这里读
FRAME_BASE_DATA = {
    0x03C: bytearray.fromhex("9A55061222002AFF"),
    0x0F3: bytearray.fromhex("F300C0F044FFFF00"),
    0x1A1: bytearray.fromhex("00F0000081"),
    0x0AB: bytearray.fromhex("00FC4055FDFFFFFF"),
    0x2A7: bytearray.fromhex("00F0FEFF14"),
    0x2EC: bytearray.fromhex("0000000000000000"),
    0x289: bytearray.fromhex("BE F02EE0E0880A07".replace(" ","")),
    0x294: bytearray.fromhex("73C7FEFF14"),
    0x30B: bytearray.fromhex("0F500FC8FFFFFFFF"),
    0x369: bytearray.fromhex("00F0A0A0A0"),
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
    0x03C, 0x0F3, 0x1A1, 0x0AB, 0x2A7, 0x2EC,
    0x289, 0x294, 0x30B, 0x369, 0x36F, 0x3A0,
    0x3D8, 0x3FD, 0x510
]

def set_frame_enabled(frame_id, enabled):
    with FRAME_ENABLED_LOCK:
        FRAME_ENABLED[frame_id] = bool(enabled)

def get_frame_enabled(frame_id):
    with FRAME_ENABLED_LOCK:
        return bool(FRAME_ENABLED.get(frame_id, True))

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
        'c_auto_switch':    False,
        'c_switch_interval': 15.0,
        'c_switch_paused':  True,
        'c_last_switch':    [0.0],
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
        'mode_e': {
            'target_id':      0x000,
            'length':         8,
            'results':        [],
            'status':         '准备开始...',
            'done':           False,
            'waiting_key':    False,
            '_action':        'skip',
            'lock':           threading.Lock(),
            'event':          threading.Event(),
        },
        'mode_d': {
            'target_id':      0x000,
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
    # Also write to CMD log window (defined later, checked safely)
    try: _cmd_log_write(ts, arb_id, data)
    except NameError: pass

# ──────────────────────────────────────────────
#  窗口注册表（统一管理所有GUI窗口生命周期）
# ──────────────────────────────────────────────
_WIN_REGISTRY = {}       # name -> {'root': tk.Tk, 'lock': Lock, 'open': bool}
_WIN_REG_LOCK = threading.Lock()

def _win_close(name):
    with _WIN_REG_LOCK:
        w = _WIN_REGISTRY.get(name)
    if w:
        w['open'] = False
        try: w['root'].destroy()
        except: pass

def _close_all_session_windows():
    """退出模式时关闭所有会话级窗口（除PCAN占用窗口）"""
    for name in ['log', 'editor', 'history']:
        _win_close(name)

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
        d[1]=(d[1]&0xF0)|(c0F3()&0x0F)
        d[0]=crc8(bytes(d[0:8]),0x00,0x2C); return bytes(d)

    c1A1 = make_counter(0,14)
    def b1A1():
        with FRAME_ENABLED_LOCK:
            if not FRAME_ENABLED.get(0x1A1, True): return b''
        d = _get_base(0x1A1)
        d[1]=(d[1]&0xF0)|(c1A1()&0x0F)
        d[0]=crc8(bytes(d[0:5]),0x00,0x2C); return bytes(d)

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

    def b289():
        with FRAME_ENABLED_LOCK:
            if not FRAME_ENABLED.get(0x289, True): return b''
        d=bytearray(8)
        for i in range(1,8): d[i]=random.randint(0,0xFF)
        d[0]=crc8(bytes(d[1:8]),0xFF,0x82); return bytes(d)

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

    def b510():
        with FRAME_ENABLED_LOCK:
            if not FRAME_ENABLED.get(0x510, True): return b''
        return bytes(_get_base(0x510))

    def b_rnd_a():
        while True:
            aid = random.randint(0x001, 0x7FF)
            if aid not in RESERVED_IDS: break
        return aid, bytes(random.randint(0,0xFF) for _ in range(8))

    return {
        0x03C:b03C, 0x0F3:b0F3, 0x1A1:b1A1, 0x0AB:b0AB,
        0x2A7:b2A7, 0x2EC:b2EC, 0x289:b289, 0x294:b294,
        0x30B:b30B, 0x369:b369, 0x36F:b36F, 0x3A0:b3A0,
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
            active    = slot_state['active']
            target_id = slot_state['id']
        if not active: return 0x000, b'\x00'*8
        if S['target_paused']: return 0x000, b'\x00'*8
        fmt=S['FUZZ_FORMAT']; bfrozen=S['bytes_frozen']; fmode=S['frozen_send_mode']
        if bfrozen and fmode==2: return 0x000, b'\x00'*8
        if bfrozen:
            if frozen_rnd[0] is None:
                frozen_rnd[0]=bytes(random.randint(0,0xFF) for _ in range(7))
            rnd=frozen_rnd[0]
        else:
            rnd=bytes(random.randint(0,0xFF) for _ in range(7))
            frozen_rnd[0]=rnd
        c=ctr(); d=bytearray(8)
        if fmt==1:   d[0]=c; d[1:8]=rnd
        elif fmt==2: d[1]=c; d[2:8]=rnd[:6]; d[0]=crc8(bytes(d[1:8]),0xFF,0x00)
        elif fmt==4: d[0:8]=rnd[:7]+rnd[6:7]  # 无CRC无CTR
        else:
            for i in range(8): d[i]=random.randint(0,0xFF)
        return target_id, bytes(d)
    return builder

def mode_c_add_target(bus, threads, target_id):
    with S['fuzz_lock']:
        if target_id in S['FUZZ_TARGET_IDS']: return False, f'0x{target_id:03X} 已在列表中'
        S['FUZZ_TARGET_IDS'].append(target_id)
    with S['slot_lock']:
        idx=S['next_slot'][0]; S['next_slot'][0]+=1
        slot={'slot':idx,'id':target_id,'active':True}
        S['slot_states'].append(slot)
    t=CyclicSender(bus,-1,make_slot_builder(slot),RANDOM_CYCLE_S,f'FZC_{idx}')
    t.start(); threads.append(t)
    return True, f'已添加 0x{target_id:03X}'

def mode_c_remove_target(target_id):
    removed=False
    with S['fuzz_lock']:
        if target_id in S['FUZZ_TARGET_IDS']:
            S['FUZZ_TARGET_IDS']=[x for x in S['FUZZ_TARGET_IDS'] if x!=target_id]
            removed=True
    if not removed: return False, f'0x{target_id:03X} 不在列表中'
    with S['slot_lock']:
        for st in S['slot_states']:
            if st['active'] and st['id']==target_id: st['active']=False; break
    return True, f'已删除 0x{target_id:03X}'

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
        if 1 not in locked and cur_byte!=1: d[1]=c
        d[0]=crc8(bytes(d[1:length]),0xFF,0x00)
    else:
        d[0]=random.randint(0,0xFF)

    return bytes(d)

# ──────────────────────────────────────────────
#  模式D：辅助
# ──────────────────────────────────────────────
def _d_send_and_wait(bus, target_id):
    D=S['mode_d']
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
    with D['lock']: target_id=D['target_id']; length=D['length']
    byte_pos=1
    while byte_pos<length and S['running'].is_set():
        with D['lock']: D['current_byte']=byte_pos; strat=D['scan_strategy']
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
_FMT_NAMES={1:"CTR+随机",2:"CRC+CTR+随机",3:"纯随机",4:"无CRC无CTR(固定)"}
_SCAN_NAMES={1:"标准逐byte",2:"分组nibble",3:"双byte联扫",4:"加速手动"}


# ══════════════════════════════════════════════
#  CMD日志窗口（独立新CMD窗口）
# ══════════════════════════════════════════════
def _open_cmd_log_named_pipe():
    global _CMD_LOG_PROC, _CMD_LOG_PIPE
    with _CMD_LOG_LOCK:
        if _CMD_LOG_PROC:
            try:
                if _CMD_LOG_PROC.poll() is None:
                    return
            except: pass
        try:
            import tempfile
            log_fifo = os.path.join(tempfile.gettempdir(), 'cansender_log.txt')
            open(log_fifo, 'w').close()
            reader = os.path.join(tempfile.gettempdir(), 'cansender_reader.py')
            with open(reader, 'w', encoding='utf-8') as f:
                f.write(
                    "import sys,os,time\n"
                    "os.system('')\n"
                    "C=chr(27)+'[96m';R=chr(27)+'[0m';G=chr(27)+'[90m'\n"
                    "GR=chr(27)+'[92m';M=chr(27)+'[95m';Y=chr(27)+'[33m'\n"
                    "FN=" + repr(log_fifo) + "\n"
                    "FX={0x03C,0xF3,0x1A1,0xAB,0x2A7,0x2EC,0x289,0x294,0x30B,0x369,0x36F,0x3A0,0x3D8,0x3FD,0x510}\n"
                    "os.system('title CAN Sender v4.0 - 实时发送日志')\n"
                    "print(C+'══════ CAN Sender v4.0  实时发送日志 ══════'+R)\n"
                    "print(G+'蓝色=固定帧  紫色=Fuzz帧  黄色=模式D'+R+'\\n')\n"
                    "seen=0\n"
                    "try:\n"
                    "    while True:\n"
                    "        try:\n"
                    "            with open(FN,'r',encoding='utf-8',errors='ignore') as f:\n"
                    "                lines=f.readlines()\n"
                    "        except: time.sleep(0.1); continue\n"
                    "        for ln in lines[seen:]:\n"
                    "            ln=ln.rstrip()\n"
                    "            if not ln: continue\n"
                    "            p=ln.split('|',3)\n"
                    "            if len(p)==4:\n"
                    "                ts,ih,dh,nm=p\n"
                    "                try: ii=int(ih.strip(),16)\n"
                    "                except: ii=0\n"
                    "                ic=C if ii in FX else (Y if nm.strip().startswith('D:') else M)\n"
                    "                print(G+ts+R+'  '+ic+ih+R+'  '+GR+dh+R+'  '+G+nm+R)\n"
                    "        seen=len(lines)\n"
                    "        sys.stdout.flush()\n"
                    "        time.sleep(0.08)\n"
                    "except KeyboardInterrupt: pass\n"
                    "except Exception as e: print('Error:',e)\n"
                    "input('\\n已停止，按回车关闭...')\n"
                )
            _CMD_LOG_PROC = subprocess.Popen(
                f'start "CAN发送日志" python "{reader}"',
                shell=True
            )
            _CMD_LOG_PIPE = log_fifo
        except Exception as e:
            pass

def _cmd_log_write(ts, arb_id, data):
    with _CMD_LOG_LOCK:
        pipe = _CMD_LOG_PIPE
    if not pipe: return
    try:
        ts_s = time.strftime('%H:%M:%S', time.localtime(ts)) + f'.{int((ts%1)*1000):03d}'
        nm   = FRAME_META.get(arb_id, (f'0x{arb_id:03X}',))[0]
        line = f'{ts_s}|0x{arb_id:03X}|{" ".join(f"{b:02X}" for b in data)}|{nm}\n'
        with open(pipe, 'a', encoding='utf-8') as f:
            f.write(line)
    except: pass

def _close_cmd_log():
    global _CMD_LOG_PROC
    with _CMD_LOG_LOCK:
        if _CMD_LOG_PROC:
            try: _CMD_LOG_PROC.terminate()
            except: pass
            _CMD_LOG_PROC = None

# ══════════════════════════════════════════════
#  适配器自动检测
# ══════════════════════════════════════════════
def auto_detect_adapter():
    global ADAPTER_TYPE, CHANNEL, BUS_TYPE
    detected = []
    try:
        b = can.Bus(interface='pcan', channel='PCAN_USBBUS1', bitrate=500000)
        b.shutdown()
        detected.append(('pcan', 'PCAN_USBBUS1', 'PCAN USB (已确认)'))
    except: pass
    try:
        import serial.tools.list_ports
        for p in serial.tools.list_ports.comports():
            desc = (p.description or '') + (p.manufacturer or '')
            kws  = ['CH340','CP210','FTDI','Arduino','ESP32','CANHacker']
            if any(k.lower() in desc.lower() for k in kws):
                detected.append(('slcan', p.device, f'SLCAN: {p.device} ({p.description[:30]})'))
    except: pass
    return detected

def select_adapter():
    global ADAPTER_TYPE, CHANNEL, BUS_TYPE
    os.system("cls")
    print(f"{BOLD}{CYAN}{'═'*62}{RESET}")
    print(f"{BOLD}{WHITE}  CAN Sender v4.0  ·  适配器选择{RESET}")
    print(f"{BOLD}{CYAN}{'═'*62}{RESET}\n")
    print(f"{CYAN}正在扫描可用适配器...{RESET}")
    detected = auto_detect_adapter()
    if detected:
        print(f"\n{GREEN}检测到以下适配器：{RESET}")
        for i,(t,ch,name) in enumerate(detected):
            print(f"  {YELLOW}{i+1}{RESET}  {name}")
        print(f"  {YELLOW}M{RESET}  手动配置")
        print(f"  {GRAY}回车 = 使用第1个{RESET}\n")
        while True:
            v = input("选择: ").strip().upper()
            if v == '' and detected:
                ADAPTER_TYPE=detected[0][0]; CHANNEL=detected[0][1]; BUS_TYPE=detected[0][0]
                print(f"{GREEN}✓ 使用: {detected[0][2]}{RESET}"); return
            if v == 'M': break
            try:
                idx=int(v)-1
                if 0<=idx<len(detected):
                    ADAPTER_TYPE=detected[idx][0]; CHANNEL=detected[idx][1]; BUS_TYPE=detected[idx][0]
                    print(f"{GREEN}✓ 使用: {detected[idx][2]}{RESET}"); return
            except: pass
            print(f"{RED}无效{RESET}")
    else:
        print(f"{YELLOW}未自动检测到适配器，请手动配置{RESET}\n")
    # 手动
    print(f"{BOLD}手动配置：{RESET}")
    print(f"  {YELLOW}1{RESET}  PCAN USB       (pcan, PCAN_USBBUS1)")
    print(f"  {YELLOW}2{RESET}  ESP32-SLCAN    (slcan, COMx)")
    print(f"  {YELLOW}3{RESET}  Arduino CANHacker (slcan, COMx)")
    print(f"  {YELLOW}4{RESET}  ESP-IDF CAN2USB  (slcan, COMx)")
    print(f"  {YELLOW}5{RESET}  socketcan (Linux)\n")
    while True:
        v = input("选择 (1-5): ").strip()
        if v == '1':
            ADAPTER_TYPE='pcan'; BUS_TYPE='pcan'
            CHANNEL = input("Channel (默认PCAN_USBBUS1): ").strip() or 'PCAN_USBBUS1'
            print(f"{GREEN}✓ PCAN: {CHANNEL}{RESET}"); return
        elif v in ('2','3','4'):
            nm={'2':'ESP32-SLCAN','3':'Arduino CANHacker','4':'ESP-IDF CAN2USB'}[v]
            ADAPTER_TYPE='slcan'; BUS_TYPE='slcan'
            CHANNEL = input(f"COM端口 (如 COM3): ").strip() or 'COM3'
            print(f"{GREEN}✓ {nm}: {CHANNEL}{RESET}"); return
        elif v == '5':
            ADAPTER_TYPE='socketcan'; BUS_TYPE='socketcan'
            CHANNEL = input("接口名 (如 can0): ").strip() or 'can0'
            print(f"{GREEN}✓ socketcan: {CHANNEL}{RESET}"); return
        print(f"{RED}请输入 1~5{RESET}")

# ══════════════════════════════════════════════
#  模式C：自动切换ID + E键改数量
# ══════════════════════════════════════════════
def mode_c_auto_switch_worker(bus, threads):
    while S['running'].is_set():
        time.sleep(0.5)
        if not S['c_auto_switch']: continue
        if S['c_switch_paused']: continue
        now = time.time()
        if now - S['c_last_switch'][0] < S['c_switch_interval']: continue
        S['c_last_switch'][0] = now
        cur = get_fuzz_ids()
        if not cur: continue
        new_ids = []
        while len(new_ids) < len(cur):
            aid = random.randint(0x001, 0x7FF)
            if aid not in RESERVED_IDS and aid not in new_ids:
                new_ids.append(aid)
        for old_id in list(cur): mode_c_remove_target(old_id)
        for nid in new_ids: mode_c_add_target(bus, threads, nid)

def ask_mode_c_change_count(bus, threads):
    if not _ensure_tk(): return
    def _build():
        win = tk.Toplevel(_tk_root)
        win.title('修改Fuzz数量'); win.geometry('400x200')
        win.configure(bg='#0d0d0d'); win.resizable(False, False)
        win.attributes('-topmost', True)
        cur_n = len(get_fuzz_ids())
        tk.Label(win, text='修改模式C的Fuzz目标数量',
                 bg='#0d0d0d', fg='#0cf', font=('Consolas',11,'bold')).pack(pady=(16,4))
        tk.Label(win, text=f'当前: {cur_n} 个ID  (修改会重置所有当前ID)',
                 bg='#0d0d0d', fg='#888', font=('Consolas',9)).pack()
        fr = tk.Frame(win, bg='#0d0d0d'); fr.pack(pady=12)
        tk.Label(fr, text='新数量:', bg='#0d0d0d', fg='#aaa', font=('Consolas',10)).pack(side='left')
        sv = tk.StringVar(value=str(cur_n))
        ent = tk.Entry(fr, textvariable=sv, width=6, bg='#1a1a1a', fg='#0f0',
                       insertbackground='white', font=('Consolas',12), relief='flat', justify='center')
        ent.pack(side='left', padx=6)
        msg_v = tk.StringVar(value='')
        tk.Label(win, textvariable=msg_v, bg='#0d0d0d', fg='#f44', font=('Consolas',9)).pack()
        def apply():
            try: n=int(sv.get()); assert 1<=n<=20
            except: msg_v.set('请输入 1~20 的整数'); return
            for old in list(get_fuzz_ids()): mode_c_remove_target(old)
            added=0
            while added<n:
                aid=random.randint(0x001,0x7FF)
                if aid not in RESERVED_IDS and aid not in get_fuzz_ids():
                    mode_c_add_target(bus,threads,aid); added+=1
            msg_v.set(f'✓ 已更新为 {n} 个新ID')
            win.after(1500, win.destroy)
        def cancel(): win.destroy()
        bf = tk.Frame(win, bg='#0d0d0d'); bf.pack()
        tk.Button(bf, text='确认修改', command=apply, bg='#003344', fg='#0cf',
                  relief='flat', font=('Consolas',10), padx=10).pack(side='left', padx=6)
        tk.Button(bf, text='取消', command=cancel, bg='#1a1a1a', fg='#888',
                  relief='flat', font=('Consolas',10), padx=8).pack(side='left')
        ent.focus_set()
    _tk_call(_build)

# ══════════════════════════════════════════════
#  模式E：算法检测
# ══════════════════════════════════════════════
def _e_make_algos():
    algos = []
    PARAMS = [
        (0xFF,0x91),(0xFF,0xD8),(0xFF,0xD6),(0xFF,0x55),(0xFF,0x9E),
        (0xFF,0xC5),(0xFF,0x17),(0xFF,0x82),(0xFF,0x00),(0x00,0x2C),
        (0x00,0x00),(0xFF,0xFF),(0xAA,0x55),(0x55,0xAA),
    ]
    # 策略1: byte[0]=CRC(b[1..N-1]), byte[1]低4位=CTR(0~14)
    for init,xor in PARAMS:
        def mk1(i=init,x=xor):
            def build(payload,ctr,length=8):
                d=bytearray(length)
                for j in range(1,length): d[j]=payload[j-1] if j-1<len(payload) else 0
                d[1]=(d[1]&0xF0)|(ctr&0x0F); d[0]=crc8(bytes(d[1:length]),i,x)
                return bytes(d)
            return build
        algos.append((f"CRC(b[1..N]) init={init:02X} xor={xor:02X} | b[1]低4=CTR(0~14)", mk1()))

    # 策略2: byte[0]=CRC(b[1..N-1]), byte[1]低4位=CTR(0~15)
    for init,xor in PARAMS[:6]:
        def mk2(i=init,x=xor):
            def build(payload,ctr,length=8):
                d=bytearray(length)
                for j in range(1,length): d[j]=payload[j-1] if j-1<len(payload) else 0
                d[1]=(d[1]&0xF0)|(ctr&0x0F); d[0]=crc8(bytes(d[1:length]),i,x)
                return bytes(d)
            return build
        algos.append((f"CRC(b[1..N]) init={init:02X} xor={xor:02X} | b[1]低4=CTR(0~15)", mk2()))

    # 策略3: byte[0]=CRC(b[1..N-1]), byte[N-1]=CTR(0~255)
    for init,xor in PARAMS[:6]:
        def mk3(i=init,x=xor):
            def build(payload,ctr,length=8):
                d=bytearray(length)
                for j in range(1,length): d[j]=payload[j-1] if j-1<len(payload) else 0
                d[length-1]=ctr&0xFF; d[0]=crc8(bytes(d[1:length]),i,x)
                return bytes(d)
            return build
        algos.append((f"CRC(b[1..N]) init={init:02X} xor={xor:02X} | b[N-1]=CTR(0~255)", mk3()))

    # 策略4: byte[0]=CRC(全部), byte[1]低4位=CTR
    for init,xor in [(0x00,0x2C),(0xFF,0x00),(0x00,0x00)]:
        def mk4(i=init,x=xor):
            def build(payload,ctr,length=8):
                d=bytearray(length)
                for j in range(length): d[j]=payload[j] if j<len(payload) else 0
                d[1]=(d[1]&0xF0)|(ctr&0x0F); d[0]=crc8(bytes(d[0:length]),i,x)
                return bytes(d)
            return build
        algos.append((f"CRC(b[0..N]) init={init:02X} xor={xor:02X} | b[1]低4=CTR", mk4()))

    # 策略5: 无CRC, byte[0]=CTR(0~255)
    def mk5(payload,ctr,length=8):
        d=bytearray(length)
        for j in range(1,length): d[j]=payload[j-1] if j-1<len(payload) else 0
        d[0]=ctr&0xFF; return bytes(d)
    algos.append(("无CRC | byte[0]=CTR(0~255)", mk5))

    # 策略6: 无CRC, byte[1]低4位=CTR(0~14)
    def mk6(payload,ctr,length=8):
        d=bytearray(length)
        for j in range(length): d[j]=payload[j] if j<len(payload) else 0
        d[1]=(d[1]&0xF0)|(ctr&0x0F); return bytes(d)
    algos.append(("无CRC | byte[1]低4位=CTR(0~14)", mk6))

    # 策略7: 无CRC无CTR，纯固定
    def mk7(payload,ctr,length=8):
        d=bytearray(length)
        for j in range(length): d[j]=payload[j] if j<len(payload) else 0
        return bytes(d)
    algos.append(("无CRC无CTR，纯固定payload", mk7))

    return algos

def _e_wait_action():
    E=S['mode_e']
    with E['lock']:
        E['waiting_key']=True
        E['_action']='skip'
    E['event'].clear()
    E['event'].wait(timeout=30)
    with E['lock']:
        E['waiting_key']=False
        action=E['_action']
        E['_action']='skip'
    return action


def mode_e_worker(bus):
    E=S['mode_e']
    with E['lock']:
        target_id=E['target_id']
        length=E['length']
        E['results']=[]
        E['done']=False
        E['waiting_key']=False
        E['_action']='skip'
    algos=_e_make_algos(); total=len(algos)
    test_payload=bytes(length)
    with E['lock']:
        E['status']=f'开始测试 {total} 种算法，目标: 0x{target_id:03X}'

    for idx,(name,build_fn) in enumerate(algos):
        if not S['running'].is_set():
            break
        with E['lock']:
            E['status']=f'[{idx+1}/{total}] 测试: {name[:40]}  发16帧，Y=有效 N=无效 空格=跳过'
        for ctr in range(16):
            if not S['running'].is_set():
                break
            try:
                data=build_fn(test_payload,ctr,length)
                bus.send(can.Message(arbitration_id=target_id,data=data,is_extended_id=False))
                record_tx(target_id,data,0)
                time.sleep(0.08)
            except Exception:
                pass
        if not S['running'].is_set():
            break
        with E['lock']:
            E['status']=f'[{idx+1}/{total}] {name[:40]} — Y=有反应 N=无 空格=跳过 Q=结束检测'
        action=_e_wait_action()
        with E['lock']:
            if action=='yes':
                E['results'].append((name,True, '✓ 有反应'))
            elif action=='no':
                E['results'].append((name,False,'✗ 无反应'))
            elif action=='quit':
                E['results'].append((name,None, '■ 用户结束检测'))
            else:
                E['results'].append((name,None, '— 已跳过'))
        if action=='quit':
            break

    with E['lock']:
        E['done']=True
        E['waiting_key']=False
        hits=[(n,d) for n,ok,d in E['results'] if ok is True]
        E['status']=f'完成！找到 {len(hits)} 种有效算法。现在按 S 保存返回 / Q 保存退出' if hits else '完成，未发现有效算法。现在按 S 保存返回 / Q 保存退出'

def draw_mode_e():
    E=S['mode_e']
    with E['lock']:
        target_id=E['target_id']; length=E['length']
        results=list(E['results']); status=E['status']; done=E['done']
    out=["\033[H\033[J"]
    out.append(f"{BOLD}{CYAN}{'═'*96}{RESET}")
    out.append(f"{BOLD}{WHITE}  模式E — 算法检测  {time.strftime('%H:%M:%S')}  "
               f"目标: {CYAN}0x{target_id:03X}{RESET}  帧长: {length}{RESET}")
    out.append(f"  {GRAY}Y=有反应  N=无反应  空格=跳过  Q=结束本轮检测{RESET}")
    out.append(f"{BOLD}{CYAN}{'═'*96}{RESET}\n")
    if results:
        out.append(f"  {BOLD}{YELLOW}{'算法描述':<48}  结果{RESET}")
        out.append(f"  {GRAY}{'─'*80}{RESET}")
        for name,ok,detail in results[-22:]:
            col=GREEN if ok is True else GRAY
            sym='✓' if ok is True else ('✗' if ok is False else '—')
            out.append(f"  {col}{sym}  {name:<46}  {detail}{RESET}")
        out.append("")
    hits=[(n,d) for n,ok,d in results if ok is True]
    if hits:
        out.append(f"  {BOLD}{GREEN}★ 有效算法 ({len(hits)} 种):{RESET}")
        for n,_ in hits: out.append(f"  {GREEN}  → {n}{RESET}")
        out.append("")
    scol=GREEN if done else CYAN
    out.append(f"  {scol}{BOLD}{status}{RESET}")
    out.append(f"\n  {GRAY}后台固定帧持续发送中{RESET}")
    sys.stdout.write("\n".join(out)); sys.stdout.flush()

def draw_normal():
    now=time.time(); mode=S['APP_MODE']; fmt=S['FUZZ_FORMAT']
    out=["\033[H\033[J"]
    hk=f"{YELLOW}M=标记  S=回菜单  Q=保存退出  ALT+Q=不保存退出  W=重开日志窗"
    if mode=='B': hk+=f"  {MAGENTA}N=换ID"
    if mode=='C':
        pl=f"{RED}P=恢复目标帧" if S['target_paused'] else f"{GREEN}P=暂停目标帧"
        fl=f"{BLUE}[=解冻随机" if S['bytes_frozen'] else f"{CYAN}[=冻结随机"
        fm="停发" if S['frozen_send_mode']==2 else "继续发"
        hk+=f"  {pl}  {fl}  {GRAY}9=冻结({fm})  {CYAN}A=管理GUI"
    out.append(f"{BOLD}{CYAN}{'═'*104}{RESET}")
    out.append(f"{BOLD}{WHITE}  CAN监控  {time.strftime('%H:%M:%S')}  [模式{mode}] {GRAY}格式:{_FMT_NAMES[fmt]}{RESET}  {hk}{RESET}")
    out.append(f"{BOLD}{CYAN}{'═'*104}{RESET}\n")
    if mode=='C':
        ps=f"{RED}● 目标帧已暂停{RESET}" if S['target_paused'] else f"{GREEN}● 目标帧发送中{RESET}"
        fs=f"{BLUE}● 随机bytes冻结{RESET}" if S['bytes_frozen'] else f"{GREEN}● 随机bytes正常{RESET}"
        out.append(f"  {ps}    {fs}\n")
    with S['frame_lock']: snap=dict(S['frame_state'])
    out.append(f"{BOLD}{YELLOW}  {'ID':<8}{'名称':<20}{'周期':>7}  {'次数':>9}  数据{RESET}")
    out.append(f"  {GRAY}{'─'*100}{RESET}")
    for aid in DISPLAY_ORDER:
        if aid not in snap: continue
        st=snap[aid]; age=now-st["ts"]; col=GRAY if age>0.5 else GREEN
        with FRAME_ENABLED_LOCK: en=FRAME_ENABLED.get(aid,True)
        en_str=f"{GREEN}●{RESET}" if en else f"{RED}○{RESET}"
        out.append(
            f"  {en_str} {CYAN}0x{aid:03X}{RESET}  {WHITE}{st['name']:<20}{RESET}"
            f"{GRAY}{st['cycle']:>6.0f}ms{RESET}  {col}{st['count']:>9}{RESET}  "
            f"{fmt_bytes_indexed(st['data'])}"
        )
        out.append(f"  {GRAY}{'':12}{st['logic']}{RESET}\n")
    rand_ids=sorted(k for k in snap if k not in RESERVED_IDS)
    if rand_ids:
        ids_str=", ".join(f"0x{i:03X}" for i in get_fuzz_ids()) or "(空)"
        lbl=f"定向Fuzz: {ids_str}" if mode in ('B','C') else f"随机帧 ({len(rand_ids)} IDs)"
        out.append(f"{BOLD}{MAGENTA}  ▶ {lbl} ◀{RESET}")
        out.append(f"  {GRAY}{'─'*100}{RESET}")
        for aid in (sorted(get_fuzz_ids()) if mode in ('B','C') else rand_ids[-15:]):
            if aid not in snap: continue
            st=snap[aid]; age=now-st["ts"]; col=GRAY if age>0.5 else MAGENTA
            out.append(
                f"    {col}0x{aid:03X}{RESET}  {GRAY}{'定向Fuzz':<20}{RESET}"
                f"{GRAY}{st['cycle']:>6.0f}ms{RESET}  {col}{st['count']:>9}{RESET}  "
                f"{fmt_bytes_indexed(st['data'])}"
            )
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
        target_id=D['target_id']; length=D['length']
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
    out.append(f"  {GRAY}T=手动/自动  +/-=间隔  P=暂停  1/2/3/4=策略  W=重开日志{RESET}")
    out.append(f"  {GRAY}Y=有反应  N/空格=无反应  L=锁定进下一byte  B=回退  [=解冻  S=保存回菜单  ALT+Q=不保存退出{RESET}")
    out.append(f"{BOLD}{CYAN}{'═'*96}{RESET}\n")
    out.append(f"  目标: {CYAN}0x{target_id:03X}{RESET}   帧长: {length}   格式: {_FMT_NAMES[S['FUZZ_FORMAT']]}\n")
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
        m=S['APP_MODE']
        if m=='D': draw_mode_d()
        elif m=='E': draw_mode_e()
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
            with D['lock']: d_id=D['target_id']; d_hits=list(D['hits']); d_locked=dict(D['locked'])
            f.write(f"【模式D穷举结果】目标: 0x{d_id:03X}\n")
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
#  GUI 窗口 1：实时发送日志
# ══════════════════════════════════════════════
def open_log_window():
    if not HAS_TK: return
    with _WIN_REG_LOCK:
        w=_WIN_REGISTRY.get('log',{})
        if w.get('open'): return

    def _worker():
        root=tk.Toplevel() if False else tk.Tk()
        with _WIN_REG_LOCK:
            _WIN_REGISTRY['log']={'root':root,'open':True}
        root.title('📡 实时发送日志  —  CAN Sender v3.0')
        root.geometry('1000x500')
        root.configure(bg='#0d0d0d')

        # 顶栏
        top=tk.Frame(root,bg='#0d0d0d'); top.pack(fill='x',padx=6,pady=(4,2))
        tk.Label(top,text='显示模式:',bg='#0d0d0d',fg='#888',font=('Consolas',9)).pack(side='left')
        mode_var=tk.StringVar(value='1 - 全部帧')
        cb=ttk.Combobox(top,textvariable=mode_var,width=18,state='readonly',
                        values=['1 - 全部帧','2 - 仅指定ID'])
        cb.pack(side='left',padx=(2,10))
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
        def do_clear():
            txt.configure(state='normal'); txt.delete('1.0',tk.END); txt.configure(state='disabled')
            cnt_v.set('0 帧'); last_seq[0]=0
        tk.Button(top,text='清空',command=do_clear,bg='#2a0000',fg='#f44',
                  relief='flat',font=('Consolas',9),padx=6).pack(side='right')

        # 文本框
        fr=tk.Frame(root,bg='#0d0d0d'); fr.pack(fill='both',expand=True,padx=6,pady=(2,6))
        sb=tk.Scrollbar(fr); sb.pack(side='right',fill='y')
        txt=tk.Text(fr,bg='#0d0d0d',fg='#0f0',font=('Consolas',10),state='disabled',
                    wrap='none',yscrollcommand=sb.set,relief='flat',selectbackground='#003322')
        txt.pack(fill='both',expand=True); sb.config(command=txt.yview)
        txt.tag_config('ts',foreground='#444')
        txt.tag_config('fix',foreground='#0cf')
        txt.tag_config('fuzz',foreground='#f8f')
        txt.tag_config('d',foreground='#fc0')
        txt.tag_config('data',foreground='#0f0')
        txt.tag_config('nm',foreground='#666')

        last_seq=[0]; shown=[0]

        def _poll():
            if not _WIN_REGISTRY.get('log',{}).get('open',False): return
            if pause_v.get():
                root.after(200,_poll); return
            mode='2' in mode_var.get()
            fids=None
            if mode:
                try: fids=set(parse_hex_id_list(fv.get()))
                except: pass
            with _log_queue_lock:
                snap=list(_log_queue)
            new=[e for e in snap if e[0]>last_seq[0]]
            if new:
                last_seq[0]=new[-1][0]
                txt.configure(state='normal')
                for seq,ts,aid,data in new:
                    if fids is not None and aid not in fids: continue
                    ts_s=time.strftime('%H:%M:%S',time.localtime(ts))
                    ms_s=f'.{int((ts%1)*1000):03d}'
                    id_tag='fix' if aid in RESERVED_IDS else ('d' if aid==S['mode_d'].get('target_id',-1) and S['APP_MODE']=='D' else 'fuzz')
                    txt.insert(tk.END,ts_s+ms_s+'  ','ts')
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
            root.after(80,_poll)

        def on_close():
            with _WIN_REG_LOCK: _WIN_REGISTRY['log']={'root':root,'open':False}
            root.destroy()
        root.protocol('WM_DELETE_WINDOW',on_close)
        root.after(200,_poll)
        root.mainloop()

    t=threading.Thread(target=_worker,daemon=True,name='LogWin')
    t.start()

# ══════════════════════════════════════════════
#  GUI 窗口 2：在线帧编辑器
# ══════════════════════════════════════════════
def open_editor_window():
    if not HAS_TK: return
    with _WIN_REG_LOCK:
        w=_WIN_REGISTRY.get('editor',{})
        if w.get('open'): return

    def _worker():
        root=tk.Tk()
        with _WIN_REG_LOCK:
            _WIN_REGISTRY['editor']={'root':root,'open':True}
        root.title('🔧 在线帧编辑器  —  CAN Sender v3.0')
        root.geometry('1060x520')
        root.configure(bg='#0d0d0d')

        tk.Label(root,text='在线帧编辑器  —  修改后实时生效到发送线程',
                 bg='#0d0d0d',fg='#0cf',font=('Consolas',11,'bold')).pack(pady=(8,4))
        tk.Label(root,text='双击数据格子可编辑（输入十六进制，如 FF 或 0xAB）；启用开关和计算方法立即生效',
                 bg='#0d0d0d',fg='#666',font=('Consolas',9)).pack()

        # 滚动容器
        container=tk.Frame(root,bg='#0d0d0d'); container.pack(fill='both',expand=True,padx=6,pady=6)
        canvas=tk.Canvas(container,bg='#0d0d0d',highlightthickness=0)
        vsb=tk.Scrollbar(container,orient='vertical',command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side='right',fill='y'); canvas.pack(side='left',fill='both',expand=True)
        inner=tk.Frame(canvas,bg='#0d0d0d'); canvas.create_window((0,0),window=inner,anchor='nw')
        inner.bind('<Configure>',lambda e: canvas.configure(scrollregion=canvas.bbox('all')))

        # 表头
        cols=['备注/名称','ID','长度','实时数据','启用','计算方法']
        widths=[160,60,40,420,50,200]
        for c,(col,w) in enumerate(zip(cols,widths)):
            tk.Label(inner,text=col,bg='#111',fg='#0cf',font=('Consolas',9,'bold'),
                     width=w//8,relief='flat',bd=1).grid(row=0,column=c,padx=1,pady=1,sticky='ew')

        row_refs=[]

        def _make_row(r, aid):
            meta=FRAME_META.get(aid,('?','?'))
            with FRAME_BASE_LOCK: bd=bytearray(FRAME_BASE_DATA.get(aid,b'\x00'*8))
            with FRAME_ENABLED_LOCK: en=FRAME_ENABLED.get(aid,True)

            # 备注
            tk.Label(inner,text=meta[0],bg='#0d0d0d',fg='#0cf',font=('Consolas',9),
                     anchor='w',width=20).grid(row=r,column=0,padx=1,pady=1,sticky='ew')
            # ID
            tk.Label(inner,text=f'0x{aid:03X}',bg='#0d0d0d',fg='#fa0',font=('Consolas',9),
                     width=7).grid(row=r,column=1,padx=1,pady=1)
            # 长度
            tk.Label(inner,text=str(len(bd)),bg='#0d0d0d',fg='#888',font=('Consolas',9),
                     width=4).grid(row=r,column=2,padx=1,pady=1)

            # 数据格子（每个byte一个Entry）
            data_frame=tk.Frame(inner,bg='#0d0d0d')
            data_frame.grid(row=r,column=3,padx=1,pady=1,sticky='w')
            byte_vars=[]
            for i,b in enumerate(bd):
                bv=tk.StringVar(value=f'{b:02X}')
                e=tk.Entry(data_frame,textvariable=bv,width=3,bg='#1a1a1a',fg='#0f0',
                           insertbackground='white',font=('Consolas',9),relief='flat',
                           justify='center')
                e.grid(row=0,column=i,padx=1)
                byte_vars.append(bv)
                def _on_change(var=bv,idx=i,frame_id=aid):
                    try:
                        val=int(var.get().strip().lstrip('0x').lstrip('0X'),16)
                        val=max(0,min(255,val))
                        with FRAME_BASE_LOCK:
                            if idx<len(FRAME_BASE_DATA[frame_id]):
                                FRAME_BASE_DATA[frame_id][idx]=val
                    except: pass
                bv.trace_add('write',lambda *a,f=_on_change: f())

            # 实时数据显示标签（每100ms更新）
            live_var=tk.StringVar(value='—')
            tk.Label(data_frame,text='→',bg='#0d0d0d',fg='#555',font=('Consolas',8)).grid(row=0,column=len(bd),padx=(6,2))
            tk.Label(data_frame,textvariable=live_var,bg='#0d0d0d',fg='#0a0',font=('Consolas',8),
                     width=28,anchor='w').grid(row=0,column=len(bd)+1,sticky='w')

            # 启用开关（改成按钮，状态更直观，也避免Checkbutton不同环境下点击不生效）
            en_v=tk.BooleanVar(value=en)
            def _sync_enable_btn(btn=None, var=en_v, frame_id=aid):
                state = get_frame_enabled(frame_id)
                var.set(state)
                if btn is not None:
                    btn.configure(text='ON' if state else 'OFF',
                                  bg='#003300' if state else '#330000',
                                  fg='#0f0' if state else '#f44')
            def _toggle(frame_id=aid, var=en_v):
                set_frame_enabled(frame_id, not get_frame_enabled(frame_id))
                _sync_enable_btn(enable_btn, var, frame_id)
            enable_btn = tk.Button(inner,text='ON' if en else 'OFF',command=_toggle,
                                   width=5,bg='#003300' if en else '#330000',
                                   fg='#0f0' if en else '#f44',relief='flat',font=('Consolas',8,'bold'))
            enable_btn.grid(row=r,column=4,padx=1,pady=1)

            # 计算方法标签（只读信息）
            tk.Label(inner,text=meta[1][:30]+'…' if len(meta[1])>30 else meta[1],
                     bg='#0d0d0d',fg='#555',font=('Consolas',8),anchor='w',
                     width=28).grid(row=r,column=5,padx=2,sticky='w')

            row_refs.append((aid,byte_vars,live_var,en_v,enable_btn))

        for r,aid in enumerate(DISPLAY_ORDER,start=1):
            _make_row(r,aid)

        # 实时刷新显示数据（只刷新live_var，不覆盖用户正在编辑的格子）
        def _refresh_live():
            if not _WIN_REGISTRY.get('editor',{}).get('open',False): return
            with S['frame_lock']: snap=dict(S['frame_state'])
            for aid,byte_vars,live_var,en_v,enable_btn in row_refs:
                state = get_frame_enabled(aid)
                en_v.set(state)
                enable_btn.configure(text='ON' if state else 'OFF',
                                     bg='#003300' if state else '#330000',
                                     fg='#0f0' if state else '#f44')
                if not state:
                    live_var.set('⛔ DISABLED')
                elif aid in snap:
                    d=snap[aid]['data']
                    live_var.set(' '.join(f'{b:02X}' for b in d))
                else:
                    live_var.set('—')
            root.after(100,_refresh_live)

        # 底部按钮
        bot=tk.Frame(root,bg='#0d0d0d'); bot.pack(fill='x',padx=6,pady=4)
        def reset_all():
            if messagebox.askyesno('确认','重置所有帧数据到默认值？'):
                pass  # TODO: 保存默认值备份，此版本不重置
        def set_all_enabled(val):
            for a in RESERVED_IDS:
                set_frame_enabled(a, val)
            for aid,_,_,en_v,enable_btn in row_refs:
                en_v.set(val)
                enable_btn.configure(text='ON' if val else 'OFF',
                                     bg='#003300' if val else '#330000',
                                     fg='#0f0' if val else '#f44')

        tk.Button(bot,text='全部启用',command=lambda:set_all_enabled(True),
                  bg='#003300',fg='#0f0',relief='flat',font=('Consolas',9),padx=8).pack(side='left',padx=4)
        tk.Button(bot,text='全部禁用',command=lambda:set_all_enabled(False),
                  bg='#330000',fg='#f44',relief='flat',font=('Consolas',9),padx=8).pack(side='left',padx=4)
        tk.Label(bot,text='⚠ 修改只在本次运行有效',bg='#0d0d0d',fg='#555',
                 font=('Consolas',8)).pack(side='right',padx=8)

        def on_close():
            # 检查是否有修改
            modified=False
            for aid in DISPLAY_ORDER:
                orig=FRAME_BASE_DATA.get(aid)
                if orig: modified=True; break
            ans=messagebox.askyesno('关闭编辑器','当前修改仅在本次运行有效，关闭后不保存。确认关闭？') if modified else True
            if ans:
                with _WIN_REG_LOCK: _WIN_REGISTRY['editor']={'root':root,'open':False}
                root.destroy()

        root.protocol('WM_DELETE_WINDOW',on_close)
        root.after(200,_refresh_live)
        root.mainloop()

    t=threading.Thread(target=_worker,daemon=True,name='EditorWin')
    t.start()

# ══════════════════════════════════════════════
#  GUI 窗口 3：历史筛选
# ══════════════════════════════════════════════
def open_history_window():
    if not HAS_TK: return
    with _WIN_REG_LOCK:
        w=_WIN_REGISTRY.get('history',{})
        if w.get('open'): return

    def _worker():
        root=tk.Tk()
        with _WIN_REG_LOCK:
            _WIN_REGISTRY['history']={'root':root,'open':True}
        root.title('🔍 历史帧筛选  —  CAN Sender v3.0')
        root.geometry('900x600')
        root.configure(bg='#0d0d0d')

        # 筛选区
        sf=tk.LabelFrame(root,text='筛选条件',bg='#0d0d0d',fg='#0cf',
                         font=('Consolas',10),relief='flat'); sf.pack(fill='x',padx=8,pady=(6,2))
        r1=tk.Frame(sf,bg='#0d0d0d'); r1.pack(fill='x',padx=4,pady=2)
        tk.Label(r1,text='ID (逗号分隔，空=全部):',bg='#0d0d0d',fg='#aaa',font=('Consolas',9)).pack(side='left')
        id_v=tk.StringVar()
        tk.Entry(r1,textvariable=id_v,width=30,bg='#1a1a1a',fg='#fa0',
                 insertbackground='white',font=('Consolas',9),relief='flat').pack(side='left',padx=4)
        tk.Label(r1,text='数据包含 (HEX):',bg='#0d0d0d',fg='#aaa',font=('Consolas',9)).pack(side='left',padx=(10,0))
        data_v=tk.StringVar()
        tk.Entry(r1,textvariable=data_v,width=24,bg='#1a1a1a',fg='#0f0',
                 insertbackground='white',font=('Consolas',9),relief='flat').pack(side='left',padx=4)
        r2=tk.Frame(sf,bg='#0d0d0d'); r2.pack(fill='x',padx=4,pady=2)
        tk.Label(r2,text='时间范围 (最近N秒，0=全部):',bg='#0d0d0d',fg='#aaa',font=('Consolas',9)).pack(side='left')
        time_v=tk.StringVar(value='0')
        tk.Entry(r2,textvariable=time_v,width=8,bg='#1a1a1a',fg='#aaa',
                 font=('Consolas',9),relief='flat').pack(side='left',padx=4)
        only_fixed=tk.BooleanVar(value=False)
        tk.Checkbutton(r2,text='仅固定帧',variable=only_fixed,bg='#0d0d0d',fg='#0cf',
                       selectcolor='#1a1a1a',activebackground='#0d0d0d',font=('Consolas',9)).pack(side='left',padx=8)
        only_fuzz=tk.BooleanVar(value=False)
        tk.Checkbutton(r2,text='仅Fuzz帧',variable=only_fuzz,bg='#0d0d0d',fg='#f8f',
                       selectcolor='#1a1a1a',activebackground='#0d0d0d',font=('Consolas',9)).pack(side='left',padx=4)
        res_v=tk.StringVar(value='')
        tk.Label(r2,textvariable=res_v,bg='#0d0d0d',fg='#888',font=('Consolas',9)).pack(side='right',padx=8)

        # 结果区
        rf=tk.Frame(root,bg='#0d0d0d'); rf.pack(fill='both',expand=True,padx=8,pady=(2,8))
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
            # 解析筛选条件
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
                if only_fixed.get() and aid not in RESERVED_IDS: continue
                if only_fuzz.get() and aid in RESERVED_IDS: continue
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
            res_v.set(f'共 {count} 条结果（最多显示2000条，按时间倒序）')

        tk.Button(r2,text='搜索',command=do_search,bg='#003344',fg='#0cf',
                  relief='flat',font=('Consolas',9,'bold'),padx=10).pack(side='left',padx=(20,0))
        tk.Button(r2,text='清空结果',command=lambda:[txt.configure(state='normal'),txt.delete('1.0',tk.END),txt.configure(state='disabled')],
                  bg='#1a1a1a',fg='#888',relief='flat',font=('Consolas',9),padx=6).pack(side='left',padx=4)

        def on_close():
            with _WIN_REG_LOCK: _WIN_REGISTRY['history']={'root':root,'open':False}
            root.destroy()
        root.protocol('WM_DELETE_WINDOW',on_close)
        root.mainloop()

    t=threading.Thread(target=_worker,daemon=True,name='HistoryWin')
    t.start()

# ══════════════════════════════════════════════
#  GUI 窗口 4：PCAN占用查看（开机后立即弹出）
# ══════════════════════════════════════════════
def get_pcan_users():
    """查询哪些进程在使用PCAN设备（通过handle/DLL占用）"""
    results=[]
    try:
        # 方法1：通过 tasklist 找包含 PCAN 相关DLL的进程
        out=subprocess.check_output(
            ['powershell','-Command',
             'Get-Process | Where-Object {$_.Modules.FileName -like "*PCAN*"} | '
             'Select-Object Id,ProcessName,MainWindowTitle | Format-Table -AutoSize'],
            creationflags=0x08000000, timeout=5, stderr=subprocess.DEVNULL
        ).decode('gbk','ignore')
        for line in out.splitlines():
            line=line.strip()
            if line and not line.startswith('Id') and not line.startswith('-'):
                results.append(line)
    except: pass
    try:
        # 方法2：通过 handle.exe 或 wmic 查设备
        out2=subprocess.check_output(
            ['powershell','-Command',
             'Get-WmiObject Win32_Process | Where-Object {$_.CommandLine -like "*PCAN*" -or $_.Name -like "*pcan*"} | '
             'Select-Object ProcessId,Name,CommandLine | Format-Table -AutoSize'],
            creationflags=0x08000000, timeout=5, stderr=subprocess.DEVNULL
        ).decode('gbk','ignore')
        for line in out2.splitlines():
            line=line.strip()
            if line and not line.startswith('Process') and not line.startswith('-') and line not in results:
                results.append(line)
    except: pass
    return results if results else ['未检测到其他程序占用PCAN（或需要管理员权限）']

def open_pcan_info_window():
    if not HAS_TK: return
    with _WIN_REG_LOCK:
        w=_WIN_REGISTRY.get('pcan',{})
        if w.get('open'): return

    def _worker():
        root=tk.Tk()
        with _WIN_REG_LOCK:
            _WIN_REGISTRY['pcan']={'root':root,'open':True}
        root.title('🔌 PCAN 占用查看  —  CAN Sender v3.0')
        root.geometry('760x420')
        root.configure(bg='#0d0d0d')

        tk.Label(root,text='PCAN 设备占用情况',bg='#0d0d0d',fg='#0cf',
                 font=('Consolas',12,'bold')).pack(pady=(10,4))
        tk.Label(root,text='显示当前 Windows 中哪些程序正在使用 PCAN 相关驱动/DLL',
                 bg='#0d0d0d',fg='#555',font=('Consolas',9)).pack()

        fr=tk.Frame(root,bg='#0d0d0d'); fr.pack(fill='both',expand=True,padx=10,pady=8)
        sb=tk.Scrollbar(fr); sb.pack(side='right',fill='y')
        txt=tk.Text(fr,bg='#0d0d0d',fg='#0f0',font=('Consolas',10),
                    yscrollcommand=sb.set,relief='flat',state='disabled')
        txt.pack(fill='both',expand=True); sb.config(command=txt.yview)
        txt.tag_config('hd',foreground='#0cf',font=('Consolas',10,'bold'))
        txt.tag_config('ok',foreground='#0f0')
        txt.tag_config('warn',foreground='#fa0')
        txt.tag_config('gray',foreground='#555')

        status_v=tk.StringVar(value='点击"刷新"扫描...')
        tk.Label(root,textvariable=status_v,bg='#0d0d0d',fg='#888',font=('Consolas',9)).pack()

        bot=tk.Frame(root,bg='#0d0d0d'); bot.pack(fill='x',padx=10,pady=(0,8))

        def do_refresh():
            status_v.set('扫描中...')
            root.update()
            lines=get_pcan_users()
            txt.configure(state='normal'); txt.delete('1.0',tk.END)
            txt.insert(tk.END,f'扫描时间: {time.strftime("%Y-%m-%d %H:%M:%S")}\n','hd')
            txt.insert(tk.END,'─'*80+'\n','gray')
            for line in lines:
                tag='warn' if any(kw in line.lower() for kw in ['pcan','explorer','python','peak']) else 'ok'
                txt.insert(tk.END,line+'\n',tag)
            txt.insert(tk.END,'\n─'*40+'\n','gray')
            txt.insert(tk.END,'提示: PCAN Explorer、Python(本程序)均会显示\n','gray')
            txt.configure(state='disabled')
            status_v.set(f'扫描完成，共 {len(lines)} 条')

        tk.Button(bot,text='刷新',command=do_refresh,bg='#003344',fg='#0cf',
                  relief='flat',font=('Consolas',10,'bold'),padx=12).pack(side='left')
        tk.Button(bot,text='自动刷新(5s)',
                  command=lambda:root.after(5000,do_refresh),
                  bg='#1a1a1a',fg='#888',relief='flat',font=('Consolas',9),padx=8).pack(side='left',padx=6)

        def on_close():
            with _WIN_REG_LOCK: _WIN_REGISTRY['pcan']={'root':root,'open':False}
            root.destroy()
        root.protocol('WM_DELETE_WINDOW',on_close)

        # 启动时自动扫描一次
        root.after(300,do_refresh)
        root.mainloop()

    t=threading.Thread(target=_worker,daemon=True,name='PcanWin')
    t.start()

# ══════════════════════════════════════════════
#  模式C GUI（保持原有）
# ══════════════════════════════════════════════
def open_mode_c_gui(bus, threads):
    if not HAS_TK: return
    with S['gui_lock']:
        if S['gui_open']: return
        S['gui_open']=True
    def gui_worker():
        root=None
        try:
            root=tk.Tk(); root.title('模式C - 动态ID管理')
            root.geometry('560x440'); root.resizable(False,False)
            root.configure(bg='#0d0d0d')
            sv=tk.StringVar(value='GUI已打开，后台帧继续发送。')
            F=tk.Frame(root,bg='#0d0d0d',padx=12,pady=12); F.pack(fill='both',expand=True)
            def Lbl(**kw): return tk.Label(F,bg='#0d0d0d',fg='#aaa',font=('Consolas',10),**kw)
            def Ent(): return tk.Entry(F,bg='#1a1a1a',fg='#0f0',insertbackground='white',font=('Consolas',10),relief='flat')
            def Btn(**kw): return tk.Button(F,bg='#1a2a1a',fg='#0f0',relief='flat',font=('Consolas',10),**kw)
            Lbl(text='模式C：动态增加/删除 Fuzz 目标 ID',font=('Consolas',11,'bold'),fg='#0cf').pack(anchor='w',pady=(0,8))
            Lbl(text='新增ID（逗号分隔）:').pack(anchor='w')
            ea=Ent(); ea.pack(fill='x',pady=(2,8))
            Lbl(text='删除ID（逗号分隔）:').pack(anchor='w')
            ed=Ent(); ed.pack(fill='x',pady=(2,8))
            Lbl(text='当前目标ID列表:').pack(anchor='w')
            lb=tk.Listbox(F,bg='#1a1a1a',fg='#0f0',font=('Consolas',10),height=8,relief='flat')
            lb.pack(fill='both',expand=True,pady=(2,8))
            br=tk.Frame(F,bg='#0d0d0d'); br.pack(fill='x',pady=(0,6))
            def refresh():
                if not S['running'].is_set():
                    try: root.destroy()
                    except: pass
                    return
                lb.delete(0,tk.END)
                for aid in get_fuzz_ids(): lb.insert(tk.END,f'0x{aid:03X}')
                root.after(300,refresh)
            def apply():
                msgs=[]
                try:
                    for aid in parse_hex_id_list(ed.get()):
                        ok,m=mode_c_remove_target(aid); msgs.append(('✓ ' if ok else '· ')+m)
                    for aid in parse_hex_id_list(ea.get()):
                        ok,m=mode_c_add_target(bus,threads,aid); msgs.append(('✓ ' if ok else '· ')+m)
                except ValueError as ex: sv.set(f'输入错误: {ex}'); return
                sv.set(' | '.join(msgs) if msgs else '无改动')
                ea.delete(0,tk.END); ed.delete(0,tk.END); refresh()
            def on_close():
                with S['gui_lock']: S['gui_open']=False; S['gui_thread']=None
                root.destroy()
            Btn(text='应用修改',command=apply).pack(side='left',padx=(0,6))
            Btn(text='关闭',command=on_close).pack(side='right')
            tk.Label(F,textvariable=sv,bg='#0d0d0d',fg='#a00',font=('Consolas',9),
                     wraplength=520,justify='left').pack(anchor='w')
            root.protocol('WM_DELETE_WINDOW',on_close); refresh(); root.mainloop()
        except: pass
        finally:
            with S['gui_lock']: S['gui_open']=False; S['gui_thread']=None
            if root:
                try: root.destroy()
                except: pass
    t=threading.Thread(target=gui_worker,daemon=True,name='ModeCGUI')
    with S['gui_lock']: S['gui_thread']=t
    t.start()

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
    print(f"{CYAN}║{RESET}       {YELLOW}CAN Bus Fuzzing & Analysis Tool  v3.0{RESET}                      {CYAN}║{RESET}")
    print(f"{CYAN}║{RESET}       {GRAY}GitHub: JackieZ123430  |  BMW F/G Series  |  SAE-J1850{RESET}      {CYAN}║{RESET}")
    print(f"{CYAN}╠══════════════════════════════════════════════════════════════════════╣{RESET}")
    print(f"{CYAN}║{RESET}  {BOLD}{YELLOW}全局按键{RESET}                                                          {CYAN}║{RESET}")
    print(f"{CYAN}║{RESET}   {GREEN}M{RESET} 打标记   {GREEN}S{RESET} 保存回菜单   {GREEN}Q{RESET} 保存退出   {RED}ALT+Q{RESET} 不保存退出       {CYAN}║{RESET}")
    print(f"{CYAN}║{RESET}   {GREEN}W{RESET} 重开日志窗口                                                  {CYAN}║{RESET}")
    print(f"{CYAN}║{RESET}                                                                      {CYAN}║{RESET}")
    print(f"{CYAN}║{RESET}  {BOLD}{YELLOW}4个GUI窗口（进入模式后自动弹出）{RESET}                                  {CYAN}║{RESET}")
    print(f"{CYAN}║{RESET}   {CYAN}📡 实时发送日志{RESET}  终端滚动，下拉筛选全部/指定ID，支持过滤        {CYAN}║{RESET}")
    print(f"{CYAN}║{RESET}   {CYAN}🔧 在线帧编辑器{RESET}  修改固定帧数据/启用开关，实时生效到发送        {CYAN}║{RESET}")
    print(f"{CYAN}║{RESET}   {CYAN}🔍 历史帧筛选  {RESET}  按ID/数据/时间筛选历史发送记录               {CYAN}║{RESET}")
    print(f"{CYAN}║{RESET}   {CYAN}🔌 PCAN占用查看{RESET}  {RED}开机画面后立即弹出{RESET}，查看哪些程序在用PCAN      {CYAN}║{RESET}")
    print(f"{CYAN}║{RESET}                                                                      {CYAN}║{RESET}")
    print(f"{CYAN}║{RESET}  {BOLD}{YELLOW}模式C: {GREEN}P{RESET}暂停帧 {GREEN}[{RESET}冻结随机 {GREEN}9{RESET}冻结模式 {GREEN}A{RESET}ID管理GUI  {BOLD}{YELLOW}模式B: {GREEN}N{RESET}换ID   {CYAN}║{RESET}")
    print(f"{CYAN}║{RESET}  {BOLD}{YELLOW}模式D: {GREEN}T{RESET}自动/手动 {GREEN}+/-{RESET}速度 {GREEN}P{RESET}暂停 {GREEN}1~4{RESET}策略 {GREEN}Y/N/L/B/[{RESET}操作     {CYAN}║{RESET}")
    print(f"{CYAN}╚══════════════════════════════════════════════════════════════════════╝{RESET}")
    print()

    # 弹出PCAN占用窗口（开机后立即）
    open_pcan_info_window()

    input(f"  {GRAY}按回车键进入主菜单...{RESET}")

# ──────────────────────────────────────────────
#  菜单
# ──────────────────────────────────────────────
def select_mode():
    global S
    S=make_fresh_session()
    os.system("cls")
    print(f"{BOLD}{CYAN}{'═'*62}{RESET}")
    print(f"{BOLD}{WHITE}  PCAN Sender v3.0  ·  GitHub: JackieZ123430{RESET}")
    print(f"{BOLD}{CYAN}{'═'*62}{RESET}\n")
    print(f"{BOLD}【发送格式】{RESET}")
    print(f"  {YELLOW}1{RESET}  byte[0]=Counter  byte[1~7]=随机  {GRAY}(默认){RESET}")
    print(f"  {YELLOW}2{RESET}  byte[0]=CRC  byte[1]=Counter  byte[2~7]=随机")
    print(f"  {YELLOW}3{RESET}  全部纯随机")
    print(f"  {YELLOW}4{RESET}  无CRC无Counter，纯固定payload\n")
    while True:
        v=input("发送格式 (1/2/3/4，回车=1): ").strip() or '1'
        if v in ('1','2','3','4'): S['FUZZ_FORMAT']=int(v); break
        print(f"{RED}请输入 1~4{RESET}")
    print(f"\n{BOLD}【工作模式】{RESET}")
    print(f"  {YELLOW}A{RESET}  全局随机ID")
    print(f"  {YELLOW}B{RESET}  随机N个ID定向  {GRAY}[N]换ID{RESET}")
    print(f"  {YELLOW}C{RESET}  手动指定ID定向  {GRAY}[P]暂停 [[]冻结 [9]冻结模式 [A]GUI [O]自动换ID [E]改数量{RESET}")
    print(f"  {YELLOW}D{RESET}  逐Byte穷举  {GRAY}支持标准/分组/联扫/加速手动，自动/手动递增{RESET}")
    print(f"  {YELLOW}E{RESET}  算法检测  {GRAY}对指定ID测试多种CRC/Counter算法组合{RESET}\n")
    while True:
        c=input("模式 (A/B/C/D/E): ").strip().upper()
        if c in ('A','B','C','D','E'): S['APP_MODE']=c; break
    if S['APP_MODE']=='B':
        while True:
            try:
                n=input("\n随机ID数量 (默认3): ").strip()
                count=int(n) if n else 3
                if 1<=count<=20: break
            except ValueError: pass
            print(f"{RED}1~20{RESET}")
        while len(S['FUZZ_TARGET_IDS'])<count:
            aid=random.randint(0x001,0x7FF)
            if aid not in RESERVED_IDS and aid not in S['FUZZ_TARGET_IDS']:
                S['FUZZ_TARGET_IDS'].append(aid)
        print(f"\n{GREEN}IDs: {', '.join(f'0x{i:03X}' for i in S['FUZZ_TARGET_IDS'])}{RESET}")
        time.sleep(1.2)
    elif S['APP_MODE']=='C':
        while True:
            val=input("\n目标ID（逗号分隔，如 21D,1F3）: ").strip()
            try:
                aids=parse_hex_id_list(val)
                if aids: S['FUZZ_TARGET_IDS']=aids; break
                print(f"{RED}请至少输入一个ID{RESET}")
            except ValueError as e: print(f"{RED}{e}{RESET}")
        print(f"\n{GREEN}目标: {', '.join(f'0x{i:03X}' for i in S['FUZZ_TARGET_IDS'])}{RESET}")
        time.sleep(1)
    elif S['APP_MODE']=='E':
        while True:
            val=input('\n算法检测目标ID（如 21D）: ').strip().upper()
            try:
                p=val[2:] if val.startswith('0X') else val
                if not p: raise ValueError()
                aid=int(p,16)
                if 0<=aid<=0x7FF: break
            except (ValueError,IndexError): pass
            print(f'{RED}请输入有效16进制ID{RESET}')
        while True:
            try:
                n=input('帧长度 (默认8): ').strip()
                fl=int(n) if n else 8
                if 1<=fl<=8: break
            except ValueError: pass
            print(f'{RED}1~8{RESET}')
        with S['mode_e']['lock']:
            S['mode_e']['target_id']=aid; S['mode_e']['length']=fl
        print(f'\n{GREEN}目标: 0x{aid:03X}  帧长: {fl}{RESET}')
        print(f'{YELLOW}进入后: Y=有反应 N=无反应 空格/S=跳过 Q=退出{RESET}')
        time.sleep(1.5)

    elif S['APP_MODE']=='D':
        while True:
            val=input('\n穷举目标ID（如 21D）: ').strip().upper()
            try:
                p=val[2:] if val.startswith('0X') else val
                if not p: raise ValueError()
                aid=int(p,16)
                if 0<=aid<=0x7FF: break
            except (ValueError,IndexError): pass
            print(f"{RED}请输入有效16进制ID (000~7FF){RESET}")
        while True:
            try:
                n=input("帧长度 (默认8): ").strip()
                fl=int(n) if n else 8
                if 1<=fl<=8: break
            except ValueError: pass
            print(f"{RED}1~8{RESET}")
        D=S['mode_d']
        D['target_id']=aid; D['length']=fl
        D['status']=f'准备穷举 0x{aid:03X}，帧长={fl}，从byte[1]开始'
        print(f"\n{GREEN}目标: 0x{aid:03X}  帧长: {fl}{RESET}")
        print(f"{YELLOW}进入后: T=切换自动/手动  1/2/3/4=切换扫描策略  += 加速  -= 减速{RESET}")
        time.sleep(1.5)

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
    S['running'].set()
    builders=_make_fixed_builders()
    threads=[
        CyclicSender(bus,0x03C,builders[0x03C],0.075,"03C"),
        CyclicSender(bus,0x0F3,builders[0x0F3],0.075,"0F3"),
        CyclicSender(bus,0x1A1,builders[0x1A1],0.020,"1A1"),
        CyclicSender(bus,0x0AB,builders[0x0AB],0.075,"0AB"),
        CyclicSender(bus,0x2A7,builders[0x2A7],0.075,"2A7"),
        CyclicSender(bus,0x2EC,builders[0x2EC],0.075,"2EC"),
        CyclicSender(bus,0x289,builders[0x289],0.075,"289"),
        CyclicSender(bus,0x294,builders[0x294],0.075,"294"),
        CyclicSender(bus,0x30B,builders[0x30B],0.075,"30B"),
        CyclicSender(bus,0x369,builders[0x369],0.075,"369"),
        CyclicSender(bus,0x36F,builders[0x36F],0.075,"36F"),
        CyclicSender(bus,0x3A0,builders[0x3A0],0.075,"3A0"),
        CyclicSender(bus,0x3D8,builders[0x3D8],0.075,"3D8"),
        CyclicSender(bus,0x3FD,builders[0x3FD],0.075,"3FD"),
        CyclicSender(bus,0x510,builders[0x510],0.075,"510"),
    ]
    mode=S['APP_MODE']
    if mode=='A':
        threads.append(CyclicSender(bus,-1,builders['rnd_a'],RANDOM_CYCLE_S,"RND"))
    elif mode=='B':
        for i in range(len(S['FUZZ_TARGET_IDS'])):
            def _mk(idx):
                ctr=make_counter(0,255,wrap=0)
                def bld():
                    with S['fuzz_lock']:
                        tid=S['FUZZ_TARGET_IDS'][idx] if idx<len(S['FUZZ_TARGET_IDS']) else 0x000
                    if not tid: return 0x000,b'\x00'*8
                    d=bytearray(8); fmt=S['FUZZ_FORMAT']
                    if fmt==1: d[0]=ctr(); [d.__setitem__(j,random.randint(0,0xFF)) for j in range(1,8)]
                    elif fmt==2:
                        d[1]=ctr()
                        for j in range(2,8): d[j]=random.randint(0,0xFF)
                        d[0]=crc8(bytes(d[1:8]),0xFF,0x00)
                    else: [d.__setitem__(j,random.randint(0,0xFF)) for j in range(8)]
                    return tid,bytes(d)
                return bld
            threads.append(CyclicSender(bus,-1,_mk(i),RANDOM_CYCLE_S,f"FZ_{i}"))
    elif mode=='C':
        for tid in S['FUZZ_TARGET_IDS']:
            with S['slot_lock']:
                idx=S['next_slot'][0]; S['next_slot'][0]+=1
                slot={'slot':idx,'id':tid,'active':True}
                S['slot_states'].append(slot)
            threads.append(CyclicSender(bus,-1,make_slot_builder(slot),RANDOM_CYCLE_S,f"FZC_{idx}"))
        threading.Thread(target=mode_c_auto_switch_worker,args=(bus,threads),daemon=True,name='CAutoSw').start()
    elif mode=='D':
        threading.Thread(target=mode_d_worker,args=(bus,),daemon=True,name='ModeD').start()
    elif mode=='E':
        threading.Thread(target=mode_e_worker,args=(bus,),daemon=True,name='ModeE').start()

    for t in threads: t.start()
    threading.Thread(target=monitor_loop,daemon=True).start()

    _open_cmd_log_named_pipe()
    open_editor_window()
    open_history_window()
    open_log_window()

    result='menu'
    while True:
        if not msvcrt.kbhit(): time.sleep(0.02); continue
        ch=msvcrt.getwch().upper()
        alt_held=_check_alt_q()

        if mode=='D':
            D=S['mode_d']
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
            elif ch=='M': do_mark()
            elif ch=='W': open_log_window(); open_editor_window(); open_history_window()
            elif ch=='S':
                S['running'].clear(); time.sleep(0.3)
                _close_all_session_windows()
                _close_cmd_log(); os.system("cls"); save_log(); result='menu'; break
            elif ch=='Q':
                if alt_held:
                    S['running'].clear(); time.sleep(0.3)
                    _close_all_session_windows()
                    _close_cmd_log(); os.system("cls")
                    print(f"{YELLOW}已退出（未保存日志）{RESET}\n")
                    result='quit_nosave'; break
                else:
                    S['running'].clear(); time.sleep(0.3)
                    _close_all_session_windows()
                    _close_cmd_log(); os.system("cls"); save_log(); result='quit'; break
            continue

        if mode=='E':
            E=S['mode_e']
            with E['lock']:
                e_waiting=E['waiting_key']
                e_done=E['done']
            if ch in ('Y','N',' '):
                if e_waiting:
                    with E['lock']:
                        E['_action']={'Y':'yes','N':'no',' ':'skip'}[ch]
                    E['event'].set()
                    continue
            elif ch=='Q' and e_waiting and not e_done:
                with E['lock']:
                    E['_action']='quit'
                E['event'].set()
                continue

        if   ch=='M': do_mark()
        elif ch=='W': open_log_window(); open_editor_window(); open_history_window()
        elif ch=='S':
            S['running'].clear(); time.sleep(0.3)
            _close_all_session_windows()
            _close_cmd_log()
            os.system("cls"); save_log(); result='menu'; break
        elif ch=='Q':
            if alt_held:
                S['running'].clear(); time.sleep(0.3)
                _close_all_session_windows()
                _close_cmd_log()
                os.system("cls")
                print(f"{YELLOW}已退出（未保存日志）{RESET}\n")
                result='quit_nosave'; break
            else:
                S['running'].clear(); time.sleep(0.3)
                _close_all_session_windows()
                _close_cmd_log()
                os.system("cls"); save_log(); result='quit'; break
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
            elif ch=='O':
                S['c_auto_switch']=not S['c_auto_switch']
                st='已开启' if S['c_auto_switch'] else '已关闭'
                sys.stdout.write(f'\033[62;0H{CYAN}  ★ 自动切换ID {st} ({S["c_switch_interval"]:.0f}s/次){RESET}\n'); sys.stdout.flush()
            elif ch=='E':
                ask_mode_c_change_count(bus,threads)

    for t in threads: t.join(timeout=0.5)
    return result

# ──────────────────────────────────────────────
#  主入口
# ──────────────────────────────────────────────
def main():
    global ADAPTER_TYPE, CHANNEL, BUS_TYPE
    show_splash()
    os.system("cls")
    # 适配器选择
    select_adapter()
    os.system("cls")
    print(f"{BOLD}{CYAN}CAN Sender 连接中... [{BUS_TYPE}:{CHANNEL}]{RESET}\n")
    try:
        bus=can.Bus(interface=BUS_TYPE,channel=CHANNEL,bitrate=BITRATE)
    except Exception as e:
        print(f"{RED}连接失败: {e}{RESET}")
        print("请检查: 1) 驱动已安装  2) 设备已连接  3) CHANNEL配置正确")
        input("按回车退出"); return
    print(f"{GREEN}已连接 {CHANNEL} @ {BITRATE//1000}kbps{RESET}\n")
    time.sleep(0.5)

    while True:
        select_mode()
        os.system("cls")
        result=run_session(bus)
        if result in ('quit','quit_nosave'): break

    bus.shutdown()
    print(f"\n{CYAN}再见！  GitHub: JackieZ123430{RESET}\n")

if __name__=="__main__":
    main()
