# output_cat_gui 设计方案

## 需求

- 功能落地到独立目录：`python/output_cat_gui/`。
- GUI 使用 **标准库 tkinter**（零第三方依赖）。
- 可运行：
  - `python3 python/output_cat_gui/app.py` 能弹窗显示猫。
  - 为避免 headless 环境无法打开窗口，保留 `--print` 模式：
    - `python3 python/output_cat_gui/app.py --print` 直接打印一只猫并退出。
- GUI 按钮：**Refresh / Copy / Quit**（或中文按钮亦可）。
- 同步提交设计方案（本文件 `DESIGN.md`），写清：需求/方案/DoD/运行方式/与 `python/output_cat` 的关系（并列示例）。
- 提交流程约束：
  - 不删除任何文件。
  - 不把 repo 根目录未跟踪的 `cat.py` / `test_cat.py` 加入提交。
  - 不包含 `sudo/apt-get` 这类安装命令。

## 方案

### 目录结构（按功能拆分为独立文件夹）

- `python/output_cat_gui/app.py`
  - 入口脚本，负责 CLI 参数解析、选择 GUI 或 `--print` 模式。
- `python/output_cat_gui/cats/`
  - `catalog.py`: ASCII 猫列表与 `get_random_cat()`。
- `python/output_cat_gui/ui/`
  - `main_window.py`: `CatApp(tk.Tk)` 主窗口、布局与按钮绑定。
- `python/output_cat_gui/clipboard/`
  - `ops.py`: `copy_to_clipboard()`，封装 Tk 剪贴板操作。

### 行为说明

- GUI 模式：
  - 窗口中用等宽字体（`TkFixedFont`）显示 ASCII 猫。
  - **Refresh**：随机换一只。
  - **Copy**：复制当前 ASCII 猫到系统剪贴板。
  - **Quit**：退出窗口。
- `--print` 模式：
  - 不导入/初始化 Tk 窗口，只输出一只猫，便于 CI / SSH / headless 环境验证。

## DoD（Definition of Done）

- [x] 使用 tkinter（标准库），零第三方依赖。
- [x] `python3 python/output_cat_gui/app.py` 可弹窗显示猫。
- [x] 按钮包含 Refresh / Copy / Quit。
- [x] `python3 python/output_cat_gui/app.py --print` 可直接打印一只猫并退出。
- [x] 代码按“不同功能独立文件夹”拆分（cats/ui/clipboard）。
- [x] 不删除任何文件；提交不包含根目录未跟踪的 `cat.py` / `test_cat.py`。

## 运行方式

- GUI 模式：

  ```bash
  python3 python/output_cat_gui/app.py
  ```

- `--print`（无 GUI / 验证模式）：

  ```bash
  python3 python/output_cat_gui/app.py --print
  ```

## 与 `python/output_cat` 的关系（并列示例）

- `python/output_cat`：命令行脚本，直接在终端打印 ASCII 猫。
- `python/output_cat_gui`：GUI 版本，支持窗口显示 + 剪贴板复制；同时提供 `--print` 以对齐 CLI 的可验证性。

并列运行示例：

```bash
# CLI 版本（终端输出）
python3 python/output_cat/cat.py

# GUI 版本（弹窗）
python3 python/output_cat_gui/app.py

# GUI 的 headless 验证模式（终端输出）
python3 python/output_cat_gui/app.py --print
```
