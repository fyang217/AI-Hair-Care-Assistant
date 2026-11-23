# AI Hair Care Assistant

A local Streamlit prototype for comparing hair-care products using traceable product, ingredient, price, and review evidence. It is designed for Chinese-language queries such as “头皮容易出油，细软塌，预算100元以内”. The project explores how a recommendation interface can show what the catalog supports and where a user still needs to verify a claim.

**[View the AI product management portfolio (PDF, Chinese)](docs/ai-hair-care-assistant-portfolio.pdf)** · [Run locally](#run-locally)

The case study covers the user problem, MVP trade-offs, the evidence workflow, and evaluation results.

![Streamlit demo showing a user query, a ranked product candidate, and its source link](docs/streamlit-demo.png)

The local demo shows a candidate, its review summary, and a link to the source page.

## Implemented in v1.2

- **Local recommendation and comparison.** Parses concerns, ingredient preferences, and budget from a query. Python rules retrieve and rank candidates, flag ingredient conflicts, and require a verified RMB quote for the same product and specification before marking a budget match.
- **Evidence inspection.** Displays ingredient and product records with available source references, summarizes the available review samples, and lets users inspect evidence used in a recommendation. Feedback is stored in a separate local SQLite database.
- **Optional DeepSeek analysis.** Uses retrieved evidence to generate an explanation and validates individual claims against source excerpts and product IDs. Local results remain available if model calls or validation fail.
- **Optional web evidence.** When enabled, the assistant checks local coverage and can use one Search call and up to two Fetch calls per request. It surfaces the source, tool timing, and available usage information. External evidence is scoped to the request and session.

## Architecture and stack

**AI Agent · LLM · RAG · Tool Calling · DeepSeek API · Search / Fetch · Streamlit**

The recommendation pipeline combines **rule-based retrieval + evidence-grounded generation**. Python retrieves and ranks candidates from SQLite. When enabled, DeepSeek generates explanations from the retrieved evidence, and the application checks each cited claim against its product and source excerpt. This is a bounded retrieval-augmented generation (RAG) workflow. For tool calling, the model proposes a search action and the Python controller validates and executes the allowed DeepSeek Search / Fetch requests.

```text
Streamlit UI / CLI -> Python controller -> SQLite catalog + deterministic rules
                             |
                             +-> optional DeepSeek parsing and evidence analysis
                             +-> optional bounded Search / Fetch -> claim validation
```

| Component | Implementation |
| --- | --- |
| Interface | Streamlit and a Python CLI |
| Application logic | Python 3.12, rule-based retrieval and ranking, claim-level validation |
| Storage and ingestion | SQLite, openpyxl for Excel ingestion |
| Optional online services | DeepSeek API, python-dotenv for local configuration |
| Validation | unittest, Streamlit AppTest, synthetic fixtures, opt-in live tests |

`app.py` and `cli.py` call the controller in `agent.py`. `storage.py` reads the catalog and imports compatible workbooks. `rag.py` assembles evidence and validates model claims. `deepseek.py`, `search_pilot.py`, and `web_tools.py` handle optional online calls and source selection. `render_search_pilot.py` is a historical evaluation tool that requires archived pilot captures and annotations, which are kept outside this repository.

## Run locally

From the repository root in PowerShell, with Python 3.12 installed:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py
```

Open the local URL shown in the terminal, typically `http://127.0.0.1:8501`. The application starts with the included SQLite snapshot and requires no API credentials for the local flow. Try the CLI and run the regression suite with:

```powershell
.\.venv\Scripts\python.exe cli.py "头皮容易出油，细软塌，预算100元以内"
.\.venv\Scripts\python.exe -m unittest discover -v
```

For optional online analysis, copy `.env.example` to `.env`, set your own `DEEPSEEK_API_KEY`, restart the app, and enable the LLM option. The web-search option is separate. Online calls send the query and relevant evidence to the configured provider, may incur charges, and depend on API access. Live tests are skipped by default.

## Data and limitations

The snapshot covers 17 products and contains 293 structured records, including collection logs, candidate and archive records, and source metadata for 10 review samples covering seven products. The reviews are sparse and include display samples and incentivized overseas samples. This release retains counts, source links, and derived labels. Review text is omitted because redistribution permission could not be confirmed, so the bundled database provides no review quotations for the model. Synthetic review text remains in test fixtures.

Prices are point-in-time observations. The snapshot currently has no verified same-specification RMB quotes that satisfy the strict budget rule, so budget results require a price check. Ingredient disclosures and brand claims are tied to the recorded source and market where available. Product fit and efficacy remain unverified, and the prototype is for shopping research.

The included catalog is a fixed evaluation snapshot. The repository contains the runtime database, test fixtures, and selected design and evaluation documents. The original Excel inputs are kept locally.

## Roadmap

- Expand from the current 17 products toward a planned 200 in stages, with more matched review metadata and verified quotes.
- Improve SKU and market-version matching and evaluate retrieval on a wider set of queries.
- Run a larger evaluation of recommendation relevance, evidence coverage, latency, and cost before considering a hosted release.

See [architecture](ARCHITECTURE.md), [product requirements](PRD.md), and [evaluation notes](EVALUATION.md) for implementation and validation details.
