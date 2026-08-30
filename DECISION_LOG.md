# Decision Log — monday.com Business Intelligence Agent

## 1. Key assumptions

- **Two boards, mapped by column title.** I assumed the Deals and Work Orders
  spreadsheets are imported as separate monday.com boards. The cleaning layer
  maps columns by a fuzzy match on their human-readable titles rather than
  monday's internal column IDs, so the app tolerates whatever column types the
  importer chooses.
- **Currency is INR and values are masked.** The amount columns are labelled
  "Masked", so figures are treated as *indicative/relative*, not exact rupees.
  The agent frames them that way.
- **Fiscal year is April–March (Indian standard).** Quarter filters like
  "Q3 FY26" are interpreted on an Apr–Mar fiscal calendar (FY26 = Apr 2025 –
  Mar 2026). This is an assumption; it is stated to the user in the answer.
- **"This quarter" / pipeline timing uses the tentative close date.** Open
  pipeline is filtered by *expected close date* when a quarter is specified;
  deals with no close date are excluded and the exclusion is reported.
- **Deal status semantics.** `Open`/`On Hold` = live pipeline; `Won` = closed-won;
  `Dead` = lost. Weighted pipeline uses probability (High 0.75 / Medium 0.5 /
  Low 0.25) — a reasonable default, not a company-provided figure.
- **Read-only integration.** Per the brief, the agent only reads from monday.com;
  it never writes.

## 2. Trade-offs chosen (and why)

- **Gemini over AWS Bedrock/Nova.** I have prior experience building tool-calling
  agents on AWS Bedrock (Nova Pro) during an internship, and considered using it.
  Under a ~6-hour deadline I chose **Gemini** for its free tier, zero billing/IAM
  friction, and first-class automatic function calling. The agent *pattern* is
  identical to what I'd build on Bedrock, so the design ports directly — I can
  walk through both. This was a deliberate speed-vs-familiarity trade-off in
  favour of a reliable, demonstrable result.
- **Deterministic tools instead of "let the LLM do the math".** All numbers come
  from pandas functions exposed as tools. The LLM only decides *which* tool to
  call and how to phrase the result. This trades a little flexibility for
  correctness and explainability — the figures are auditable and never
  hallucinated.
- **Streamlit over a React + FastAPI split.** A single-file Streamlit app gives a
  working conversational UI and free public hosting fastest. The trade-off is a
  less custom UI and a server-rendered model, which is acceptable for a prototype
  whose value is the agent, not the front end.
- **Clean-on-read with a short TTL cache** rather than a persisted warehouse.
  Simpler, always fresh, satisfies "query monday.com dynamically". The trade-off
  is recomputation cost, mitigated by a 5-minute cache.
- **Normalize-and-flag, never drop silently.** Rows with problems are kept and
  the issues are surfaced as caveats. This trades tidier tables for honesty about
  data quality — which the brief explicitly rewards.

## 3. How I interpreted "leadership updates"

I interpreted *"help prepare data for leadership updates"* as producing a concise
**executive briefing** a founder could paste into a weekly review, combining both
boards into one snapshot:

- **Pipeline** — open deals, total and probability-weighted pipeline value, and a
  stage breakdown.
- **Won revenue by sector** — where closed business is coming from.
- **Sector performance** — win/loss/open counts and win-rate per sector.
- **Operations & collections** — work-order execution status, billed vs
  collected vs receivable.

This is exposed as a single `leadership_update` tool and triggered by asking the
agent "give me a leadership update". The agent formats it into short, labelled
sections rather than dumping raw numbers, and includes data-quality caveats so
leadership knows how much to trust each figure.

## 4. What I'd do differently with more time

- **Richer query understanding.** Add explicit handling for owner-level and
  client-level questions, time-series/trend queries ("how has pipeline changed
  month over month"), and fuzzy sector matching (e.g. "energy" → Renewables +
  Powerline) with a confirmation step.
- **Cross-board joins.** Link Deals ↔ Work Orders on deal name / serial to answer
  questions like "of the deals we won, how many are actually being executed and
  billed?" A join key exists but needs careful fuzzy matching given the messy names.
- **Charts.** Return small Plotly charts (pipeline funnel, revenue-by-sector bar)
  alongside the text for a more leadership-ready output.
- **Evaluation harness.** A set of golden question→answer pairs to regression-test
  the agent's accuracy as prompts/tools change.
- **Production hardening.** Retry/backoff on monday rate limits, structured
  logging, and a proper FastAPI backend + React UI if this graduated past a prototype.
- **Confidence on masked values.** Clarify the masking scheme with stakeholders so
  totals can be presented with appropriate confidence and units.

## 5. AI tools used

- Used an AI coding assistant to scaffold the FastAPI/agent structure, draft the
  monday.com GraphQL client and pandas cleaning rules, and generate boilerplate
  and docs.
- I directed the architecture (separation of LLM reasoning from deterministic
  computation, the normalize-and-flag approach, the leadership-update
  interpretation), reviewed and corrected the generated code (notably the
  monday cursor-pagination pattern and the fiscal-quarter maths), and made the
  tech-stack decisions. I can explain every component and the reasoning behind it.

## 6. Challenges faced

- **Real-world messy data:** repeated header rows embedded mid-file, duplicate
  rows, blank deal values, inconsistent sector/status spellings, mixed date and
  month formats, and typos ("BIlled"). Handled in the normalization layer with
  visible caveats.
- **monday.com pagination:** the correct pattern is a first `items_page` call
  followed by top-level `next_items_page(cursor)` calls — not re-querying
  `items_page`. Corrected during review.
- **Masked/relative values:** figures are indicative, so the agent is careful to
  frame them as such rather than implying precise rupee amounts.
