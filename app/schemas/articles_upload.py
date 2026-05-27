from pydantic import BaseModel
from typing import Optional, Any


class ArticleUploadResponse(BaseModel):
    id: int
    name: str
    files: str
    files_type: str
    content: Any
    summary: Any
    author: Optional[str]

    class Config:
        from_attributes = True