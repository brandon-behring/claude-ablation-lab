## Binary search

Binary search finds a target in a **sorted** array by repeatedly halving the search interval.

### The invariant

Maintain `lo` and `hi` such that the target, if present, is always within `[lo, hi)`. Each step probes `mid = lo + (hi - lo) // 2`; if `a[mid] < target` the answer lies right of `mid` (`lo = mid + 1`), otherwise it lies at or left of `mid` (`hi = mid`). The interval shrinks every iteration, which guarantees termination.

### Complexity

Each comparison halves the interval, so the worst case is **O(log n)** comparisons and O(1) extra space. This is optimal for comparison-based search on a sorted array.

### Classic pitfalls

- **Overflow**: `(lo + hi) / 2` can overflow in fixed-width arithmetic; use `lo + (hi - lo) / 2`.
- **Off-by-one loops**: mixing a closed interval `[lo, hi]` with the half-open update rules loops forever on two-element ranges. Pick one convention and keep it.
- **Unsorted input**: the invariant is meaningless if the array is not sorted — the result is silently wrong, not an error.
- **Equal elements**: to find the *first* match, keep searching left after a hit (`hi = mid`) instead of returning immediately.
