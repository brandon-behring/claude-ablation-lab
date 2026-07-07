## At-least-once vs exactly-once delivery

**At-least-once** delivery redelivers any message not acknowledged in time: no message is lost, but consumers can see duplicates — a crash after processing but before acking replays the message. **At-most-once** (ack-before-process) trades the opposite way: no duplicates, but crashes lose messages.

True **exactly-once delivery** across an unreliable network is impossible in general (the two-generals problem): the sender cannot distinguish a lost message from a lost acknowledgment. What systems ship as "exactly-once" is **exactly-once processing**: at-least-once delivery plus deduplication — idempotent consumers, unique message IDs checked against a processed-set, or transactional consume-process-produce (e.g. Kafka reading offsets and writing results in one transaction).

The engineering consequence: design consumers to be idempotent and treat duplicates as normal input, not an error path. Then the broker's redelivery machinery becomes a correctness feature instead of a hazard.
