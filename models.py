from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class Event(BaseModel):
    """The data contract: every device sends events in this shape."""

    schema_version: int = Field(default=1, ge=1)
    device_id: str = Field(min_length=1, max_length=64)
    event_type: str = Field(min_length=1, max_length=64)
    sequence: int = Field(ge=0)
    timestamp: datetime  # accepts a Unix number (1711800000) or an ISO string
    data: dict[str, Any] = Field(default_factory=dict)  # device-specific payload
