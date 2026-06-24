class_name ApiClient
extends Node

const GameTypes = preload("res://scripts/GameTypes.gd")

@export var base_url: String = "http://127.0.0.1:8000"

var _state_request: HTTPRequest
var _poll_request: HTTPRequest
var _command_request: HTTPRequest
var _state_busy: bool = false
var _poll_busy: bool = false
var _command_busy: bool = false


func _ready() -> void:
	_state_request = _make_request_node("StateRequest")
	_poll_request = _make_request_node("PollRequest")
	_command_request = _make_request_node("CommandRequest")


func fetch_state() -> Dictionary:
	return await _request_json(_state_request, "_state_busy", HTTPClient.METHOD_GET, "/api/state")


func poll_state() -> Dictionary:
	return await _request_json(_poll_request, "_poll_busy", HTTPClient.METHOD_GET, "/api/state")


func post_tick(count: int) -> Dictionary:
	return await _request_json(
		_command_request,
		"_command_busy",
		HTTPClient.METHOD_POST,
		"/api/tick",
		{"count": count}
	)


func post_god_chat(faction_id: String, message: String) -> Dictionary:
	return await _request_json(
		_command_request,
		"_command_busy",
		HTTPClient.METHOD_POST,
		"/api/god/chat",
		{"faction_id": faction_id, "message": message}
	)


func post_give_resource(faction_id: String, resource: String, amount: int) -> Dictionary:
	return await _request_json(
		_command_request,
		"_command_busy",
		HTTPClient.METHOD_POST,
		"/api/god/give",
		{"faction_id": faction_id, "resource": resource, "amount": amount}
	)


func post_weather(x: int, y: int, weather: String, duration: int) -> Dictionary:
	return await _request_json(
		_command_request,
		"_command_busy",
		HTTPClient.METHOD_POST,
		"/api/god/weather",
		{"x": x, "y": y, "weather": weather, "duration": duration}
	)


func post_answer_petition(petition_id: int, approve: bool) -> Dictionary:
	return await _request_json(
		_command_request,
		"_command_busy",
		HTTPClient.METHOD_POST,
		"/api/god/answer",
		{"petition_id": petition_id, "approve": approve}
	)


func _make_request_node(node_name: String) -> HTTPRequest:
	var request := HTTPRequest.new()
	request.name = node_name
	request.timeout = 60.0
	add_child(request)
	return request


func _request_json(
	request_node: HTTPRequest,
	busy_flag: String,
	method: HTTPClient.Method,
	path: String,
	body: Dictionary = {}
) -> Dictionary:
	if get(busy_flag):
		return _error_response(0, "request already in flight")

	set(busy_flag, true)
	var headers: PackedStringArray = []
	var body_text := ""
	if method != HTTPClient.METHOD_GET:
		headers.append("Content-Type: application/json")
		body_text = JSON.stringify(body)

	var request_error := request_node.request(_build_url(path), headers, method, body_text)
	if request_error != OK:
		set(busy_flag, false)
		return _error_response(0, "request failed: %s" % error_string(request_error))

	var completed: Array = await request_node.request_completed
	set(busy_flag, false)

	var result_code := int(completed[0])
	var response_code := int(completed[1])
	var response_body := PackedByteArray(completed[3]).get_string_from_utf8()

	if result_code != HTTPRequest.RESULT_SUCCESS:
		return _error_response(response_code, "network error (%d)" % result_code)
	var parsed := _parse_json_response(response_body)
	if not bool(parsed.get("ok", false)):
		var label := "HTTP %d" % response_code if response_code > 0 else "response"
		return _error_response(response_code, "%s returned non-JSON response: %s" % [label, parsed.get("error", "")])
	var payload: Variant = parsed.get("payload", {})
	if response_code < 200 or response_code >= 300:
		var api_error := GameTypes.ApiError.from_payload(response_code, payload)
		return {
			"ok": false,
			"status_code": response_code,
			"payload": payload,
			"error": api_error.error,
		}
	return {
		"ok": true,
		"status_code": response_code,
		"payload": payload,
		"error": "",
	}


func _parse_json_response(response_body: String) -> Dictionary:
	if response_body.strip_edges().is_empty():
		return {
			"ok": false,
			"payload": {},
			"error": "empty body",
		}
	var parser := JSON.new()
	var parse_error := parser.parse(response_body)
	if parse_error != OK:
		return {
			"ok": false,
			"payload": {},
			"error": "%s near line %d. Body starts with: %s" % [
				parser.get_error_message(),
				parser.get_error_line(),
				_response_preview(response_body),
			],
		}
	return {
		"ok": true,
		"payload": parser.data,
		"error": "",
	}


func _response_preview(response_body: String) -> String:
	var compact := response_body.strip_edges().replace("\n", " ").replace("\r", " ")
	if compact.length() > 180:
		return "%s..." % compact.substr(0, 180)
	return compact


func _build_url(path: String) -> String:
	var normalized := base_url.strip_edges()
	if normalized.ends_with("/"):
		normalized = normalized.trim_suffix("/")
	return "%s%s" % [normalized, path]


func _error_response(status_code: int, message: String) -> Dictionary:
	return {
		"ok": false,
		"status_code": status_code,
		"payload": {},
		"error": message,
	}
