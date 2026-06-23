# Godot Client

桌面客户端位于 `clients/godot/SimAgentGodotClient`，默认连接本机 `http://127.0.0.1:8000` 的 FastAPI 游戏后端。

## 启动后端

```bash
uv run python example/08_god_simulator_web.py
```

## 启动 Godot 客户端

```bash
godot --path clients/godot/SimAgentGodotClient
```

如果后端地址不是默认值，可在客户端左侧面板修改“后端地址”并点击“连接并刷新”。

## 当前能力

- 请求 `/api/state` 并渲染地图、地块详情、阵营面板
- 使用 `assets/illustrations/` 中的透明贴图渲染俯视 2D 沙盒地图、地形格、阵营单位和首领图标
- 执行 `tick 1`、`tick 5`、`tick 20`
- 赐资源、改天气、批准/拒绝祈求
- 神谕私聊
- `tick` 和 `god chat` 期间每秒轮询 `/api/state`，优先展示已通过校验的首领计划摘要
- 状态变化驱动的 UI 动画：地块闪光、人口/士兵/资源浮字、天气循环、军事/迁民箭头、右侧新条目滑入、按钮点击反馈

## 动画结构

- `scripts/GameStore.gd` 会在每次状态刷新时计算前后状态差异，并发出 `state_transition(previous_state, current_state, diff)`。
- `scripts/MapView.gd` 是当前主地图视图，按后端 tile 状态绘制俯视海岛风格地图。
- `scripts/FxLayer.gd` 专门负责地图短特效，例如地块闪光、浮字、迁民/战斗箭头。
- `scripts/Main.gd` 消费 diff，把地图变化交给 `MapView` 和 `FxLayer`，把新增事件、祈求、神谕消息做面板淡入滑入。
- 迁民、调兵、攻击、袭击等箭头从新增事件文本解析坐标触发；天气循环直接读取 tile 的后端状态。
- `scripts/Sandbox3DView.gd` 保留为实验脚本，但默认隐藏；当前目标样式是俯视 2D 沙盒。
- 后续战斗闪光、占领旗帜、建筑装饰和更细单位动画应继续挂在 diff 或新增事件上，不需要在客户端重写规则。

## 贴图约定

- 原始贴图来自仓库根目录的 `illustrations/`。
- Godot 实际引用的是项目内的 `assets/illustrations/`，文件名已转成 ASCII，避免 Godot 资源路径和脚本字符串受中文路径影响。
- 当前贴图是透明背景 PNG，单位图标会被缩放到单个地图格内，避免大图遮挡地图。

## 当前限制

- 仍以后端规则引擎为准，客户端不做合法性推断
- 事件文案以原始后端消息为主，后续可再做更细的本地化整理
- 当前地图仍是功能原型：已有俯视贴图、领地高亮、天气动画、单位/首领图标和事件特效，但还没有完整建筑装饰、道路系统和专门的地图编辑器
