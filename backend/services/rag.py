"""
AutoGrade — RAG Module

ChromaDB + Gemini text-embedding-004 for rubric generation
from textbook/notes content.
"""

from __future__ import annotations

import json
import hashlib
import os

from config import get_gemini_client, gemini_generate_with_retry
from models.schemas import QuestionRubric, RubricCriterion

try:
    import chromadb
except ImportError:
    chromadb = None


# =========================================================
# CONSTANTS
# =========================================================

CHUNK_SIZE = 300  # tokens (approximate by words)
EMBEDDING_MODEL = "text-embedding-004"


# =========================================================
# CHROMADB CLIENT
# =========================================================

_chroma_client = None


def _get_chroma_client():
    """Get or create a persistent ChromaDB client."""
    global _chroma_client

    if _chroma_client is not None:
        return _chroma_client

    if chromadb is None:
        raise RuntimeError(
            "chromadb is not installed. Run: pip install chromadb"
        )

    persist_dir = os.getenv("CHROMADB_PERSIST_DIR", "./chroma_data")
    _chroma_client = chromadb.PersistentClient(path=persist_dir)

    return _chroma_client


# =========================================================
# TEXT CHUNKING
# =========================================================

def _chunk_text(text: str, chunk_size: int = CHUNK_SIZE) -> list[str]:
    """Split text into chunks of approximately chunk_size words."""
    words = text.split()
    chunks = []

    for i in range(0, len(words), chunk_size):
        chunk = " ".join(words[i : i + chunk_size])
        if chunk.strip():
            chunks.append(chunk)

    return chunks


# =========================================================
# EMBED TEXT
# =========================================================

def _embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a list of texts using Gemini text-embedding-004."""
    client = get_gemini_client()
    embeddings = []

    for text in texts:
        response = client.models.embed_content(
            model=EMBEDDING_MODEL,
            contents=text,
        )
        embeddings.append(response.embeddings[0].values)

    return embeddings


# =========================================================
# SETUP: INDEX TEXTBOOK CONTENT
# =========================================================

def setup_rubric_knowledge(
    exam_id: str,
    textbook_text: str,
) -> str:
    """
    Chunk textbook/notes into 300-word chunks, embed with
    Gemini text-embedding-004, and store in ChromaDB.

    Parameters
    ----------
    exam_id : str
        The exam identifier.
    textbook_text : str
        Full text extracted from the textbook/notes.

    Returns
    -------
    collection_name : str
        The ChromaDB collection name.
    """
    collection_name = f"rubric_knowledge_{exam_id}"
    client = _get_chroma_client()

    # Delete existing collection if it exists
    try:
        client.delete_collection(collection_name)
    except Exception:
        pass

    collection = client.create_collection(
        name=collection_name,
        metadata={"exam_id": exam_id}
    )

    chunks = _chunk_text(textbook_text)

    if not chunks:
        print("[RAG] No text chunks to index.")
        return collection_name

    print(f"[RAG] Indexing {len(chunks)} chunks for exam {exam_id}...")

    embeddings = _embed_texts(chunks)

    ids = [
        hashlib.md5(chunk.encode()).hexdigest()
        for chunk in chunks
    ]

    collection.add(
        ids=ids,
        documents=chunks,
        embeddings=embeddings,
    )

    print(f"[RAG] Indexed {len(chunks)} chunks into {collection_name}.")
    return collection_name


# =========================================================
# QUERY: GENERATE RUBRIC FOR A QUESTION
# =========================================================

RUBRIC_GEN_SCHEMA = {
    "type": "object",
    "properties": {
        "criteria": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "max_marks": {"type": "number"}
                },
                "required": ["description", "max_marks"]
            }
        }
    },
    "required": ["criteria"]
}


def generate_rubric_for_question(
    exam_id: str,
    question_number: int,
    question_text: str,
    max_marks: float,
) -> QuestionRubric:
    """
    Retrieve top-3 textbook chunks relevant to the question,
    then ask Gemini to propose a marking rubric.

    Parameters
    ----------
    exam_id : str
        The exam identifier.
    question_number : int
        The question number.
    question_text : str
        The question text.
    max_marks : float
        Maximum marks for this question.

    Returns
    -------
    QuestionRubric
        Proposed rubric for teacher approval.
    """

    # Retrieve top-3 relevant chunks
    context_chunks = _retrieve_chunks(exam_id, question_text, top_k=3)

    context = "\n\n---\n\n".join(context_chunks) if context_chunks else ""


    prompt = f"""
Based on this textbook content, propose a marking rubric
for this question worth {max_marks} marks.

TEXTBOOK CONTEXT:
{context}

QUESTION:
{question_text}

Return JSON: a list of criteria, each with "description" (str)
and "max_marks" (number). The sum of all max_marks MUST equal
{max_marks}. Keep criteria clear and practical.
"""

    response = gemini_generate_with_retry(
        contents=[prompt],
        config={
            "response_mime_type": "application/json",
            "response_json_schema": RUBRIC_GEN_SCHEMA,
            "temperature": 0.2,
        }
    )

    if not response.text:
        # Fallback: single criterion
        return QuestionRubric(
            question_number=question_number,
            question_text=question_text,
            max_marks=max_marks,
            criteria=[
                RubricCriterion(
                    description="Complete and correct answer",
                    max_marks=max_marks,
                )
            ],
        )

    data = json.loads(response.text)
    criteria_data = data.get("criteria", [])

    criteria = [
        RubricCriterion(
            description=c.get("description", ""),
            max_marks=float(c.get("max_marks", 0)),
        )
        for c in criteria_data
    ]

    return QuestionRubric(
        question_number=question_number,
        question_text=question_text,
        max_marks=max_marks,
        criteria=criteria,
    )


# =========================================================
# RETRIEVE CHUNKS
# =========================================================

def _retrieve_chunks(
    exam_id: str,
    query: str,
    top_k: int = 3,
) -> list[str]:
    """Retrieve the top-k most relevant chunks from ChromaDB."""

    collection_name = f"rubric_knowledge_{exam_id}"

    try:
        client = _get_chroma_client()
        collection = client.get_collection(collection_name)
    except Exception:
        return []

    query_embedding = _embed_texts([query])[0]

    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=top_k,
    )

    documents = results.get("documents", [[]])[0]
    return documents
