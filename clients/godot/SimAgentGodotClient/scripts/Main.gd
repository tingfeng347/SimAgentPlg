extends Control

const MapViewScript = preload("res://scripts/MapView.gd")
const FxLayerScript = preload("res://scripts/FxLayer.gd")
const Sandbox3DViewScript = preload("res://scripts/Sandbox3DView.gd")

var _api_client
var _store
var _sandbox_view
var _map_view
var _fx_layer
var _action_buttons: Array = []
var _animated_event_keys := {}
var _animated_chat_keys := {}
var _animated_petition_keys := {}

var _backend_url_input: LineEdit
var _world_meta_label: Label
var _pause_label: Label
var _busy_label: Label
var _error_label: Label
var _faction_cards: VBoxContainer
var _map_stage: Control
var _tile_detail_label: RichTextLabel
var _give_faction_option: OptionButton
var _give_resource_option: OptionButton
var _give_amount_spin: SpinBox
var _coord_x_spin: SpinBox
var _coord_y_spin: SpinBox
var _weather_option: OptionButton
var _weather_duration_spin: SpinBox
var _chat_faction_option: OptionButton
var _chat_input: LineEdit
var _chat_messages_box: VBoxContainer
var _petitions_box: VBoxContainer
var _events_box: VBoxContainer
var _chat_scroll: ScrollContainer
var _events_scroll: ScrollContainer

var _faction_names := {
	"human": "人类",
	"elf": "精灵",
	"orc": "兽人",
}

var _resource_names := {
	"food": "食物",
	"wood": "木材",
	"stone": "石料",
}

var _weather_names := {
	"clear": "晴朗",
	"rain": "降雨",
	"drought": "干旱",
	"storm": "风暴",
}

var _terrain_names := {
	"plain": "平原",
	"forest": "森林",
	"hill": "丘陵",
	"water": "水域",
	"mountain": "山地",
}

var _relation_names := {
	"neutral": "中立",
	"allied": "同盟",
	"non_aggression": "互不侵犯",
	"trade": "贸易",
	"tribute": "纳贡",
	"war": "战争",
}

var _event_kind_names := {
	"world": "世界",
	"tick": "推进",
	"god": "神迹",
	"rule_reject": "规则拒绝",
	"resource": "资源",
	"discovery": "发现",
	"territory": "领土",
	"military": "军事",
	"battle": "战斗",
	"elimination": "淘汰",
	"diplomacy": "外交",
	"petition": "祈求",
	"god_chat": "私聊",
	"decree": "法令",
	"leader": "首领",
	"population": "人口",
	"build": "建造",
	"weather": "天气",
	"pause": "暂停",
	"resume": "恢复",
}


func _ready() -> void:
	_api_client = $ApiClient
	_store = $GameStore
	_store.configure(_api_client)
	_store.state_transition.connect(_on_state_transition)
	_store.state_changed.connect(_on_state_changed)
	_store.selection_changed.connect(_on_selection_changed)
	_store.busy_changed.connect(_on_busy_changed)
	_store.error_raised.connect(_on_error_raised)
	_build_ui()
	_backend_url_input.text = _api_client.base_url
	call_deferred("_bootstrap")


func _process(_delta: float) -> void:
	if _store.is_busy:
		_busy_label.text = "%s · %ds" % [_store.busy_label, _store.busy_elapsed_seconds()]
	else:
		_busy_label.text = ""


func _build_ui() -> void:
	add_theme_color_override("font_color", Color("f2f4f6"))

	var background := ColorRect.new()
	background.color = Color("11171a")
	background.anchor_right = 1.0
	background.anchor_bottom = 1.0
	add_child(background)
	move_child(background, 0)

	var root := MarginContainer.new()
	root.anchor_right = 1.0
	root.anchor_bottom = 1.0
	root.offset_left = 16
	root.offset_top = 16
	root.offset_right = -16
	root.offset_bottom = -16
	add_child(root)

	var shell := HBoxContainer.new()
	shell.add_theme_constant_override("separation", 14)
	shell.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	shell.size_flags_vertical = Control.SIZE_EXPAND_FILL
	root.add_child(shell)

	shell.add_child(_build_left_panel())
	shell.add_child(_build_center_panel())
	shell.add_child(_build_right_panel())

	_sandbox_view = Sandbox3DViewScript.new()
	_sandbox_view.name = "Sandbox3DView"
	_sandbox_view.anchor_right = 1.0
	_sandbox_view.anchor_bottom = 1.0
	_sandbox_view.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_sandbox_view.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_sandbox_view.visible = false
	_map_stage.add_child(_sandbox_view)
	_sandbox_view.tile_selected.connect(_on_map_tile_selected)

	_map_view = MapViewScript.new()
	_map_view.name = "MapView"
	_map_stage.add_child(_map_view)
	_fx_layer = FxLayerScript.new()
	_fx_layer.name = "FxLayer"
	_fx_layer.z_index = 10
	_map_stage.add_child(_fx_layer)
	_map_view.tile_selected.connect(_on_map_tile_selected)
	_map_stage.gui_input.connect(_on_map_stage_gui_input)
	_map_stage.resized.connect(_sync_map_view_size)
	_sync_map_view_size()

	set_process(true)


func _build_left_panel() -> Control:
	var panel := PanelContainer.new()
	panel.custom_minimum_size = Vector2(340, 0)
	panel.size_flags_vertical = Control.SIZE_EXPAND_FILL

	var scroll := ScrollContainer.new()
	scroll.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	panel.add_child(scroll)

	var box := VBoxContainer.new()
	box.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	box.add_theme_constant_override("separation", 10)
	scroll.add_child(box)

	box.add_child(_section_title("Godot 规则观察台"))
	box.add_child(_hint_label("桌面客户端直连 FastAPI，世界状态以后端为准。"))

	var backend_row := VBoxContainer.new()
	backend_row.add_child(_field_label("后端地址"))
	_backend_url_input = LineEdit.new()
	_backend_url_input.placeholder_text = "http://127.0.0.1:8000"
	_backend_url_input.text_submitted.connect(_on_backend_connect_pressed)
	backend_row.add_child(_backend_url_input)
	var backend_button := _action_button("连接并刷新", _on_backend_connect_pressed)
	backend_row.add_child(backend_button)
	box.add_child(backend_row)

	_world_meta_label = _info_label("等待连接后端")
	box.add_child(_world_meta_label)
	_pause_label = _info_label("")
	_pause_label.modulate = Color("f3bf74")
	box.add_child(_pause_label)
	_busy_label = _info_label("")
	_busy_label.modulate = Color("8dc7f2")
	box.add_child(_busy_label)
	_error_label = _info_label("")
	_error_label.modulate = Color("e98787")
	box.add_child(_error_label)

	box.add_child(_section_title("Tick"))
	var tick_row := HBoxContainer.new()
	tick_row.add_theme_constant_override("separation", 8)
	tick_row.add_child(_action_button("推进 1 刻", _on_tick_pressed.bind(1)))
	tick_row.add_child(_action_button("推进 5 刻", _on_tick_pressed.bind(5)))
	tick_row.add_child(_action_button("推进 20 刻", _on_tick_pressed.bind(20)))
	box.add_child(tick_row)

	box.add_child(_section_title("赐资源"))
	_give_faction_option = OptionButton.new()
	box.add_child(_labeled_node("阵营", _give_faction_option))
	_give_resource_option = OptionButton.new()
	box.add_child(_labeled_node("资源", _give_resource_option))
	_give_amount_spin = _int_spin(1, 250, 20)
	box.add_child(_labeled_node("数量", _give_amount_spin))
	box.add_child(_action_button("执行赐予", _on_give_pressed))

	box.add_child(_section_title("改天气"))
	_coord_x_spin = _int_spin(0, 31, 0)
	_coord_y_spin = _int_spin(0, 19, 0)
	var coord_row := HBoxContainer.new()
	coord_row.add_theme_constant_override("separation", 8)
	coord_row.add_child(_labeled_node("x", _coord_x_spin))
	coord_row.add_child(_labeled_node("y", _coord_y_spin))
	box.add_child(coord_row)
	_weather_option = OptionButton.new()
	box.add_child(_labeled_node("天气", _weather_option))
	_weather_duration_spin = _int_spin(0, 50, 5)
	box.add_child(_labeled_node("持续刻数", _weather_duration_spin))
	box.add_child(_action_button("执行天气", _on_weather_pressed))

	box.add_child(_section_title("阵营面板"))
	var faction_scroll := ScrollContainer.new()
	faction_scroll.custom_minimum_size = Vector2(0, 320)
	faction_scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	box.add_child(faction_scroll)
	_faction_cards = VBoxContainer.new()
	_faction_cards.add_theme_constant_override("separation", 8)
	_faction_cards.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	faction_scroll.add_child(_faction_cards)

	return panel


func _build_center_panel() -> Control:
	var panel := VBoxContainer.new()
	panel.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	panel.size_flags_vertical = Control.SIZE_EXPAND_FILL
	panel.add_theme_constant_override("separation", 12)

	var header := PanelContainer.new()
	header.custom_minimum_size = Vector2(0, 56)
	panel.add_child(header)
	var header_box := VBoxContainer.new()
	header_box.add_theme_constant_override("separation", 4)
	header.add_child(header_box)
	header_box.add_child(_section_title("世界地图"))
	header_box.add_child(_hint_label("点击地块查看详情；显示坐标使用 (行, 列)，请求仍使用内部 x/y。"))

	var map_panel := PanelContainer.new()
	map_panel.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	map_panel.size_flags_vertical = Control.SIZE_EXPAND_FILL
	panel.add_child(map_panel)

	_map_stage = Control.new()
	_map_stage.custom_minimum_size = Vector2(760, 560)
	_map_stage.mouse_filter = Control.MOUSE_FILTER_STOP
	_map_stage.clip_contents = true
	_map_stage.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_map_stage.size_flags_vertical = Control.SIZE_EXPAND_FILL
	map_panel.add_child(_map_stage)

	var detail_panel := PanelContainer.new()
	detail_panel.custom_minimum_size = Vector2(0, 180)
	panel.add_child(detail_panel)
	var detail_box := VBoxContainer.new()
	detail_box.add_theme_constant_override("separation", 6)
	detail_panel.add_child(detail_box)
	detail_box.add_child(_section_title("地块详情"))
	_tile_detail_label = RichTextLabel.new()
	_tile_detail_label.fit_content = true
	_tile_detail_label.bbcode_enabled = false
	_tile_detail_label.scroll_active = true
	_tile_detail_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_tile_detail_label.custom_minimum_size = Vector2(0, 120)
	_tile_detail_label.text = "点击地图中的地块后显示详情。"
	detail_box.add_child(_tile_detail_label)

	return panel


func _build_right_panel() -> Control:
	var panel := PanelContainer.new()
	panel.custom_minimum_size = Vector2(420, 0)
	panel.size_flags_horizontal = Control.SIZE_FILL
	panel.size_flags_vertical = Control.SIZE_EXPAND_FILL

	var box := VBoxContainer.new()
	box.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	box.size_flags_vertical = Control.SIZE_EXPAND_FILL
	box.add_theme_constant_override("separation", 10)
	panel.add_child(box)

	var petitions_panel := PanelContainer.new()
	petitions_panel.custom_minimum_size = Vector2(0, 220)
	petitions_panel.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	box.add_child(petitions_panel)
	var petitions_body := VBoxContainer.new()
	petitions_body.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	petitions_body.add_theme_constant_override("separation", 6)
	petitions_panel.add_child(petitions_body)
	petitions_body.add_child(_section_title("待处理祈求"))
	var petitions_scroll := ScrollContainer.new()
	petitions_scroll.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	petitions_scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	petitions_body.add_child(petitions_scroll)
	_petitions_box = VBoxContainer.new()
	_petitions_box.add_theme_constant_override("separation", 8)
	_petitions_box.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_petitions_box.custom_minimum_size = Vector2(360, 0)
	petitions_scroll.add_child(_petitions_box)

	var chat_panel := PanelContainer.new()
	chat_panel.custom_minimum_size = Vector2(0, 260)
	chat_panel.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	box.add_child(chat_panel)
	var chat_body := VBoxContainer.new()
	chat_body.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	chat_body.add_theme_constant_override("separation", 6)
	chat_panel.add_child(chat_body)
	chat_body.add_child(_section_title("神谕私聊"))
	_chat_faction_option = OptionButton.new()
	chat_body.add_child(_labeled_node("目标阵营", _chat_faction_option))
	_chat_scroll = ScrollContainer.new()
	_chat_scroll.custom_minimum_size = Vector2(0, 140)
	_chat_scroll.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_chat_scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	chat_body.add_child(_chat_scroll)
	_chat_messages_box = VBoxContainer.new()
	_chat_messages_box.add_theme_constant_override("separation", 6)
	_chat_messages_box.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_chat_messages_box.custom_minimum_size = Vector2(360, 0)
	_chat_scroll.add_child(_chat_messages_box)
	_chat_input = LineEdit.new()
	_chat_input.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_chat_input.placeholder_text = "输入神谕内容"
	_chat_input.text_submitted.connect(_on_chat_text_submitted)
	_chat_input.text_changed.connect(_on_chat_text_changed)
	chat_body.add_child(_chat_input)
	chat_body.add_child(_action_button("发送神谕", _on_chat_pressed))

	var events_panel := PanelContainer.new()
	events_panel.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	events_panel.size_flags_vertical = Control.SIZE_EXPAND_FILL
	box.add_child(events_panel)
	var events_body := VBoxContainer.new()
	events_body.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	events_body.size_flags_vertical = Control.SIZE_EXPAND_FILL
	events_body.add_theme_constant_override("separation", 6)
	events_panel.add_child(events_body)
	events_body.add_child(_section_title("事件时间线"))
	_events_scroll = ScrollContainer.new()
	_events_scroll.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_events_scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	events_body.add_child(_events_scroll)
	_events_box = VBoxContainer.new()
	_events_box.add_theme_constant_override("separation", 6)
	_events_box.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_events_box.custom_minimum_size = Vector2(360, 0)
	_events_scroll.add_child(_events_box)

	return panel


func _bootstrap() -> void:
	await _store.refresh_state()


func _sync_map_view_size() -> void:
	if _map_view == null or _map_stage == null:
		return
	_map_view.position = Vector2.ZERO
	_map_view.set_drawing_size(_map_stage.size)
	if _sandbox_view != null:
		_sandbox_view.position = Vector2.ZERO
		_sandbox_view.set_drawing_size(_map_stage.size)
	if _fx_layer != null:
		_fx_layer.position = Vector2.ZERO
		_fx_layer.set_iso_metrics(_map_view.origin, _map_view.tile_width, _map_view.tile_height, _map_view.row_step, _map_stage.size)


func _on_backend_connect_pressed(_text: String = "") -> void:
	_api_client.base_url = _backend_url_input.text.strip_edges()
	_error_label.text = ""
	await _store.refresh_state()


func _on_tick_pressed(count: int) -> void:
	_error_label.text = ""
	await _store.start_tick(count)


func _on_give_pressed() -> void:
	_error_label.text = ""
	await _store.give_resource(
		_option_value(_give_faction_option),
		_option_value(_give_resource_option),
		int(_give_amount_spin.value)
	)


func _on_weather_pressed() -> void:
	_error_label.text = ""
	await _store.set_weather(
		int(_coord_x_spin.value),
		int(_coord_y_spin.value),
		_option_value(_weather_option),
		int(_weather_duration_spin.value)
	)


func _on_chat_pressed() -> void:
	var message := _chat_input.text.strip_edges()
	if message.is_empty():
		return
	_error_label.text = ""
	var ok: bool = await _store.send_god_chat(_option_value(_chat_faction_option), message)
	if ok:
		_chat_input.text = ""
	_refresh_chat_send_enabled()


func _on_chat_text_submitted(_text: String) -> void:
	await _on_chat_pressed()


func _on_chat_text_changed(_text: String) -> void:
	_refresh_chat_send_enabled()


func _on_map_stage_gui_input(event: InputEvent) -> void:
	_map_view.handle_gui_input(event)


func _on_map_tile_selected(tile) -> void:
	_store.select_tile(tile)


func _on_petition_answer_pressed(petition_id: int, approve: bool) -> void:
	_error_label.text = ""
	await _store.answer_petition(petition_id, approve)


func _on_state_transition(_previous_state, current_state, diff: Dictionary) -> void:
	_capture_animated_keys(diff)
	_sync_map_view_size()
	if _fx_layer == null:
		return
	for tile_change in diff.get("tiles", []):
		var color := _color_for_tile_change(tile_change)
		_flash_tile(int(tile_change.get("x", 0)), int(tile_change.get("y", 0)), color)
		var population_delta := int(tile_change.get("population_delta", 0))
		if population_delta != 0:
			_float_text(
				int(tile_change.get("x", 0)),
				int(tile_change.get("y", 0)),
				"%+d 人口" % population_delta,
				Color("8ee38c") if population_delta > 0 else Color("e87b73")
			)
		var soldiers_delta := int(tile_change.get("soldiers_delta", 0))
		if soldiers_delta != 0:
			_float_text(
				int(tile_change.get("x", 0)),
				int(tile_change.get("y", 0)),
				"%+d 士兵" % soldiers_delta,
				Color("f0d36a") if soldiers_delta > 0 else Color("ff8f72")
			)
	for faction_change in diff.get("factions", []):
		_spawn_faction_resource_floats(current_state, faction_change)
	for event in diff.get("events", []):
		_play_event_animation(event)


func _on_state_changed(state) -> void:
	_error_label.text = ""
	_world_meta_label.text = "第 %d 刻 · 种子 %d · 地图 %dx%d" % [state.tick, state.seed, state.width, state.height]
	_pause_label.text = "已暂停：%s" % state.pause_reason if state.paused and not state.pause_reason.is_empty() else ""
	_sync_option_data(state)
	_render_factions(state)
	_render_petitions(state)
	_render_god_chat(state)
	_render_events(state)
	_map_view.set_state(state)
	_map_view.set_selected_tile(_store.selected_tile)
	if _sandbox_view != null:
		_sandbox_view.set_state(state)
		_sandbox_view.set_selected_tile(_store.selected_tile)
	_sync_map_view_size()
	_clear_animated_keys()


func _on_selection_changed(tile) -> void:
	_map_view.set_selected_tile(tile)
	if _sandbox_view != null:
		_sandbox_view.set_selected_tile(tile)
	_render_tile_details(tile)
	if tile != null:
		_coord_x_spin.value = tile.x
		_coord_y_spin.value = tile.y


func _on_busy_changed(is_busy: bool, _label: String, _kind: String) -> void:
	for button in _action_buttons:
		button.disabled = is_busy
	_render_petitions(_store.state)
	_refresh_chat_send_enabled()


func _on_error_raised(message: String) -> void:
	_error_label.text = "错误：%s" % message


func _sync_option_data(state) -> void:
	_coord_x_spin.max_value = max(0, state.width - 1)
	_coord_y_spin.max_value = max(0, state.height - 1)
	_sync_option_button(_give_faction_option, _collect_faction_ids(state), _faction_names)
	_sync_option_button(_chat_faction_option, _collect_faction_ids(state), _faction_names)
	_sync_option_button(_give_resource_option, state.resources, _resource_names)
	_sync_option_button(_weather_option, state.weather_types, _weather_names)
	_refresh_chat_send_enabled()


func _render_tile_details(tile) -> void:
	if tile == null:
		_tile_detail_label.text = "点击地图中的地块后显示详情。"
		return
	_tile_detail_label.text = "\n".join([
		"显示坐标（行, 列）：(%d, %d)" % [tile.y, tile.x],
		"内部坐标 x/y：(%d, %d)" % [tile.x, tile.y],
		"地形：%s" % _terrain_names.get(tile.terrain, tile.terrain),
		"归属：%s" % _display_faction(tile.owner),
		"出生地：%s" % _display_faction(tile.home_of),
		"天气：%s%s" % [
			_weather_names.get(tile.weather, tile.weather),
			"（剩余 %d 刻）" % tile.weather_duration if tile.weather_duration > 0 else ""
		],
		"人口：%s" % _format_count_dict(tile.population, true),
		"士兵：%s" % _format_count_dict(tile.soldiers, true),
		"职业：%s" % _format_professions(tile.professions),
		"房屋：%d · 容量：%d" % [tile.houses, tile.capacity],
		"庇护：%s" % ("是" if tile.protected else "否"),
	])


func _render_factions(state) -> void:
	_clear_children(_faction_cards)
	for faction in state.factions:
		var card := PanelContainer.new()
		_faction_cards.add_child(card)
		var body := VBoxContainer.new()
		body.add_theme_constant_override("separation", 4)
		card.add_child(body)

		var title := Label.new()
		title.text = "%s%s" % [
			_display_faction(faction.faction_id),
			" · 已淘汰" if faction.eliminated else ""
		]
		title.add_theme_font_size_override("font_size", 18)
		body.add_child(title)

		var text := Label.new()
		text.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
		text.text = "\n".join([
			"首领：%s" % faction.leader_name,
			"出生地：%s" % _format_home_tile(faction.home_tile),
			"人口 %d/%d · 士兵 %d · 领土 %d · 房屋 %d" % [
				faction.population, faction.population_capacity, faction.soldiers, faction.territory_count, faction.houses
			],
			"资源：食物 %d · 木材 %d · 石料 %d" % [
				int(faction.resources.get("food", 0)),
				int(faction.resources.get("wood", 0)),
				int(faction.resources.get("stone", 0))
			],
			"职业：%s" % _format_jobs(faction.jobs),
			"外交：%s" % _format_diplomacy(faction.diplomacy),
			"已发现：%s" % _format_known_factions(faction.known_factions),
			"上次计划：%s" % _format_last_plan(faction.last_plan_snapshot),
			"最近规则错误：%s" % _format_memory_items(faction.leader_memory.get("rule_errors", [])),
			"神谕记忆：%s" % _format_memory_items(faction.leader_memory.get("god_dialogue", [])),
		])
		body.add_child(text)


func _render_petitions(state) -> void:
	_clear_children(_petitions_box)
	if state == null or state.petitions.is_empty():
		_petitions_box.add_child(_hint_label("暂无待处理祈求。"))
		return
	for petition in state.petitions:
		var card := PanelContainer.new()
		card.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		_petitions_box.add_child(card)
		if _animated_petition_keys.has(_petition_key(petition)):
			_animate_panel_entry(card)
		var body := VBoxContainer.new()
		body.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		body.add_theme_constant_override("separation", 4)
		card.add_child(body)
		body.add_child(_info_label("#%d · %s · %s" % [
			petition.petition_id,
			_display_faction(petition.faction_id),
			petition.kind
		]))
		body.add_child(_hint_label("紧急度 %s · 创建于第 %d 刻" % [petition.urgency, petition.created_tick]))
		body.add_child(_wrapped_label("请求：%s" % _format_dictionary(petition.request)))
		body.add_child(_wrapped_label("理由：%s" % petition.reason))
		var row := HBoxContainer.new()
		row.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		row.add_theme_constant_override("separation", 8)
		var approve := Button.new()
		approve.text = "批准"
		approve.disabled = _store.is_busy
		approve.pressed.connect(_on_petition_answer_pressed.bind(petition.petition_id, true))
		row.add_child(approve)
		var reject := Button.new()
		reject.text = "拒绝"
		reject.disabled = _store.is_busy
		reject.pressed.connect(_on_petition_answer_pressed.bind(petition.petition_id, false))
		row.add_child(reject)
		body.add_child(row)


func _render_god_chat(state) -> void:
	_clear_children(_chat_messages_box)
	if state == null:
		return
	var faction_id := _option_value(_chat_faction_option)
	var has_messages := false
	for message in state.god_chats:
		if message.faction_id != faction_id:
			continue
		has_messages = true
		var card := PanelContainer.new()
		card.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		_chat_messages_box.add_child(card)
		if _animated_chat_keys.has(_chat_key(message)):
			_animate_panel_entry(card)
		var label := Label.new()
		label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
		label.text = "第 %d 刻 · %s\n%s" % [
			message.tick,
			"神" if message.speaker == "god" else "首领",
			message.content
		]
		card.add_child(label)
	if not has_messages:
		_chat_messages_box.add_child(_hint_label("暂无私聊。神谕只影响首领意图，不会自动兑现资源。"))
	call_deferred("_scroll_to_bottom", _chat_scroll)


func _render_events(state) -> void:
	_clear_children(_events_box)
	if state == null:
		return
	for index in range(state.events.size() - 1, -1, -1):
		var event = state.events[index]
		var label := _wrapped_label("[第 %d 刻] %s · %s" % [
			event.tick,
			_event_kind_names.get(event.kind, event.kind),
			event.message
		])
		_events_box.add_child(label)
		if _animated_event_keys.has(_event_key(event)):
			_animate_panel_entry(label)
	call_deferred("_scroll_to_top", _events_scroll)


func _scroll_to_bottom(scroll: ScrollContainer) -> void:
	if scroll == null:
		return
	scroll.scroll_vertical = int(scroll.get_v_scroll_bar().max_value)


func _scroll_to_top(scroll: ScrollContainer) -> void:
	if scroll == null:
		return
	scroll.scroll_vertical = 0


func _refresh_chat_send_enabled() -> void:
	if _action_buttons.is_empty():
		return
	var send_button: Button = _action_buttons[-1]
	send_button.disabled = _store.is_busy or _chat_input.text.strip_edges().is_empty()


func _sync_option_button(button: OptionButton, values: Array, labels: Dictionary) -> void:
	var wanted := _option_value(button)
	button.clear()
	for value in values:
		button.add_item(labels.get(str(value), str(value)))
		button.set_item_metadata(button.item_count - 1, str(value))
	if button.item_count == 0:
		return
	var selected_index := 0
	for index in range(button.item_count):
		if str(button.get_item_metadata(index)) == wanted:
			selected_index = index
			break
	button.select(selected_index)


func _option_value(button: OptionButton) -> String:
	if button == null or button.item_count == 0 or button.selected < 0:
		return ""
	return str(button.get_item_metadata(button.selected))


func _collect_faction_ids(state) -> Array:
	var result: Array = []
	for faction in state.factions:
		result.append(faction.faction_id)
	return result


func _section_title(text: String) -> Label:
	var label := Label.new()
	label.text = text
	label.add_theme_font_size_override("font_size", 20)
	return label


func _field_label(text: String) -> Label:
	var label := Label.new()
	label.text = text
	return label


func _info_label(text: String) -> Label:
	var label := Label.new()
	label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	label.text = text
	label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	return label


func _hint_label(text: String) -> Label:
	var label := _info_label(text)
	label.modulate = Color("9bb0b8")
	return label


func _wrapped_label(text: String) -> Label:
	var label := Label.new()
	label.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	label.text = text
	return label


func _labeled_node(text: String, node: Control) -> Control:
	var box := VBoxContainer.new()
	box.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	node.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	box.add_child(_field_label(text))
	box.add_child(node)
	return box


func _int_spin(min_value: int, max_value: int, default_value: int) -> SpinBox:
	var spin := SpinBox.new()
	spin.min_value = min_value
	spin.max_value = max_value
	spin.step = 1
	spin.rounded = true
	spin.value = default_value
	return spin


func _action_button(text: String, callable: Callable) -> Button:
	var button := Button.new()
	button.text = text
	button.pressed.connect(func():
		_animate_button_press(button)
		callable.call()
	)
	_action_buttons.append(button)
	return button


func _capture_animated_keys(diff: Dictionary) -> void:
	_animated_event_keys.clear()
	_animated_chat_keys.clear()
	_animated_petition_keys.clear()
	for event in diff.get("events", []):
		_animated_event_keys[_event_key(event)] = true
	for message in diff.get("god_chats", []):
		_animated_chat_keys[_chat_key(message)] = true
	for petition in diff.get("petitions", []):
		_animated_petition_keys[_petition_key(petition)] = true


func _clear_animated_keys() -> void:
	_animated_event_keys.clear()
	_animated_chat_keys.clear()
	_animated_petition_keys.clear()


func _animate_panel_entry(control: Control) -> void:
	control.modulate.a = 0.0
	control.position.x += 18.0
	var tween := create_tween()
	tween.set_parallel(true)
	tween.tween_property(control, "modulate:a", 1.0, 0.18).set_trans(Tween.TRANS_QUAD).set_ease(Tween.EASE_OUT)
	tween.tween_property(control, "position:x", control.position.x - 18.0, 0.18).set_trans(Tween.TRANS_QUAD).set_ease(Tween.EASE_OUT)


func _animate_button_press(button: Button) -> void:
	button.pivot_offset = button.size / 2.0
	var tween := create_tween()
	tween.tween_property(button, "scale", Vector2(0.94, 0.94), 0.045).set_trans(Tween.TRANS_QUAD).set_ease(Tween.EASE_OUT)
	tween.tween_property(button, "scale", Vector2.ONE, 0.11).set_trans(Tween.TRANS_BACK).set_ease(Tween.EASE_OUT)


func _color_for_tile_change(tile_change: Dictionary) -> Color:
	var fields: Array = tile_change.get("fields", [])
	if fields.has("owner"):
		return Color("f0d36a")
	if fields.has("weather"):
		return Color("8dc7f2")
	if fields.has("population") or fields.has("soldiers"):
		return Color("f2f4f6")
	if fields.has("protected"):
		return Color("f1f2b8")
	return Color("d8ac55")


func _spawn_faction_resource_floats(state, faction_change: Dictionary) -> void:
	var faction = faction_change.get("faction")
	if faction == null or _fx_layer == null:
		return
	var home_tile: Dictionary = faction.home_tile
	if home_tile.is_empty():
		return
	var x := int(home_tile.get("x", 0))
	var y := int(home_tile.get("y", 0))
	var offset := 0
	for resource in faction_change.get("resource_delta", {}).keys():
		var delta := int(faction_change["resource_delta"][resource])
		if delta == 0:
			continue
		_float_text(
			x,
			y,
			"%+d %s" % [delta, _resource_names.get(str(resource), str(resource))],
			Color("f0d36a") if delta > 0 else Color("e87b73")
		)
		offset += 1


func _play_event_animation(event) -> void:
	if _fx_layer == null:
		return
	var message := str(event.message)
	var parsed := _movement_from_event(message)
	if not parsed.is_empty():
		_travel_arrow(
			int(parsed.get("from_x", 0)),
			int(parsed.get("from_y", 0)),
			int(parsed.get("to_x", 0)),
			int(parsed.get("to_y", 0)),
			_event_arrow_color(str(event.kind), message)
		)
	if str(event.kind) in ["battle", "elimination"]:
		_shake(8.0, 0.18)
	elif message.begins_with("storm at ") or message.contains(" attacked ") or message.contains(" captured "):
		_shake(5.5, 0.14)


func _movement_from_event(message: String) -> Dictionary:
	var regex := RegEx.new()
	regex.compile("\\((\\d+), (\\d+)\\)")
	var matches := regex.search_all(message)
	if matches.size() < 2:
		return {}
	if message.contains(" moved ") and message.contains(" from ") and message.contains(" to "):
		return _coords_pair(matches[0], matches[1])
	if message.contains(" settled tile ") and message.contains(" from "):
		return _coords_pair(matches[1], matches[0])
	if message.contains(" attacked ") and message.contains(" at "):
		return _coords_pair(matches[0], matches[1])
	if message.contains(" captured ") and message.contains(" from "):
		return _coords_pair(matches[0], matches[1])
	if message.contains(" raided ") and message.contains(" from "):
		return _coords_pair(matches[0], matches[1])
	return {}


func _coords_pair(from_match: RegExMatch, to_match: RegExMatch) -> Dictionary:
	return {
		"from_x": int(from_match.get_string(1)),
		"from_y": int(from_match.get_string(2)),
		"to_x": int(to_match.get_string(1)),
		"to_y": int(to_match.get_string(2)),
	}


func _event_arrow_color(kind: String, message: String) -> Color:
	if kind == "battle" or message.contains(" attacked ") or message.contains(" captured ") or message.contains(" raided "):
		return Color("ff8068")
	if message.contains(" moved ") and message.contains(" soldiers "):
		return Color("f0d36a")
	if message.contains(" settled tile "):
		return Color("8ee38c")
	return Color("8dc7f2")


func _flash_tile(x: int, y: int, color: Color) -> void:
	if _sandbox_view != null and _sandbox_view.visible:
		_sandbox_view.flash_tile(x, y, color)
	elif _fx_layer != null:
		_fx_layer.flash_tile(x, y, color)


func _float_text(x: int, y: int, text: String, color: Color) -> void:
	if _sandbox_view != null and _sandbox_view.visible:
		_sandbox_view.float_text(x, y, text, color)
	elif _fx_layer != null:
		_fx_layer.float_text(x, y, text, color)


func _travel_arrow(from_x: int, from_y: int, to_x: int, to_y: int, color: Color) -> void:
	if _sandbox_view != null and _sandbox_view.visible:
		_sandbox_view.travel_arrow(from_x, from_y, to_x, to_y, color)
	elif _fx_layer != null:
		_fx_layer.travel_arrow(from_x, from_y, to_x, to_y, color)


func _shake(strength: float, duration: float) -> void:
	if _sandbox_view != null and _sandbox_view.visible:
		_sandbox_view.shake(strength, duration)
	elif _fx_layer != null:
		_fx_layer.shake(strength, duration)


func _event_key(event) -> String:
	return "%s|%s|%s|%s" % [str(event.tick), event.kind, event.message, event.faction_id]


func _chat_key(message) -> String:
	return str(message.message_id)


func _petition_key(petition) -> String:
	return str(petition.petition_id)


func _clear_children(node: Node) -> void:
	if node == null:
		return
	for child in node.get_children():
		child.queue_free()


func _display_faction(faction_id: String) -> String:
	if faction_id.is_empty():
		return "无"
	return _faction_names.get(faction_id, faction_id)


func _format_home_tile(home_tile: Dictionary) -> String:
	if home_tile.is_empty():
		return "未知"
	return "(%d, %d)" % [int(home_tile.get("y", 0)), int(home_tile.get("x", 0))]


func _format_jobs(jobs: Dictionary) -> String:
	var keys := ["idle", "farmer", "lumberjack", "miner", "builder"]
	var labels := {
		"idle": "闲置",
		"farmer": "农民",
		"lumberjack": "伐木工",
		"miner": "矿工",
		"builder": "建筑工",
	}
	var parts: Array[String] = []
	for key in keys:
		parts.append("%s%d" % [labels[key], int(jobs.get(key, 0))])
	return " ".join(parts)


func _format_diplomacy(diplomacy: Dictionary) -> String:
	if diplomacy.is_empty():
		return "暂无"
	var parts: Array[String] = []
	for other_id in diplomacy.keys():
		parts.append("%s:%s" % [
			_display_faction(str(other_id)),
			_relation_names.get(str(diplomacy[other_id]), str(diplomacy[other_id]))
		])
	return " ".join(parts)


func _format_known_factions(known_factions: Array) -> String:
	if known_factions.is_empty():
		return "无"
	var parts: Array[String] = []
	for faction_id in known_factions:
		parts.append(_display_faction(str(faction_id)))
	return "、".join(parts)


func _format_last_plan(snapshot: Dictionary) -> String:
	if snapshot.is_empty():
		return "暂无"
	var summary := str(snapshot.get("strategy_summary", snapshot.get("turn_intent", "暂无")))
	var tick_value := int(snapshot.get("tick", -1))
	var after_execution: Variant = snapshot.get("after_execution", {})
	if after_execution is Dictionary and not after_execution.is_empty():
		return "第 %d 刻：%s" % [tick_value, summary]
	return "第 %d 刻：%s（尚未执行完）" % [tick_value, summary]


func _format_memory_items(items: Variant) -> String:
	if not (items is Array) or items.is_empty():
		return "无"
	var parts: Array[String] = []
	for item in items:
		if item is Dictionary:
			if item.has("error"):
				parts.append("第%s刻：%s" % [str(item.get("tick", "?")), str(item.get("error", ""))])
			elif item.has("content"):
				parts.append("第%s刻 %s：%s" % [
					str(item.get("tick", "?")),
					"神" if str(item.get("speaker", "")) == "god" else "首领",
					str(item.get("content", ""))
				])
		else:
			parts.append(str(item))
	return "；".join(parts.slice(max(parts.size() - 4, 0), parts.size()))


func _format_dictionary(values: Dictionary) -> String:
	if values.is_empty():
		return "无"
	var parts: Array[String] = []
	for key in values.keys():
		parts.append("%s=%s" % [str(key), str(values[key])])
	return ", ".join(parts)


func _format_count_dict(values: Dictionary, translate_faction: bool) -> String:
	if values.is_empty():
		return "无"
	var parts: Array[String] = []
	for key in values.keys():
		var name := _display_faction(str(key)) if translate_faction else str(key)
		parts.append("%s:%s" % [name, str(values[key])])
	return " ".join(parts)


func _format_professions(values: Dictionary) -> String:
	if values.is_empty():
		return "无"
	var parts: Array[String] = []
	for faction_id in values.keys():
		parts.append("%s %s" % [_display_faction(str(faction_id)), _format_jobs(values[faction_id])])
	return "；".join(parts)
