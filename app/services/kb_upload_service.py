import os
import uuid

from sqlalchemy.orm import Session

from app.models.articles_upload import ArticlesUpload
from app.utils.file_extractors import (
    extract_pdf_text,
    extract_docx_text,
    extract_csv_text,
    extract_txt_text
)

from app.services.vector_store_service import VectorStoreService
from app.agents.mistral_analysis_agent import MistralAnalysisAgent
from app.services.graph_db_service import GraphDBService


UPLOAD_DIR = "storage/uploads"

os.makedirs(UPLOAD_DIR, exist_ok=True)


class KBUploadService:

    def __init__(self):
        self.vector_service = VectorStoreService()

        self.mistral_agent = MistralAnalysisAgent(
            api_key=os.getenv("MISTRAL_API_KEY")
        )
        self.graph_db_service = GraphDBService()

    async def upload_files(self, db: Session, files):

        uploaded_articles = []

        for file in files:

            extension = file.filename.split(".")[-1].lower()

            unique_name = f"{uuid.uuid4()}_{file.filename}"
            file_path = os.path.join(UPLOAD_DIR, unique_name)

            with open(file_path, "wb") as f:
                f.write(await file.read())

            extracted_data = self.extract_content(
                extension,
                file_path
            )

            content = extracted_data["content"]
            author = extracted_data["author"]

            ai_analysis = self.mistral_agent.analyze_uploaded_document(
                content
            )

            ai_title = ai_analysis.get("title")

            ai_summary = {
                "summary": ai_analysis.get("summary"),
                "purpose": ai_analysis.get("purpose"),
                "category": ai_analysis.get("category"),
                "keywords": ai_analysis.get("keywords")
            }

            final_author = author or ai_analysis.get("author")

            article = ArticlesUpload(
                name=ai_title,
                files=file_path,
                files_type=extension,
                content={
                    "text": content[:50000]
                },
                summary=ai_summary,
                author=final_author
            )

            db.add(article)
            db.commit()
            db.refresh(article)

            self.vector_service.add_document(
                document_id=str(article.id),
                content=content,
                metadata={
                    "article_id": article.id,
                    "title": ai_title,
                    "author": final_author,
                    "file_type": extension
                }
            )

            # Call Mistral for dynamic Graph extraction
            graph_data = self.mistral_agent.extract_graph_relations(content)

            # Insert into GraphDB
            self.graph_db_service.add_runbook_to_graph(
                article_id=str(article.id),
                title=ai_title,
                summary_data=ai_summary,
                author=final_author,
                graph_data=graph_data
            )

            uploaded_articles.append(article)

        # Generate HTML graph after all files are processed
        if uploaded_articles:
            self.graph_db_service.generate_html_graph()

        return uploaded_articles

    def extract_content(self, extension, file_path):

        if extension == "pdf":
            return extract_pdf_text(file_path)

        elif extension == "docx":
            return extract_docx_text(file_path)

        elif extension == "csv":
            return extract_csv_text(file_path)

        elif extension == "txt":
            return extract_txt_text(file_path)

        else:
            raise Exception(f"Unsupported file type: {extension}")