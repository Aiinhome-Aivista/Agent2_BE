import chromadb
from chromadb.utils import embedding_functions


class VectorStoreService:

    def __init__(self):
        self.client = chromadb.PersistentClient(path="storage/chromadb")

        self.embedding_function = embedding_functions.DefaultEmbeddingFunction()

        self.collection = self.client.get_or_create_collection(
            name="knowledge_base_documents",
            embedding_function=self.embedding_function
        )

    def add_document(
        self,
        document_id: str,
        content: str,
        metadata: dict
    ):

        self.collection.add(
            ids=[document_id],
            documents=[content],
            metadatas=[metadata]
        )