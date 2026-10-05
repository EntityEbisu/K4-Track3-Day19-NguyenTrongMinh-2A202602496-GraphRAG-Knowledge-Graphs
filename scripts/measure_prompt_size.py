"""Measure the real GraphRAG prompt size against the local model's context limit.

Read-only: queries Neo4j and the embedding endpoint, never mutates the graph.
    .venv/Scripts/python.exe -B scripts/measure_prompt_size.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.chdir(REPO)

from dotenv import load_dotenv
load_dotenv(REPO / ".env")

from src.chunking import RecursiveChunker
from src.graph import GRAPH_PROMPT, Neo4jGraph, load_markdown_docs
from src.llm import MeteredLLM
from src.models import Document
from src.store import EmbeddingStore

CONTEXT_LIMIT = 10_752          # measured: "exceeds the available context size (10752)"
CHECK_NEWS = "news-100260918080821054"
QUESTIONS = json.loads(Path("data/benchmark_kg.json").read_text(encoding="utf-8"))


def approx_tokens(text: str) -> int:
    return int(len(text) / 3.3)      # Vietnamese averages ~3.3 chars/token


def main() -> int:
    llm = MeteredLLM()
    graph = Neo4jGraph("bolt://localhost:7687", "neo4j", "password123")

    chunker = RecursiveChunker(chunk_size=800)
    chunks = [
        Document(id=f"{doc.id}#{i}", content=piece, metadata={**doc.metadata, "doc_id": doc.id})
        for doc in load_markdown_docs("data/drug_news") + load_markdown_docs("data/drug_law")
        for i, piece in enumerate(chunker.chunk(doc.content))
    ]
    store = EmbeddingStore("measure_ctx", embedding_fn=llm.embed)
    store.add_documents(chunks)

    print(f"{'Q':<4}{'facts':>6}{'fact chars':>12}{'chunk chars':>13}{'est tok':>9}{'server tok':>12}{'headroom':>10}")
    print("-" * 70)
    rows = []
    for item in QUESTIONS:
        question = item["question"]
        facts = graph.context(question, [CHECK_NEWS])
        hits = store.search(question, top_k=3)
        prompt = GRAPH_PROMPT.format(
            facts="\n".join(f"- {f}" for f in facts) or "- (không có dữ kiện nào)",
            chunks="\n\n".join(f"[{i}] {c['content']}" for i, c in enumerate(hits, start=1)),
            question=question,
        )
        response = llm._chat_client.chat.completions.create(
            model=llm.chat_model_id, max_tokens=1, temperature=0,
            messages=[{"role": "user", "content": prompt}])
        real = response.usage.prompt_tokens
        facts_chars = sum(len(f) for f in facts)
        chunk_chars = sum(len(c["content"]) for c in hits)
        print(f"{item['id']:<4}{len(facts):>6}{facts_chars:>12,}{chunk_chars:>13,}"
              f"{approx_tokens(prompt):>9,}{real:>12,}{CONTEXT_LIMIT - real:>10,}")
        rows.append((item["id"], len(facts), facts_chars, real))

    worst = max(rows, key=lambda r: r[3])
    print("-" * 70)
    print(f"worst question: {worst[0]} at {worst[3]:,} tokens "
          f"({worst[3] / CONTEXT_LIMIT:.0%} of the {CONTEXT_LIMIT:,}-token limit)")
    print(f"largest single fact: {max(len(f) for item in QUESTIONS for f in graph.context(item['question'], [CHECK_NEWS])):,} chars")
    graph.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())