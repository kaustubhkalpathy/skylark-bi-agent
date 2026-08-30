# Skylark Drones — monday.com Business Intelligence Agent

A conversational AI agent that answers founder- and executive-level business
questions by reading **live data** from two monday.com boards — **Deals** (sales
pipeline) and **Work Orders** (project execution) — cleaning the real-world messy
data on the fly, and returning insights with clear data-quality caveats.

> Example: *"How's our pipeline looking for the Renewables sector this quarter?"*
> → the agent fetches both boards from monday.com, normalizes them, computes the
> answer, and explains the number (including what was excluded and why).

---

## 1. Architecture overview

```
                +---------------------------+
   User  <----> |  Streamlit chat UI        |   streamlit_app.py
                |  (hosted, no local setup) |
                +------------+--------------+
                             |
                             v
                +---------------------------+
                |  BIAgent (Gemini)         |   bi_agent/agent.py
                |  tool-calling loop        |
                +------------+--------------+
                             |  calls Python tools
                             v
        +--------------------+---------------------+
        |          analytics.py (BI logic)         |
        |  pipeline_summary / revenue_by_sector /  |
        |  sector_performance / operational_metrics|
        |  / leadership_update / list_dimensions   |
        +--------------------+---------------------+
                             |  reads cleaned DataFrames
                             v
                +---------------------------+
                |  DataStore                |   bi_agent/data_store.py
                |  (fetch + clean + cache)  |
                +------+-------------+------+
                       |             |
                       v             v
        +----------------+   +--------------------+
        | monday_client  |   |  normalize.py      |
        | GraphQL, R/O   |   |  pandas cleaning   |
        +--------+-------+   +--------------------+
                 |
                 v
        monday.com API (Deals + Work Orders boards)
```

**Flow:** the user asks a question → Gemini decides which tool(s) to call →
tools read cleaned pandas DataFrames → the DataStore fetched those from
monday.com (with a short TTL cache) and ran the normalization layer → Gemini
composes an executive-friendly answer with caveats.

### Key design choices
- **Gemini automatic function calling.** We register plain Python functions as
  tools; Gemini picks and calls them and we return JSON. This keeps the "brain"
  (LLM) and the "math" (deterministic pandas) cleanly separated — numbers are
  never hallucinated, they always come from a tool.
- **monday.com is queried dynamically** via its GraphQL API (read-only). CSV data
  is **never** hardcoded, per the assignment requirement.
- **Normalization is transparent.** The cleaning layer emits human-readable
  caveats (e.g. "12 deals have no value; revenue excludes them") that the agent
  surfaces so figures are trustworthy.

---

## 2. Tech stack (and why)

| Layer      | Choice                        | Why |
|------------|-------------------------------|-----|
| LLM        | Google Gemini (`gemini-3.6-flash`) | Free tier, fast, first-class tool-calling, zero billing risk under a tight deadline. The app auto-falls-back to another available model if this one is retired. |
| Agent SDK  | `google-generativeai` (automatic function calling) | Minimal glue; the SDK runs the tool loop. Easy to explain. |
| Backend    | Python + pandas               | pandas is the right tool for messy tabular cleaning and aggregation. |
| Integration| monday.com GraphQL API v2 (read-only) | Required; cursor pagination via `items_page` + `next_items_page`. |
| UI / host  | Streamlit + Streamlit Community Cloud | One file for a chat UI; free public hosting, testable with no local setup. |

See `DECISION_LOG.md` for trade-offs and alternatives considered.

---

## 3. Project structure

```
app/
  streamlit_app.py          # chat UI (entry point)
  requirements.txt
  .env.example              # copy to .env and fill in
  bi_agent/
    config.py               # env / secrets loading
    monday_client.py        # read-only monday.com GraphQL client (paginated)
    normalize.py            # pandas cleaning + data-quality caveats
    data_store.py           # fetch -> clean -> cache bridge
    analytics.py            # deterministic BI computations (tool bodies)
    agent.py                # Gemini tool-calling agent
```

---

## 4. monday.com setup

1. Create a free monday.com account.
2. Create a board named **Deals** and import `Deal funnel Data.xlsx`
   (board menu → **Import** → **Excel**).
3. Create a board named **Work Orders** and import `Work_Order_Tracker Data.xlsx`
   the same way.
4. Get a **personal API token**: avatar (bottom-left) → **Administration** →
   **Connections** → **API**, or **Developers** → **My access tokens**. Copy it.
5. Get each **board ID** from its URL:
   `https://<account>.monday.com/boards/<BOARD_ID>`.

> The importer may create extra/typed columns. The cleaning layer maps columns by
> a fuzzy match on their titles, so exact column types don't matter.

---

## 5. Configuration

Copy `.env.example` to `.env` and fill in:

```
GEMINI_API_KEY=...              # from https://aistudio.google.com/apikey
GEMINI_MODEL=gemini-3.6-flash
MONDAY_API_TOKEN=...            # monday personal API token
MONDAY_DEALS_BOARD_ID=...       # number from the Deals board URL
MONDAY_WORK_ORDERS_BOARD_ID=... # number from the Work Orders board URL
DATA_CACHE_TTL=300
```

`.env` is git-ignored. For deployment, provide the same keys as host secrets.

---

## 6. Run locally

```bash
cd app
pip install -r requirements.txt
streamlit run streamlit_app.py
```

Open http://localhost:8501 and try:
- "Give me a leadership update."
- "How's our pipeline looking for Renewables this quarter?"
- "Which sectors are driving the most won revenue?"
- "What's our win rate by sector?"
- "How healthy are collections on our work orders?"

---

## 7. Deploy (Streamlit Community Cloud)

1. Push this repo to GitHub.
2. Go to https://share.streamlit.io → **New app** → pick the repo.
3. Set **Main file path** to `app/streamlit_app.py`.
4. In **Advanced settings → Secrets**, paste the same keys as in `.env`
   (TOML format, e.g. `GEMINI_API_KEY = "..."`).
5. Deploy. You get a public URL that needs no local setup.

---

## 8. Data resilience (what the cleaning layer handles)

- Missing / null values across every column, kept as rows (never silently dropped).
- **Embedded header rows** inside the Deals data (removed).
- **Exact duplicate rows** (removed, with a count reported).
- Inconsistent **date** formats parsed to timestamps.
- Inconsistent **sector** spellings normalized (e.g. "renewables"/"Renewable" → "Renewables").
- Inconsistent **recurring-project months** ("Dec", "November") normalized.
- Messy **currency** strings parsed to numbers (commas / symbols stripped).
- Typos like **"BIlled"** normalized in billing status.
- All of the above surfaced to the user as **data-quality caveats**.

---

## 9. AI tools used

Built with the help of AI coding assistants (see `DECISION_LOG.md` for details).
All architecture and technical decisions are explained there and can be walked
through in an interview.

## 10. Limitations & future work

See `DECISION_LOG.md` §"What I'd do differently with more time".
