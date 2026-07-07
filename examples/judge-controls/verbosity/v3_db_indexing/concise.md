## When to add a database index — and what it costs

An index buys fast lookups on a column at the price of slower writes and extra storage. Add one when a **selective** predicate appears in hot queries — WHERE, JOIN keys, ORDER BY prefixes — and the query planner is otherwise scanning far more rows than it returns; `EXPLAIN` output showing sequential scans on large tables with small result sets is the classic tell.

The costs are concrete. Every INSERT/UPDATE/DELETE must also maintain each index, so write-heavy tables pay per index on every mutation; bulk loads often drop and rebuild indexes for this reason. Indexes consume storage comparable to the indexed data, and they can go unused: low-selectivity columns (booleans, small enums) rarely justify one, because the planner ignores an index that filters little.

Two refinements do disproportionate work: **composite indexes** must match the query's leftmost-prefix pattern (an index on `(a, b)` serves `WHERE a=?` and `WHERE a=? AND b=?`, not `WHERE b=?` alone), and **covering indexes** that include every selected column let the engine answer from the index without touching the table. The discipline: index the queries you actually run, verify with the planner, and delete indexes nothing uses.
