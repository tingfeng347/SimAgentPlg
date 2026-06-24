from __future__ import annotations

from simagentplg.game.world import WorldState

TERRAIN_GLYPHS = {
    "plain": ".",
    "forest": "T",
    "hill": "n",
    "water": "~",
    "mountain": "^",
}
FACTION_GLYPHS = {
    "human": "H",
    "elf": "E",
    "orc": "O",
}


def render_map(world: WorldState) -> str:
    lines = [f"Tick {world.tick}"]
    for y in range(world.height):
        row = []
        for x in range(world.width):
            tile = world.tile_at(x, y)
            glyph = FACTION_GLYPHS.get(tile.owner or "", "")
            if not glyph:
                glyph = TERRAIN_GLYPHS[tile.terrain]
            home_of = world.home_of_tile(x, y)
            if home_of is not None:
                glyph = FACTION_GLYPHS.get(home_of, "?").lower()
            if tile.weather == "storm":
                glyph = "!"
            elif tile.weather == "drought":
                glyph = ";"
            elif tile.protected:
                glyph = "*"
            row.append(glyph)
        lines.append("".join(row))
    return "\n".join(lines)


def render_status(world: WorldState) -> str:
    lines = [f"World tick: {world.tick}"]
    if world.paused:
        lines.append(f"PAUSED: {world.pause_reason}")
    for faction_id, faction in sorted(world.factions.items()):
        lines.append(
            (
                f"{faction.name} ({faction_id}) | leader={faction.leader_name} | "
                f"home={faction.home_tile} | "
                f"eliminated={faction.eliminated} | "
                f"pop={world.total_population(faction_id)} | "
                f"soldiers={world.total_soldiers(faction_id)} | "
                f"tiles={len(world.faction_tiles(faction_id))} | "
                f"resources={faction.resources.as_dict()} | "
                f"diplomacy={faction.diplomacy}"
            )
        )
    return "\n".join(lines)


def render_inbox(world: WorldState) -> str:
    pending = [
        petition
        for petition in world.petitions
        if petition.status == "pending"
    ]
    if not pending:
        return "No pending petitions."
    return "\n".join(
        (
            f"#{petition.petition_id} [{petition.urgency}] "
            f"{petition.faction_id} asks for {petition.kind}: "
            f"{petition.reason} request={petition.request}"
        )
        for petition in pending
    )


def render_log(world: WorldState, limit: int = 12) -> str:
    events = world.events[-limit:]
    if not events:
        return "No events yet."
    return "\n".join(
        f"[{event.tick:03d}] {event.kind}: {event.message}"
        for event in events
    )
