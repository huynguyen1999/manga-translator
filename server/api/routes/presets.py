from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, status

from server.api.schemas.preset import (
    CreatePresetRequest,
    PresetListResponse,
    PresetResponse,
    UpdatePresetRequest,
)
from server.preset_repository import (
    InvalidPreset,
    PresetConflict,
    PresetNotFound,
    PresetRepository,
)


def preset_http_error(error: Exception) -> HTTPException:
    if isinstance(error, HTTPException):
        return error
    if isinstance(error, PresetNotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error))
    if isinstance(error, PresetConflict):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error))
    if isinstance(error, InvalidPreset):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error))
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(error))


def create_preset_router(
    get_preset_repo: Callable[[], PresetRepository],
) -> tuple[APIRouter, tuple[Callable[..., Any], ...]]:
    router = APIRouter()

    @router.get("/presets", response_model=PresetListResponse, tags=["api", "presets"])
    @router.get("/api/presets", response_model=PresetListResponse, tags=["api", "presets"])
    async def list_presets():
        try:
            presets = await get_preset_repo().list_presets()
            return {"presets": presets}
        except Exception as error:
            raise preset_http_error(error) from error

    @router.post(
        "/presets",
        response_model=PresetResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["api", "presets"],
    )
    @router.post(
        "/api/presets",
        response_model=PresetResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["api", "presets"],
    )
    async def create_preset(data: CreatePresetRequest):
        try:
            return await get_preset_repo().create_preset(
                name=data.name,
                description=data.description,
                settings=data.settings,
                is_default=data.isDefault,
            )
        except Exception as error:
            raise preset_http_error(error) from error

    @router.get("/presets/{preset_id}", response_model=PresetResponse, tags=["api", "presets"])
    @router.get("/api/presets/{preset_id}", response_model=PresetResponse, tags=["api", "presets"])
    async def get_preset(preset_id: str):
        try:
            return await get_preset_repo().get_preset(preset_id)
        except Exception as error:
            raise preset_http_error(error) from error

    @router.put("/presets/{preset_id}", response_model=PresetResponse, tags=["api", "presets"])
    @router.put("/api/presets/{preset_id}", response_model=PresetResponse, tags=["api", "presets"])
    async def update_preset(preset_id: str, data: UpdatePresetRequest):
        try:
            return await get_preset_repo().update_preset(
                preset_id=preset_id,
                name=data.name,
                description=data.description,
                settings=data.settings,
                is_default=data.isDefault,
            )
        except Exception as error:
            raise preset_http_error(error) from error

    @router.delete("/presets/{preset_id}", tags=["api", "presets"])
    @router.delete("/api/presets/{preset_id}", tags=["api", "presets"])
    async def delete_preset(preset_id: str):
        try:
            await get_preset_repo().delete_preset(preset_id)
            return {"status": "deleted", "id": preset_id}
        except Exception as error:
            raise preset_http_error(error) from error

    @router.post(
        "/presets/{preset_id}/default",
        response_model=PresetResponse,
        tags=["api", "presets"],
    )
    @router.post(
        "/api/presets/{preset_id}/default",
        response_model=PresetResponse,
        tags=["api", "presets"],
    )
    async def set_default_preset(preset_id: str):
        try:
            return await get_preset_repo().set_default_preset(preset_id)
        except Exception as error:
            raise preset_http_error(error) from error

    return router, (
        list_presets,
        create_preset,
        get_preset,
        update_preset,
        delete_preset,
        set_default_preset,
    )
