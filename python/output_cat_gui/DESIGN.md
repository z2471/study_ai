# output_cat_gui 设计方案

## 需求

- 新功能必须放到独立目录：`python/output_cat_gui/`（不改动/不删除现有文件）。
- 使用 **tkinter（标准库）** 实现 GUI 窗口：
  - 能显示 **ASCII 猫**（`Label` / `Text` 均可）。
  - 可选按钮：**刷新** / **复制**。
- 同步提交设计方案（本文件 `DESIGN.md`），写清：需求 / 方案 / DoD / 运行方式。
- **不把 repo 根目录**当前未跟踪的 `cat.py` / `test_cat.py` 纳入提交（除非明确说明必要且有价值）。

## 方案

目录结构：

- `python/output_cat_gui/app.py`
  - `CATS`: 内置若干 ASCII 猫字符串。
  - `get_random_cat()`: 随机返回一只猫。
  - `CatApp(tk.Tk)`: GUI 主窗口。
    - `Label` 显示 ASCII 文本（使用 `TkFixedFont` 以获得等宽显示效果）。
    - `Refresh` 按钮：随机刷新。
    - `Copy` 按钮：复制当前 ASCII 猫到系统剪贴板。
    - `Quit` 按钮：退出。
  - 命令行参数：
    - `--print`：**不启动 GUI**，直接打印一只猫并退出。

> 说明：增加 `--print` 的原因是某些本地/CI/SSH headless 环境无法打开 Tk 窗口；为了满足“本地验证可运行”，提供一个可自动化执行的验证入口。

## DoD（Definition of Done）

- [ ] 新增目录 `python/output_cat_gui/`，且不删除任何现有文件。
- [ ] `python/output_cat_gui/app.py` 可运行：
  - [ ] 启动后能看到窗口与 ASCII 猫。
  - [ ] `Refresh` 可刷新内容。
  - [ ] `Copy` 可把当前内容写入剪贴板。
- [ ] `DESIGN.md` 已补齐：需求/方案/DoD/运行方式。
- [ ] `git status` 不包含根目录未跟踪的 `cat.py` / `test_cat.py` 被加入提交。
- [ ] 提交包含：`python/output_cat_gui/app.py` 与 `python/output_cat_gui/DESIGN.md`。

## 运行方式

- GUI 模式：
  
  ```bash
  python3 python/output_cat_gui/app.py
  ```

- 验证/无 GUI 模式（推荐用于自动化验证）：

  ```bash
  python3 python/output_cat_gui/app.py --print
  ```
