import json
import os
from copy import deepcopy

DEFAULT_SETTINGS = {
    "menu_keepalive_enabled": True,
    "menu_keepalive_terminal_state": "ACC",
    "session_start_terminal_state": "ACC",
    "default_3a0_enabled": True,
    "default_speed_enabled": False,
    "default_speed_value": 0.0,
    "default_speed_jitter": 0.0,
    "default_rpm_enabled": False,
    "default_rpm_value": 0,
    "default_rpm_jitter": 50,
    "auto_open_log_window": True,
    "auto_open_editor_window": True,
    "auto_open_history_window": True,
    "auto_open_pcan_window": True,
    "auto_open_control_window": True,
    "show_splash": True,
    "modern_menu_enabled": True,
    "modern_startup_gui_enabled": False,
    "remember_last_choice": True,
    "last_fuzz_format": 1,
    "last_app_mode": "A",
    "last_mode_b_count": 3,
    "last_mode_c_ids": "",
    "last_mode_d_ids": "",
    "last_mode_d_length": 8,
    "frame_enabled_defaults": {},
}

TERMINAL_STATES = ("ACC", "IGN", "CUSTOM")


def merge_defaults(user_data: dict | None) -> dict:
    out = deepcopy(DEFAULT_SETTINGS)
    if isinstance(user_data, dict):
        for k, v in user_data.items():
            if k == "frame_enabled_defaults" and isinstance(v, dict):
                out[k].update(v)
            else:
                out[k] = v
    if out.get("menu_keepalive_terminal_state") not in TERMINAL_STATES:
        out["menu_keepalive_terminal_state"] = DEFAULT_SETTINGS["menu_keepalive_terminal_state"]
    if out.get("session_start_terminal_state") not in TERMINAL_STATES:
        out["session_start_terminal_state"] = DEFAULT_SETTINGS["session_start_terminal_state"]
    if out.get("last_app_mode") not in ("A", "B", "C", "D"):
        out["last_app_mode"] = DEFAULT_SETTINGS["last_app_mode"]
    try:
        out["last_fuzz_format"] = int(out.get("last_fuzz_format", 1))
    except Exception:
        out["last_fuzz_format"] = DEFAULT_SETTINGS["last_fuzz_format"]
    if out["last_fuzz_format"] not in (1, 2, 3, 4):
        out["last_fuzz_format"] = DEFAULT_SETTINGS["last_fuzz_format"]
    try:
        out["last_mode_b_count"] = int(out.get("last_mode_b_count", 3))
    except Exception:
        out["last_mode_b_count"] = DEFAULT_SETTINGS["last_mode_b_count"]
    if out["last_mode_b_count"] < 1:
        out["last_mode_b_count"] = DEFAULT_SETTINGS["last_mode_b_count"]
    try:
        out["last_mode_d_length"] = int(out.get("last_mode_d_length", 8))
    except Exception:
        out["last_mode_d_length"] = DEFAULT_SETTINGS["last_mode_d_length"]
    if out["last_mode_d_length"] < 1 or out["last_mode_d_length"] > 8:
        out["last_mode_d_length"] = DEFAULT_SETTINGS["last_mode_d_length"]
    return out


def load_settings(path: str) -> dict:
    if not os.path.exists(path):
        data = merge_defaults(None)
        save_settings(path, data)
        return data
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return merge_defaults(raw)
    except Exception:
        data = merge_defaults(None)
        save_settings(path, data)
        return data


def save_settings(path: str, settings: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(merge_defaults(settings), f, ensure_ascii=False, indent=2)


def toggle(settings: dict, key: str) -> bool:
    settings[key] = not bool(settings.get(key, False))
    return settings[key]
