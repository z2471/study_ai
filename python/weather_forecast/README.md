# python.weather_forecast

独立功能目录：通过抓取 **wttr.in** 的 JSON 接口获取天气预报，并在控制台输出（JSON/表格）。

- 数据源：`https://wttr.in/<city>?format=j1`
- 依赖：`requests`（尽量轻依赖）

## 安装

在仓库根目录：

```bash
pip install -r requirements.txt
```

## CLI 用法

### 模块方式（推荐）

```bash
python -m python.weather_forecast --city 北京 --days 3
```

输出格式：

- JSON（默认）：

```bash
python -m python.weather_forecast --city Beijing --days 2 --format json
```

- 表格：

```bash
python -m python.weather_forecast --city Beijing --days 2 --format table
```

### 旧脚本（兼容）

仓库里可能还保留了早期的 `python/weather_forecast/weather_forecast.py`（含更多数据源尝试）。
本功能的 **正式入口** 是 `python -m python.weather_forecast`。

## 代码接口

```python
from python.weather_forecast import fetch_forecast

payload = fetch_forecast("北京", 3)
print(payload["forecast"][0]["date"])
```
