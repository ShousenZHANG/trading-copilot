# ADR-0008: Gold evidence, historical admission waivers and contribution boundaries

Status: records the existing gold implementation; missing written rationale restored
2026-10-02. This record introduces no new waiver or expansion of execution scope.

Chinese investment gold is a CNY book with one benchmark identity, `GOLD.CNY`
(SGE Au99.99). Reusing a multi-ETF allocation rule would always target the same
single asset. A contribution schedule is a cash-deployment policy, while a
trend filter makes an additional performance claim; those need different evidence.

1. **Evidence is an SGE cache, not a second source.** The vendored
   `reference/sge-au9999-daily.csv` starts on 2016-12-19. Its provenance points
   to Shanghai Gold Exchange and the refresh script. Two wrappers of that
   dataset do not independently corroborate a price. Live snapshot quality
   and a merchant's retail quote remain separate from this historical series.
2. **Only the existing historical gaps receive the gold CLI's waivers.**
   `backtest_cli.GOLD_WAIVERS` records `span` (under the 15-year minimum) and
   `stress_2008` (the available series contains no 2008 history). A plain report
   leaves both failures visible and unwaived; `--adopt` supplies their stated
   reasons to the admission gate. Failures remain in the immutable adoption,
   alongside the waiver reasons. Gold uses the SGE stress-session table;
   2020/2022 coverage, costs, parameter count and out-of-sample checks remain
   enforced. Costs, parameters and out-of-sample rules cannot be waived by
   the gate. This is permission to use a shorter historical baseline, not
   evidence of performance in 2008.
3. **A holding curve does not validate a contribution schedule.** The engine
   rebalances a one-instrument universe to its target weight; subsequent
   scheduled additions are not a stream of new external contributions.
   `schedule_disclosure` therefore travels on every gold adoption/evaluation.
   `pause_below_trend` cannot be adopted: the existing backtest does not test
   its effect on live contribution decisions. No sensitivity result should
   imply that different contribution intervals were measured as equivalent.
4. **Retail sizing needs the user's own book and observed ask.** Gold has its
   own adopted-rule pointer, CNY completeness declaration, total budget,
   contribution amount, minimum/increment and daily order cap. A merchant
   quote supplies an all-in CNY ask per fine gram and observation time; shape
   and freshness validation does not verify that the reported price is true.
   Attaching it creates a new immutable snapshot, whose ID must be evaluated.
   Missing prerequisites keep the result research_only. The engine rounds
   additions downward and reports refusals/disclosures beside amounts; it
   does not submit trades or maintain cash through broker synchronization.

Changing these boundaries requires new measured evidence and a separate decision.
A contribution backtest, broker integration or automatic scheduler is not supplied
by this historical waiver.
