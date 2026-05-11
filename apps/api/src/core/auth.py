# Authentication middleware and helpers.
# Sets the tenant context on every authenticated request.
# Implementation depends on the chosen auth strategy (JWT, API key, etc.).
#
# For the demo: tenant identity comes from the X-Tenant-ID request header.
# Replace with JWT validation before going to production.

from fastapi import Header, HTTPException


async def get_tenant_id(x_tenant_id: str = Header(...)) -> str:
    """Extract tenant identity from the X-Tenant-ID header."""
    if not x_tenant_id.strip():
        raise HTTPException(status_code=401, detail="X-Tenant-ID header is required")
    return x_tenant_id.strip()
