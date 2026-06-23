class_name MapView
extends Node2D

signal tile_selected(tile)

const ASSET_ROOT := "res://assets/illustrations"
const MAP_PADDING := 18.0
const ROW_STEP_RATIO := 0.34
const TEXTURE_ANCHOR_Y := 0.34

var state
var selected_tile
var drawing_size := Vector2(960, 640)
var tile_size := 32.0
var tile_width := 32.0
var tile_height := 22.0
var row_step := 8.0
var origin := Vector2.ZERO
var _weather_phase := 0.0
var _terrain_textures := {}
var _unit_textures := {}
var _assets_loaded := false

var _terrain_colors := {
	"plain": Color("7bbb55"),
	"forest": Color("3f8f45"),
	"hill": Color("88a45a"),
	"water": Color("4ba7d4"),
	"mountain": Color("8d8a73"),
}

var _faction_colors := {
	"human": Color("4b7bd7"),
	"elf": Color("5fa36b"),
	"orc": Color("c65f51"),
}


func _ready() -> void:
	_load_assets()


func set_state(next_state) -> void:
	state = next_state
	update_layout_metrics()
	queue_redraw()


func set_selected_tile(tile) -> void:
	selected_tile = tile
	queue_redraw()


func set_drawing_size(size: Vector2) -> void:
	drawing_size = size
	update_layout_metrics()
	queue_redraw()


func update_layout_metrics() -> void:
	if state == null:
		origin = Vector2(MAP_PADDING, MAP_PADDING)
		return

	var texture_ratio := _terrain_height_ratio()
	var available_width: float = max(96.0, drawing_size.x - MAP_PADDING * 2.0)
	var available_height: float = max(96.0, drawing_size.y - MAP_PADDING * 2.0)
	var width_span: float = max(1.0, float(state.width + state.height) * 0.5)
	var height_span: float = max(1.0, float(max(0, state.width + state.height - 2)) * texture_ratio * ROW_STEP_RATIO + texture_ratio)
	tile_width = floor(max(18.0, min(available_width / width_span, available_height / height_span)))
	tile_height = tile_width * texture_ratio
	row_step = max(4.0, tile_height * ROW_STEP_RATIO)
	tile_size = tile_width

	var map_width: float = float(state.width + state.height) * tile_width * 0.5
	var map_height: float = float(max(0, state.width + state.height - 2)) * row_step + tile_height
	var map_left: float = MAP_PADDING + floor((available_width - map_width) * 0.5)
	var map_top: float = MAP_PADDING + floor((available_height - map_height) * 0.5)
	origin = Vector2(
		map_left + float(state.height) * tile_width * 0.5,
		map_top + tile_height * TEXTURE_ANCHOR_Y
	)


func tile_center(x: int, y: int) -> Vector2:
	return origin + Vector2(float(x - y) * tile_width * 0.5, float(x + y) * row_step)


func tile_rect(x: int, y: int) -> Rect2:
	var center := tile_center(x, y)
	return Rect2(center - Vector2(tile_width * 0.5, tile_height * TEXTURE_ANCHOR_Y), Vector2(tile_width, tile_height))


func handle_gui_input(event: InputEvent) -> void:
	if state == null:
		return
	if event is InputEventMouseButton and event.pressed and event.button_index == MOUSE_BUTTON_LEFT:
		var tile = _tile_from_position(event.position)
		if tile != null:
			tile_selected.emit(tile)


func _process(delta: float) -> void:
	_weather_phase = fmod(_weather_phase + delta, 10.0)
	if state != null:
		queue_redraw()


func _draw() -> void:
	draw_rect(Rect2(Vector2.ZERO, drawing_size), Color("2f86bd"), true)
	if state == null:
		return
	update_layout_metrics()

	var sorted_tiles: Array = state.tiles.duplicate()
	sorted_tiles.sort_custom(func(a, b):
		var depth_a: int = int(a.x) + int(a.y)
		var depth_b: int = int(b.x) + int(b.y)
		if depth_a == depth_b:
			return int(a.x) < int(b.x)
		return depth_a < depth_b
	)

	for tile in sorted_tiles:
		_draw_tile_base(tile)
	for tile in sorted_tiles:
		_draw_tile_labels(tile)


func _draw_tile_base(tile) -> void:
	var terrain_color: Color = _terrain_colors.get(tile.terrain, Color("4d6849"))
	draw_colored_polygon(_tile_diamond_points(tile.x, tile.y, 0.98), terrain_color)
	_draw_terrain_texture(tile)

	if tile.terrain != "water" and _has_water_neighbor(tile.x, tile.y):
		_draw_polygon_outline(_tile_diamond_points(tile.x, tile.y, 0.84), Color(0.92, 0.82, 0.52, 0.34), max(1.0, tile_width * 0.035))

	if not tile.owner.is_empty():
		var overlay: Color = _faction_colors.get(tile.owner, Color("a8a8a8"))
		overlay.a = 0.15
		draw_colored_polygon(_tile_diamond_points(tile.x, tile.y, 0.94), overlay)
		_draw_polygon_outline(_tile_diamond_points(tile.x, tile.y, 0.92), _faction_colors.get(tile.owner, Color.WHITE), max(1.0, tile_width * 0.028))

	_draw_weather_overlay(tile)

	if tile.home_of:
		var home_center := tile_center(tile.x, tile.y) + Vector2(-tile_width * 0.18, -row_step * 0.18)
		draw_circle(home_center, max(3.5, tile_width * 0.075), Color("ffe27a"))

	if tile.protected:
		_draw_polygon_outline(_tile_diamond_points(tile.x, tile.y, 0.74), Color("f1f2b8"), max(1.5, tile_width * 0.04))

	_draw_tile_unit(tile)

	if selected_tile != null and tile.x == selected_tile.x and tile.y == selected_tile.y:
		_draw_polygon_outline(_tile_diamond_points(tile.x, tile.y, 1.04), Color.WHITE, max(2.0, tile_width * 0.045))


func _draw_tile_labels(tile) -> void:
	if tile.weather == "clear":
		return
	var font: Font = ThemeDB.fallback_font
	if font == null:
		return
	var font_size: int = max(10, int(tile_width * 0.22))
	var short_label: String = _weather_label(tile.weather)
	var duration_label: String = str(tile.weather_duration) if tile.weather_duration > 0 else ""
	var center := tile_center(tile.x, tile.y)
	draw_string(
		font,
		center + Vector2(-tile_width * 0.34, row_step * 0.55),
		"%s%s" % [short_label, duration_label],
		HORIZONTAL_ALIGNMENT_CENTER,
		tile_width * 0.68,
		font_size,
		Color.WHITE
	)


func _draw_terrain_texture(tile) -> void:
	var texture: Texture2D = _terrain_texture(tile.terrain)
	if texture == null:
		return
	var tint := _terrain_tint(tile.terrain)
	draw_texture_rect_region(texture, tile_rect(tile.x, tile.y), _full_texture_region(texture), tint)


func _terrain_tint(terrain: String) -> Color:
	match terrain:
		_:
			return Color.WHITE


func _draw_tile_unit(tile) -> void:
	if tile.owner.is_empty():
		return
	var kind := _dominant_unit_kind(tile, tile.owner)
	var texture: Texture2D = _unit_texture(tile.owner, kind)
	if texture != null:
		_draw_billboard_icon(texture, tile.x, tile.y, 0.70, Vector2(0.0, -0.10))
	if tile.home_of == tile.owner:
		var leader_texture: Texture2D = _unit_texture(tile.owner, "leader")
		if leader_texture != null:
			_draw_billboard_icon(leader_texture, tile.x, tile.y, 0.88, Vector2(0.18, -0.30))


func _draw_billboard_icon(texture: Texture2D, x: int, y: int, scale_factor: float, offset_tiles: Vector2) -> void:
	var max_side: float = tile_width * scale_factor
	var texture_size := Vector2(float(texture.get_width()), float(texture.get_height()))
	if texture_size.x <= 0.0 or texture_size.y <= 0.0:
		return
	var factor: float = max_side / max(texture_size.x, texture_size.y)
	var size: Vector2 = texture_size * factor
	var center: Vector2 = tile_center(x, y) + Vector2(offset_tiles.x * tile_width, offset_tiles.y * tile_height) - Vector2(0.0, row_step * 0.70)
	var target := Rect2(center - size * 0.5, size)
	draw_texture_rect(texture, target, false)


func _dominant_unit_kind(tile, faction_id: String) -> String:
	if int(tile.soldiers.get(faction_id, 0)) > 0:
		return "soldier"
	var jobs: Dictionary = tile.professions.get(faction_id, {})
	var best_kind := ""
	var best_count := 0
	for kind in ["farmer", "lumberjack", "miner", "builder", "idle"]:
		var count := int(jobs.get(kind, 0))
		if count > best_count:
			best_kind = kind
			best_count = count
	return best_kind


func _has_water_neighbor(x: int, y: int) -> bool:
	for offset in [Vector2i(1, 0), Vector2i(-1, 0), Vector2i(0, 1), Vector2i(0, -1)]:
		var tile = state.tile_at(x + offset.x, y + offset.y)
		if tile != null and tile.terrain == "water":
			return true
	return false


func _tile_from_position(position: Vector2):
	if state == null or tile_width <= 0.0 or row_step <= 0.0:
		return null
	var local := position - origin
	var half_width := tile_width * 0.5
	var diagonal_x := local.x / half_width
	var diagonal_y := local.y / row_step
	var approx_x := int(round((diagonal_y + diagonal_x) * 0.5))
	var approx_y := int(round((diagonal_y - diagonal_x) * 0.5))

	var best_tile = null
	var best_score := INF
	for y in range(approx_y - 2, approx_y + 3):
		for x in range(approx_x - 2, approx_x + 3):
			if x < 0 or y < 0 or x >= state.width or y >= state.height:
				continue
			var center := tile_center(x, y)
			var score: float = abs(position.x - center.x) / max(1.0, half_width) + abs(position.y - center.y) / max(1.0, row_step)
			if score < best_score:
				best_score = score
				best_tile = state.tile_at(x, y)
	if best_score > 1.65:
		return null
	return best_tile


func _tile_diamond_points(x: int, y: int, scale: float = 1.0) -> PackedVector2Array:
	var center := tile_center(x, y)
	var half_width := tile_width * 0.5 * scale
	var half_height := row_step * scale
	return PackedVector2Array([
		center + Vector2(0.0, -half_height),
		center + Vector2(half_width, 0.0),
		center + Vector2(0.0, half_height),
		center + Vector2(-half_width, 0.0),
	])


func _draw_polygon_outline(points: PackedVector2Array, color: Color, width: float = 1.0) -> void:
	for index in range(points.size()):
		draw_line(points[index], points[(index + 1) % points.size()], color, width)


func _weather_label(weather: String) -> String:
	match weather:
		"rain":
			return "雨"
		"drought":
			return "旱"
		"storm":
			return "暴"
		_:
			return ""


func _draw_weather_overlay(tile) -> void:
	if tile.weather == "clear":
		return
	var points := _tile_diamond_points(tile.x, tile.y, 0.92)
	var rect := tile_rect(tile.x, tile.y)
	if tile.weather == "rain":
		draw_colored_polygon(points, Color(0.32, 0.60, 0.80, 0.18))
		var slide := fmod(_weather_phase * tile_width * 1.8 + float(tile.x + tile.y) * 5.0, tile_width)
		for i in range(-1, 4):
			var start := rect.position + Vector2(float(i) * tile_width * 0.28 + slide * 0.35, fmod(slide + float(i) * 7.0, tile_height))
			draw_line(start, start + Vector2(-4.0, 8.0), Color(0.72, 0.86, 1.0, 0.62), 1.1)
	elif tile.weather == "drought":
		var pulse := 0.26 + 0.09 * sin(_weather_phase * 5.0 + float(tile.x + tile.y))
		draw_colored_polygon(points, Color(0.80, 0.56, 0.28, pulse))
		var crack_color := Color(0.30, 0.18, 0.10, 0.42)
		var center := tile_center(tile.x, tile.y)
		draw_line(center + Vector2(-tile_width * 0.18, row_step * 0.42), center + Vector2(-tile_width * 0.02, -row_step * 0.12), crack_color, 1.0)
		draw_line(center + Vector2(-tile_width * 0.02, -row_step * 0.12), center + Vector2(tile_width * 0.20, row_step * 0.20), crack_color, 1.0)
	elif tile.weather == "storm":
		var flash := 1.0 if fmod(_weather_phase + float(tile.x * 3 + tile.y), 1.8) < 0.11 else 0.0
		draw_colored_polygon(points, Color(0.09, 0.10, 0.14, 0.58 + flash * 0.16))
		var bolt_color := Color(1.0, 0.87, 0.38, 0.88 if flash > 0.0 else 0.48)
		var center := tile_center(tile.x, tile.y)
		draw_line(center + Vector2(-tile_width * 0.06, -row_step * 0.78), center + Vector2(tile_width * 0.10, -row_step * 0.10), bolt_color, 1.6)
		draw_line(center + Vector2(tile_width * 0.10, -row_step * 0.10), center + Vector2(-tile_width * 0.02, -row_step * 0.10), bolt_color, 1.6)
		draw_line(center + Vector2(-tile_width * 0.02, -row_step * 0.10), center + Vector2(tile_width * 0.16, row_step * 0.70), bolt_color, 1.6)


func _load_assets() -> void:
	if _assets_loaded:
		return
	_assets_loaded = true
	_terrain_textures = {
		"plain": _load_texture("%s/terrain/plain.png" % ASSET_ROOT),
		"forest": _load_texture("%s/terrain/forest.png" % ASSET_ROOT),
		"hill": _load_texture("%s/terrain/hill.png" % ASSET_ROOT),
		"mountain": _load_texture("%s/terrain/mountain.png" % ASSET_ROOT),
		"water": _load_texture("%s/terrain/water.png" % ASSET_ROOT),
	}
	for faction_id in ["human", "elf", "orc"]:
		_unit_textures[faction_id] = {
			"idle": _load_texture("%s/%s/idle.png" % [ASSET_ROOT, faction_id]),
			"farmer": _load_texture("%s/%s/farmer.png" % [ASSET_ROOT, faction_id]),
			"lumberjack": _load_texture("%s/%s/lumberjack.png" % [ASSET_ROOT, faction_id]),
			"miner": _load_texture("%s/%s/miner.png" % [ASSET_ROOT, faction_id]),
			"builder": _load_texture("%s/%s/builder.png" % [ASSET_ROOT, faction_id]),
			"soldier": _load_texture("%s/%s/soldier.png" % [ASSET_ROOT, faction_id]),
			"leader": _load_texture("%s/%s/leader.png" % [ASSET_ROOT, faction_id]),
		}


func _load_texture(path: String) -> Texture2D:
	var image := Image.new()
	var error := image.load(ProjectSettings.globalize_path(path))
	if error != OK:
		push_warning("Failed to load texture %s: %s" % [path, error_string(error)])
		var fallback := Image.create(8, 8, false, Image.FORMAT_RGBA8)
		fallback.fill(Color("d8ac55"))
		return ImageTexture.create_from_image(fallback)
	return ImageTexture.create_from_image(image)


func _terrain_texture(terrain: String) -> Texture2D:
	return _terrain_textures.get(terrain)


func _unit_texture(faction_id: String, kind: String) -> Texture2D:
	if faction_id.is_empty() or kind.is_empty():
		return null
	return _unit_textures.get(faction_id, {}).get(kind)


func _terrain_height_ratio() -> float:
	var texture: Texture2D = _terrain_texture("plain")
	if texture == null or texture.get_width() <= 0:
		return 0.67
	return float(texture.get_height()) / float(texture.get_width())


func _full_texture_region(texture: Texture2D) -> Rect2:
	return Rect2(Vector2.ZERO, Vector2(float(texture.get_width()), float(texture.get_height())))
