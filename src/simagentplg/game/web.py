from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from simagentplg import ModelConfig
from simagentplg.game.engine import GameEngine
from simagentplg.game.leader import LLMLeaderController
from simagentplg.game.world import (
    DEFAULT_FACTIONS,
    RESOURCE_TYPES,
    WEATHER_TYPES,
    WorldState,
    create_default_world,
)

STATIC_DIR = Path(__file__).with_name("web_static")


class TickRequest(BaseModel):
    count: int = Field(default=1, ge=1, le=50)


class GiveRequest(BaseModel):
    faction_id: str
    resource: str
    amount: int = Field(gt=0)


class WeatherRequest(BaseModel):
    x: int
    y: int
    weather: str
    duration: int | None = Field(default=None, ge=0, le=50)


class AnswerRequest(BaseModel):
    petition_id: int
    approve: bool


class GodChatRequest(BaseModel):
    faction_id: str
    message: str = Field(min_length=1)


def create_engine(
    *,
    seed: int = 7,
    width: int = 32,
    height: int = 20,
    config: ModelConfig | None = None,
) -> GameEngine:
    world = create_default_world(width=width, height=height, seed=seed)
    model_config = config or ModelConfig.from_env()
    leaders = {
        faction_id: LLMLeaderController.create(
            config=model_config,
            faction_id=faction_id,
            world_provider=lambda world=world: world,
        )
        for faction_id in DEFAULT_FACTIONS
    }
    return GameEngine(world, leaders=leaders, log_ticks=True)


def create_game_app(engine: GameEngine | None = None) -> FastAPI:
    app = FastAPI(title="SimAgentPlg God Simulator")
    app.state.engine = engine or create_engine()
    app.state.tick_lock = asyncio.Lock()

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/state")
    async def state() -> dict[str, Any]:
        return serialize_state(app.state.engine.world)

    @app.post("/api/tick")
    async def tick(request: TickRequest):
        if app.state.tick_lock.locked():
            return JSONResponse(
                {"error": "world is already advancing"},
                status_code=409,
            )
        async with app.state.tick_lock:
            await app.state.engine.tick(request.count)
            return serialize_state(app.state.engine.world)

    @app.post("/api/god/give")
    async def give(request: GiveRequest):
        try:
            app.state.engine.god.give_resource(
                request.faction_id,
                request.resource,
                request.amount,
            )
        except Exception as exc:
            return _error(exc)
        return serialize_state(app.state.engine.world)

    @app.post("/api/god/weather")
    async def weather(request: WeatherRequest):
        try:
            app.state.engine.god.set_weather(
                request.x,
                request.y,
                request.weather,
                request.duration,
            )
        except Exception as exc:
            return _error(exc)
        return serialize_state(app.state.engine.world)

    @app.post("/api/god/answer")
    async def answer(request: AnswerRequest):
        try:
            app.state.engine.god.answer_petition(
                request.petition_id,
                request.approve,
            )
        except Exception as exc:
            return _error(exc)
        return serialize_state(app.state.engine.world)

    @app.post("/api/god/chat")
    async def god_chat(request: GodChatRequest):
        if app.state.tick_lock.locked():
            return JSONResponse(
                {"error": "world is already advancing"},
                status_code=409,
            )
        async with app.state.tick_lock:
            world = app.state.engine.world
            try:
                faction = world.factions.get(request.faction_id)
                if faction is None:
                    raise ValueError(f"unknown faction {request.faction_id!r}")
                if faction.eliminated:
                    raise ValueError(f"faction {request.faction_id!r} is eliminated")
                message = request.message.strip()
                if not message:
                    raise ValueError("message must not be empty")
                controller = app.state.engine.leaders.get(request.faction_id)
                if controller is None:
                    raise ValueError(
                        f"missing leader controller for faction {request.faction_id}"
                    )
                chat_method = getattr(controller, "chat_with_god", None)
                if chat_method is None:
                    raise ValueError(
                        f"leader {request.faction_id} cannot answer god chat"
                    )
                world.add_god_chat_message(
                    faction_id=request.faction_id,
                    speaker="god",
                    content=message,
                )
                reply = await chat_method(world)
                world.add_god_chat_message(
                    faction_id=request.faction_id,
                    speaker="leader",
                    content=reply,
                )
            except Exception as exc:
                return _error(exc)
            return serialize_state(world)

    return app


def serialize_state(world: WorldState) -> dict[str, Any]:
    return {
        "tick": world.tick,
        "seed": world.seed,
        "width": world.width,
        "height": world.height,
        "paused": world.paused,
        "pause_reason": world.pause_reason,
        "resources": list(RESOURCE_TYPES),
        "weather_types": list(WEATHER_TYPES),
        "tiles": [
            {
                "x": tile.x,
                "y": tile.y,
                "terrain": tile.terrain,
                "owner": tile.owner,
                "home_of": world.home_of_tile(tile.x, tile.y),
                "weather": tile.weather,
                "weather_duration": tile.weather_duration,
                "population": dict(tile.population),
                "soldiers": dict(tile.soldiers),
                "professions": {
                    faction_id: tile.professions_of(faction_id)
                    for faction_id in tile.population
                },
                "houses": tile.houses,
                "capacity": tile.capacity(),
                "protected": tile.protected,
            }
            for tile in world.tiles
        ],
        "factions": [
            {
                "faction_id": faction_id,
                "name": faction.name,
                "leader_name": faction.leader_name,
                "resources": faction.resources.as_dict(),
                "population": world.total_population(faction_id),
                "soldiers": world.total_soldiers(faction_id),
                "jobs": world.total_jobs(faction_id),
                "houses": world.total_houses(faction_id),
                "population_capacity": world.population_capacity(faction_id),
                "territory_count": len(world.faction_tiles(faction_id)),
                "home_tile": (
                    {"x": faction.home_tile[0], "y": faction.home_tile[1]}
                    if faction.home_tile is not None
                    else None
                ),
                "eliminated": faction.eliminated,
                "known_factions": sorted(faction.known_factions),
                "diplomacy": {
                    other_id: faction.relation_to(other_id)
                    for other_id in sorted(faction.known_factions)
                    if other_id != faction_id
                },
                "last_plan_snapshot": dict(faction.last_plan_snapshot),
                "leader_memory": dict(faction.leader_memory),
                "leader_context_window_count": len(faction.leader_context_window),
            }
            for faction_id, faction in sorted(world.factions.items())
        ],
        "petitions": [
            petition.as_dict()
            for petition in world.petitions
            if petition.status == "pending"
        ],
        "god_chats": [
            message.as_dict()
            for message in world.god_chats[-80:]
        ],
        "events": [
            event.as_dict()
            for event in world.events[-80:]
        ],
    }


def _error(exc: Exception) -> JSONResponse:
    return JSONResponse({"error": str(exc)}, status_code=400)
