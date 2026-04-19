# scripts/ingest.py
import os
import logging
from pathlib import Path
from dotenv import load_dotenv
import chromadb
from chromadb.config import Settings
from sentence_transformers import SentenceTransformer
import re

import sys
sys.path.append(str(Path(__file__).parent.parent))
from core.cleaner import Cleaner

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CHROMA_HOST = os.getenv("CHROMA_HOST", "localhost")
CHROMA_PORT = int(os.getenv("CHROMA_PORT", 8000))
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
DATA_PATH = Path(os.getenv("DATA_PATH", "./data"))

CHUNK_SIZE = 400
CHUNK_OVERLAP = 50

# maps collection name -> (filename, cleaner method)
CORPUS_MAP = {
    "plaid":          ("plaid-llm_full.txt",                  "clean_plaid"),
    "stackoverflow":  ("Stack_overflow_plaid_topqa.txt",       "clean_stackoverflow"),
    "rest_book":      ("The_little_book_on_rest_services.txt", "clean_rest_book"),
    "tutorialspoint": ("toturialspoint_webAPI_learning.txt",   "clean_tutorialspoint"),
}


def chunk_text(text):
    # split on sentence boundaries
    sentences = re.split(r'(?<=[.?!])\s+', text)
    sentences = [s.strip() for s in sentences if s.strip()]
    
    chunks = []
    current_words = []
    
    for sentence in sentences:
        sentence_words = sentence.split()
        
        # if adding this sentence exceeds chunk size, save current and start new
        if len(current_words) + len(sentence_words) > CHUNK_SIZE:
            if current_words:
                chunks.append(" ".join(current_words))
            # overlap: carry last CHUNK_OVERLAP words into next chunk
            current_words = current_words[-CHUNK_OVERLAP:] + sentence_words
        else:
            current_words.extend(sentence_words)
    
    # catch the last chunk
    if current_words:
        chunks.append(" ".join(current_words))
    
    return [c for c in chunks if c.strip()]


def embed_and_load(collection, chunks, collection_name, model):
    batch_size = 64
    total = 0
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i:i + batch_size]
        try:
            embeddings = model.encode(batch).tolist()
            ids = [f"{collection_name}_{i + j}" for j in range(len(batch))]
            collection.add(documents=batch, embeddings=embeddings, ids=ids)
            total += len(batch)
            logger.info(f"  Loaded batch {i // batch_size + 1} ({total}/{len(chunks)} chunks)")
        except Exception as e:
            logger.warning(f"  Batch {i // batch_size + 1} failed: {e}")
            continue
    return total


def ingest_corpus(client, model, cleaner, collection_name, filename, clean_method):
    filepath = DATA_PATH / filename
    if not filepath.exists():
        logger.warning(f"File not found, skipping: {filepath}")
        return

    logger.info(f"\nIngesting '{collection_name}' from {filename}...")

    try:
        raw = filepath.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        logger.error(f"Could not read {filepath}: {e}")
        return

    # clean
    try:
        clean_fn = getattr(cleaner, clean_method)
        cleaned_chunks = clean_fn(raw)
    except Exception as e:
        logger.error(f"Cleaning failed for {collection_name}: {e}")
        return

    if not cleaned_chunks:
        logger.warning(f"No chunks after cleaning for {collection_name}, skipping.")
        return

    # plaid comes back as one big text block, needs chunking
    # other corpora come back pre-chunked by section/QA
    if collection_name == "plaid":
        chunks = []
        for block in cleaned_chunks:
            chunks.extend(chunk_text(block))
    else:
        chunks = cleaned_chunks

    logger.info(f"  Total chunks to embed: {len(chunks)}")

    # get or create collection
    try:
        collection = client.get_or_create_collection(name=collection_name)
    except Exception as e:
        logger.error(f"Could not create ChromaDB collection '{collection_name}': {e}")
        return

    total_loaded = embed_and_load(collection, chunks, collection_name, model)
    logger.info(f"  Done. {total_loaded} chunks loaded into '{collection_name}'")


def main():
    logger.info("Connecting to ChromaDB...")
    try:
        client = chromadb.HttpClient(
            host=CHROMA_HOST,
            port=CHROMA_PORT,
            settings=Settings(anonymized_telemetry=False)
        )
    except Exception as e:
        logger.error(f"Could not connect to ChromaDB at {CHROMA_HOST}:{CHROMA_PORT}: {e}")
        return

    logger.info(f"Loading embedding model: {EMBEDDING_MODEL}")
    try:
        model = SentenceTransformer(EMBEDDING_MODEL)
    except Exception as e:
        logger.error(f"Could not load embedding model: {e}")
        return

    cleaner = Cleaner()

    for collection_name, (filename, clean_method) in CORPUS_MAP.items():
        ingest_corpus(client, model, cleaner, collection_name, filename, clean_method)

    logger.info("\nAll corpora ingested.")


if __name__ == "__main__":
    main()