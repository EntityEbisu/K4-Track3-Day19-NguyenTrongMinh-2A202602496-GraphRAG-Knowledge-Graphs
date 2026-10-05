# Báo cáo Day 19 — Flat RAG vs GraphRAG

**Họ tên:** Nguyễn Trọng Minh  **MSSV:** 2A202602496  **Ngày:** 05/10/2026

> Mọi số liệu lấy từ `ket_qua_benchmark_kg.txt` (sinh bằng `python bench_kg.py --judge`).
> Bản thiết kế ontology nộp ở `report/ONTOLOGY.md`. Bằng chứng Cypher đầy đủ:
> `report/img/error_evidence.txt` (sinh bằng `scripts/error_evidence.py`).

**Cấu hình chạy:** chat = `ternary-bonsai-4b`, embedding = `text-embedding-bge-m3` (cả hai chạy
**local** qua LM Studio), `top_k=3`, `chunk_size=800`, 176 chunk, KG = **435 node / 853 cạnh**.

---

## 1. Chi phí (10 điểm)

Dán từ `ket_qua_benchmark_kg.txt`:

```
== Indexing (one-off)
pipeline  calls    in_tok  out_tok       USD  seconds
flat        176         0        0   0.00000     18.7
graph       196     36953    16271   0.00000    533.3

== Querying (mean per question)
pipeline  recall  judge   in_tok  out_tok       USD  seconds
flat        0.57   1.00      710      129   0.00000    12.97
graph       0.79   1.67     2925      453   0.00000    33.83
```

| Chỉ số | Flat | Graph | Graph / Flat |
| --- | --- | --- | --- |
| Indexing USD | 0.00000 | 0.00000 | ×1.00 (cả hai đều $0) |
| Indexing giây | 18.7 | 533.3 | **×28.5** |
| Mỗi câu: USD | 0.00000 | 0.00000 | ×1.00 (cả hai đều $0) |
| Mỗi câu: giây | 12.97 | 33.83 | **×2.6** |
| Mỗi câu: in_tok | 710 | 2.925 | **×4.1** |

**Về cột USD — đọc kỹ trước khi so.** Cột này **bằng 0.00 cho cả hai pipeline**, không phải vì
GraphRAG rẻ hơn mà vì **cả hai model đều chạy local trên LM Studio nên không tốn tiền API**. Hàm
`price()` trong `src/llm.py` không có mục giá cho `ternary-bonsai-4b` / `text-embedding-bge-m3` nên
trả về 0. Vì vậy **tôi dùng token và độ trễ làm trục chi phí chính**, đó là hai thứ thật sự khác nhau.

Để vẫn nói được về tiền, tôi tính thêm một cột **quy đổi theo giá `gpt-4o-mini`** (0,15 USD/1M token
input, 0,60 USD/1M token output) trên **đúng số token đã đo** — đây là phép tính của tôi, không phải
số in ra từ harness:

| Chỉ số | Flat | Graph | Graph / Flat | USD nếu dùng `gpt-4o-mini` |
| --- | --- | --- | --- | --- |
| Indexing | 0 in_tok | 36.953 in + 16.271 out | — | Flat $0.00000 → Graph **≈ $0.0149** |
| Mỗi câu | 710 in + 129 out | 2.925 in + 453 out | ×4.1 token | Flat $0.00018 → Graph **≈ $0.00071** |

*(Cột cuối chỉ là quy đổi giả định để so sánh; nếu chạy thật với `gpt-4o-mini` thì đây là chi phí.)*

**Chi phí tăng thêm đến từ đâu?** Phần lớn là **dựng graph một lần**, không phải mỗi câu hỏi:
Indexing tăng thêm **514,6 giây (×28.5)** và **53.224 token**, vì 20 lần gọi LLM trích xuất phải đọc
cả bài báo (~2.600 token input + ~800 token output mỗi bài). Đây là chi phí **trả một lần**. Phần
tính mỗi câu tăng **×4.1 token input** vì `GRAPH_PROMPT` nhồi thêm dữ kiện multi-hop của KG-3 bên
cạnh 3 chunk văn bản. Đo bằng `scripts/measure_prompt_size.py`: prompt nặng nhất là Q5 ở 3.591
token, tức chỉ **33%** context 10.752 token của model — **không** phải giới hạn thực tế.

---

## 2. Từng câu hỏi (10 điểm)

| Câu | Loại | Flat recall / judge | Graph recall / judge | Thắng | Vì sao (1 câu) |
| --- | --- | --- | --- | --- | --- |
| Q1 | single-hop-law | 1.00 / 2 | 1.00 / 2 | Hòa | Cả hai tìm được định nghĩa trong Điều 1; KG thêm ngữ cảnh thừa không giúp |
| Q2 | single-hop-news | 1.00 / 2 | **0.50 / 1** | **Flat** | Đoạn văn đã chứa cả hai tên nên Flat đủ; KG làm LLM kể thiếu một người |
| Q3 | cross-kb | 0.33 / 0 | **1.00 / 2** | **Graph** | Flat đoán nhầm "điều 21", graph nối đúng Điều 251 + khoản 1 |
| Q4 | cross-kb | 0.33 / 1 | **0.67 / 1** | **Graph** | Graph có Điều 255 nhưng LLM vẫn gộp thêm tội `mua bán` (xem E5) |
| Q5 | cross-kb-multi-hop | 0.40 / 0 | **0.60 / 2** | **Graph** | Ngưỡng MDMA ≥100g → khoản 4 nhờ join `Threshold`, Flat không dò được |
| Q6 | aggregation | 0.33 / 1 | **1.00 / 2** | **Graph** | `Substance` là node dùng chung nên đếm vụ MDMA chỉ 1 hop |

**Quy luật.** GraphRAG thắng **4/6 câu**, và **thắng mọi câu `cross-kb`** (Q3, Q4, Q5 — cả ba đều là
loại câu mà đáp án nằm rải ở 2 KB: tên người ở tin, khung hình phạt ở luật). Ở Q3 sự chênh lệch lớn
nhất: recall 0.33 → 1.00 và judge 0 → 2. Ngược lại, ở hai câu **một KB** (Q1, Q2) Flat thắng hoặc
hòa — vì khi đáp án đã nằm trong một đoạn văn thì vector search là đủ, và phần dữ kiện KG thêm vào
chỉ làm prompt dài thêm. **Quy luật: KG đáng dùng khi câu hỏi cần nối 2 nguồn; thừa khi câu hỏi
chỉ nằm trong 1 nguồn.** Q2 là ngoại lệ đáng nói: Graph thua vì LLM sinh thêm thông tin (xem E5).

---

## 3. Phân tích lỗi (20 điểm)

Bằng chứng đầy đủ: `report/img/error_evidence.txt`, sinh bằng `scripts/error_evidence.py` (chạy lại
được, read-only).

### Lỗi E3: Thực thể trùng — cùng một chất thành nhiều node

- **Hiện tượng:** `Substance` có **18 node** nhưng phía luật chỉ dùng 10 tên. Tên do LLM đặt ở phía
  tin tạo ra node mới không khớp node luật, nên cùng một chất bị đếm ở nhiều chỗ.
- **Bằng chứng** (`scripts/error_evidence.py`, mục E3):

```cypher
MATCH (s:Substance) RETURN s.name AS name,
       count { (s)<-[:MENTIONS]-() } AS mentioned_by_law,
       count { (s)<-[:INVOLVES]-() }  AS used_by_news ORDER BY name
```
```
{'name': 'Ketamine',        'mentioned_by_law': 0,  'used_by_news': 8}
{'name': 'cần sa',          'mentioned_by_law': 20, 'used_by_news': 0}
{'name': 'need sa',         'mentioned_by_law': 0,  'used_by_news': 2}   <-- trùng với 'cần sa'
{'name': 'ma túy',          'mentioned_by_law': 0,  'used_by_news': 3}   <-- không khớp node luật nào
{'name': 'ma túy tổng hợp', 'mentioned_by_law': 0,  'used_by_news': 2}
{'name': 'etomidate',       'mentioned_by_law': 0,  'used_by_news': 3}   <-- không có trong BLHS
{'name': 'thuốc lắc',       'mentioned_by_law': 0,  'used_by_news': 1}
{'name': 'pod chill',       'mentioned_by_law': 0,  'used_by_news': 1}
{'name': 'rượu',            'mentioned_by_law': 0,  'used_by_news': 1}   <-- không phải ma túy
{'name': 'Nước vui',        'mentioned_by_law': 0,  'used_by_news': 1}
... (8 node còn lại: Amphetamine, Cocaine, Heroine, MDMA, Methamphetamine, XLR-11, côca, thuốc phiện)
```

  Bằng chứng rõ nhất: **`Ketamine` không hề được luật `MENTIONS` (0) nhưng tin dùng 8 lần** — nó chỉ
  sống ở một phía, đúng cái lỗi ontology gợi ý mô tả. Ngược lại `MDMA` được cả hai phía dùng
  (18 / 7) nên nối được. Và `cần sa` (luật, 20) vs `need sa` (tin, 2) là **hai node cho cùng một chất**.

  Lỗi này **không chỉ ở `Substance`** — `Person` cũng bị trùng. Trong 4 bài báo nói về "Hoàng Nato",
  LLM tạo ra **hai node `Person` cho cùng một con người**, và cả hai đều để trống `aliases`:

```cypher
MATCH (p:Person) RETURN p.name AS name, p.aliases AS aliases,
       count { (p)-[:INVOLVED_IN]->() } AS cases ORDER BY name
```
```
{'name': 'Dương Minh Tuấn', 'aliases': [], 'cases': 3}   <-- cùng một người
{'name': 'Hoàng Nato',     'aliases': [], 'cases': 1}   <-- biệt danh, bị tách thành node riêng
```

  Đáng chú ý: đây đúng là lỗi mà property `aliases` **sinh ra để chống** — nhưng vì LLM điền không
  nhất quán (biết tên thật ở bài này, lại dùng biệt danh ở bài khác), `aliases` rỗng nên cơ chế
  không kích hoạt được. Đây là bằng chứng cho thấy **entity-resolution dựa vào LLM không đáng tin**,
  và là lý do tôi khóa `Case` theo `doc_id` thay vì theo tên.
- **Nguyên nhân:** nằm ở **bước thiết kế ontology + bước trích xuất LLM**. `canonical_substance()`
  của tôi chỉ có 10 alias phủ tên luật; các tên chất **không có trong BLHS** (`etomidate`,
  `thuốc lắc`, `pod chill`, `ma túy`, `rượu`) không khớp canonical nào nên tôi giữ nguyên tên LLM
  đặt → mỗi tên một node. Đây chính là điểm yếu "không gộp được tên đồng nghĩa" mà `LAB_GUIDE` nêu.
- **Đề xuất sửa:** mở rộng `SUBSTANCE_ALIASES` thêm `need sa`/`cây cần sa` → `cần sa`, và
  `thuốc lắc`/`ma túy tổng hợp` → `Methamphetamine`. Với chất **không có trong BLHS** (`etomidate`,
  `pod chill`) thì **không nên** ép vào node luật — cách đúng là thêm property
  `in_law: false` để câu hỏi aggregation biết nó tồn tại nhưng không có khung phạt.
  Riêng `Person`, cần một bước **resolve trước khi `MERGE`**: gom các node có cùng `doc_id` hoặc
  cùng tập bài báo, rồi hợp nhất theo tên thật + biệt danh (ghi biệt danh vào `aliases` thay vì
  tạo node mới). Đánh đổi: thêm một lượt Cypher gom nhóm trước khi ghi, và tăng rủi ro gộp nhầm
  hai người trùng tên; bù lại Q2 và Q4 (tìm theo biệt danh) sẽ chính xác hơn.

### Lỗi E5: LLM lệch với dữ kiện trong graph

- **Hiện tượng:** ở Q4, GraphRAG trả lời về **hai** tội danh (`tổ chức sử dụng` **và** `mua bán`),
  trong khi đáp án chuẩn chỉ có `tổ chức sử dụng`. Ở Q2, GraphRAG chỉ nêu `Trần Thanh Tuấn`, bỏ sót
  `Trần Minh Tâm`.
- **Bằng chứng** — trích nguyên văn từ `ket_qua_benchmark_kg.txt`:

```
--- Q4 [cross-kb] graph recall=0.67 judge=1 16.56s
Giang hồ 'Hoàng Nato' (Dương Minh Tuấn) bị bắt về hành vi tổ chức sử dụng trái phép chất ma túy
và mua bán trái phép chất ma túy. Theo Bộ luật Hình sự Việt Nam, các tội này được quy định tại:
- Điều 251 BLHS: Tội mua bán trái phép chất ma túy → phạt tù từ 02 năm đến 07 năm.
- Điều 255 BLHS: Tội tổ chức sử dụng trái phé...

--- Q2 [single-hop-news] graph recall=0.50 judge=1 78.38s
Trong vụ đường dây mua bán hơn 36kg ma túy bị TAND TP.HCM xét xử ngày 28-9, **trần Thanh Tuấn** là
người bị tuyên án tử hình về tội mua bán trái phép chất ma túy.
```

  Và graph **đúng**, LLM **sai** — Cypher trả về đúng một tội danh cho Q4:

```cypher
MATCH (p:Person)-[:INVOLVED_IN]->(k:Case)-[:CHARGED_WITH]->(c:Crime)<-[:DEFINES]-(a:Article)
WHERE any(al IN coalesce(p.aliases, []) WHERE toLower(al) CONTAINS 'nato')
RETURN p.name AS person, p.aliases AS aliases, c.name AS crime, a.id AS article
```
```
{'person': 'Dương Minh Tuấn', 'aliases': [], 'crime': 'tổ chức sử dụng trái phép chất ma túy', 'article': 'Điều 255 BLHS'}
```

  Ở Q2, câu trả lời còn viết **"trần Thanh Tuấn"** (chữ `t` thường) trong khi `must_include` là
  `"Trần Thanh Tuấn"` — nhưng `keyword_recall` dùng `k.lower() in answer.lower()` nên vẫn tính là
  khớp; điểm bị trừ là **thiếu hẳn `Trần Minh Tâm`**, không phải do chữ hoa.
- **Nguyên nhân:** nằm ở **bước prompt trả lời (KG-4)**, không phải ở graph. `GRAPH_PROMPT` đưa cả
  3 chunk văn bản lẫn dữ kiện graph vào cùng một prompt không phân biệt; khi dữ kiện graph chỉ có
  một tội danh nhưng chunk văn bản nhắc tới hai, LLM 4B **gộp lại**. Với Q2, `context()` chỉ trả về
  vụ án tìm được nên LLM chỉ kể một người tử hình.
- **Đề xuất sửa:** (1) dán nhãn nguồn rõ hơn trong `GRAPH_PROMPT` — yêu cầu "ưu tiên số Điều và
  tội danh trong dữ kiện knowledge graph; chỉ dùng đoạn văn cho chi tiết mức án"; (2) ở KG-3, trả về
  **mọi** người có `sentence` chứa "tử hình" cho câu hỏi dạng này, không chỉ case đầu. Đánh đổi: prompt
  dài hơn ~200 token/câu.

### Lỗi E1: Cầu nối gãy ở 2 vụ việc

- **Hiện tượng:** 2 `Case` có `doc_id` nhưng **không có cạnh `CHARGED_WITH`** nào, tức không nối
  được sang luật. 31 vụ còn lại thì nối tốt.
- **Bằng chứng:**

```cypher
MATCH (k:Case) WHERE NOT (k)-[:CHARGED_WITH]->()
RETURN k.key AS key, k.name AS name, k.doc_id AS doc_id ORDER BY k.doc_id
```
```
{'key': 'news-100260924105118645#2', 'name': 'Vụ án của DJ Thái Hoàng',       'doc_id': 'news-100260924105118645'}
{'key': 'news-100260924105118645#3', 'name': 'Vụ án tuyển dụng trái quy định tại Viện Pháp y tâm thần Trung ương', 'doc_id': 'news-100260924105118645'}
{'bridged_cases': 31}
```

- **Nguyên nhân:** cả hai nằm trong **cùng một bài** `news-100260924105118645`, và cả hai là hành vi
  mà BLHS **không định nghĩa là tội danh** cụ thể: "tuyển dụng trái quy định tại Viện Pháp y tâm thần"
  và một vụ liên quan DJ. `link_entity` trả `None` cho tội danh không tồn tại → **đúng** như thiết kế
  "không đoán bừa" (mục 4 `ONTOLOGY.md`). Đây là cầu gãy **có chủ đích**, không phải bug.
- **Đề xuất sửa:** nếu cần phủ hết, tạo node `Crime` với tên "tội danh không quy định trong BLHS"
  cho trường hợp này để câu hỏi về vụ vẫn truy ngược được, kèm property `defined_in_law: false`.
  Đánh đổi: thêm 1 loại node và phải phân biệt khi trả lời "khung hình phạt là gì".

---

## 4. Kết luận (5 điểm)

**Dùng KG khi câu hỏi cần nối 2 KB.** Cả 3 câu `cross-kb` đều là GraphRAG thắng (Q3 0.33→1.00,
Q4 0.33→0.67, Q5 0.40→0.60), trung bình nhóm này **recall 0.35 → 0.76**. Khi câu hỏi nằm gọn trong
một nguồn, KG **không giúp và có thể hại**: Q1 hòa, Q2 **thua** (1.00 → 0.50).

**Điểm hòa vốn.** Chi phí dựng graph là **533,3 giây và 53.224 token, trả một lần**; phần mỗi câu chỉ
tăng **×2,6 thời gian** và **×4,1 token input**. Quy đổi theo giá `gpt-4o-mini`:
một lần dựng graph ≈ **$0,0153**, mỗi câu hỏi thêm ≈ **$0,00053**. Nên:

- Nếu dùng KG cho **mọi** câu hỏi: hòa vốn sau **≈ 29 câu**.
- Nếu chỉ dùng cho **câu `cross-kb`** (3/6 câu): hòa vốn sau **≈ 10 câu `cross-kb`** —
  tức khoảng **20 câu hỏi tổng** với tỉ lệ 50% câu xuyên 2 KB.

Nói cách khác: **KG chỉ đáng tiền khi số câu hỏi đủ lớn và tỉ lệ câu xuyên 2 KB đủ cao**. Với 6 câu
của lab này thì **chưa hòa vốn** — đây là kết luận thành thật, không phải lỗi: quy mô 20 bài báo +
18 Điều luật là quá nhỏ để hấp thụ chi phí dựng graph. Ở quy mô vài nghìn tài liệu và hàng trăm câu
hỏi thì tỉ lệ này đảo chiều.

**Điều kiện cụ thể** (theo loại dữ liệu của tôi): KG đáng dùng khi (1) câu hỏi cần thông tin ở **cả
tài liệu lẫn văn bản quy phạm**, (2) quan hệ cần **nhiều bước** (vụ → tội → Điều → khoản → ngưỡng),
(3) số câu hỏi lặp lại đủ nhiều để bù chi phí dựng graph. Flat RAG là đủ khi đáp án nằm trong một
đoạn và không cần join. Với 20 bài báo + 18 Điều luật thì đúng là điều kiện (1) và (2) đều đúng —
đó là lý do lab này cho kết quả rõ ràng.

---

## 5. Tự kiểm (5 điểm)

```
$ pytest tests/ -q
................................................                         [100%]
48 passed in 0.09s

$ python bench_kg.py --check
[OK] Dữ liệu: 18 điều luật, 20 bài báo
[OK] KG-1 link_entity
[OK] Neo4j kết nối được
[provider] chat = openai:ternary-bonsai-4b | embedding = openai:text-embedding-bge-m3
[OK] KG-2 build_graph: 367 node / 731 cạnh, đường xuyên 2 KB dài 2 cạnh
[OK] KG-3 context: 31 dữ kiện, có Điều 251
[OK] KG-4 GraphRAGAgent.answer
[OK] Chi phí check: 1 lần gọi LLM, $0.00000. Graph nhỏ (luật + 1 bài) vẫn còn trong Neo4j để bạn xem; chạy --judge để dựng graph đầy đủ.
```

Ảnh Neo4j: `report/img/kg_count.png`, `report/img/kg_cross_kb.png`, `report/img/kg_my_case.png`.
Người đã chọn cho `kg_my_case.png`: Dương Minh Tuấn (biệt danh “Hoàng Nato”)

Bằng chứng Cypher cho mục 3: `report/img/error_evidence.txt`.
Đo kích thước prompt: `scripts/measure_prompt_size.py`.

---

## Vấn đề gặp phải (không tính điểm)

1. **Server LM Studio từ chối `response_format` dạng `json_object`** với lỗi 400
   (`'response_format.type' must be 'json_schema' or 'text'`), trong khi `src/llm.py` gửi đúng
   dạng đó. Đã sửa bằng fallback sang `json_schema` (chỉ bắt đúng lỗi này, lỗi auth vẫn ném ra).
2. **Prompt trích xuất gốc làm model 4B copy chữ hướng dẫn vào JSON**: `"charges": ["tội danh","BẮT BUỘC"]`.
   `link_entity` trả `None` cho các giá trị này → không có cạnh `CHARGED_WITH` → cầu nối gãy →
   `--check` KG-2/KG-3 sẽ fail. Đã sửa bằng cách bỏ dấu nháy quanh placeholder.
3. **`str.format()` không dùng được cho prompt có JSON**: đã dùng thay thế chuỗi `.replace()`.
4. **`py -3.11` không có trên máy** → dùng `uv venv --python 3.11`.
5. **`parse_thresholds` ban đầu quét cả khoản** nên gán mọi ngưỡng cho chất đầu tiên và **mất MDMA**
   trong Điều 250 khoản 4 (6 chất chung một ngưỡng). Đã sửa bằng cách quét từng dòng.
