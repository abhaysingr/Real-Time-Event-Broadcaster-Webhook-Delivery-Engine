
from datetime import datetime, timezone
from uuid import uuid4

from pydantic import AnyHttpUrl, BaseModel, Field


class EventRequest(BaseModel):
    event_type: str = Field(min_length=1, max_length=100)
    data: dict = Field(default_factory=dict)
    webhook_url: AnyHttpUrl | None = None


class EventResponse(BaseModel):
    event_id: str
    event_type: str
    data: dict
    created_at: datetime


def build_event(request: EventRequest) -> dict:
    return EventResponse(
        event_id=str(uuid4()),
        event_type=request.event_type,
        data=request.data,
        created_at=datetime.now(timezone.utc),
    ).model_dump(mode="json")
