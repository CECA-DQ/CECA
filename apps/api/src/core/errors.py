from fastapi import HTTPException


class CECAError(Exception):
    """Base domain error. Subclass with a code and HTTP status."""
    code: str = "internal_error"
    status_code: int = 500

    def __init__(self, detail: str = "") -> None:
        self.detail = detail
        super().__init__(detail)

    def to_http(self) -> HTTPException:
        return HTTPException(status_code=self.status_code, detail={"code": self.code, "detail": self.detail})


class NotFoundError(CECAError):
    code = "not_found"
    status_code = 404


class UnauthorizedError(CECAError):
    code = "unauthorized"
    status_code = 401


class ForbiddenError(CECAError):
    code = "forbidden"
    status_code = 403


class AdapterError(CECAError):
    """Raised when an external adapter call fails."""
    code = "adapter_error"
    status_code = 502


class PipelineError(CECAError):
    """Raised when a pipeline step fails."""
    code = "pipeline_error"
    status_code = 500
