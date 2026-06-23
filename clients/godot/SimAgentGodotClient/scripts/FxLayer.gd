class_name FxLayer
extends Node2D

var drawing_size := Vector2(960, 640)
var map_origin := Vector2.ZERO
var tile_size := 24.0
var tile_width := 24.0
var tile_height := 16.0
var row_step := 6.0
var visual_subdivisions := 1

var _flashes: Array[Dictionary] = []
var _arrows: Array[Dictionary] = []
var _shake_age := 0.0
var _shake_duration := 0.0
var _shake_strength := 0.0


func _ready() -> void:
	set_process(true)


func set_map_metrics(origin: Vector2, current_tile_size: float, size: Vector2) -> void:
	map_origin = origin
	tile_size = current_tile_size
	tile_width = current_tile_size
	tile_height = current_tile_size
	row_step = current_tile_size * 0.5
	visual_subdivisions = 1
	drawing_size = size
	queue_redraw()


func set_iso_metrics(origin: Vector2, current_tile_width: float, current_tile_height: float, current_row_step: float, size: Vector2, subdivisions: int = 1) -> void:
	map_origin = origin
	tile_width = current_tile_width
	tile_height = current_tile_height
	row_step = current_row_step
	tile_size = current_tile_width
	visual_subdivisions = max(1, subdivisions)
	drawing_size = size
	queue_redraw()


func flash_tile(x: int, y: int, color: Color = Color("f5d76e")) -> void:
	_flashes.append({
		"x": x,
		"y": y,
		"age": 0.0,
		"duration": 0.22,
		"color": color,
	})
	queue_redraw()


func travel_arrow(from_x: int, from_y: int, to_x: int, to_y: int, color: Color = Color("f0d36a")) -> void:
	_arrows.append({
		"from_x": from_x,
		"from_y": from_y,
		"to_x": to_x,
		"to_y": to_y,
		"age": 0.0,
		"duration": 0.55,
		"color": color,
	})
	queue_redraw()


func float_text(x: int, y: int, text: String, color: Color = Color.WHITE) -> void:
	var label := Label.new()
	label.text = text
	label.modulate = color
	label.add_theme_font_size_override("font_size", 15)
	label.position = _tile_center(x, y) + Vector2(-18.0, -12.0)
	label.z_index = 20
	add_child(label)

	var tween := create_tween()
	tween.set_parallel(true)
	tween.tween_property(label, "position", label.position + Vector2(0.0, -28.0), 0.38).set_trans(Tween.TRANS_QUAD).set_ease(Tween.EASE_OUT)
	tween.tween_property(label, "modulate:a", 0.0, 0.38).set_trans(Tween.TRANS_QUAD).set_ease(Tween.EASE_IN)
	tween.finished.connect(label.queue_free)


func shake(strength: float = 6.0, duration: float = 0.16) -> void:
	_shake_strength = strength
	_shake_duration = duration
	_shake_age = 0.001


func _process(delta: float) -> void:
	var needs_redraw := false
	for flash in _flashes:
		flash["age"] = float(flash["age"]) + delta
	_flashes = _flashes.filter(func(flash): return float(flash["age"]) < float(flash["duration"]))
	for arrow in _arrows:
		arrow["age"] = float(arrow["age"]) + delta
	_arrows = _arrows.filter(func(arrow): return float(arrow["age"]) < float(arrow["duration"]))
	if _shake_age > 0.0:
		_shake_age += delta
		var progress: float = clamp(_shake_age / max(_shake_duration, 0.001), 0.0, 1.0)
		var power: float = (1.0 - progress) * _shake_strength
		position = Vector2(
			sin(_shake_age * 95.0) * power,
			cos(_shake_age * 113.0) * power * 0.72
		)
		if progress >= 1.0:
			_shake_age = 0.0
			position = Vector2.ZERO
		needs_redraw = true
	if not _flashes.is_empty() or not _arrows.is_empty():
		needs_redraw = true
	if needs_redraw:
		queue_redraw()


func _draw() -> void:
	for flash in _flashes:
		var age: float = float(flash["age"])
		var duration: float = float(flash["duration"])
		var alpha: float = clamp(1.0 - age / duration, 0.0, 1.0)
		var color: Color = flash["color"]
		color.a = 0.58 * alpha
		var points := _tile_diamond_points(int(flash["x"]), int(flash["y"]), 1.04)
		draw_colored_polygon(points, color)
		_draw_polygon_outline(points, Color(color.r, color.g, color.b, 0.9 * alpha), 2.0)
	for arrow in _arrows:
		_draw_arrow(arrow)


func _tile_center(x: int, y: int) -> Vector2:
	var middle := visual_subdivisions / 2
	return _visual_cell_center(x * visual_subdivisions + middle, y * visual_subdivisions + middle)


func _tile_rect(x: int, y: int) -> Rect2:
	var points := _tile_diamond_points(x, y, 1.0)
	var min_x := INF
	var min_y := INF
	var max_x := -INF
	var max_y := -INF
	for point in points:
		min_x = min(min_x, point.x)
		min_y = min(min_y, point.y)
		max_x = max(max_x, point.x)
		max_y = max(max_y, point.y)
	return Rect2(Vector2(min_x, min_y), Vector2(max_x - min_x, max_y - min_y))


func _tile_diamond_points(x: int, y: int, scale: float = 1.0) -> PackedVector2Array:
	var first_x := x * visual_subdivisions
	var first_y := y * visual_subdivisions
	var last_x := first_x + visual_subdivisions - 1
	var last_y := first_y + visual_subdivisions - 1
	var top := _visual_cell_center(first_x, first_y) + Vector2(0.0, -row_step)
	var right := _visual_cell_center(last_x, first_y) + Vector2(tile_width * 0.5, 0.0)
	var bottom := _visual_cell_center(last_x, last_y) + Vector2(0.0, row_step)
	var left := _visual_cell_center(first_x, last_y) + Vector2(-tile_width * 0.5, 0.0)
	var center := (top + right + bottom + left) * 0.25
	return PackedVector2Array([
		center + (top - center) * scale,
		center + (right - center) * scale,
		center + (bottom - center) * scale,
		center + (left - center) * scale,
	])


func _visual_cell_center(visual_x: int, visual_y: int) -> Vector2:
	return map_origin + Vector2(float(visual_x - visual_y) * tile_width * 0.5, float(visual_x + visual_y) * row_step)


func _draw_polygon_outline(points: PackedVector2Array, color: Color, width: float = 1.0) -> void:
	for index in range(points.size()):
		draw_line(points[index], points[(index + 1) % points.size()], color, width)


func _draw_arrow(arrow: Dictionary) -> void:
	var age: float = float(arrow["age"])
	var duration: float = float(arrow["duration"])
	var progress: float = clamp(age / duration, 0.0, 1.0)
	var alpha: float = sin(progress * PI)
	var start: Vector2 = _tile_center(int(arrow["from_x"]), int(arrow["from_y"]))
	var end: Vector2 = _tile_center(int(arrow["to_x"]), int(arrow["to_y"]))
	var current_end: Vector2 = start.lerp(end, clamp(progress * 1.25, 0.0, 1.0))
	var color: Color = arrow["color"]
	color.a = 0.92 * alpha
	draw_line(start, current_end, color, 3.0)

	var direction: Vector2 = (current_end - start).normalized()
	if direction.length() <= 0.0:
		return
	var side: Vector2 = Vector2(-direction.y, direction.x)
	var head_size: float = max(5.0, tile_size * 0.22)
	var p1: Vector2 = current_end
	var p2: Vector2 = current_end - direction * head_size + side * head_size * 0.55
	var p3: Vector2 = current_end - direction * head_size - side * head_size * 0.55
	draw_colored_polygon(PackedVector2Array([p1, p2, p3]), color)
