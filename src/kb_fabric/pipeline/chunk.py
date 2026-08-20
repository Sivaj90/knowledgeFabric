"""Chunk step (HLD §7.3, local VPC HLD's onyx-inspired chunker config).

LangChain semantic/recursive chunking. Per the local VPC HLD's stated
config decision (§ Reference research): no chunk overlap, for clean
recombination and because it matches the "chunk = atomic permission unit"
rule -- overlapping chunks would mean the same sentence could carry two
different ACL decisions depending on which chunk it landed in.
"""

from langchain_text_splitters import RecursiveCharacterTextSplitter

# Concrete numbers pinned here (were an open item in the local VPC HLD --
# "chunk size/overlap TBC"). 1000 chars (~200-250 tokens) balances retrieval
# granularity against the HNSW/embedding cost of very small chunks; 0
# overlap per the onyx-pattern decision above.
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 0


def chunk_text(text: str) -> list[str]:
    """Split parsed document text into chunk strings. Empty/whitespace-only
    output from parse_file() yields an empty chunk list (no empty chunks
    written to the DB)."""
    if not text or not text.strip():
        return []

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    return [c for c in splitter.split_text(text) if c.strip()]
