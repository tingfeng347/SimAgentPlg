from __future__ import annotations

from simagentplg.game.world import SETTLEMENT_IDLE_COST

CIVILIAN_PROFESSION_PRIORITY = ("idle", "builder", "miner", "lumberjack", "farmer")
CIVILIAN_PROFESSION_TYPES = frozenset(CIVILIAN_PROFESSION_PRIORITY)


def civilian_count(tile, faction_id: str) -> int:
    return tile.population_of(faction_id)


def movable_civilian_jobs(tile, faction_id: str) -> dict[str, int]:
    jobs = tile.professions_of(faction_id)
    return {
        profession: jobs.get(profession, 0)
        for profession in CIVILIAN_PROFESSION_PRIORITY
    }


def choose_civilian_profession(
    tile,
    faction_id: str,
    preferred: str | None = None,
) -> str | None:
    jobs = movable_civilian_jobs(tile, faction_id)
    if preferred:
        if preferred in CIVILIAN_PROFESSION_TYPES and jobs.get(preferred, 0) > 0:
            return preferred
        return None
    for profession in CIVILIAN_PROFESSION_PRIORITY:
        if jobs.get(profession, 0) > 0:
            return profession
    return None


def can_move_civilian_from(tile, faction_id: str, amount: int = SETTLEMENT_IDLE_COST) -> bool:
    if amount <= 0:
        return False
    population_after = tile.population_of(faction_id) - amount
    return population_after > 0 or tile.soldiers_of(faction_id) > 0


def can_receive_civilian(tile, faction_id: str, amount: int = SETTLEMENT_IDLE_COST) -> bool:
    return tile.population_of(faction_id) + amount <= tile.capacity()


def find_civilian_donor(
    world,
    faction_id: str,
    *,
    target: tuple[int, int],
    origin: tuple[int, int] | None = None,
    profession: str | None = None,
):
    if origin is not None:
        if not world.in_bounds(*origin):
            return None
        tile = world.tile_at(*origin)
        if (
            tile.owner == faction_id
            and abs(tile.x - target[0]) + abs(tile.y - target[1]) == 1
            and choose_civilian_profession(tile, faction_id, profession) is not None
            and can_move_civilian_from(tile, faction_id)
        ):
            return tile
        return None

    candidates = []
    for tile in world.faction_tiles(faction_id):
        if abs(tile.x - target[0]) + abs(tile.y - target[1]) != 1:
            continue
        if choose_civilian_profession(tile, faction_id, profession) is None:
            continue
        if not can_move_civilian_from(tile, faction_id):
            continue
        candidates.append(tile)
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda tile: (
            movable_civilian_jobs(tile, faction_id).get(
                choose_civilian_profession(tile, faction_id, profession) or "idle",
                0,
            ),
            tile.population_of(faction_id),
        ),
    )


def move_civilian(
    origin_tile,
    target_tile,
    faction_id: str,
    *,
    profession: str | None = None,
    amount: int = SETTLEMENT_IDLE_COST,
) -> tuple[int, str | None]:
    if amount <= 0:
        return 0, None
    if not can_move_civilian_from(origin_tile, faction_id, amount):
        return 0, None
    if not can_receive_civilian(target_tile, faction_id, amount):
        return 0, None
    selected = choose_civilian_profession(origin_tile, faction_id, profession)
    if selected is None:
        return 0, None

    origin_tile.ensure_professions(faction_id)
    target_tile.ensure_professions(faction_id)
    origin_jobs = origin_tile.professions.get(faction_id)
    if origin_jobs is None:
        return 0, None
    moved = min(amount, origin_jobs.get(selected, 0), origin_tile.population_of(faction_id))
    if moved <= 0:
        return 0, None

    origin_jobs[selected] = origin_jobs.get(selected, 0) - moved
    origin_tile.population[faction_id] = origin_tile.population_of(faction_id) - moved
    if origin_tile.population[faction_id] <= 0:
        origin_tile.population.pop(faction_id, None)
    origin_tile.ensure_professions(faction_id)

    target_tile.set_population(faction_id, target_tile.population_of(faction_id) + moved)
    target_tile.ensure_professions(faction_id)
    target_jobs = target_tile.professions.setdefault(
        faction_id,
        {job: 0 for job in CIVILIAN_PROFESSION_PRIORITY},
    )
    for job in CIVILIAN_PROFESSION_PRIORITY:
        target_jobs.setdefault(job, 0)
    target_jobs["idle"] = max(0, target_jobs.get("idle", 0) - moved)
    target_jobs[selected] = target_jobs.get(selected, 0) + moved
    target_tile.ensure_professions(faction_id)
    return moved, selected
