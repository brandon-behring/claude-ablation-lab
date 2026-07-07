## The CAP theorem

CAP says a distributed data store can guarantee at most two of three properties **while a network partition is happening**: Consistency (every read sees the latest write), Availability (every request gets a response), and Partition tolerance (the system survives dropped/delayed messages between nodes).

Since real networks partition, P is not optional — the actual choice is **C vs A during a partition**. A CP system (e.g. a quorum-based store refusing minority-side writes) returns errors rather than stale data; an AP system (e.g. Dynamo-style stores) keeps answering on both sides and reconciles divergence later.

Two practical refinements matter. First, the choice is per-operation, not per-system: one database can serve linearizable reads on a critical path and eventually-consistent reads elsewhere. Second, PACELC extends the picture to normal operation: **e**lse (no partition), the trade-off is latency vs consistency — coordinating replicas on every write costs round-trips even when nothing is failing.
