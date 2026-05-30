# Multi-tenancy

Every table holding client data carries a `tenant_id` column, and every query filters by it. This was a day-one decision — retrofitting multi-tenancy later is prohibitively expensive. Today there is one tenant; tomorrow there will be ~100.

## How tenant identity flows

A request's tenant comes from the `X-Tenant-ID` header (demo; in production this is extracted from a JWT — `core/auth.py` is the single place to change). The value is placed in a `ContextVar` so it is available anywhere in the request without threading it through every call.

- `src/core/tenant.py` — `current_tenant: ContextVar[str]` and `get_current_tenant()` (raises if unset).
- `src/db/session.py` — `tenant_session(tenant_id)` opens a DB session with the tenant context set, and resets it on exit.

```python
async with tenant_session(tenant_id) as session:
    rows = await session.scalars(MyModel.query_for_tenant(tenant_id))
```

## Base mixin

`src/models/base.py` — every model with client data inherits `TenantOwnedMixin`, which adds an indexed `tenant_id` column and a `query_for_tenant(tenant_id)` classmethod returning a pre-filtered `select()`.

## Rules

- A new table with client data **must** inherit `TenantOwnedMixin`. No exceptions.
- Never filter by `tenant_id` inline/ad hoc — always use `Model.query_for_tenant(tenant_id)`.
- `tenant_id` is always indexed.
- Write a tenant-isolation test for every `TenantOwned` entity: prove that one tenant cannot read another tenant's data. (See `tests/` — one such test per entity.)
