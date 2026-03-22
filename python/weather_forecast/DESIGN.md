# python.weather_forecast — DESIGN

## 1) 目标
在仓库内新增/完善 `python/weather_forecast/` 作为独立功能目录，实现一个“python 爬虫获取天气预报”。

这里的“爬虫/抓取”定义为：**通过 HTTP GET 请求公共 API，获取 JSON 并进行结构化解析输出**。

核心要求：
1. **使用 Open-Meteo API**（无需 API key）。
2. 提供 CLI：`python -m python.weather_forecast --city 北京 --days 3`（至少支持 `city`、`days`）。
   - `city`：内置少量映射表（city→经纬度）。
   - 兜底：支持 `--lat --lon`。
3. 输出未来 N 天游览：日期、最高/最低温、降水概率（或天气码）。
4. 同目录提供本 `DESIGN.md`：目标 / DoD / 接口 / 错误处理 / 扩展点。
5. 最少依赖：优先标准库 `urllib`（本实现仅用标准库；无需 requests）。

---

## 2) 数据源（Open-Meteo）
- Forecast API：`https://api.open-meteo.com/v1/forecast`
- 文档：<https://open-meteo.com/en/docs>

请求参数（本功能使用 daily 级别）：
- `latitude`, `longitude`
- `daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max,weathercode`
- `forecast_days=N`
- `timezone=auto`

返回关键字段：
- `daily.time[]`：日期（YYYY-MM-DD）
- `daily.temperature_2m_max[]` / `daily.temperature_2m_min[]`：最高/最低温（°C）
- `daily.precipitation_probability_max[]`：当日最大降水概率（%）
- `daily.weathercode[]`：WMO weather interpretation code

---

## 3) 输出结构（稳定 JSON）
`fetch_forecast()` 返回 JSON-serializable dict：

```json
{
  "source": "open-meteo",
  "city": "北京",
  "latitude": 39.9042,
  "longitude": 116.4074,
  "days": 3,
  "fetched_at": "2026-03-22T02:00:00+00:00",
  "forecast": [
    {
      "date": "YYYY-MM-DD",
      "tmax_c": 10.0,
      "tmin_c": 1.0,
      "precip_prob_max": 80,
      "weathercode": 61
    }
  ]
}
```

说明：
- `tmax_c/tmin_c/precip_prob_max/weathercode` 允许为 `null`（best-effort），但 `date` 是硬要求。

---

## 4) CLI 设计
入口：`python -m python.weather_forecast`

参数：
- `--city <str>`：城市名（推荐；但只支持内置映射表中的少量城市）
- `--lat <float> --lon <float>`：当 city 未映射时的兜底方式
- `--days <int>`：天数（默认 3；内部上限 16）
- `--format json|table`：输出格式（默认 json）
- `--timeout <int>`：HTTP 超时秒数（默认 10）

行为规则：
- 若 `--city` 可解析为经纬度：直接请求 Open-Meteo。
- 若 `--city` 不能解析：必须提供 `--lat/--lon`，否则报错（ValueError）。
- 若不提供 `--city`：必须提供 `--lat/--lon`。

---

## 5) 异常处理策略
### A. 输入错误（ValueError）
- `days` 非正整数
- city 未映射且未提供 `--lat/--lon`
- `--lat/--lon` 不是数字

CLI 处理：打印到 stderr，退出码 `2`。

### B. 网络失败（RuntimeError）
- DNS/连接失败：`URLError`
- HTTP 非 2xx：`HTTPError`（包含状态码与响应片段）
- JSON 解析失败

CLI 处理：stderr 输出 `Error: ...`，退出码 `1`。

### C. 返回字段缺失/结构变化（RuntimeError）
- `daily` 缺失
- 关键数组缺失/不是 list
- `daily.time` 里出现无效日期

原则：输出结构必须稳定；无法保证稳定时直接 fail-fast。

---

## 6) DoD（Definition of Done）
- [x] 新增 `python/weather_forecast/client.py`：使用标准库 `urllib` 调 Open-Meteo
- [x] `python/weather_forecast/__main__.py` 支持：
  - `python -m python.weather_forecast --city 北京 --days 3`
  - `--lat/--lon` 兜底
  - `--format json|table`
- [x] 输出未来 N 天：date、tmax/tmin、precip_prob_max、weathercode
- [x] `DESIGN.md` 覆盖：目标 / 接口 / 异常处理 / 扩展点

---

## 7) 扩展点
- 扩展城市映射：在 `client.py::CITY_TO_LATLON` 增加更多条目。
- 接入地理编码：可增加 `geocode.py`（例如调用 Open-Meteo Geocoding API），在不破坏现有 CLI 的前提下实现“任意城市名”。
- 更丰富的输出字段：风速、降水量、体感温度等（在 `daily` 参数里追加字段，并在输出结构中增加 key）。
