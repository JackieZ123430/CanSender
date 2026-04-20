from persistent_settings import save_settings, toggle


def _ask_bool(prompt: str, default: bool) -> bool:
    suffix = 'Y/n' if default else 'y/N'
    while True:
        raw = input(f"{prompt} ({suffix}): ").strip().lower()
        if not raw:
            return default
        if raw in ('y', 'yes', '1'):
            return True
        if raw in ('n', 'no', '0'):
            return False
        print('请输入 y 或 n')


def _ask_int(prompt: str, default: int, min_v: int, max_v: int) -> int:
    while True:
        raw = input(f"{prompt} (默认{default}): ").strip()
        if not raw:
            return default
        try:
            v = int(raw)
            if min_v <= v <= max_v:
                return v
        except ValueError:
            pass
        print(f'请输入 {min_v}~{max_v}')


def _ask_float(prompt: str, default: float, min_v: float, max_v: float) -> float:
    while True:
        raw = input(f"{prompt} (默认{default}): ").strip()
        if not raw:
            return float(default)
        try:
            v = float(raw)
            if min_v <= v <= max_v:
                return v
        except ValueError:
            pass
        print(f'请输入 {min_v}~{max_v}')


def _ask_terminal(prompt: str, default: str) -> str:
    while True:
        raw = input(f"{prompt} [ACC/IGN/CUSTOM] (默认{default}): ").strip().upper()
        if not raw:
            return default
        if raw in ('ACC', 'IGN', 'CUSTOM'):
            return raw
        print('请输入 ACC / IGN / CUSTOM')


def edit_settings_interactive(settings: dict, settings_path: str) -> dict:
    while True:
        print('\n' + '=' * 72)
        print('设置中心')
        print('=' * 72)
        print(f"1  菜单保活               : {'开' if settings['menu_keepalive_enabled'] else '关'}")
        print(f"2  菜单保活 Terminal状态  : {settings['menu_keepalive_terminal_state']}")
        print(f"3  进入模式时Terminal状态 : {settings['session_start_terminal_state']}")
        print(f"4  默认3A0启用           : {'开' if settings['default_3a0_enabled'] else '关'}")
        print(f"5  默认车速设置           : {'开' if settings['default_speed_enabled'] else '关'} / {settings['default_speed_value']:.1f} / ±{settings['default_speed_jitter']:.1f}")
        print(f"6  默认转速设置           : {'开' if settings['default_rpm_enabled'] else '关'} / {settings['default_rpm_value']} / ±{settings['default_rpm_jitter']}")
        print(f"7  自动打开日志窗口       : {'开' if settings['auto_open_log_window'] else '关'}")
        print(f"8  自动打开在线帧编辑器   : {'开' if settings['auto_open_editor_window'] else '关'}")
        print(f"9  自动打开历史筛选窗口   : {'开' if settings['auto_open_history_window'] else '关'}")
        print(f"10 自动打开PCAN占用窗口   : {'开' if settings['auto_open_pcan_window'] else '关'}")
        print(f"11 自动打开会话控制中心   : {'开' if settings.get('auto_open_control_window', True) else '关'}")
        print(f"12 现代化主菜单           : {'开' if settings.get('modern_menu_enabled', True) else '关'}")
        print(f"13 记忆上次格式/模式      : {'开' if settings.get('remember_last_choice', True) else '关'}")
        print(f"14 启动现代GUI选择器      : {'开' if settings.get('modern_startup_gui_enabled', True) else '关'}")
        print('S  保存并返回')
        print('Q  放弃修改返回')
        choice = input('选择: ').strip().upper()

        if choice == '1':
            toggle(settings, 'menu_keepalive_enabled')
        elif choice == '2':
            settings['menu_keepalive_terminal_state'] = _ask_terminal('菜单保活 Terminal状态', settings['menu_keepalive_terminal_state'])
        elif choice == '3':
            settings['session_start_terminal_state'] = _ask_terminal('进入模式时 Terminal状态', settings['session_start_terminal_state'])
        elif choice == '4':
            toggle(settings, 'default_3a0_enabled')
        elif choice == '5':
            settings['default_speed_enabled'] = _ask_bool('启用默认车速设置', settings['default_speed_enabled'])
            settings['default_speed_value'] = _ask_float('默认车速 km/h', settings['default_speed_value'], 0.0, 300.0)
            settings['default_speed_jitter'] = _ask_float('默认车速抖动 km/h', settings['default_speed_jitter'], 0.0, 20.0)
        elif choice == '6':
            settings['default_rpm_enabled'] = _ask_bool('启用默认转速设置', settings['default_rpm_enabled'])
            settings['default_rpm_value'] = _ask_int('默认转速 rpm', settings['default_rpm_value'], 0, 9000)
            settings['default_rpm_jitter'] = _ask_int('默认转速抖动 rpm', settings['default_rpm_jitter'], 0, 1000)
        elif choice == '7':
            toggle(settings, 'auto_open_log_window')
        elif choice == '8':
            toggle(settings, 'auto_open_editor_window')
        elif choice == '9':
            toggle(settings, 'auto_open_history_window')
        elif choice == '10':
            toggle(settings, 'auto_open_pcan_window')
        elif choice == '11':
            toggle(settings, 'auto_open_control_window')
        elif choice == '12':
            toggle(settings, 'modern_menu_enabled')
        elif choice == '13':
            toggle(settings, 'remember_last_choice')
        elif choice == '14':
            toggle(settings, 'modern_startup_gui_enabled')
        elif choice == 'S':
            save_settings(settings_path, settings)
            print('设置已保存')
            return settings
        elif choice == 'Q':
            print('已放弃设置修改')
            return settings
