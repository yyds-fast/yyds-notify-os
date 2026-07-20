# yyds-notify-os

A lightweight and reliable cross-platform desktop notification library for Python.

一个轻量级、零 Python 运行时依赖的跨平台桌面系统通知库。

[English README](https://github.com/yyds-fast/yyds-notify-os/blob/main/README.md)

---

## 💡 核心特性

* **零 Python 运行时依赖**：只使用 Python 标准库；系统通知由 PowerShell、AppleScript、`notify-send` 等平台工具完成，缺少可选工具时自动降级。
* **有界异步调度**：默认使用最多 4 个后台工作线程和 64 个待处理槽位，避免高频调用导致内存无限增长。
* **可靠的覆盖/替换机制 (`replace_id`)**：同一 ID 的更新严格串行执行，尚未开始的旧更新会合并为最新值，避免进度通知倒退与卡片堆叠。
* **Windows 深度视觉打磨**：升级为微软现代的 `ToastGeneric` 模板，原生支持自定义图标 (`icon`) 和静音/映射系统预设声音 (`sound`)。
* **路径智能补全**：自动将已存在的本地图标相对路径转换为绝对路径，规避底层进程工作目录差异。
* **可观测的失败与兜底**：无 GUI 或系统通知失败时写入 `sys.stderr` 并前置 ASCII 蜂鸣器；后台未预期异常会记录到 `yyds_notify_os` logger。
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

## 📂 使用示例 (Examples)

你可以在 [example/](https://github.com/yyds-fast/yyds-notify-os/tree/main/example) 目录中找到可运行的使用示例：
* [demo.py](https://github.com/yyds-fast/yyds-notify-os/blob/main/example/demo.py)：展示 Python API、自定义设置和 `replace_id` 动态更新。
* [demo.sh](https://github.com/yyds-fast/yyds-notify-os/blob/main/example/demo.sh)：展示命令行参数及更新通知。

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
    sound="sms",                 # Windows 映射短信音；macOS 将其作为声音名称
    urgency="normal",            # 紧急度: 'low', 'normal', 'critical' (Linux)
    timeout=5,                   # 显示时间 (秒, Linux)
    app_name="自定义应用名"       # Windows / Linux 的应用名称
)
```

异步调用返回 `True` 只表示任务已成功入队；如需得到系统命令的实际执行结果，请使用 `block=True`。参数会在入队前验证，非法的 `urgency`、`timeout`、`sound` 或 `replace_id` 会抛出 `ValueError`。

### Windows 预设声音映射支持 (`sound` 参数值)
* `"default"`: 默认提示音
* `"im"`: 即时通讯音效
* `"mail"`: 邮件提示音
* `"reminder"`: 提醒音效
* `"sms"`: 短信提示音
* `"alarm"`: 系统警报音
* `"call"`: 系统来电音

### 接口别名
你可以直接使用以下任意接口，它们是完全等价的：
* `yyds_notify_os.notify(...)`
* `yyds_notify_os.send(...)`
* `yyds_notify_os.show(...)`

长时间运行的应用通常无需手动管理线程池；需要提前停止接收异步任务时，可以调用 `yyds_notify_os.shutdown(wait=True, timeout=5)`。调用后，后续异步请求会同步降级执行。

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

# 指定平台声音，并在独立后台进程中发送
yyds-notify "构建完成" "产物已生成" --sound reminder --async

# 查看全部可用参数
yyds-notify --help
```

---

## 🛡️ 底层技术细节 (Technical Implementation)

1. **Windows**：
   - 优先通过 PowerShell + Windows Runtime (WinRT) 组装现代的 `ToastGeneric` XML 架构发送通知，支持自定义 `appLogoOverride` 图标。
   - 所有文本、路径等数据均通过**进程局部环境变量**进行参数传递，彻底规避命令行特殊字符转义漏洞与乱码问题。
   - 为 `replace_id` 自动设置 `ToastNotification.Tag`，实现同标识卡片的替换更新。
   - 首选创建自定义 AppName Notifier；若系统不接受该标识，则降级使用 PowerShell 的已知 AppID。
   - 针对不支持 WinRT 的 Windows 旧版本环境，最终捕获异常并降级使用经典的 `System.Windows.Forms.NotifyIcon` 右下角通知气泡。

2. **macOS**：
   - 自动检测并优先使用 `terminal-notifier` 工具（若系统已安装，推荐使用 `brew install terminal-notifier`），并用 `-group` 支持 `replace_id` 更新。
   - 若未安装，则自动回退到 AppleScript (`osascript`) 并**委托给 `Finder` 应用上下文运行** (`tell application "Finder" to display notification ...`)。这极大地规避了由于终端（Terminal / VS Code 等）本身没有通知权限而导致通知被系统静默拦截丢弃的问题。
   - 文本通过进程环境变量或参数数组传递，避免拼接到 shell 命令中。

3. **Linux**：
   - 优先检测 `DISPLAY` 与 `WAYLAND_DISPLAY` 环境变量。
   - 有图形环境时，第一选择是调用 Linux 标准的 `notify-send`（`libnotify`）。**支持由完整参数到精简参数的多层降级重试机制**（如系统不支持 `-r` 或 `-a` 则自动剥离该标志运行，直至用最基础命令显示通知），若系统没有 `notify-send` 命令，则尝试寻找 `zenity --notification` 唤起通知。
   - 无图形环境（Headless/SSH）下，自动退化为向终端标准错误流中写入 `\a[Notification] Title: Message`（前置 `\a` 控制符触发终端蜂鸣音提示）。
