from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from backend.proxy.network_policy import NetworkPolicy

router = APIRouter(prefix="/v1/admin/network", tags=["admin"])


class NetworkPolicyResponse(BaseModel):
    blocked_domain_patterns: list[str]
    configuration_version: int

    @classmethod
    def from_contract(cls, value: NetworkPolicy) -> "NetworkPolicyResponse":
        return cls(
            blocked_domain_patterns=list(value.blocked_domain_patterns),
            configuration_version=value.configuration_version,
        )


class NetworkPolicyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    blocked_domain_patterns: list[str] = Field(max_length=1_000)


@router.get("", response_model=NetworkPolicyResponse)
async def get_network_policy(request: Request) -> NetworkPolicyResponse:
    return NetworkPolicyResponse.from_contract(
        await request.app.state.network_policy.settings()
    )


@router.patch("", response_model=NetworkPolicyResponse)
async def update_network_policy(
    update: NetworkPolicyUpdate,
    request: Request,
) -> NetworkPolicyResponse:
    try:
        value = await request.app.state.network_policy.update(
            update.blocked_domain_patterns
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return NetworkPolicyResponse.from_contract(value)
