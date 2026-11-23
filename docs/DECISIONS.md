# Decision Log

> One entry per decision. Format: **Decision · Options · Why · Tradeoff**. Feeds deck slide 13.
> Add to this whenever a choice is made that isn't already in `ARCHITECTURE.md`.

---

### D1 · Hybrid framing: deterministic core + bounded AI
- **Options:** pure rate-ingestion tool; chat copilot over data; one autonomous agent; hybrid.
- **Why:** The hard requirements (exact money, audit) need code; the messy inputs (rate notices) and narrative outputs (explanations) need AI. Hybrid puts each where it's strongest.
- **Tradeoff:** More components than a single agent; mitigated by strict module boundaries.

### D2 · The LLM never computes or writes money
- **Options:** LLM computes deltas; LLM suggests, code verifies; code computes, LLM explains.
- **Why:** Code computes, LLM explains. Removes a whole class of errors and makes audit trivial.
- **Tradeoff:** LLM can't "discover" novel math; that's intentional.
- **Enforced by:** import-boundary test (`engine/` cannot import `llm/`); memo placeholders filled by code.

### D3 · Bitemporal rates (effective time + knowledge time)
- **Options:** overwrite rates in place; version table with effective dates only; bitemporal.
- **Why:** Retro changes require answering "what did we believe at close?" and "what do we believe now?". Bitemporal answers both with one function.
- **Tradeoff:** Slightly more complex queries; worth it because the same mechanism also handles retro enrollment.

### D4 · Knowledge time = `approved_at`, not `published_date`
- **Why:** We can't book revenue on a rate we haven't approved. Ties knowledge to governance.
- **Tradeoff:** Delay between publication and approval shows up as a temporary variance (which is true).

### D5 · Freeze close snapshots instead of recomputing "what was booked"
- **Options:** recompute original with old `as_of`; store immutable snapshot at close.
- **Why:** The ledger is the source of truth for what was booked. A snapshot is provable even if pricing code later changes.
- **Tradeoff:** Extra storage (trivial).
- **Revised by D18:** the snapshot is the baseline, but retro deltas are measured against snapshot + adjustments already approved.

### D6 · Rules first, LLM only for ambiguous / multi-cause
- **Options:** LLM classifies everything; rules only; rules then LLM.
- **Why:** Most variances are obvious; rules are free, instant, and exact. LLM adds value on the ambiguous tail. Evals compare both.
- **Tradeoff:** Rules need maintenance; knowledge store helps rules evolve.
- **Revised by D20:** rules only propose a *possible* cause, rated strong/weak instead of a made-up confidence number.

### D7 · Group similar variances before investigating
- **Why:** 200 identical STALE_RATE rows are one finding to a human. Cost scales with patterns, not rows.
- **Tradeoff:** A grouping key that's too coarse could hide an outlier; key includes delta amount to avoid that.

### D8 · Evidence pack over free-roaming tool agent (tool loop as bounded escape hatch)
- **Options:** fully agentic tool loop; code-assembled evidence + one call; hybrid.
- **Why:** Reproducible, cacheable, cheaper, easier to eval. Bounded loop (max 3 calls) available for multi-cause.
- **Tradeoff:** Agent can't explore beyond the pack by default; if evidence is missing, it must say `escalate_to_human`.

### D9 · Reject invalid LLM output, never repair silently
- **Why:** Silent repair hides model errors from evals and auditors. One retry with the validation error, then route to a human.
- **Tradeoff:** Occasional extra human work.

### D10 · Groundedness check on every explanation and memo
- **Why:** Evidence IDs must exist; every dollar figure must match a computed value. Turns "trust the AI" into "verify the AI".
- **Tradeoff:** Strict matching may flag correct-but-rounded prose; prompt asks for exact amounts.

### D11 · Maker-checker approvals + hash-chained audit log
- **Why:** Mirrors finance controls (SOX-style); tamper-evidence is cheap to add.
- **Tradeoff:** Demo needs a user switcher to show two roles.

### D12 · DuckDB + Streamlit + no orchestration framework
- **Options:** Postgres, SQLite; React; Airflow/LangGraph.
- **Why:** Zero setup, fast analytical joins, readable. A 9-step sequential close doesn't need a framework. Production maps to Azure SQL/Fabric + Durable Functions.
- **Tradeoff:** Not multi-user; acceptable for a prototype.

### D13 · Provider abstraction + disk cache keyed by prompt/model/schema/prompt-version
- **Why:** Offline demo, deterministic evals (`--replay`), cost control, easy switch to Azure OpenAI.
- **Tradeoff:** Cache must be invalidated on prompt change — handled by including prompt version in the key.

### D14 · Model tiering by task
- **Why:** Extraction: mid-tier (low volume, high accuracy need). Single-cause confirmation: small model. Multi-cause investigation: strong model. All configurable in `config.yaml`.

### D15 · First-of-month proration as a pluggable strategy
- **Why:** Common Medicaid convention; isolating it lets a state with daily proration be supported without touching the pricer.

### D16 · PHI minimisation is structural, not a filter
- **Why:** Tool return types simply don't include names/DOB, so they can't reach a prompt. A minimiser also asserts on forbidden keys as defence in depth.
- **Expanded by D24–D26.**

### D24 · Treat synthetic data as real PHI: privacy by design
- **Options:** note privacy as a production concern only; build real controls into the prototype.
- **Why:** The system is presented as a real solution. Controls that run and are tested (classification, gateway, masking, privacy eval) are far more convincing than a slide, and they cost little to build now versus retrofitting later.
- **Tradeoff:** About 1–2 extra hours of build; slightly more indirection on the LLM path.

### D25 · Pseudonymise member IDs before any LLM call
- **Options:** send raw member_id; drop IDs entirely; send a keyed HMAC token.
- **Why:** member_id is a health plan beneficiary number, one of HIPAA's 18 identifiers. A stable HMAC token keeps the LLM's evidence references usable (code maps tokens back) while the raw ID never leaves.
- **Tradeoff:** We still don't claim Safe Harbor de-identification (months and regions remain), so production still needs a BAA. Stated openly.

### D26 · Privacy gateway: allow-list, scan, block, log
- **Why:** Structural exclusion (D16) is the first line; the gateway is defence in depth. It scans the final prompt for names, DOB patterns and raw IDs and blocks the call on a hit. An egress log records exactly what left the system (tokenised).
- **Tradeoff:** A false positive blocks a call; it fails safe and shows up in the review queue.
- **Measured by:** the privacy eval, which scans the cache, logs, audit payloads and exports and must report 0 leaks.

---

*Entries D17–D21 came from a design review of v1 of the plan. Each fixes a correctness gap.*

### D17 · Rate precedence is per cell and effective interval, not per table
- **Problem found:** v1 merged an amendment onto the latest full table and let the latest approval win per month. A January-effective amendment merged onto v2 would carry v2's April rates back into Jan–Mar for cells the amendment never mentioned.
- **Options:** merged full tables per version; cell-level rows with their own intervals.
- **Why:** An amendment only changes what it says it changes. Resolving each cell independently makes that structurally true.
- **Tradeoff:** Pricing resolves 16 cells separately instead of picking one table; negligible cost. Proven by the West Child precedence test.

### D18 · Retro deltas are incremental: new expected − previously booked
- **Problem found:** v1 computed `repriced − original close snapshot`. After a first revision (+$80) was posted, a second revision would propose the cumulative $120 again instead of $40.
- **Why:** `previously_booked = close snapshot + approved adjustments` means each revision books only its own increment. The snapshot remains the untouched historical baseline.
- **Tradeoff:** Retro must read prior adjustments; trivial. Proven by the +$80 then +$40 test.
- **Revised by D22:** "prior adjustments" means every non-rejected one, including those still pending approval.
- **Also:** state retro payments are matched to adjustment lines, so each revision shows owed / paid / outstanding (e.g. $80 / $20 / $60).

### D19 · Bank matching auto-links only on a unique explicit reference
- **Problem found:** v1's amount-plus-date fallback could clear the wrong deposit when two share an amount and window, and v1 labelled the $20 shortfall as a "fee" with no evidence.
- **Why:** Wrongly clearing cash is worse than asking a human. Amount/date matches become review candidates. A shortfall is "cause unknown" unless a source record explains it.
- **Tradeoff:** More items in the review queue when references are missing.

### D20 · Three-level cause model: observed → possible → confirmed
- **Problem found:** v1 rules jumped from weak signals (two lines, amount equals an old rate, a negative line) to a cause, with invented confidence values.
- **Why:** Code reports what it observed; rules or the LLM propose a possible cause with corroborating fields; only a human confirms. Matches how auditors think about evidence.
- **Tradeoff:** One more status to show in the UI.
- **Related:** the extractor's source cross-check is described as necessary, not sufficient. A number appearing in the PDF doesn't prove it belongs to that cell.

### D22 · Pending adjustments count as booked; approve and post are atomic
- **Problem found (review 2):** D18 counted only *approved* adjustments. If v3's +$80 was still pending when v4 arrived, v4 would propose +$120.
- **Options:** track three separate balances (entitlement / unposted / settled); count every non-rejected adjustment and remove the approved-but-unposted state.
- **Why:** The second is enough to prevent double-counting and is far simpler. Approval writes the ledger in the same transaction, with a unique `posting_key`, so retries can't post twice.
- **Tradeoff:** A rejected adjustment's member-months wait for the next retro run to be re-proposed.

### D23 · Historical lookups ignore current status
- **Problem found (review 2):** pricing filtered on `status = approved`, so once v1 was marked `superseded`, pricing "as of February" couldn't find it.
- **Why:** A version's truth at a past date doesn't change when it's later superseded. Lookups use `approved_at <= as_of` (excluding draft/rejected); `superseded` is display-only. Missing rates raise `UNPRICED` instead of returning $0.
- **Tradeoff:** None meaningful.

### D27 · (Phase 0) LLM returns money as strings; Pydantic is the gate, not the API schema
- **Why:** JSON numbers are floats on most parsers, so `pmpm` comes back as a `"420.00"` string (regex-checked) and code converts it to `Decimal`. The JSON schema sent to the API is a shape hint with unsupported constraints stripped; our Pydantic models re-validate everything, with `extra="forbid"`.
- **Tradeoff:** The model can produce output the API schema allows but Pydantic rejects; that triggers the one retry, then a human.

### D28 · (Phase 0) Model per task, refusal fallbacks on
- **Choice:** `claude-opus-5-5` for rate extraction, complex investigations and memos; `claude-haiku-5-5` for simple single-cause investigations (the brief's cost tiering). All in `config.yaml`.
- **Refusal fallbacks:** Opus-tier calls use the server-side fallback (`fallbacks: "default"`), so a rare policy decline is retried on a fallback model in the same call instead of failing. If the whole chain declines, the item goes to a human. Can be turned off with `llm.refusal_fallbacks: false`.
- **Tradeoff:** Two models means two cache namespaces; acceptable for the cost saving on high-volume simple cases.

### D29 · (Phase 0) There is no free-text LLM entry point
- **Why:** `LLMClient.call` only accepts a structured payload, which always passes the privacy gateway. No code path can send an arbitrary string to a model.
- **Tradeoff:** Slightly more ceremony when writing a new agent.

### D30 · (Phase 1) The generator builds its truth separately from the engine's logic
- **Why:** The generator defines each member's coverage segments directly and derives the truth calendar with a plain first-of-month check, with no knowledge-time logic. Rates use a simple loop over versions. The engine must rebuild everything from the raw files (spans with received dates, rate documents), so evals compare two independent paths.
- **Tradeoff:** Rate resolution can't be fully independent (both apply "latest approval wins per cell"); the hand-written edge-case tests (D21) cover that rule separately.

### D31 · (Phase 1) Realistic causes, not random noise
- **Why:** Most seeded errors have a business reason a finance analyst would recognise: the state lags a category change when a child turns 19 or a region move; the state keeps paying the old East Child rate after the April change; post-termination payments, half of them recouped the next month. This makes the investigator's explanations checkable and the demo believable.
- **Overlap cases:** 19 member-months have two causes (e.g. a duplicate of a stale-rate line; an unexpected payment later recouped).

### D32 · (Phase 1) The retro amendment tests both precedence and partial settlement
- **v3** raises North Adult to $420 from Jan 1 (open-ended) and corrects South Disabled for **Jan–Mar only**, because v2 already set a new South Disabled rate from April. The extractor must capture that end date; pricing must not let v3 override v2's April rate.
- **The state pays the v3 retro difference in June for Jan–Mar only**, so April North Adult stays outstanding ($20 per member). This is the "owed / paid / outstanding" demo.

### D33 · (Phase 1) Retro enrollment = late-arriving coverage only; retro terminations parked
- **Why:** A late span that *adds* coverage fits the pricing rule (latest received span wins). A late *termination* needs a rule for a newer record that shortens an older span. Parked as an edge case to discuss later, per the scope agreement.
- **Tradeoff:** The prototype shows retro enrollment increasing revenue but not reducing it.

### D34 · (Phase 1) A fictional state and fictional people
- **Why:** The rate documents come from a fictional "Westlake Department of Health Services" (email domain `.example`), so nothing imitates a real agency. Names are invented and chosen not to clash with domain words (regions, categories, months), so the privacy scanner has no false positives.

### D35 · (Phase 2) "Paid before the amendment" is not a stale rate
- **Problem found while building:** after v3, April North Adult shows paid $400 vs expected $420. A naive rule calls that STALE_RATE (the state used an old rate). But the state paid on Apr 25 and v3 was approved May 22: it *couldn't* have known.
- **Rule:** if the paid amount equals an older version's rate AND the current rate was approved after the payment date, the possible cause is RETRO_RATE_CHANGE (retro difference not yet settled), not STALE_RATE.
- **Why it matters:** the recommended action differs (wait for or chase the retro payment vs. dispute the rate).

### D36 · (Phase 2) A retro run picks up every knowledge change since the last run
- **Why:** The retro engine reprices closed months with *all* current knowledge, so one run may include late enrollment *and* a new rate. Example from the simulation: spans received May 15 were caught by the v3 retro run on May 22. Totals are exact either way; each adjustment line records old/new version so the real cause stays traceable.
- **Tradeoff:** An adjustment's trigger label names the event that started the run, not every cause inside it.

### D37 · (Phase 2) Ground-truth rates stand in for extraction when verifying the engine
- **Why:** To prove the engine on its own, `workflow/simulate.py` loads the true rate tables as drafts and approves them on the timeline. Phase 3 replaces this with extracted rates; the same comparison then measures the extractor.

### D38 · (Phase 2) The close posts revenue; retro posts prior-period adjustments
- **Why:** The ledger then tells the whole story: original monthly revenue (Dr Capitation Receivable / Cr Capitation Revenue) plus each approved prior-period adjustment tagged to its original month. Account names are illustrative.

### D39 · (Phase 3) Explain patterns, not rows
- **Result:** the year's 462 variances become 140 investigation calls; the 170 East Child stale-rate rows are one finding. Grouping key = rule hits + our cell + state cell + month + delta + observed type, so an outlier amount gets its own group. 14 multi-cause groups go to the stronger model, the rest to the cheaper one.
- **Tradeoff:** one explanation is applied to every member in a group; the group's member list is shown with it.

### D40 · (Phase 3) Date-of-birth scanning is scoped to the payload's members
- **Problem found in the first end-to-end run:** the scanner blocked any date equal to *any* member's DOB. With 2,000 members, children's birthdays collide with ordinary enrollment dates, so legitimate calls were blocked (fail-safe, but useless).
- **Fix:** names are still checked population-wide; DOBs only for the members the payload is about (found before pseudonymising). Structural exclusion remains the first line of defence.
- **Tradeoff:** a different member's DOB appearing as a plain date in someone else's evidence isn't flagged; it carries no identity link in that context.

### D41 · (Phase 3) References are placeholders too
- **Problem found:** the memo check correctly rejected memos that typed "v3" or "2026-04" (they contain digits). Version and event references now come in as `{trigger_ref}`, in the title as well as the body. The rule "the model never types a number" stays strict.

### D42 · (Phase 3) Mock is a stand-in and costs $0
- **Why:** mock responders (a heuristic parser for the three documents; an investigator that echoes rule hits) prove the plumbing: gateway, schemas, validation, groundedness, cache. They say nothing about AI quality and are reported at $0 so they can't be mistaken for real spend. Real accuracy and cost come from the live run, then replay.

### D43 · (Phase 3) A draft with validation errors cannot be approved
- **Why:** errors (rate not found at cited source, missing cells, mid-month dates) block approval in `governance.approvals`; flags (big moves, open-ended amendment over a later change, low confidence) only warn. Re-ingesting the same file is detected by its SHA-256.

### D44 · (Phase 4) The provider is part of the LLM cache key
- **Bug found while building replay:** the key was model + prompt + schema. A mock answer cached during development would have been served to the live Anthropic run, silently making "real" results mock results.
- **Fix:** provider name is in the key; a test proves a mock cache entry is a miss for the live provider.

### D45 · (Phase 4) Deterministic adjustment IDs
- **Why:** adjustment IDs appear in memo prompts. Random IDs meant every run had new prompts, so replay could never hit the cache. IDs are now a hash of trigger + reference + time.

### D46 · (Phase 4) How classification is scored, and what it found
- **Scoring:** each flagged variance's predicted cause set must equal the ground-truth set exactly. Rules-only counts a case as resolved only when exactly one strong rule fires; everything else is "unresolved". Rules+LLM uses the rule answer when it resolved, otherwise the LLM's grounded answer.
- **Finding:** 5 multi-cause member-months were confidently answered by ONE strong rule and never reached the LLM. In each, the member's rate cell was also revised retroactively (v3), so the variance amount mixes a payment problem with the rate revision. That's a real rules limitation surfaced by the eval, reported rather than tuned away.
- **Fix applied (after review):** a new observed fact, `rate_revised_after_close`, adds a weak RETRO_RATE_CHANGE signal whenever another rule fires, so these cases route to the investigator. Result: multi-cause exact match 80% -> 100% (mock); triage-mode LLM calls 14 -> 19. Single-cause accuracy unchanged.

### D47 · (Phase 4) Adversarial evals report what the gates do NOT catch
- **Why:** an extraction that swaps two cells *together with* their source references passes the cross-check (each number really is at its cited spot). The eval keeps this case, labelled "not designed to catch", because the control is the approver's diff review. Showing the boundary is more credible than a wall of 100%.

### D48 · (Phase 4) Privacy is measured, not asserted
- **Method:** scan every cached LLM request/response, the egress log and every audit payload for raw member IDs, any member name, and the DOB of any member a record refers to (found via pseudonymous tokens). Plus 200 injected-PHI payloads that the gateway must block.

### D49 · (Phase 5) A plain, restartable close orchestrator with a human sign-off gate
- **Steps:** retro check → price → reconcile members → reconcile bank → investigate → ready for review. Each step writes an audit event and records progress in `close_runs`; a re-run resumes after the last completed step (steps are idempotent).
- **Gate:** sign-off is refused while any rate draft or proposed adjustment is undecided. Unreviewed variances only warn.
- **Approval of a rate version triggers the retro engine** (and its memo) immediately.

### D50 · (Phase 5) A demo clock, because the system is bitemporal
- **Why:** approvals stamped with today's date (Nov 2026) would be later than every close and break the story. All UI actions use a stored clock that steps through the scenario timeline; one-click resets rebuild the world to a chosen moment ("v3 just arrived" is the demo's starting point).

### D51 · (Phase 5) DuckDB top-1 query returned the wrong row; use max()
- **Found during the UI walkthrough:** on the persisted demo database (after a reset deleted and reinserted audit rows), `ORDER BY seq DESC LIMIT 1` returned the second-highest row while `LIMIT 2` and `max(seq)` were correct (DuckDB 2.0.1). The audit append then reused a sequence number and failed, after the rate approval had already committed. In-memory tests never hit it.
- **Fix:** look up the last event with `max(seq)`; a regression test exercises a file database with delete, reinsert and reopen.

### D52 · (Phase 5) Recoverable follow-ups
- **Why:** rate approval and the retro proposal are separate transactions (approval must not be undone because a memo call failed). If the follow-up fails, "Run the retro engine now" re-runs it; incremental deltas make that safe to repeat.

### D53 · (Phase 5) Retro adjustments show their cause split
- **Why:** a retro run includes everything known now (D36), so the v3 adjustment also carries 12 late-enrollment member-months. The UI now splits rate revision vs. late enrollment, and the memo states the split through placeholders.

### D54 · (Live run) The provider flag must never fall back silently
- **Bug found on the first live attempt:** `--provider anthropic` didn't reach the provider factory, which fell back to `config.yaml` (mock) and built an empty mock. No money was spent, but a "live" run could have reported mock results. The flag now builds the provider explicitly; a regression test covers it.

### D55 · (Live run) Cost controls: cost mode and a hard spend cap
- **Cost mode:** complex investigations and memos use Haiku *unless* an answer from the configured model (Opus) is already cached, so nothing paid for is ever re-bought.
- **Spend cap:** the client refuses new calls once its new spend reaches `--max-spend`; refused items go to a human (BudgetExceeded is an LLMError, already routed to review).
- **Result:** the whole live evaluation cost about **$0.35**. Regenerating results with `--max-spend 0` proves they come entirely from cache.

### D56 · (Live run) What the real models showed
- Extraction (Opus): 34/34 cells exact across PDF, Excel and email. Groundedness 139/140; memos 2/2.
- Multi-cause: primary cause correct for every case. Opus named both causes 7/7 on genuine two-cause cases; Haiku 6/11 (dropped "recoupment"). Both models declined to call "rate revised after close" a cause of a payment gap (0/7), which points to my label definition, not model weakness.
- **Decision:** keep Opus as the default for complex investigations (about 2 cents each); Haiku for simple ones. Agree the retro-cause definition with finance before changing the prompt (a prompt change invalidates the cache and costs a re-run).

*Entries D57–D62 came from a third design review (financial correctness after the happy path). All six findings were confirmed in code and fixed, with regression tests in `tests/test_review3_fixes.py`.*

### D57 · Unpriceable member-months block the close and are never reversed by retro
- **Problem:** the close booked only priced rows, so a missing rate or an enrollment conflict silently understated revenue. The retro engine treated "can't price now" as $0 and could propose a −$400 reversal of a correctly booked month.
- **Fix:** `close_month` refuses while any member-month is UNPRICED or ENROLLMENT_CONFLICT, and sign-off lists them as blockers. The retro engine skips unpriceable member-months and reports them as exceptions. A missing price is a data problem, not evidence of ineligibility.

### D58 · Adjustments are revalidated against what actually POSTED
- **Problem:** D22 counts pending proposals in the retro baseline, which prevents double-counting when proposing but not when posting. v4 (+$40, computed on top of a pending v3 +$80) could be posted first and v3 rejected, leaving the ledger wrong.
- **Fix:** at approval, every line's `previously_booked` must equal close snapshot + *posted* adjustments for that member-month. If not, the adjustment becomes **stale** (never posted) and the next retro run regenerates it. Rejecting an adjustment marks later pending proposals on the same member-months stale. Test: reject v3 → v4 stale → regenerated +$120 → ledger +$120.

### D59 · Bank links scoped to the as-of date; one link per remit
- **Problem:** a historical bank view loaded every saved link, including links to deposits after the cutoff (KeyError). Manual confirmation allowed two deposits on one remit.
- **Fix:** links are filtered to deposits and remits in scope (in the workflow and in the matcher); confirming a second deposit for a remit is refused while matching is one-to-one.

### D60 · Sign-off books exactly what was reviewed
- **Problem:** sign-off repriced at its own timestamp, so a rate or enrollment change between review and sign-off could book an unreviewed amount.
- **Fix:** the close stores a fingerprint of the priced result (rows + exceptions). At sign-off, a fresh pricing must match it; otherwise the run is invalidated (audited) and must be rerun.

### D61 · Settlement labelled honestly; every payment batch shown
- **Settlement:** remains per member-month (D22), but the Retro page now says so explicitly: state retro payments and "remaining" are shared across revisions touching the same member-month, and "remaining" can include base underpayments. Revision-level allocation needs a revision reference on the state's line (roadmap).
- **Cash panel:** shows every remittance batch dated in the month's year and month, plus unmatched deposits, instead of the first batch found by month number.

### D62 · Report what an uncached run costs, and what "grounded" means
- **Problem:** after regenerating results from cache, both report columns showed 100% cache hits and $0, which hid the real cost; "139/140 grounded" could be read as "139/140 correct".
- **Fix:** the client prices every response from its token counts, cached or not (`uncached_equivalent_usd`, measured $0.32 for the half-year), next to the actual new spend. Reports and the deck say "passed the evidence and amount checks", not "correct".

### D63 · A provider outage sends AI work to a human; the close still completes
- **Problem:** invalid output, refusals and the spend cap already went to the human queue, but a network error, timeout, rate limit or overload from the API was not caught, so it would stop the investigate step.
- **Fix:** after the SDK's own retries, transient errors (connection, timeout, 429, 5xx/529) become `LLMUnavailable`, an `LLMError`, so the investigator and memo writer mark the items "needs human" and carry on. Configuration errors (bad key, bad request) still fail loudly, so a broken setup can't hide as a quiet human queue.
- **Tested:** `tests/test_llm_outage.py` simulates the outage for every investigation: all 462 variances land as "needs human" and the retro total is still $23,931.00.
- **Tradeoff:** no automatic switch to a second provider in the prototype; that belongs in a production LLM gateway.

### D64 · Two-tab UI: a plain analyst view, and a "behind the scenes" view read from real records
- **Why:** the six-page UI was hard for a non-technical user to follow, and a technical reviewer wants to see how each number was reached, not more dashboards.
- **Analyst view:** today's to-do cards (new document, rates to approve, correction to approve, bank deposit to confirm, close to run or sign off), three numbers for a month as they stood at its close (so the reasons below add up exactly to the gap), and findings to agree with or change. Approval buttons stay: people approving is a core control.
- **Behind the scenes:** nothing scripted. The story is rendered from the audit log; the close walkthrough from the close-step events; "trace one member" from enrollment, the rate row and its source cell, remittance lines, bank links, rule hits, the exact cached AI request and answer, and the decision; AI calls from `llm_calls` plus the cache. To link each explanation to its AI call, the investigate and memo audit events now record the call's cache key.
- **Tradeoff:** the old pages were retired; some detail (journal-entry table, CSV exports, evals page) is no longer on screen.

### D65 · Generated documents are byte-identical across runs
- **Problem:** reportlab and openpyxl stamp the current time into the PDF and Excel files, so each `make data` changed their sha256. The sha256 is a document's identity for idempotent ingestion, so the demo showed an already-read document as new.
- **Fix:** PDF built with `invariant=1`; Excel timestamps (document properties and zip entries) pinned to the publish date. A test regenerates both and compares hashes.

### D21 · Hand-written test oracles, independent of the generator
- **Why:** If the generator and the engine shared a bug, generator-derived expected values would agree with it. Core edge-case tests use values typed in by hand.
- **Tradeoff:** A few fixtures to maintain by hand.
