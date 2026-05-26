from pydantic import BaseModel
from typing import Optional, List


class EmployeeResponse(BaseModel):
    id: int
    first_name: str
    last_name: str
    contact: Optional[str] = None
    location: Optional[str] = None
    company: Optional[str] = None
    department: Optional[str] = None
    position: Optional[str] = None
    status: Optional[str] = None

    model_config = {"from_attributes": True}


class CursorPage(BaseModel):
    items: List[EmployeeResponse]
    next_cursor: Optional[str] = None   # opaque token; None means no more pages
    has_next: bool
    total: int                          # total matching records (for display)
    page_size: int
