from sqlalchemy import Column, BigInteger, String, TIMESTAMP, text, JSON

from app.db.base_class import Base


class ArticlesUpload(Base):
    __tablename__ = "articles_upload"

    id = Column(BigInteger, primary_key=True, autoincrement=True)

    name = Column(String(255), nullable=False)

    files = Column(String(500), nullable=False)

    files_type = Column(String(100), nullable=False)

    content = Column(JSON)

    summary = Column(JSON)

    author = Column(String(255))

    created_at = Column(
        TIMESTAMP,
        server_default=text("CURRENT_TIMESTAMP")
    )