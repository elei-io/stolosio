from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.fleet import FleetConfiguration
from backend.proxy.contracts import ProviderName

router = APIRouter(prefix="/v1/admin/fleets", tags=["admin"])


class FleetConfigurationResponse(BaseModel):
    provider: ProviderName
    minimum_instances: int
    maximum_instances: int
    session_capacity_per_instance: int
    scale_down_cooldown_seconds: int
    desired_instances: int
    configuration_version: int
    enabled: bool
    controller_status: str | None

    @classmethod
    def from_contract(cls, value: FleetConfiguration) -> "FleetConfigurationResponse":
        return cls(
            provider=value.provider,
            minimum_instances=value.minimum_instances,
            maximum_instances=value.maximum_instances,
            session_capacity_per_instance=value.session_capacity_per_instance,
            scale_down_cooldown_seconds=value.scale_down_cooldown_seconds,
            desired_instances=value.desired_instances,
            configuration_version=value.configuration_version,
            enabled=value.enabled,
            controller_status=value.controller_status,
        )


class FleetConfigurationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    minimum_instances: int | None = Field(default=None, ge=0, le=100)
    maximum_instances: int | None = Field(default=None, ge=0, le=100)
    session_capacity_per_instance: int | None = Field(default=None, ge=1, le=100)
    scale_down_cooldown_seconds: int | None = Field(default=None, ge=1)
    enabled: bool | None = None

    @model_validator(mode="after")
    def at_least_one_value(self) -> "FleetConfigurationUpdate":
        if not self.model_fields_set:
            raise ValueError("At least one fleet setting is required")
        return self


@router.get("", response_model=list[FleetConfigurationResponse])
async def list_fleets(request: Request) -> list[FleetConfigurationResponse]:
    values = await request.app.state.fleet_admin.configurations()
    return [FleetConfigurationResponse.from_contract(value) for value in values]


@router.get("/{provider}", response_model=FleetConfigurationResponse)
async def get_fleet(provider: ProviderName, request: Request) -> FleetConfigurationResponse:
    value = await request.app.state.fleet_admin.configuration(provider)
    if value is None:
        raise HTTPException(status_code=404, detail="managed fleet not found")
    return FleetConfigurationResponse.from_contract(value)


@router.patch("/{provider}", response_model=FleetConfigurationResponse)
async def update_fleet(
    provider: ProviderName,
    update: FleetConfigurationUpdate,
    request: Request,
    actor: Annotated[str, Header(alias="X-Harbor-Actor")] = "local-admin",
) -> FleetConfigurationResponse:
    if request.app.state.environment != "development":
        raise HTTPException(status_code=404, detail="not found")
    try:
        value = await request.app.state.fleet_admin.update(
            provider,
            update.model_dump(exclude_unset=True),
            actor=actor,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if value is None:
        raise HTTPException(status_code=404, detail="managed fleet not found")
    return FleetConfigurationResponse.from_contract(value)
