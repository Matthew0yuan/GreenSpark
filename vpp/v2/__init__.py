"""v2 dispatcher: three reserve layers and one stored-energy value curve on top of the frozen v1 dispatcher
(reports/v2_plan.md). v1's planner, real-time execution, contracts, bills and ledger are called, not copied; with
every v2 feature off (config.V1) a run is v1's forecast+offer(value), result for result."""
