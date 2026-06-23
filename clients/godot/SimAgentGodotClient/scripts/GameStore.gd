class_name GameStore
extends Node

const GameTypes = preload("res://scripts/GameTypes.gd")
const ApiClientScript = preload("res://scripts/ApiClient.gd")

signal state_changed(state)
signal state_transition(previous_state, current_state, diff)
signal selection_changed(tile)
signal busy_changed(is_busy, label, kind)
signal error_raised(message)

var api_client
var state
var selected_tile
var is_busy: bool = false
var busy_label: String = ""
var busy_kind: String = ""
var busy_started_at_msec: int = 0

var _poll_timer: Timer
var _poll_in_flight: bool = false
var _selected_coords := Vector2i(-1, -1)


func _ready() -> void:
	_poll_timer = Timer.new()
	_poll_timer.wait_time = 1.0
	_poll_timer.one_shot = false
	_poll_timer.timeout.connect(_on_poll_timer_timeout)
	add_child(_poll_timer)


func configure(client) -> void:
	api_client = client


func refresh_state() -> bool:
	if api_client == null:
		error_raised.emit("ApiClient 未配置")
		return false
	var response: Dictionary = await api_client.fetch_state()
	return _apply_response(response, false)


func start_tick(count: int) -> bool:
	if api_client == null:
		error_raised.emit("ApiClient 未配置")
		return false
	_begin_busy("strategy", "推进 %d 刻" % count)
	var response: Dictionary = await api_client.post_tick(count)
	var ok := _apply_response(response, true)
	_end_busy()
	return ok


func send_god_chat(faction_id: String, message: String) -> bool:
	if api_client == null:
		error_raised.emit("ApiClient 未配置")
		return false
	_begin_busy("chat", "神谕送达中")
	var response: Dictionary = await api_client.post_god_chat(faction_id, message)
	var ok := _apply_response(response, true)
	_end_busy()
	return ok


func give_resource(faction_id: String, resource: String, amount: int) -> bool:
	if api_client == null:
		error_raised.emit("ApiClient 未配置")
		return false
	_begin_busy("simple", "赐予资源")
	var response: Dictionary = await api_client.post_give_resource(faction_id, resource, amount)
	var ok := _apply_response(response, true)
	_end_busy()
	return ok


func set_weather(x: int, y: int, weather: String, duration: int) -> bool:
	if api_client == null:
		error_raised.emit("ApiClient 未配置")
		return false
	_begin_busy("simple", "改变天气")
	var response: Dictionary = await api_client.post_weather(x, y, weather, duration)
	var ok := _apply_response(response, true)
	_end_busy()
	return ok


func answer_petition(petition_id: int, approve: bool) -> bool:
	if api_client == null:
		error_raised.emit("ApiClient 未配置")
		return false
	_begin_busy("simple", "处理祈求")
	var response: Dictionary = await api_client.post_answer_petition(petition_id, approve)
	var ok := _apply_response(response, true)
	_end_busy()
	return ok


func select_tile(tile) -> void:
	selected_tile = tile
	if tile == null:
		_selected_coords = Vector2i(-1, -1)
	else:
		_selected_coords = Vector2i(tile.x, tile.y)
	selection_changed.emit(selected_tile)


func busy_elapsed_seconds() -> int:
	if not is_busy:
		return 0
	return int((Time.get_ticks_msec() - busy_started_at_msec) / 1000.0)


func _begin_busy(kind: String, label: String) -> void:
	is_busy = true
	busy_kind = kind
	busy_label = label
	busy_started_at_msec = Time.get_ticks_msec()
	if kind == "strategy" or kind == "chat":
		_poll_timer.start()
	busy_changed.emit(is_busy, busy_label, busy_kind)


func _end_busy() -> void:
	_poll_timer.stop()
	_poll_in_flight = false
	is_busy = false
	busy_kind = ""
	busy_label = ""
	busy_started_at_msec = 0
	busy_changed.emit(is_busy, busy_label, busy_kind)


func _on_poll_timer_timeout() -> void:
	if _poll_in_flight or api_client == null:
		return
	_poll_in_flight = true
	var response: Dictionary = await api_client.poll_state()
	_poll_in_flight = false
	_apply_response(response, false)


func _apply_response(response: Dictionary, allow_error_signal: bool) -> bool:
	if bool(response.get("ok", false)):
		_set_state(GameTypes.GameState.from_dict(response.get("payload", {})))
		return true
	if allow_error_signal:
		error_raised.emit(str(response.get("error", "请求失败")))
	return false


func _set_state(next_state) -> void:
	var previous_state = state
	var diff := _build_state_diff(previous_state, next_state)
	state = next_state
	if _selected_coords.x >= 0 and _selected_coords.y >= 0:
		selected_tile = state.tile_at(_selected_coords.x, _selected_coords.y)
	else:
		selected_tile = null
	if previous_state != null:
		state_transition.emit(previous_state, state, diff)
	state_changed.emit(state)
	selection_changed.emit(selected_tile)


func _build_state_diff(previous_state, current_state) -> Dictionary:
	var diff := {
		"tiles": [],
		"factions": [],
		"events": [],
		"god_chats": [],
		"petitions": [],
	}
	if previous_state == null or current_state == null:
		return diff

	var previous_tiles := _index_by_key(previous_state.tiles, "display_key")
	for tile in current_state.tiles:
		var previous_tile = previous_tiles.get(tile.display_key())
		if previous_tile == null:
			continue
		var tile_change := _diff_tile(previous_tile, tile)
		if not tile_change.is_empty():
			diff["tiles"].append(tile_change)

	var previous_factions := _index_by_property(previous_state.factions, "faction_id")
	for faction in current_state.factions:
		var previous_faction = previous_factions.get(faction.faction_id)
		if previous_faction == null:
			continue
		var faction_change := _diff_faction(previous_faction, faction)
		if not faction_change.is_empty():
			diff["factions"].append(faction_change)

	diff["events"] = _new_items_by_id(previous_state.events, current_state.events, "tick", "kind", "message", "faction_id")
	diff["god_chats"] = _new_items_by_id(previous_state.god_chats, current_state.god_chats, "message_id")
	diff["petitions"] = _new_items_by_id(previous_state.petitions, current_state.petitions, "petition_id")
	return diff


func _diff_tile(previous_tile, tile) -> Dictionary:
	var changed_fields: Array[String] = []
	for field in ["terrain", "owner", "home_of", "weather", "weather_duration", "houses", "capacity", "protected"]:
		if previous_tile.get(field) != tile.get(field):
			changed_fields.append(field)
	if not _dictionary_equal(previous_tile.population, tile.population):
		changed_fields.append("population")
	if not _dictionary_equal(previous_tile.soldiers, tile.soldiers):
		changed_fields.append("soldiers")
	if not _dictionary_equal(previous_tile.professions, tile.professions):
		changed_fields.append("professions")
	if changed_fields.is_empty():
		return {}
	return {
		"x": tile.x,
		"y": tile.y,
		"tile": tile,
		"previous_tile": previous_tile,
		"fields": changed_fields,
		"population_delta": _sum_dictionary(tile.population) - _sum_dictionary(previous_tile.population),
		"soldiers_delta": _sum_dictionary(tile.soldiers) - _sum_dictionary(previous_tile.soldiers),
	}


func _diff_faction(previous_faction, faction) -> Dictionary:
	var resource_delta := {}
	for resource in faction.resources.keys():
		var delta := int(faction.resources.get(resource, 0)) - int(previous_faction.resources.get(resource, 0))
		if delta != 0:
			resource_delta[resource] = delta
	var changed_fields: Array[String] = []
	for field in ["population", "soldiers", "houses", "population_capacity", "territory_count", "eliminated"]:
		if previous_faction.get(field) != faction.get(field):
			changed_fields.append(field)
	if resource_delta.is_empty() and changed_fields.is_empty():
		return {}
	return {
		"faction_id": faction.faction_id,
		"faction": faction,
		"previous_faction": previous_faction,
		"fields": changed_fields,
		"resource_delta": resource_delta,
		"population_delta": faction.population - previous_faction.population,
		"soldiers_delta": faction.soldiers - previous_faction.soldiers,
	}


func _index_by_key(items: Array, method_name: String) -> Dictionary:
	var result := {}
	for item in items:
		result[item.call(method_name)] = item
	return result


func _index_by_property(items: Array, property_name: String) -> Dictionary:
	var result := {}
	for item in items:
		result[item.get(property_name)] = item
	return result


func _new_items_by_id(previous_items: Array, current_items: Array, id_a: String, id_b: String = "", id_c: String = "", id_d: String = "") -> Array:
	var previous_keys := {}
	for item in previous_items:
		previous_keys[_item_key(item, id_a, id_b, id_c, id_d)] = true
	var result := []
	for item in current_items:
		if not previous_keys.has(_item_key(item, id_a, id_b, id_c, id_d)):
			result.append(item)
	return result


func _item_key(item, id_a: String, id_b: String, id_c: String, id_d: String) -> String:
	var parts: Array[String] = [str(item.get(id_a))]
	for id in [id_b, id_c, id_d]:
		if not id.is_empty():
			parts.append(str(item.get(id)))
	return "|".join(parts)


func _dictionary_equal(left: Dictionary, right: Dictionary) -> bool:
	return JSON.stringify(left) == JSON.stringify(right)


func _sum_dictionary(values: Dictionary) -> int:
	var total := 0
	for value in values.values():
		total += int(value)
	return total
