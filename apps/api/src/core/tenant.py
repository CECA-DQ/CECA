from contextvars import ContextVar

current_tenant: ContextVar[str] = ContextVar("current_tenant")


def get_current_tenant() -> str:
    try:
        return current_tenant.get()
    except LookupError:
        raise RuntimeError("Tenant context not set — ensure the auth middleware ran")
