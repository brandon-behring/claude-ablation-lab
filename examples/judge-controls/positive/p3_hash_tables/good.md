## Hash tables

A hash table maps keys to array slots through a hash function, buying **expected O(1)** lookup, insert, and delete.

### Where the O(1) comes from — and its fine print

`h(key) mod m` picks a bucket directly, so no scanning is needed. But the guarantee is *expected* time under a well-distributed hash function: adversarial or degenerate key sets can drive every key into one bucket, giving **O(n) worst case**. That is why languages randomize string hashing (hash-flooding defenses).

### Collisions

Two keys hashing to the same bucket must be resolved:

- **Chaining** stores a small list per bucket; simple, tolerates high load.
- **Open addressing** probes alternative slots (linear, quadratic, double hashing); cache-friendly, but deletions need tombstones and clustering degrades probes.

### Load factor and resizing

Load factor `α = n / m` measures fullness. Chained tables typically resize around α ≈ 1, open addressing near α ≈ 0.7, doubling `m` and rehashing every element. A single resize is O(n), but doubling makes the **amortized** insert cost O(1) — the expensive rebuilds are rare and paid for by the cheap inserts between them.
