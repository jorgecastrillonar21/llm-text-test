from __future__ import annotations

import uuid

from fastapi import APIRouter, status
from sqlalchemy import select

from app.api.deps import DbSession
from app.api.schemas import CharacterCreate, CharacterRead, WorldCreate, WorldRead
from app.domain.errors import NotFoundError
from app.infrastructure.db import models

router = APIRouter(tags=["worlds"])


@router.get("/worlds", response_model=list[WorldRead])
async def list_worlds(db: DbSession) -> list[models.World]:
    rows = await db.execute(select(models.World).order_by(models.World.created_at.desc()))
    return list(rows.scalars())


@router.post("/worlds", response_model=WorldRead, status_code=status.HTTP_201_CREATED)
async def create_world(payload: WorldCreate, db: DbSession) -> models.World:
    world = models.World(**payload.model_dump())
    db.add(world)
    await db.commit()
    return world


@router.get("/worlds/{world_id}", response_model=WorldRead)
async def get_world(world_id: uuid.UUID, db: DbSession) -> models.World:
    world = await db.get(models.World, world_id)
    if world is None:
        raise NotFoundError("World", world_id)
    return world


@router.get("/worlds/{world_id}/characters", response_model=list[CharacterRead])
async def list_characters(world_id: uuid.UUID, db: DbSession) -> list[models.Character]:
    world = await db.get(models.World, world_id)
    if world is None:
        raise NotFoundError("World", world_id)
    rows = await db.execute(
        select(models.Character)
        .where(models.Character.world_id == world_id)
        .order_by(models.Character.created_at)
    )
    return list(rows.scalars())


@router.post(
    "/worlds/{world_id}/characters",
    response_model=CharacterRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_character(
    world_id: uuid.UUID, payload: CharacterCreate, db: DbSession
) -> models.Character:
    world = await db.get(models.World, world_id)
    if world is None:
        raise NotFoundError("World", world_id)
    character = models.Character(world_id=world_id, **payload.model_dump())
    db.add(character)
    await db.commit()
    return character


@router.get("/characters/{character_id}", response_model=CharacterRead)
async def get_character(character_id: uuid.UUID, db: DbSession) -> models.Character:
    character = await db.get(models.Character, character_id)
    if character is None:
        raise NotFoundError("Character", character_id)
    return character
