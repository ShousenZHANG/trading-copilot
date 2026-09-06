---
description: Explicit deep research using four parallel analysts, sequential debates, one stored evidence snapshot and the shared decision policy. Ordinary guidance uses investment-chat.
argument-hint: <TICKER> [--mode=tactical|accumulation] [--horizon=daily|swing|long_term] [--debate-rounds=1] [--risk-rounds=1] [--resume=<RUN_ID>]
---

Run this pipeline only when the user explicitly invokes /analyze or requests the full deep pipeline. Ordinary investment questions use investment-chat. A long report is produced only when expressly requested.

Resolve the first argument to a supported US stock/ETF, ^NDX, ^IXIC, or GOLD.CNY. Index levels are research benchmarks. GOLD.CNY means Chinese investment bars/coins bought in RMB; SGE prices remain benchmarks, not merchant prices. The shared core resolves supported aliases. Default debate/risk rounds are 1; preserve the user's accumulation mode or horizon.

## Execution plan

### Step 0: Prepare or explicitly resume a validated run

From the project root run:

    uv run --no-project --quiet --script scripts/copilot_cli.py prepare-run <INSTRUMENT> --mode <MODE> --horizon <HORIZON>

The result contains run_id, run_dir, manifest and snapshot. Use its actual canonical instrument_id, snapshot_id, portfolio_version, valid_until, mode and horizon in every brief. The run directory identifies immutable inputs, not a ticker/date folder convention. Read mcp__trading-copilot__get_investment_context and save the exact returned context as <run_dir>/context.json. Missing portfolio completeness remains unknown; old recommendation memory and data/positions.md do not establish actual complete holdings.

If the user supplies --resume=<RUN_ID>, run:

    uv run --no-project --quiet --script scripts/copilot_cli.py resume-run <RUN_ID>

Proceed only when the core accepts snapshot expiry, portfolio version and code/prompt version. Rejected resume requires fresh prepare-run. Re-run stages against the accepted immutable snapshot: there is no typed stage-completion record, so file existence, byte count, same-day filenames and an old report never authorize a stage skip or final recommendation. Start fresh debate histories for the rerun.

Build one common brief: {instrument_id, snapshot_id, portfolio_version, run_dir, mode, horizon, decision_at, valid_until}. Every analyst reads mcp__trading-copilot__get_evidence_snapshot(snapshot_id). All numeric claims cite actual evidence IDs and fields from that same snapshot. The service attaches per-instrument research sections and research_issues; inspect their status and critical_evidence_eligible flags separately from market quality. Missing SEC, FRED, news or ETF evidence is a gap, not proof that nothing changed.

Read docs/strategy.md if present. If historical recommendation reflections are relevant, use scripts/memory.py past-context read-only and save a clearly labeled slice as 00-past-context.md; otherwise put the journal's prior-decision summary there. Reflections are not fills or proof of strategy effectiveness.

### Step 1: Analysts (PARALLEL — fan out 4 in a single message)

Dispatch a single message containing 4 Agent tool calls, using the identical common brief:

- market-analyst → <run_dir>/01-market.md
- social-analyst → <run_dir>/02-social.md
- news-analyst → <run_dir>/03-news.md
- fundamentals-analyst → <run_dir>/04-fundamentals.md

For explicit deep GOLD.CNY research replace fundamentals-analyst with macro-analyst → <run_dir>/05-macro.md. This does not change normal concise /gold behavior.

Wait for all four. Each artifact identifies its snapshot and coverage gaps. Log failures to <run_dir>/_errors.md; a missing report remains a missing input. On an actual dispatch rate limit, fall back to serial and record it. Analysts have no dependencies on each other. A coverage-gap note is legitimate output; absent social/news access does not establish neutral sentiment.

### Step 2: Bull/Bear debate

Within each round run bull-researcher, then bear-researcher sequentially. Supply the common brief, available analyst artifacts, current transcript and preceding opposing argument. Append to <run_dir>/debate_history.md under "## Bull (round N)" and "## Bear (round N)". Internal debate remains English. Debaters may challenge interpretation, not upgrade evidence quality or invent missing facts.

### Step 3: Research Manager

Dispatch research-manager (Opus) with the common brief, analyst reports and debate. It writes <run_dir>/06-research-plan.md, preserving the five-tier Recommendation/Rationale/Strategic Actions schema. This is a research proposal, not policy approval.

### Step 4: Trader

Dispatch trader with the same snapshot/context, research plan and analyst artifacts. It writes <run_dir>/07-trader-proposal.md. Preserve its three-tier action contract. Exact sizing requires actual complete portfolio inputs and policy authorization, not a confidence-to-percentage rule.

### Step 5: Risk debate (3-way, fixed order)

Within each round dispatch aggressive-debator → conservative-debator → neutral-debator, strictly sequentially. Supply the common brief, analyst reports, trader proposal, journal context and preceding risk transcript. Append role/round sections to <run_dir>/risk_debate_history.md. Internal debate stays English. Unknown risk inputs remain unknown; a persuasive argument cannot make a machine check pass.

### Step 6: Portfolio Manager

Dispatch portfolio-manager (Opus) with all prior artifacts, common brief and journal context. It writes <run_dir>/08-portfolio-decision.md as a five-tier research proposal with missing inputs disclosed. It neither records a trade nor approves its own risk gate.

### Step 7: Validate shape, assess policy, return the conversation decision

First check artifact shape:

    python scripts/validate_outputs.py run <RUN_DIR>

Repair failures before proceeding. Success establishes markdown contracts only, not current-data/risk eligibility.

Read the PM's explicit Rating field once. Map Buy/Overweight → buy, Hold → hold, Underweight → reduce, Sell → sell. The advisor's Strong Buy/Reduce/Avoid scale is separate; never parse rating words from prose or append that scale to legacy memory.

Build the structured proposal with instrument_id, action, mode, horizon, reasons, conditions, actual evidence_ids, plus the manifest's snapshot_id and portfolio_version. Include required_indicators when an indicator is necessary to the thesis. Optional price/quantity/target_weight/stop_loss values need the correct instrument units and supported evidence; omit unsupported fields. Critical research claims require critical_evidence_eligible=true and the actual matching research evidence ID. An unverified source may explain a gap, not justify an actionable thesis.

Every literal numerical fact in reasons/conditions requires claims=[{evidence_id, path, value}]. Use a JSON Pointer relative to the cited stored evidence record, such as /data/value, or /instruments/<INSTRUMENT>/price and /instruments/<INSTRUMENT>/indicators/<field> for computed values linked to market evidence. The claim value must exactly match the stored scalar; normal displayed rounding is allowed, arbitrary rescaling is not. Dates and indicator labels such as RSI14 are labels rather than metric values. Unsupported future price thresholds stay out of reasons/conditions. Prefer a supported qualitative reconsideration condition when no computed threshold exists. The machine checks source/value identity, not the truth of every qualitative interpretation.

Call mcp__trading-copilot__assess_investment_proposal(snapshot_id, proposal). If MCP is unavailable, save proposal JSON and use the same implementation:

    uv run --no-project --quiet --script scripts/copilot_cli.py review <SNAPSHOT_ID> --input <PROPOSAL_JSON>

The tool commits the assessed recommendation and returns {decision, message}. Save its exact decision object to <run_dir>/assessed-decision.json. Preserve action, scope, evidence, timestamps and versions. Expiry during this long pipeline requires fresh collection and renewed affected analysis; never relabel old evidence as current.

Return the assessed message in concise Chinese: action/view and horizon, at most two reasons, reconsideration conditions, data date/source links and gaps. data_insufficient pauses affected advice; it never means sell. A research-only index/gold view is not an executable purchase. No default long report or automatic legacy memory append.

Only when the user expressly requests a report, align the PM's explicit rating and scope with the assessed outcome, then run:

    python scripts/assemble_report.py --ticker <INSTRUMENT> --date <YYYY-MM-DD> --run-dir <RUN_DIR> --assessed-decision <RUN_DIR>/assessed-decision.json --out <RUN_DIR>/report.md

The assembler verifies the exact committed Decision, snapshot expiry and current portfolio version. Its top conclusion is assessed output; agent prose is a research appendix. A rejected/data-insufficient decision is not publishable as a Buy report.

Actual purchases/sales use investment-chat's record_investment_operation workflow with the user's original statement and stable idempotency key. This pipeline's proposals and recommendations never become fills.
