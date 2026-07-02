# yyds-notify-os

A high-performance, lightweight, and easy-to-use cross-platform desktop notification library for Python.

一个高性能、轻量级、零依赖的跨平台桌面系统通知库。

[English README](README.md)

---

## 💡 核心特性

* **零外部依赖 (Zero Dependencies)**：只使用 Python 标准库，通过底层 `subprocess` 调用系统原生通知命令行工具。
* **极速无感导入 (Fast Startup)**：代码经精细优化，导入时间低于 1ms，对运行性能几乎零影响。
* **默认非阻塞 (Non-blocking by Default)**：默认在后台守护线程中触发通知，绝不阻塞主程序的 GUI 循环或核心业务流程。
* **支持覆盖/替换机制 (`replace_id`)**：频繁发送时自动覆盖旧通知卡片（如进度条或动态监控），避免 Action Center 中卡片堆叠。
* **Windows 深度视觉打磨**：升级为微软现代的 `ToastGeneric` 模板，原生支持自定义图标 (`icon`) 和静音/映射系统预设声音 (`sound`)。
* **路径智能补全**：自动将相对路径的图标转换为绝对路径，规避底层进程执行报错。
* **优雅兜底 (Robust Fallback)**：当环境没有 GUI（如 SSH 终端、Headless CI 容器）或通知发送失败时，自动退化为控制台标准错误（sys.stderr）输出，并前置 ASCII 蜂鸣器控制符 (`\a`) 触发终端嘀声提示，确保程序永不崩溃的同时给出即时反馈。
* **包含命令行工具 (CLI Integrated)**：附带快捷可用的 `yyds-notify` / `yyds-notify-os` 命令，且默认显示应用名优化为 `"yyds-notify"`，方便脚本快速集成。

---

## 🚀 安装 (Installation)

```bash
pip install -U yyds-notify-os
```

或者从本地源码开发安装：
```bash
pip install -e .
```

---

## 💻 Python API 使用方法 (Usage)

```python
import yyds_notify_os as notify
import time

# 1. 极简用法 (默认异步非阻塞，最上方默认显示应用名 "yyds-notify")
notify.send("任务完成", "数据处理已结束！")

# 2. 动态替换/覆盖已有通知 (避免卡片堆积，适合进度更新)
for i in range(1, 6):
    notify.send("下载中", f"已完成 {i*20}%", replace_id="download_progress_1")
    time.sleep(1)

# 3. 阻断式调用并返回是否成功 (True/False)
success = notify.send("服务器警告", "CPU 温度过高！", urgency="critical", block=True)

# 4. 跨平台个性化参数配置
notify.send(
    title="日程提醒",
    message="下午 2:00 有一个技术评审会议",
    subtitle="工作会议",          # 支持 macOS / Linux-zenity
    icon="assets/bell.png",      # 自动解析为绝对路径 (Windows & Linux 支持)
    sound="sms",                 # Windows 映射 SMS 短信音, macOS 播放默认声音
    urgency="normal",            # 紧急度: 'low', 'normal', 'critical' (Linux)
    timeout=5,                   # 显示时间 (秒, Linux)
    app_name="自定义应用名"       # 最上方显示的自定义应用名称
)
```

### Windows 预设声音映射支持 (`sound` 参数值)
* `"default"`: 默认提示音
* `"im"`: 即时通讯音效
* `"mail"`: 邮件提示音
* `"reminder"`: 提醒音效
* `"sms"`: 短信提示音
* `"alarm"`: 持续警报音（循环）
* `"call"`: 持续来电音（循环）

### 接口别名
你可以直接使用以下任意接口，它们是完全等价的：
* `yyds_notify_os.notify(...)`
* `yyds_notify_os.send(...)`
* `yyds_notify_os.show(...)`

---

## 🛠️ 命令行使用 (CLI Usage)

安装完成后，你可以在终端中直接使用 `yyds-notify` 发送通知：

```bash
# 基础通知 (最上方默认显示 "yyds-notify")
yyds-notify "温馨提示" "您关注的商品已降价！"

# 自定义最上方显示的应用名称
yyds-notify "温馨提示" "股票已上涨！" -a "我的自定义应用"

# 使用 ID 动态更新通知
yyds-notify "任务状态" "正在编译模块 A..." -r "compile_task"
yyds-notify "任务状态" "正在编译模块 B..." -r "compile_task"

# 带副标题、紧急级别与声音的通知
yyds-notify "系统错误" "内存占用超过 95%！" -s "服务器监控" -u critical --sound

# 查看全部可用参数
yyds-notify --help
```

---

## 🛡️ 底层技术细节 (Technical Implementation)

1. **Windows**：
   - 优先通过 PowerShell + Windows Runtime (WinRT) 组装现代的 `ToastGeneric` XML 架构发送通知，支持自定义 `appLogoOverride` 图标。
   - 所有文本、路径等数据均通过**进程局部环境变量**进行参数传递，彻底规避命令行特殊字符转义漏洞与乱码问题。
   - 为 `replace_id` 自动设置 `ToastNotification.Tag`，实现同标识卡片的替换更新。
   - **首选创建自定义 AppName Notifier**；若在旧版 Windows 发生异常，自动在 catch 中**降级使用 100% 注册成功的 PowerShell 进程 ID** 进行通知推送，确保百分百送达。
   - 针对不支持 WinRT 的 Windows 旧版本环境，最终捕获异常并降级使用经典的 `System.Windows.Forms.NotifyIcon` 右下角通知气泡。

2. **macOS**：
   - 使用系统的 `osascript` 运行 AppleScript 代码块 `display notification` 发送通知。
   - 内部已做好字符串转义逻辑，杜绝任意字符带来的命令注入风险。

3. **Linux**：
   - 优先检测 `DISPLAY` 与 `WAYLAND_DISPLAY` 环境变量。
   - 有图形环境时，第一选择是调用 Linux 标准的 `notify-send`（`libnotify`）。**支持由完整参数到精简参数的多层降级重试机制**（如系统不支持 `-r` 或 `-a` 则自动剥离该标志运行，直至用最基础命令显示通知），若系统没有 `notify-send` 命令，则尝试寻找 `zenity --notification` 唤起通知。
   - 无图形环境（Headless/SSH）下，自动退化为向终端标准错误流中写入 `\a[Notification] Title: Message`（前置 `\a` 控制符触发终端蜂鸣音提示）。
