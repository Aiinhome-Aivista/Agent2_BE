import chromadb
from sentence_transformers import SentenceTransformer

client = chromadb.PersistentClient(path="./chroma_db")

collection = client.get_or_create_collection("runbooks")

model = SentenceTransformer('all-MiniLM-L6-v2')


def store_runbook_embedding(
    runbook_id,
    text,
    metadata
):
    embedding = model.encode(text).tolist()

    collection.add(
        ids=[str(runbook_id)],
        embeddings=[embedding],
        documents=[text],
        metadatas=[metadata]
    )