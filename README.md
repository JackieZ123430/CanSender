# PCAN CAN Sender & CRC Fuzzing Tool  
# PCAN CAN 发送与 CRC Fuzz 工具

A Python-based CAN sending, logging, profile editing, and fuzzing tool for bench-side CAN bus research.

一个基于 Python 的 CAN 发送、日志记录、配置编辑和 Fuzz 测试工具，主要用于架台环境下的 CAN 总线研究。

The current user interface is mainly in Chinese.

当前程序界面语言主要为中文。

This public version does not include vehicle-specific CAN IDs, payloads, online frames, or base frame data.

公开版本不包含具体车型的 CAN ID、payload、在线帧或基础帧数据。

Users can add their own local frame profiles for testing and research.

用户可以根据自己的测试需求添加本地帧配置文件。

---

## Features  
## 功能

- PCAN interface support through `python-can`  
  支持通过 `python-can` 使用 PCAN 设备

- CAN message sending and cyclic transmission  
  支持 CAN 报文发送和循环发送

- Multiple fuzzing modes  
  支持多种 Fuzz 模式

- Runtime frame editor  
  运行时帧编辑器

- Send history and log window  
  发送历史和日志窗口

- PCAN usage monitor  
  PCAN 占用检测窗口

- CRC / checksum helper module  
  CRC / checksum 辅助模块

- Local settings persistence  
  本地设置持久化保存

---

## Frame Profiles  
## 帧配置文件

The tool can work with external local frame profile files.

本工具可以配合外部本地帧配置文件使用。

Example profile format:

示例配置格式：

```json
{
  "profiles": [
    {
      "name": "Example Frame",
      "id": "0x123",
      "cycle_ms": 100,
      "data": "00 00 00 00 00 00 00 00",
      "checksum": {
        "type": "crc8",
        "poly": "0x1D",
        "init": "0xFF",
        "xor_out": "0x00",
        "range": "1:8"
      },
      "counter": {
        "byte": 1,
        "mode": "low4",
        "min": 0,
        "max": 14
      }
    }
  ]
}
```

The example above is only a dummy profile format.

上面的内容只是一个虚拟配置格式示例。

---

## Checksum Support  
## Checksum 支持

The tool includes generic checksum helper functions:

本工具包含通用 checksum 辅助函数：

- CRC8
- XOR8
- SUM8
- Rolling counter helper

These helpers can be used when building or testing custom CAN frames.

这些辅助函数可用于构建或测试自定义 CAN 报文。

---

## Hardware Requirements  
## 硬件要求

- Windows PC  
  Windows 电脑

- PEAK PCAN-USB or compatible PCAN interface  
  PEAK PCAN-USB 或兼容 PCAN 设备

- Bench-side CAN device or test node  
  架台 CAN 设备或测试节点

- Correct CAN wiring and termination  
  正确的 CAN 接线和终端电阻

Default CAN settings:

默认 CAN 设置：

```python
CHANNEL = "PCAN_USBBUS1"
BITRATE = 500_000
BUS_TYPE = "pcan"
```

You may need to edit these values for your own hardware.

你可能需要根据自己的硬件修改这些参数。

---

## Software Requirements  
## 软件要求

- Python 3.10+
- PEAK PCAN driver
- `python-can`
- Tkinter

Install dependency:

安装依赖：

```bash
pip install python-can
```

---

## How to Run  
## 如何运行

```bash
python main.py
```

If the PCAN device cannot be opened, check:

如果 PCAN 设备无法打开，请检查：

1. PCAN driver is installed  
   PCAN 驱动是否已经安装

2. PCAN adapter is connected  
   PCAN 设备是否已经连接

3. No other software is using the PCAN device  
   是否有其他软件正在占用 PCAN

4. Channel name is correct  
   通道名称是否正确

5. Bitrate is correct  
   波特率是否正确

---

## Project Structure  
## 项目结构

```text
.
├── main.py                  # Entry point / 程序入口
├── app_core.py              # Main app logic / 主程序逻辑
├── checksums.py             # Generic checksum helpers / 通用 checksum 工具
├── runtime_profiles.py      # Runtime profile handling / 运行时配置处理
├── keepalive_service.py     # Cyclic sender service / 循环发送服务
├── persistent_settings.py   # Settings loader / 设置加载器
├── settings_center.py       # CLI settings menu / 设置菜单
├── profiles.example.json    # Dummy example profile / 虚拟示例配置
└── README.md
```

---

## Safety Notice  
## 安全说明

This project is for bench testing, education, and CAN bus research only.

本项目仅用于架台测试、学习和 CAN 总线研究。

Do not use this tool on public roads.

不要在公共道路车辆上使用本工具。

Do not connect this tool to safety-critical systems unless you fully understand the risk.

如果不了解风险，不要连接到安全关键系统。

Incorrect CAN messages may cause unexpected device behavior.

错误的 CAN 报文可能导致设备异常行为。

Use at your own risk.

使用风险由使用者自行承担。

---

## Legal Notice  
## 法律说明

This project is not affiliated with any vehicle manufacturer.

本项目与任何汽车制造商无关。

All trademarks belong to their respective owners.

所有商标归其各自所有者所有。

---

## License  
## 许可证

MIT License is recommended for an open-source release.

如果公开开源，建议使用 MIT License。
