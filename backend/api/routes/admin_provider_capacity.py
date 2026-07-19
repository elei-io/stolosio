from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.proxy.contracts import ProviderName
from backend.proxy.external_capacity import (
    ExternalCapacity,
    ExternalCapacityEnablementError,
)

router = APIRouter(prefix="/v1/admin/providers", tags=["admin"])


class ExternalCapacityResponse(BaseModel):
    provider: ProviderName
    enabled: bool
    max_active_sessions: int
    max_queued_attempts: int
    configuration_version: int

    @classmethod
    def from_contract(cls, value: ExternalCapacity) -> "ExternalCapacityResponse":
        return cls(
            provider=value.provider,
            enabled=value.enabled,
            max_active_sessions=value.max_active_sessions,
            max_queued_attempts=value.max_queued_attempts,
            configuration_version=value.configuration_version,
        )


class ExternalCapacityUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    max_active_sessions: int | None = Field(default=None, ge=0)
    max_queued_attempts: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def at_least_one_value(self) -> "ExternalCapacityUpdate":
        if not self.model_fields_set:
            raise ValueError("At least one capacity setting is required")
        return self


@router.get("/capacity", response_model=list[ExternalCapacityResponse])
async def list_external_capacity(request: Request) -> list[ExternalCapacityResponse]:
    values = await request.app.state.external_capacity.list()
    return [ExternalCapacityResponse.from_contract(value) for value in values]


@router.get("/{provider}/capacity", response_model=ExternalCapacityResponse)
async def get_external_capacity(
    provider: ProviderName,
    request: Request,
) -> ExternalCapacityResponse:
    value = await request.app.state.external_capacity.get(provider)
    if value is None:
        raise HTTPException(status_code=404, detail="external provider capacity not found")
    return ExternalCapacityResponse.from_contract(value)


@router.patch("/{provider}/capacity", response_model=ExternalCapacityResponse)
async def update_external_capacity(
    provider: ProviderName,
    update: ExternalCapacityUpdate,
    request: Request,
    actor: Annotated[str, Header(alias="X-Harbor-Actor")] = "local-admin",
) -> ExternalCapacityResponse:
    try:
        value = await request.app.state.external_capacity.update(
            provider,
            update.model_dump(exclude_unset=True),
            actor=actor,
        )
    except ExternalCapacityEnablementError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if value is None:
        raise HTTPException(status_code=404, detail="external provider capacity not found")
    return ExternalCapacityResponse.from_contract(value)
