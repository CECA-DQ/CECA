from sqlalchemy import String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, declared_attr
from sqlalchemy import select


class Base(DeclarativeBase):
    pass


class TenantOwnedMixin:
    """Mixin for every table that holds client data. Required — no exceptions."""

    @declared_attr
    def tenant_id(cls) -> Mapped[str]:
        return mapped_column(String, index=True, nullable=False)

    @classmethod
    def query_for_tenant(cls, tenant_id: str):
        return select(cls).where(cls.tenant_id == tenant_id)
