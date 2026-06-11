---
name: 查询天气的免费网站
description: 通过多个免费天气网站查询实时天气和预报
---

# 天气查询 Skill

本 Skill 集成了以下免费的天气查询服务，可直接通过 HTTP 请求获取天气信息。

## 支持的免费天气网站

### 1. wttr.in
- **网址**：https://wttr.in
- **说明**：命令行风格的天气服务，支持 `curl wttr.in/城市名` 返回简洁的文本天气信息。
- **特点**：无需 API Key，支持中英文，可自定义输出格式（如 `?format="%c+%t"`）。

### 2. Open-Meteo
- **网址**：https://open-meteo.com
- **API 端点**：`https://api.open-meteo.com/v1/forecast`
- **说明**：免费、开源的天气 API，返回 JSON 格式的预报和实时数据。
- **特点**：无需注册，支持全球任意经纬度查询，最多 16 天预报。

### 3. NOAA Weather API (美国)
- **网址**：https://www.weather.gov/documentation/services-web-api
- **说明**：美国国家气象局官方 API，提供权威的天气、预警和观测数据。
- **特点**：完全免费，无需 API Key，但建议设置 User-Agent 标识。

### 4. 7Timer!
- **网址**：https://www.7timer.info
- **API 端点**：`http://www.7timer.info/bin/api.pl`
- **说明**：提供气象学和天文学天气数据，支持全球查询。
- **特点**：免费，无需 API Key，返回 JSON 或 XML 格式。

### 5. WeatherAPI (免费层级)
- **网址**：https://www.weatherapi.com
- **说明**：提供免费层级的 API（每天 100 万次调用，需注册获取 API Key）。
- **特点**：实时天气、预报、历史数据，免费版足够个人使用。

## 使用建议

- 快速查询推荐 **wttr.in**，无需解析 JSON。
- 需要结构化数据推荐 **Open-Meteo**，完全免费且功能强大。
- 美国区域查询推荐 **NOAA API**，数据权威。