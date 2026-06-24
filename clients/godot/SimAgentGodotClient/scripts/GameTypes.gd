class_name GameTypes
extends RefCounted


class ApiError:
	extends RefCounted

	var status_code: int = 0
	var error: String = ""

	static func from_payload(status_code_value: int, payload: Variant) -> ApiError:
		var item := ApiError.new()
		item.status_code = status_code_value
		if payload is Dictionary:
			item.error = str(payload.get("error", ""))
		if item.error.is_empty():
			if status_code_value > 0:
				item.error = "HTTP %d" % status_code_value
			else:
				item.error = "unknown error"
		return item


class TileState:
	extends RefCounted

	var x: int = 0
	var y: int = 0
	var terrain: String = ""
	var owner: String = ""
	var home_of: String = ""
	var weather: String = "clear"
	var weather_duration: int = 0
	var population: Dictionary = {}
	var soldiers: Dictionary = {}
	var professions: Dictionary = {}
	var houses: int = 0
	var capacity: int = 0
	var protected: bool = false

	static func from_dict(data: Dictionary) -> TileState:
		var item := TileState.new()
		item.x = int(data.get("x", 0))
		item.y = int(data.get("y", 0))
		item.terrain = str(data.get("terrain", "plain"))
		item.owner = "" if data.get("owner") == null else str(data.get("owner"))
		item.home_of = "" if data.get("home_of") == null else str(data.get("home_of"))
		item.weather = str(data.get("weather", "clear"))
		item.weather_duration = int(data.get("weather_duration", 0))
		if data.get("population") is Dictionary:
			for key in data["population"].keys():
				item.population[str(key)] = int(data["population"][key])
		if data.get("soldiers") is Dictionary:
			for key in data["soldiers"].keys():
				item.soldiers[str(key)] = int(data["soldiers"][key])
		if data.get("professions") is Dictionary:
			for key in data["professions"].keys():
				var jobs: Dictionary = {}
				if data["professions"][key] is Dictionary:
					for job_key in data["professions"][key].keys():
						jobs[str(job_key)] = int(data["professions"][key][job_key])
				item.professions[str(key)] = jobs
		item.houses = int(data.get("houses", 0))
		item.capacity = int(data.get("capacity", 0))
		item.protected = bool(data.get("protected", false))
		return item

	func display_key() -> String:
		return "%d:%d" % [x, y]


class FactionState:
	extends RefCounted

	var faction_id: String = ""
	var name: String = ""
	var leader_name: String = ""
	var resources: Dictionary = {}
	var population: int = 0
	var soldiers: int = 0
	var jobs: Dictionary = {}
	var houses: int = 0
	var population_capacity: int = 0
	var territory_count: int = 0
	var home_tile: Dictionary = {}
	var eliminated: bool = false
	var known_factions: Array[String] = []
	var diplomacy: Dictionary = {}
	var last_plan_snapshot: Dictionary = {}
	var leader_memory: Dictionary = {}
	var leader_context_window_count: int = 0

	static func from_dict(data: Dictionary) -> FactionState:
		var item := FactionState.new()
		item.faction_id = str(data.get("faction_id", ""))
		item.name = str(data.get("name", ""))
		item.leader_name = str(data.get("leader_name", ""))
		if data.get("resources") is Dictionary:
			for key in data["resources"].keys():
				item.resources[str(key)] = int(data["resources"][key])
		item.population = int(data.get("population", 0))
		item.soldiers = int(data.get("soldiers", 0))
		if data.get("jobs") is Dictionary:
			for key in data["jobs"].keys():
				item.jobs[str(key)] = int(data["jobs"][key])
		item.houses = int(data.get("houses", 0))
		item.population_capacity = int(data.get("population_capacity", 0))
		item.territory_count = int(data.get("territory_count", 0))
		if data.get("home_tile") is Dictionary:
			item.home_tile = data["home_tile"].duplicate(true)
		item.eliminated = bool(data.get("eliminated", false))
		if data.get("known_factions") is Array:
			for value in data["known_factions"]:
				item.known_factions.append(str(value))
		if data.get("diplomacy") is Dictionary:
			for key in data["diplomacy"].keys():
				item.diplomacy[str(key)] = str(data["diplomacy"][key])
		if data.get("last_plan_snapshot") is Dictionary:
			item.last_plan_snapshot = data["last_plan_snapshot"].duplicate(true)
		if data.get("leader_memory") is Dictionary:
			item.leader_memory = data["leader_memory"].duplicate(true)
		item.leader_context_window_count = int(data.get("leader_context_window_count", 0))
		return item


class PetitionState:
	extends RefCounted

	var petition_id: int = 0
	var faction_id: String = ""
	var kind: String = ""
	var request: Dictionary = {}
	var reason: String = ""
	var urgency: String = "medium"
	var status: String = "pending"
	var created_tick: int = 0

	static func from_dict(data: Dictionary) -> PetitionState:
		var item := PetitionState.new()
		item.petition_id = int(data.get("petition_id", 0))
		item.faction_id = str(data.get("faction_id", ""))
		item.kind = str(data.get("kind", ""))
		if data.get("request") is Dictionary:
			item.request = data["request"].duplicate(true)
		item.reason = str(data.get("reason", ""))
		item.urgency = str(data.get("urgency", "medium"))
		item.status = str(data.get("status", "pending"))
		item.created_tick = int(data.get("created_tick", 0))
		return item


class GodChatMessage:
	extends RefCounted

	var message_id: int = 0
	var tick: int = 0
	var faction_id: String = ""
	var speaker: String = ""
	var content: String = ""

	static func from_dict(data: Dictionary) -> GodChatMessage:
		var item := GodChatMessage.new()
		item.message_id = int(data.get("message_id", 0))
		item.tick = int(data.get("tick", 0))
		item.faction_id = str(data.get("faction_id", ""))
		item.speaker = str(data.get("speaker", ""))
		item.content = str(data.get("content", ""))
		return item


class EventEntry:
	extends RefCounted

	var tick: int = 0
	var kind: String = ""
	var message: String = ""
	var faction_id: String = ""

	static func from_dict(data: Dictionary) -> EventEntry:
		var item := EventEntry.new()
		item.tick = int(data.get("tick", 0))
		item.kind = str(data.get("kind", ""))
		item.message = str(data.get("message", ""))
		item.faction_id = "" if data.get("faction_id") == null else str(data.get("faction_id"))
		return item


class GameState:
	extends RefCounted

	var tick: int = 0
	var seed: int = 0
	var width: int = 0
	var height: int = 0
	var paused: bool = false
	var pause_reason: String = ""
	var resources: Array[String] = []
	var weather_types: Array[String] = []
	var tiles: Array = []
	var factions: Array = []
	var petitions: Array = []
	var god_chats: Array = []
	var events: Array = []
	var _tile_index: Dictionary = {}

	static func from_dict(data: Dictionary) -> GameState:
		var item := GameState.new()
		item.tick = int(data.get("tick", 0))
		item.seed = int(data.get("seed", 0))
		item.width = int(data.get("width", 0))
		item.height = int(data.get("height", 0))
		item.paused = bool(data.get("paused", false))
		item.pause_reason = "" if data.get("pause_reason") == null else str(data.get("pause_reason"))
		if data.get("resources") is Array:
			for value in data["resources"]:
				item.resources.append(str(value))
		if data.get("weather_types") is Array:
			for value in data["weather_types"]:
				item.weather_types.append(str(value))
		if data.get("tiles") is Array:
			for entry in data["tiles"]:
				if entry is Dictionary:
					item.tiles.append(TileState.from_dict(entry))
		if data.get("factions") is Array:
			for entry in data["factions"]:
				if entry is Dictionary:
					item.factions.append(FactionState.from_dict(entry))
		if data.get("petitions") is Array:
			for entry in data["petitions"]:
				if entry is Dictionary:
					item.petitions.append(PetitionState.from_dict(entry))
		if data.get("god_chats") is Array:
			for entry in data["god_chats"]:
				if entry is Dictionary:
					item.god_chats.append(GodChatMessage.from_dict(entry))
		if data.get("events") is Array:
			for entry in data["events"]:
				if entry is Dictionary:
					item.events.append(EventEntry.from_dict(entry))
		for tile in item.tiles:
			item._tile_index[tile.display_key()] = tile
		return item

	func tile_at(x: int, y: int):
		return _tile_index.get("%d:%d" % [x, y])

