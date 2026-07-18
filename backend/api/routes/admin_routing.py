from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.proxy.contracts import ProviderName
from backend.proxy.routing import RoutingSettings

router = APIRouter(prefix="/v1/admin/routing", tags=["admin"])


class RoutingResponse(BaseModel):
    default_provider: ProviderName
    existing_domain_probe_rate_basis_points: int
    required_health_confirmations: int
    health_policy_version: int
    configuration_version: int

    @classmethod
    def from_contract(cls, value: RoutingSettings) -> "RoutingResponse":
        return cls(
            default_provider=value.default_provider,
            existing_domain_probe_rate_basis_points=(value.existing_domain_probe_rate_basis_points),
            required_health_confirmations=value.required_health_confirmations,
            health_policy_version=value.health_policy_version,
            configuration_version=value.configuration_version,
        )


class RoutingUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_provider: ProviderName | None = None
    existing_domain_probe_rate_basis_points: int | None = Field(default=None, ge=0, le=10_000)
    required_health_confirmations: int | None = Field(default=None, ge=1, le=100)

    @model_validator(mode="after")
    def require_value(self) -> "RoutingUpdate":
        if not self.model_fields_set:
            raise ValueError("At least one routing setting is required")
        return self


class ProviderRoutingResponse(BaseModel):
    provider: ProviderName
    automatic_enabled: bool
    cost_units_per_second: int
    provider_contract_version: int


class ProviderRoutingUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    automatic_enabled: bool | None = None
    cost_units_per_second: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def require_value(self) -> "ProviderRoutingUpdate":
        if not self.model_fields_set:
            raise ValueError("At least one provider setting is required")
        return self


@router.get("", response_model=RoutingResponse)
async def get_routing(request: Request) -> RoutingResponse:
    return RoutingResponse.from_contract(await request.app.state.routing.settings())


@router.patch("", response_model=RoutingResponse)
async def update_routing(
    update: RoutingUpdate,
    request: Request,
) -> RoutingResponse:
    if request.app.state.environment != "development":
        raise HTTPException(status_code=404, detail="not found")
    try:
        value = await request.app.state.routing.update_settings(
            **update.model_dump(exclude_unset=True)
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return RoutingResponse.from_contract(value)


@router.get("/providers", response_model=list[ProviderRoutingResponse])
async def list_provider_routing(request: Request) -> list[ProviderRoutingResponse]:
    rows = await request.app.state.routing.provider_profiles()
    return [
        ProviderRoutingResponse(
            provider=ProviderName(row.provider),
            automatic_enabled=row.automatic_enabled,
            cost_units_per_second=row.cost_units_per_second,
            provider_contract_version=row.provider_contract_version,
        )
        for row in rows
    ]


@router.patch("/providers/{provider}", response_model=ProviderRoutingResponse)
async def update_provider_routing(
    provider: ProviderName,
    update: ProviderRoutingUpdate,
    request: Request,
) -> ProviderRoutingResponse:
    if request.app.state.environment != "development":
        raise HTTPException(status_code=404, detail="not found")
    try:
        row = await request.app.state.routing.update_provider(
            provider,
            **update.model_dump(exclude_unset=True),
        )
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return ProviderRoutingResponse(
        provider=ProviderName(row.provider),
        automatic_enabled=row.automatic_enabled,
        cost_units_per_second=row.cost_units_per_second,
        provider_contract_version=row.provider_contract_version,
    )
