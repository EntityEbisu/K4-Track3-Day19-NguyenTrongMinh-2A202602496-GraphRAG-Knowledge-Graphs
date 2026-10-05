"""Knowledge Graph (Neo4j) + GraphRAG over two drug-topic knowledge bases.

Contract (fixed — bench_kg.py and the tests rely on it):
    link_entity(name, known)                       -> one of `known` or None          (TODO KG-1)
    build_graph(graph, law_docs, news_docs, llm_fn)   load both KBs into Neo4j      (TODO KG-2)
        every node created from ONE document carries the property `doc_id`
    Neo4jGraph.context(question, doc_ids)         -> list[str] facts               (TODO KG-3)
    GraphRAGAgent.answer(question, top_k)         -> str                           (TODO KG-4)

Everything else in this file is a HINT: one possible ontology (below). Use it as is, change it,
or design your own — your own ontology + report/ONTOLOGY.md earns the bonus (see SUBMISSION.md).

Suggested ontology (Crime is the bridge between the law KB and the news KB):

    (:Article {id, title, law, doc_id})-[:DEFINES]->(:Crime {name})
    (:Article)-[:HAS_CLAUSE]->(:Clause {id, number, penalty, text})-[:MENTIONS]->(:Substance {name})
    (:Case {name, summary, date, doc_id})-[:CHARGED_WITH]->(:Crime)
    (:Case)-[:INVOLVES {amount}]->(:Substance)
    (:Case)-[:LOCATED_IN]->(:Location {name})
    (:Person {name, aliases})-[:INVOLVED_IN {role, sentence, charge}]->(:Case)
"""

from __future__ import annotations

import difflib
import json
import re
from pathlib import Path
from typing import Any, Callable

from .models import Document
from .store import EmbeddingStore

# Canonical substance names: the ones BLHS Chương XX lists, plus common ones in Vietnamese news.
SUBSTANCES = ["Heroine", "Cocaine", "Methamphetamine", "Amphetamine", "MDMA", "XLR-11", "Ketamine",
              "cần sa", "thuốc phiện", "côca"]
CLAUSE_START = re.compile(r"^(\d+)\.\s", re.MULTILINE)
FOOTNOTE = re.compile(r"\[\d+\]")

def load_markdown_docs(folder: str | Path) -> list[Document]:
    """Read crawler output (.md with a flat `key: "value"` front matter) into Documents."""
    docs = []
    for path in sorted(Path(folder).glob("*.md")):
        raw = path.read_text(encoding="utf-8")
        _, front, body = raw.split("---", 2)
        metadata = {k: json.loads(v) for k, v in re.findall(r'^(\w+): (".*")$', front, re.MULTILINE)}
        docs.append(Document(id=metadata.get("doc_id", path.stem), content=body.strip(), metadata=metadata))
    return docs

def normalize_crime(name: str) -> str:
    """'Tội Mua bán trái phép chất ma túy' -> 'mua bán trái phép chất ma túy'."""
    name = re.sub(r"\s+", " ", name.strip().strip("\"'“”").lower())
    return name.removeprefix("tội ").strip()

def link_entity(name: str, known: list[str], normalize: Callable[[str], str] = normalize_crime) -> str | None:
    """Map a free-text mention (e.g. a charge written by a journalist) onto one canonical name in `known`."""
    if not name:
        return None
    normalized_known = {normalize(k): k for k in known}
    wanted = normalize(name)
    if wanted in normalized_known:
        return normalized_known[wanted]
    close = difflib.get_close_matches(wanted, list(normalized_known), n=1, cutoff=0.8)
    return normalized_known[close[0]] if close else None

def find_substances(text: str) -> list[str]:
    lowered = text.lower()
    return [name for name in SUBSTANCES if name.lower() in lowered]

# ----------------------------------------------------------------------------------------------
# Custom ontology (Nguyen Trong Minh): Case keyed by (doc_id, slug) + Substance alias table +
# explicit PenaltyThreshold nodes. See report/ONTOLOGY.md.
#
#   (:Article {id, title, law, doc_id})-[:DEFINES]->(:Crime {name})
#   (:Article)-[:HAS_CLAUSE]->(:Clause {id, number, penalty, text, doc_id})-[:MENTIONS]->(:Substance {name, canonical})
#   (:Clause)-[:SETS_THRESHOLD]->(:Threshold {id, substance, min_g, max_g, text, doc_id})
#   (:Case {key, name, summary, date, doc_id})-[:CHARGED_WITH]->(:Crime)
#   (:Case)-[:INVOLVES {amount}]->(:Substance)
#   (:Case)-[:LOCATED_IN]->(:Location {name})
#   (:Person {name, aliases})-[:INVOLVED_IN {role, charge, sentence}]->(:Case)
#
# Why this differs from the suggestion (see ONTOLOGY.md §7):
#   1. `Case.key = doc_id + "#" + index` instead of an LLM-invented name. Measured: with the hint
#      key, a 4B model invented "Vụ mua bán 36kg ma túy tại TP.HCM" for an article containing
#      neither "36kg" nor "TP.HCM" — an unstable MERGE key, i.e. the E3 duplicate-entity bug.
#   2. `Substance.canonical` + SUBSTANCE_ALIASES merges synonyms (ketamine/ketamin/"K") so law-side
#      and news-side mentions collapse onto ONE node; the hint schema cannot merge them.
#   3. `Threshold` nodes model the statutory mass cut-offs, so Q5 ("MDMA >= 100g -> khoản 4") is a
#      graph join instead of a keyword match. The hint schema stores thresholds only as free text.

def _slug(text: str) -> str:
    """ASCII-ish slug; Vietnamese diacritics collapse so the key is stable across model runs."""
    folded = (text.replace("đ", "d").replace("Đ", "D")
              .encode("ascii", "ignore").decode("ascii").lower())
    return re.sub(r"[^a-z0-9]+", "-", folded).strip("-")[:60]

# Canonical name -> the surface forms seen in law text or in news prose.
SUBSTANCE_ALIASES = {
    "Heroine": ["heroine", "heroin"],
    "Cocaine": ["cocaine", "côcain", "cô ca"],
    "Methamphetamine": ["methamphetamine", "methamphetamin", "meth"],
    "Amphetamine": ["amphetamine", "amfetamin"],
    "MDMA": ["mdma", "m.d.m.a"],
    "XLR-11": ["xlr-11", "xlr11"],
    "Ketamine": ["ketamine", "ketamin", "keta"],
    "Cannabis": ["cần sa", "can sa", "cây cần sa"],
    "Opium": ["thuốc phiện", "thuoc phien", "opium"],
    "CocaLeaf": ["côca", "coca", "lá côca", "la coca"],
}

def canonical_substance(name: str) -> str:
    """Map any surface form onto one canonical Substance name (empty string when unknown)."""
    lowered = name.strip().lower()
    if not lowered:
        return ""
    for canonical, aliases in SUBSTANCE_ALIASES.items():
        if lowered == canonical.lower() or any(a in lowered for a in aliases):
            return canonical
    return ""

# Kinds used to tell a real offence apart from procedural/administrative articles.
CRIME_PREFIX = "tội "

def parse_law_article(doc: Document) -> dict[str, Any]:
    """Deterministic (regex) extraction for one 'Điều' — law text is regular enough to skip the LLM."""
    article_id = doc.metadata["article"]                       # "Điều 251 BLHS"
    title = doc.metadata["title"].split(". ", 1)[-1]           # "Tội mua bán trái phép chất ma túy"
    body = FOOTNOTE.sub("", doc.content)
    starts = list(CLAUSE_START.finditer(body))
    clauses = []
    for index, start in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(body)
        text = body[start.start():end].strip()
        first_line = text.splitlines()[0]
        penalty = re.search(r"\bbị ((?:phạt|tù|cảnh cáo).+?)(?::|$)", first_line)
        clauses.append({
            "id": f"{article_id} khoản {start.group(1)}",
            "number": int(start.group(1)),
            "penalty": penalty.group(1).rstrip(".") if penalty else "",
            "text": text,
            "substances": find_substances(text),
            "thresholds": parse_thresholds(text, article_id, int(start.group(1))),
        })
    return {
        "id": article_id,
        "law": doc.metadata.get("law", ""),
        "title": title,
        "doc_id": doc.id,
        "crime": normalize_crime(title) if title.startswith("Tội ") else None,
        "clauses": clauses,
    }

# "… có khối lượng từ 100 gam trở lên", "từ 0,1 gam đến dưới 05 gam", "từ 05 gam đến dưới 30 gam".
# The connector "từ" is optional: Điều 250 khoản 4 reads "có khối lượng 100 gam trở lên" (no "từ").
MASS_UNITS = {"gam": 1.0, "g": 1.0, "kilôgam": 1000.0, "kg": 1000.0, "tấn": 1_000_000.0}
_NUMBER = r"\d+(?:[.,]\d+)?"
_UNIT = r"(gam|kg|kilôgam|g|tấn)"
_THRESHOLD_RANGE = re.compile(
    rf"khối lượng\s+(?:từ\s+)?({_NUMBER})\s*{_UNIT}\s+đến\s+dưới\s+({_NUMBER})\s*{_UNIT}")
_THRESHOLD_MIN = re.compile(rf"khối lượng\s+(?:từ\s+)?({_NUMBER})\s*{_UNIT}\s+trở lên")

def _to_grams(value: str, unit: str) -> float:
    return float(value.replace(",", ".")) * MASS_UNITS[unit.lower()]

def parse_thresholds(clause_text: str, article_id: str, clause_number: int) -> list[dict[str, Any]]:
    """Pull the statutory mass cut-offs out of one clause so Q5 can be answered by a join.

    Scoped per LINE, not per clause: a clause holds several lettered points and each point names its
    own substances, e.g. Điều 250 khoản 4 point b) is
    "Heroine, Cocaine, Methamphetamine, Amphetamine, MDMA hoặc XLR-11 có khối lượng 100 gam trở lên"
    — one cut-off, six substances. Matching on the whole clause would attach every cut-off to the
    clause's first substance only and silently lose MDMA.
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line in clause_text.splitlines():
        ranges = _THRESHOLD_RANGE.findall(line)
        mins = _THRESHOLD_MIN.findall(line)
        if not ranges and not mins:
            continue
        spans = [(m.start(), m.end()) for m in
                 list(_THRESHOLD_RANGE.finditer(line)) + list(_THRESHOLD_MIN.finditer(line))]
        # Only substances mentioned before the cut-off share it.
        head = line[:min(start for start, _ in spans)] if spans else line
        substances = find_substances(head)
        for match in _THRESHOLD_RANGE.finditer(line):
            lo = _to_grams(match.group(1), match.group(2))
            hi = _to_grams(match.group(3), match.group(4))
            for substance in substances:
                key = f"{substance}|{lo}|{hi}"
                if key in seen:
                    continue
                seen.add(key)
                out.append({
                    "id": f"{article_id} khoản {clause_number} | {substance} | {lo:g}-{hi:g}g",
                    "substance": substance,
                    "min_g": lo,
                    "max_g": hi,
                    "text": match.group(0),
                })
        for match in _THRESHOLD_MIN.finditer(line):
            lo = _to_grams(match.group(1), match.group(2))
            for substance in substances:
                key = f"{substance}|{lo}|inf"
                if key in seen:
                    continue
                seen.add(key)
                out.append({
                    "id": f"{article_id} khoản {clause_number} | {substance} | {lo:g}g+",
                    "substance": substance,
                    "min_g": lo,
                    "max_g": None,
                    "text": match.group(0),
                })
    return out

# Placeholders are deliberately UNQUOTED. With the starter prompt's quoted placeholders a 4B model
# copies the instruction text into the JSON ("charges": ["tội danh","BẮT BUỘC"]), which link_entity
# then correctly drops to None -> no CHARGED_WITH edge -> the bridge node never forms.
NEWS_EXTRACTION_PROMPT = """Trích xuất knowledge graph từ bài báo tiếng Việt về ma túy.
Chỉ dùng thông tin có trong bài. Không suy đoán, không bổ sung chi tiết ngoài bài.

Trả về DUY NHẤT một object JSON đúng shape sau, giữ nguyên tên key:
{
  "cases": [
    {
      "name": <tên ngắn gọn mô tả vụ việc; chỉ dùng chi tiết CÓ TRONG BÀI>,
      "summary": <tóm tắt 1-2 câu>,
      "date": <YYYY-MM-DD hoặc chuỗi rỗng>,
      "location": <tỉnh/thành hoặc chuỗi rỗng>,
      "charges": [<mỗi tội danh, chép NGUYÊN VĂN từ danh sách hợp lệ bên dưới>],
      "substances": [{"name": <tên chất>, "amount": <khối lượng nếu bài có nêu>}] ,
      "people": [{"name": <họ tên>, "aliases": [<biệt danh nếu có>], "role": <bị cáo|bị can|nghi phạm|người liên quan>,
                  "charge": <tội danh chép nguyên văn hoặc chuỗi rỗng>, "sentence": <mức án>}]
    }
  ]
}

DANH SÁCH TỘI DANH HỢP LỆ — chỉ được dùng đúng các chuỗi này, viết thường, KHÔNG thêm tiền tố "Tội":
{crimes}

DANH SÁCH CHẤT HỢP LỆ: {substances}

Nếu bài có nhiều vụ việc riêng biệt, tách thành nhiều phần tử trong cases.
Nếu bài không nói về vụ án cụ thể, trả về {{"cases": []}}.
Chỉ trả về JSON, không thêm chữ nào khác.

Tiêu đề: {title}
Nội dung:
{content}"""

def extract_news_cases(doc: Document, llm_fn: Callable[[str], str], known_crimes: list[str]) -> list[dict]:
    """LLM extraction for one news article; charges are re-linked to law-KB crimes in code."""
    prompt = NEWS_EXTRACTION_PROMPT.format(
        crimes="\n  - ".join(known_crimes), substances=", ".join(SUBSTANCES),
        title=doc.metadata.get("title", ""), content=doc.content[:12000],
    )
    try:
        cases = json.loads(llm_fn(prompt)).get("cases", [])
    except (json.JSONDecodeError, AttributeError):
        return []
    for case in cases:
        case["charges"] = sorted({c for c in (link_entity(x, known_crimes) for x in case.get("charges", [])) if c})
        for person in case.get("people", []):
            person["charge"] = link_entity(person.get("charge") or "", known_crimes) or ""
        # Substance surface forms collapse onto the canonical law-side node name.
        for substance in case.get("substances", []):
            canonical = canonical_substance(str(substance.get("name", "")))
            substance["name"] = canonical or substance.get("name", "")
        case["substances"] = [s for s in case.get("substances", []) if s.get("name")]
    return cases

# ----------------------------------------------------------------------------------------------
# Neo4j
# ----------------------------------------------------------------------------------------------

class Neo4jGraph:
    """Thin wrapper over the official neo4j driver."""

    def __init__(self, uri: str, user: str, password: str) -> None:
        from neo4j import GraphDatabase

        self.driver = GraphDatabase.driver(uri, auth=(user, password), notifications_min_severity="OFF")
        self.driver.verify_connectivity()

    def close(self) -> None:
        self.driver.close()

    def run(self, cypher: str, **params: Any) -> list[dict]:
        records, _, _ = self.driver.execute_query(cypher, params)
        return [record.data() for record in records]

    def reset(self) -> None:
        """Delete every node, relationship and constraint (bench_kg.py calls this before build_graph)."""
        self.run("MATCH (n) DETACH DELETE n")
        for row in self.run("SHOW CONSTRAINTS YIELD name RETURN name"):
            self.run(f"DROP CONSTRAINT `{row['name']}` IF EXISTS")

    def stats(self) -> dict[str, int]:
        nodes = self.run("MATCH (n) RETURN count(n) AS n")[0]["n"]
        rels = self.run("MATCH ()-[r]->() RETURN count(r) AS n")[0]["n"]
        return {"nodes": nodes, "relationships": rels}

    def seed_facts(self, question: str, doc_ids: list[str], skip_labels: tuple[str, ...] = (),
                   limit: int = 60) -> tuple[list[str], list[str]]:
        """Ontology-independent first step: seed nodes + their 1-hop edges as text facts.

        Seeds = nodes whose `doc_id` is in doc_ids, or whose `name`/`aliases` appear in the question.
        Returns (seed elementIds, facts). Nodes with a label in skip_labels are left out of the facts.
        """
        seeds = self.run(
            """
            MATCH (n)
            WHERE n.doc_id IN $doc_ids
               OR (n.name IS :: STRING AND size(n.name) >= 3 AND toLower($q) CONTAINS toLower(n.name))
               OR any(a IN coalesce(n.aliases, []) WHERE size(a) >= 3 AND toLower($q) CONTAINS toLower(a))
            RETURN elementId(n) AS id
            """,
            q=question, doc_ids=doc_ids,
        )
        seed_ids = [row["id"] for row in seeds]
        edges = self.run(
            """
            MATCH (s)-[r]-(m)
            WHERE elementId(s) IN $ids
              AND none(l IN labels(s) + labels(m) WHERE l IN $skip)
            WITH DISTINCT r LIMIT $limit
            WITH startNode(r) AS a, r, endNode(r) AS b
            RETURN labels(a)[0] AS a_label, coalesce(a.name, a.id) AS a_name, type(r) AS rel,
                   properties(r) AS props, labels(b)[0] AS b_label, coalesce(b.name, b.id) AS b_name
            """,
            ids=seed_ids, skip=list(skip_labels), limit=limit,
        )
        facts = []
        for e in edges:
            props = ", ".join(f"{k}: {v}" for k, v in e["props"].items() if v)
            facts.append(f"({e['a_label']}: {e['a_name']}) -[{e['rel']}{' {' + props + '}' if props else ''}]-> "
                         f"({e['b_label']}: {e['b_name']})")
        return seed_ids, facts

    # ---------------------------------------------------------------- HINT — suggested ontology: writes

    def suggested_constraints(self) -> None:
        for label, key in [("Article", "id"), ("Clause", "id"), ("Crime", "name"), ("Case", "name"),
                           ("Substance", "name"), ("Person", "name"), ("Location", "name")]:
            self.run(f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) REQUIRE n.{key} IS UNIQUE")

    def add_law_article(self, article: dict) -> None:
        self.run(
            """
            MERGE (a:Article {id: $id}) SET a.title = $title, a.law = $law, a.doc_id = $doc_id
            FOREACH (crime IN CASE WHEN $crime IS NULL THEN [] ELSE [$crime] END |
                MERGE (c:Crime {name: crime}) MERGE (a)-[:DEFINES]->(c))
            WITH a
            UNWIND $clauses AS clause
            MERGE (cl:Clause {id: clause.id})
              SET cl.number = clause.number, cl.penalty = clause.penalty, cl.text = clause.text, cl.doc_id = $doc_id
            MERGE (a)-[:HAS_CLAUSE]->(cl)
            FOREACH (s IN clause.substances | MERGE (sub:Substance {name: s}) MERGE (cl)-[:MENTIONS]->(sub))
            """,
            **article,
        )

    def add_news_case(self, case: dict, doc: Document) -> None:
        self.run(
            """
            MERGE (k:Case {name: $name})
              SET k.summary = $summary, k.date = $date, k.doc_id = $doc_id, k.source_title = $title
            FOREACH (loc IN CASE WHEN $location = '' THEN [] ELSE [$location] END |
                MERGE (l:Location {name: loc}) MERGE (k)-[:LOCATED_IN]->(l))
            FOREACH (crime IN $charges | MERGE (c:Crime {name: crime}) MERGE (k)-[:CHARGED_WITH]->(c))
            FOREACH (s IN $substances | MERGE (sub:Substance {name: s.name}) MERGE (k)-[r:INVOLVES]->(sub)
                SET r.amount = s.amount)
            FOREACH (p IN $people | MERGE (person:Person {name: p.name})
                SET person.aliases = coalesce(p.aliases, [])
                MERGE (person)-[r:INVOLVED_IN]->(k) SET r.role = p.role, r.charge = p.charge, r.sentence = p.sentence)
            """,
            name=case.get("name") or doc.metadata.get("title", doc.id),
            summary=case.get("summary", ""), date=case.get("date", ""), location=case.get("location", ""),
            charges=case.get("charges", []), people=[p for p in case.get("people", []) if p.get("name")],
            substances=[s for s in case.get("substances", []) if s.get("name")],
            doc_id=doc.id, title=doc.metadata.get("title", ""),
        )

    # ---------------------------------------------------------------- KG-3

    def context(self, question: str, doc_ids: list[str], max_facts: int = 60) -> list[str]:
        """Graph facts for a question: seeds + 1 hop, then the legal basis of every case reached."""
        # TODO KG-3: multi-hop retrieval over YOUR ontology.
        #   1. self.seed_facts(question, doc_ids) -> (seed_ids, facts)   (ontology-independent, already written)
        #   2. From the seeds, walk to the other KB through your bridge node (Cypher, see LAB_GUIDE Bước 5)
        #   3. Append one readable string per fact; return the list.
        #
        # HINT (suggested ontology):
        #   a. Cases that are a seed or next to one -> add f"Vụ việc '{name}': {summary}" to facts
        #        MATCH (k:Case) WHERE elementId(k) IN $ids OR EXISTS { MATCH (s)--(k) WHERE elementId(s) IN $ids }
        #   b. For those cases follow
        #        (Case)-[:CHARGED_WITH]->(Crime)<-[:DEFINES]-(Article)-[:HAS_CLAUSE]->(Clause)
        #      keep clause 1 + clauses that MENTION a Substance the case INVOLVES
        #   c. Articles named in the question ("Điều 251" -> re.findall(r"[Đđ]iều (\d+)", question)):
        #      clause 1 + clauses mentioning find_substances(question)
        #   d. One fact per clause: f"[{article_id} - {title}] khoản {number}: {text}"
        raise NotImplementedError("TODO KG-3 Neo4jGraph.context (src/graph.py) - kiểm tra: python bench_kg.py --check")

# ---------------------------------------------------------------------------------------------- KG-2

def build_graph(graph: Neo4jGraph, law_docs: list[Document], news_docs: list[Document],
                llm_fn: Callable[..., str]) -> None:
    """Load both KBs into an empty graph. llm_fn(prompt, json_mode=False) -> str (metered OpenAI chat)."""
    # TODO KG-2: create YOUR ontology in Neo4j from both KBs.
    #   Contract: every node created from one document has the property doc_id = Document.id.
    #   Fastest start: the HINT helpers above (parse_law_article, extract_news_cases, suggested_constraints,
    #   add_law_article, add_news_case). Own ontology + report/ONTOLOGY.md = bonus (SUBMISSION.md).
    raise NotImplementedError("TODO KG-2 build_graph (src/graph.py) - kiểm tra: python bench_kg.py --build --limit 2")

# ---------------------------------------------------------------------------------------------- KG-4

GRAPH_PROMPT = """Trả lời câu hỏi chỉ dựa trên ngữ cảnh (đoạn văn bản và dữ kiện từ knowledge graph).
Nêu rõ số Điều luật khi có. Nếu ngữ cảnh không đủ, nói không đủ thông tin.

Dữ kiện knowledge graph:
{facts}

Đoạn văn bản:
{chunks}

Câu hỏi: {question}
Trả lời:"""

class GraphRAGAgent:
    """Hybrid GraphRAG: the same vector top-k as flat RAG, plus facts expanded from the graph."""

    def __init__(self, store: EmbeddingStore, graph: Neo4jGraph, llm_fn: Callable[[str], str]) -> None:
        self.store = store
        self.graph = graph
        self.llm_fn = llm_fn

    def answer(self, question: str, top_k: int = 3) -> str:
            chunks = self.store.search(question, top_k=top_k)
            doc_ids: list[str] = []
            for chunk in chunks:
                doc_id = chunk["metadata"].get("doc_id")
                if doc_id and doc_id not in doc_ids:
                    doc_ids.append(doc_id)
            facts = self.graph.context(question, doc_ids)
            prompt = GRAPH_PROMPT.format(
                facts="\n".join(f"- {fact}" for fact in facts) or "- (không có dữ kiện nào trong knowledge graph)",
                chunks="\n\n".join(f"[{i}] {chunk['content']}" for i, chunk in enumerate(chunks, start=1)),
                question=question,
            )
            return self.llm_fn(prompt)
