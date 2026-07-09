"""
MekongAI Nexus - RAG (Retrieval-Augmented Generation) Pipeline.

PostgreSQL pgvector-based document indexing, chunking, and hybrid retrieval.

Modules:
- models: Dataclasses for Chunk, SearchResult, IndexResult
- chunker: LangChain-based smart document chunking
- vector_store: PostgreSQL pgvector vector store
- indexer: Document index pipeline (chunk → embed → store)
- retriever: Hybrid retrieval (vector + graph + FTS)
- graph_rag: Knowledge Graph-based retrieval
- light_rag: In-memory single-document QA
"""
