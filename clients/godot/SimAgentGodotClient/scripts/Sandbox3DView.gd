class_name Sandbox3DView
extends SubViewportContainer

signal tile_selected(tile)

var state
var selected_tile

var _viewport: SubViewport
var _world_root: Node3D
var _map_root: Node3D
var _unit_root: Node3D
var _effect_root: Node3D
var _camera: Camera3D
var _selected_marker: MeshInstance3D
var _terrain_materials := {}
var _unit_textures := {}
var _terrain_textures := {}
var _faction_colors := {
	"human": Color("4b7bd7"),
	"elf": Color("5fa36b"),
	"orc": Color("c65f51"),
}


func _ready() -> void:
	stretch = false
	mouse_filter = Control.MOUSE_FILTER_STOP
	_build_scene()
	_load_assets()
	set_process(true)


func set_state(next_state) -> void:
	state = next_state
	_rebuild_world()


func set_drawing_size(size: Vector2) -> void:
	if _viewport != null:
		_viewport.size = Vector2i(max(1, int(size.x)), max(1, int(size.y)))


func set_selected_tile(tile) -> void:
	selected_tile = tile
	_update_selected_marker()


func _process(delta: float) -> void:
	if _selected_marker != null and selected_tile != null:
		_selected_marker.rotation.y += delta * 1.6


func _gui_input(event: InputEvent) -> void:
	if state == null or _camera == null:
		return
	if event is InputEventMouseButton and event.pressed and event.button_index == MOUSE_BUTTON_LEFT:
		var tile = _tile_from_screen(event.position)
		if tile != null:
			tile_selected.emit(tile)


func _build_scene() -> void:
	_viewport = SubViewport.new()
	_viewport.render_target_update_mode = SubViewport.UPDATE_ALWAYS
	_viewport.size = Vector2i(1280, 720)
	add_child(_viewport)

	_world_root = Node3D.new()
	_viewport.add_child(_world_root)

	_map_root = Node3D.new()
	_map_root.name = "MapRoot3D"
	_world_root.add_child(_map_root)

	_unit_root = Node3D.new()
	_unit_root.name = "UnitRoot3D"
	_world_root.add_child(_unit_root)

	_effect_root = Node3D.new()
	_effect_root.name = "EffectRoot3D"
	_world_root.add_child(_effect_root)

	var light := DirectionalLight3D.new()
	light.rotation_degrees = Vector3(-52.0, 36.0, 0.0)
	light.light_energy = 2.2
	_world_root.add_child(light)

	var ambient := WorldEnvironment.new()
	var environment := Environment.new()
	environment.background_mode = Environment.BG_COLOR
	environment.background_color = Color("10171a")
	environment.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	environment.ambient_light_color = Color("9fb2aa")
	environment.ambient_light_energy = 0.72
	ambient.environment = environment
	_world_root.add_child(ambient)

	_camera = Camera3D.new()
	_camera.name = "SandboxCamera"
	_camera.projection = Camera3D.PROJECTION_PERSPECTIVE
	_camera.fov = 43.0
	_world_root.add_child(_camera)

	_selected_marker = MeshInstance3D.new()
	var marker_mesh := TorusMesh.new()
	marker_mesh.inner_radius = 0.48
	marker_mesh.outer_radius = 0.54
	_selected_marker.mesh = marker_mesh
	var marker_material := StandardMaterial3D.new()
	marker_material.albedo_color = Color("fff1a8")
	marker_material.emission_enabled = true
	marker_material.emission = Color("fff1a8")
	marker_material.emission_energy_multiplier = 0.8
	_selected_marker.material_override = marker_material
	_selected_marker.visible = false
	_world_root.add_child(_selected_marker)


func _load_assets() -> void:
	_terrain_textures = {
		"plain": _load_texture("res://assets/illustrations/terrain/plain.png"),
		"forest": _load_texture("res://assets/illustrations/terrain/plain.png"),
		"hill": _load_texture("res://assets/illustrations/terrain/hill.png"),
		"mountain": _load_texture("res://assets/illustrations/terrain/hill.png"),
		"water": _load_texture("res://assets/illustrations/terrain/water.png"),
	}
	for faction_id in ["human", "elf", "orc"]:
		_unit_textures[faction_id] = {
			"idle": _load_texture("res://assets/illustrations/%s/idle.png" % faction_id),
			"farmer": _load_texture("res://assets/illustrations/%s/farmer.png" % faction_id),
			"lumberjack": _load_texture("res://assets/illustrations/%s/lumberjack.png" % faction_id),
			"miner": _load_texture("res://assets/illustrations/%s/miner.png" % faction_id),
			"builder": _load_texture("res://assets/illustrations/%s/builder.png" % faction_id),
			"soldier": _load_texture("res://assets/illustrations/%s/soldier.png" % faction_id),
			"leader": _load_texture("res://assets/illustrations/%s/leader.png" % faction_id),
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


func _rebuild_world() -> void:
	if state == null or _map_root == null:
		return
	_clear_children(_map_root)
	_clear_children(_unit_root)
	_clear_children(_effect_root)
	_position_camera()
	for tile in state.tiles:
		_add_tile(tile)
		_add_tile_units(tile)
	_update_selected_marker()


func flash_tile(x: int, y: int, color: Color = Color("f5d76e")) -> void:
	var tile = state.tile_at(x, y) if state != null else null
	if tile == null:
		return
	var marker := MeshInstance3D.new()
	var mesh := BoxMesh.new()
	mesh.size = Vector3(0.98, 0.04, 0.98)
	marker.mesh = mesh
	marker.position = Vector3(float(x), _tile_height(tile) + 0.06, float(y))
	var material := StandardMaterial3D.new()
	material.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	material.albedo_color = Color(color.r, color.g, color.b, 0.62)
	material.emission_enabled = true
	material.emission = color
	material.emission_energy_multiplier = 0.8
	marker.material_override = material
	_effect_root.add_child(marker)
	var tween := create_tween()
	tween.tween_property(marker, "scale", Vector3(1.12, 1.0, 1.12), 0.22).set_trans(Tween.TRANS_QUAD).set_ease(Tween.EASE_OUT)
	tween.parallel().tween_property(material, "albedo_color:a", 0.0, 0.22).set_trans(Tween.TRANS_QUAD).set_ease(Tween.EASE_IN)
	tween.finished.connect(marker.queue_free)


func float_text(x: int, y: int, text: String, color: Color = Color.WHITE) -> void:
	var tile = state.tile_at(x, y) if state != null else null
	if tile == null:
		return
	var label := Label3D.new()
	label.text = text
	label.billboard = BaseMaterial3D.BILLBOARD_ENABLED
	label.fixed_size = true
	label.font_size = 34
	label.modulate = color
	label.position = Vector3(float(x), _tile_height(tile) + 1.25, float(y))
	_effect_root.add_child(label)
	var tween := create_tween()
	tween.set_parallel(true)
	tween.tween_property(label, "position", label.position + Vector3(0.0, 0.6, 0.0), 0.42).set_trans(Tween.TRANS_QUAD).set_ease(Tween.EASE_OUT)
	tween.tween_property(label, "modulate:a", 0.0, 0.42).set_trans(Tween.TRANS_QUAD).set_ease(Tween.EASE_IN)
	tween.finished.connect(label.queue_free)


func travel_arrow(from_x: int, from_y: int, to_x: int, to_y: int, color: Color = Color("f0d36a")) -> void:
	var from_tile = state.tile_at(from_x, from_y) if state != null else null
	var to_tile = state.tile_at(to_x, to_y) if state != null else null
	if from_tile == null or to_tile == null:
		return
	var start := Vector3(float(from_x), _tile_height(from_tile) + 0.72, float(from_y))
	var end := Vector3(float(to_x), _tile_height(to_tile) + 0.72, float(to_y))
	var label := Label3D.new()
	label.text = ">"
	label.billboard = BaseMaterial3D.BILLBOARD_ENABLED
	label.fixed_size = true
	label.font_size = 54
	label.modulate = color
	label.position = start
	_effect_root.add_child(label)
	var tween := create_tween()
	tween.set_parallel(true)
	tween.tween_property(label, "position", end, 0.55).set_trans(Tween.TRANS_QUAD).set_ease(Tween.EASE_OUT)
	tween.tween_property(label, "modulate:a", 0.0, 0.55).set_trans(Tween.TRANS_QUAD).set_ease(Tween.EASE_IN)
	tween.finished.connect(label.queue_free)


func shake(strength: float = 6.0, duration: float = 0.16) -> void:
	var base_position := _camera.position
	var tween := create_tween()
	var pulses := 4
	for index in range(pulses):
		var sign := -1.0 if index % 2 == 0 else 1.0
		var offset := Vector3(sign * strength * 0.012, 0.0, -sign * strength * 0.008)
		tween.tween_property(_camera, "position", base_position + offset, duration / float(pulses))
	tween.tween_property(_camera, "position", base_position, duration / float(pulses))


func _position_camera() -> void:
	var center: Vector3 = Vector3(float(state.width) * 0.5 - 0.5, 0.0, float(state.height) * 0.5 - 0.5)
	var span: float = max(float(state.width), float(state.height))
	_camera.position = center + Vector3(span * 0.42, span * 0.72, span * 0.78)
	_camera.look_at(center, Vector3.UP)


func _add_tile(tile) -> void:
	var mesh_instance := MeshInstance3D.new()
	mesh_instance.name = "Tile_%d_%d" % [tile.x, tile.y]
	var height := _tile_height(tile)
	var mesh := BoxMesh.new()
	mesh.size = Vector3(0.96, height, 0.96)
	mesh_instance.mesh = mesh
	mesh_instance.position = Vector3(float(tile.x), height * 0.5 - 0.08, float(tile.y))
	mesh_instance.material_override = _terrain_material(tile)
	_map_root.add_child(mesh_instance)

	if not tile.owner.is_empty():
		var ring := MeshInstance3D.new()
		var ring_mesh := TorusMesh.new()
		ring_mesh.inner_radius = 0.43
		ring_mesh.outer_radius = 0.48
		ring.mesh = ring_mesh
		ring.rotation_degrees.x = 90.0
		ring.position = Vector3(float(tile.x), height + 0.012, float(tile.y))
		var ring_material := StandardMaterial3D.new()
		ring_material.albedo_color = _faction_colors.get(tile.owner, Color.WHITE)
		ring_material.emission_enabled = true
		ring_material.emission = ring_material.albedo_color
		ring_material.emission_energy_multiplier = 0.28
		ring.material_override = ring_material
		_map_root.add_child(ring)

	if tile.weather != "clear":
		_add_weather_marker(tile, height)


func _add_tile_units(tile) -> void:
	if tile.owner.is_empty():
		return
	var faction_id: String = tile.owner
	var unit_kind: String = _dominant_unit_kind(tile, faction_id)
	if unit_kind.is_empty():
		return
	var sprite := Sprite3D.new()
	sprite.name = "Unit_%d_%d" % [tile.x, tile.y]
	sprite.texture = _unit_textures.get(faction_id, {}).get(unit_kind)
	sprite.billboard = BaseMaterial3D.BILLBOARD_ENABLED
	sprite.pixel_size = 0.0012
	sprite.fixed_size = true
	sprite.modulate = Color.WHITE
	sprite.position = Vector3(float(tile.x), _tile_height(tile) + 0.74, float(tile.y))
	_unit_root.add_child(sprite)

	if tile.home_of == faction_id:
		var leader := Sprite3D.new()
		leader.name = "Leader_%d_%d" % [tile.x, tile.y]
		leader.texture = _unit_textures.get(faction_id, {}).get("leader")
		leader.billboard = BaseMaterial3D.BILLBOARD_ENABLED
		leader.pixel_size = 0.0010
		leader.fixed_size = true
		leader.position = Vector3(float(tile.x) + 0.22, _tile_height(tile) + 1.05, float(tile.y) - 0.18)
		_unit_root.add_child(leader)


func _add_weather_marker(tile, height: float) -> void:
	var marker := MeshInstance3D.new()
	var mesh := SphereMesh.new()
	mesh.radius = 0.18
	mesh.height = 0.36
	marker.mesh = mesh
	marker.position = Vector3(float(tile.x) - 0.28, height + 0.22, float(tile.y) - 0.28)
	var material := StandardMaterial3D.new()
	if tile.weather == "rain":
		material.albedo_color = Color(0.35, 0.68, 1.0, 0.70)
	elif tile.weather == "drought":
		material.albedo_color = Color(0.95, 0.58, 0.22, 0.78)
	else:
		material.albedo_color = Color(0.72, 0.70, 0.86, 0.85)
	material.emission_enabled = true
	material.emission = material.albedo_color
	material.emission_energy_multiplier = 0.45
	marker.material_override = material
	_map_root.add_child(marker)


func _terrain_material(tile) -> StandardMaterial3D:
	var key := "%s:%s" % [tile.terrain, tile.owner]
	if _terrain_materials.has(key):
		return _terrain_materials[key]
	var material := StandardMaterial3D.new()
	material.albedo_texture = _terrain_textures.get(tile.terrain)
	material.roughness = 0.9
	material.uv1_scale = Vector3(2.0, 2.0, 2.0)
	material.albedo_color = _terrain_tint(tile)
	_terrain_materials[key] = material
	return material


func _terrain_tint(tile) -> Color:
	var tint := Color.WHITE
	if tile.terrain == "forest":
		tint = Color("5a8f5d")
	elif tile.terrain == "mountain":
		tint = Color("9a9a9a")
	if not tile.owner.is_empty():
		tint = tint.lerp(_faction_colors.get(tile.owner, Color.WHITE), 0.22)
	return tint


func _tile_height(tile) -> float:
	match tile.terrain:
		"water":
			return 0.08
		"hill":
			return 0.34
		"mountain":
			return 0.62
		"forest":
			return 0.24
		_:
			return 0.16


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


func _tile_from_screen(screen_position: Vector2):
	var ray_origin := _camera.project_ray_origin(screen_position)
	var ray_direction := _camera.project_ray_normal(screen_position)
	if abs(ray_direction.y) < 0.0001:
		return null
	var distance := -ray_origin.y / ray_direction.y
	if distance < 0.0:
		return null
	var hit := ray_origin + ray_direction * distance
	var x := int(round(hit.x))
	var y := int(round(hit.z))
	if x < 0 or y < 0 or x >= state.width or y >= state.height:
		return null
	return state.tile_at(x, y)


func _update_selected_marker() -> void:
	if _selected_marker == null:
		return
	if selected_tile == null:
		_selected_marker.visible = false
		return
	_selected_marker.visible = true
	_selected_marker.position = Vector3(
		float(selected_tile.x),
		_tile_height(selected_tile) + 0.08,
		float(selected_tile.y)
	)


func _clear_children(node: Node) -> void:
	for child in node.get_children():
		child.queue_free()
