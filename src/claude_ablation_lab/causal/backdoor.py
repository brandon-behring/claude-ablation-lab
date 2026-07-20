"""Backdoor-criterion validity: the grader core for the causal family.

Why this exists (decision 13): valid adjustment sets are usually **not unique** — the
minimal set, the full backdoor set, and assorted sufficient supersets can all be correct
for one graph. Grading by exact match against a single gold answer scores a *different
valid set* as zero, which reads as model failure but is grader failure, and the two are
indistinguishable after the fact. Because every item's DAG is generated, validity can be
*checked* instead of *matched*: a proposed set is correct iff it satisfies Pearl's
backdoor criterion relative to (treatment, outcome) in that DAG.

The criterion, for ordered pair (X, Y) and set Z:

1. **No descendant condition** — no node in Z is a descendant of X (conditioning on a
   descendant of the treatment can open collider paths or block mediation).
2. **Blocking condition** — Z blocks every backdoor path from X to Y, i.e. Z
   d-separates X from Y in the graph with all edges *out of* X removed.

Implementation notes
--------------------

d-separation is decided by the standard reachability ("Bayes-ball") construction: a path
is blocked at a chain or fork node iff that node is conditioned on, and at a collider
node iff neither the collider nor any of its descendants is conditioned on. The
implementation below walks (node, direction) states so each of the 2·|V| states is
visited at most once — linear, and small enough to verify by hand.

This module sits on the critical path of every causal-family number, so it carries
known-answer fixtures in ``tests/test_backdoor.py`` for the graph shapes the item
generator dials through: confounder triangle, collider, M-bias, front-door,
butterfly/bowtie. Everything here is pure and deterministic.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

__all__ = ["Dag", "is_valid_adjustment_set", "is_minimal_adjustment_set"]


@dataclass(frozen=True)
class Dag:
    """A directed acyclic graph as ``child -> tuple(parents)``.

    Parameters
    ----------
    parents:
        Mapping from each node to its parents. Nodes that appear only as parents are
        implicitly declared. Acyclicity and self-loops are validated at construction —
        a malformed graph must fail here, not misgrade items later.

    Raises
    ------
    ValueError
        If the graph contains a self-loop or a directed cycle.
    """

    parents: dict[str, tuple[str, ...]]
    _children: dict[str, tuple[str, ...]] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        nodes: set[str] = set(self.parents)
        for child, parent_list in self.parents.items():
            if child in parent_list:
                raise ValueError(f"self-loop on node {child!r}")
            nodes.update(parent_list)

        children: dict[str, list[str]] = {node: [] for node in nodes}
        for child, parent_list in self.parents.items():
            for parent in parent_list:
                children[parent].append(child)

        # Kahn's algorithm: if a topological order doesn't consume every node, there is
        # a cycle, and every downstream verdict would be garbage.
        indegree = {node: len(self.parents.get(node, ())) for node in nodes}
        queue = deque(node for node, degree in indegree.items() if degree == 0)
        seen = 0
        while queue:
            node = queue.popleft()
            seen += 1
            for child in children[node]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    queue.append(child)
        if seen != len(nodes):
            raise ValueError("graph contains a directed cycle; not a DAG")

        object.__setattr__(
            self, "_children", {node: tuple(kids) for node, kids in children.items()}
        )

    @property
    def nodes(self) -> frozenset[str]:
        return frozenset(self._children)

    def parents_of(self, node: str) -> tuple[str, ...]:
        return self.parents.get(node, ())

    def children_of(self, node: str) -> tuple[str, ...]:
        return self._children.get(node, ())

    def descendants_of(self, node: str) -> frozenset[str]:
        """All strict descendants of *node*."""
        seen: set[str] = set()
        queue = deque(self.children_of(node))
        while queue:
            current = queue.popleft()
            if current in seen:
                continue
            seen.add(current)
            queue.extend(self.children_of(current))
        return frozenset(seen)

    def _require_nodes(self, *requested: str) -> None:
        missing = [node for node in requested if node not in self._children]
        if missing:
            raise ValueError(f"unknown node(s) {missing!r}; graph has {sorted(self._children)}")

    # ------------------------------------------------------------------ d-separation

    def d_separated(self, x: str, y: str, given: frozenset[str]) -> bool:
        """True iff every path between *x* and *y* is blocked by *given*.

        Reachability over (node, direction) states. Direction ``"up"`` means the walk
        arrived at the node from one of its children (moving against the arrows);
        ``"down"`` means it arrived from a parent (moving with them). Transition rules
        encode exactly the three path shapes:

        - chain / fork (non-collider): traversable iff the middle node is **not** in
          *given*;
        - collider: traversable iff the collider **or one of its descendants** is in
          *given*.
        """
        self._require_nodes(x, y)
        conditioned_or_ancestral = self._conditioning_closure(given)

        # (node, came_from_child) states; start "up" from x as if leaving it upward —
        # both edge directions out of x are explored by the initial expansion below.
        visited: set[tuple[str, str]] = set()
        queue: deque[tuple[str, str]] = deque([(x, "up")])
        while queue:
            node, direction = queue.popleft()
            if (node, direction) in visited:
                continue
            visited.add((node, direction))
            if node == y and node != x:
                return False  # reached y along an active path

            if direction == "up":
                # Arrived from a child (or starting at x): may continue to parents
                # (chain upward) and to children (fork) iff node is unconditioned.
                if node not in given:
                    for parent in self.parents_of(node):
                        queue.append((parent, "up"))
                    for child in self.children_of(node):
                        queue.append((child, "down"))
            else:
                # Arrived from a parent. Continuing downward through node is a chain:
                # allowed iff node is unconditioned. Turning back up through node is a
                # collider at node: allowed iff node or a descendant is conditioned.
                if node not in given:
                    for child in self.children_of(node):
                        queue.append((child, "down"))
                if node in conditioned_or_ancestral:
                    for parent in self.parents_of(node):
                        queue.append((parent, "up"))
        return True

    def _conditioning_closure(self, given: frozenset[str]) -> frozenset[str]:
        """Nodes that open colliders: the conditioned set plus its ancestors' view.

        A collider is active iff *it or any of its descendants* is in ``given`` —
        equivalently, iff the collider is an ancestor of (or member of) the
        conditioned set. Computed once by walking up from every conditioned node.
        """
        opened: set[str] = set()
        queue = deque(given)
        while queue:
            node = queue.popleft()
            if node in opened:
                continue
            opened.add(node)
            queue.extend(self.parents_of(node))
        return frozenset(opened)


def is_valid_adjustment_set(
    dag: Dag, *, treatment: str, outcome: str, adjustment: frozenset[str]
) -> bool:
    """Does *adjustment* satisfy the backdoor criterion for (treatment → outcome)?

    Parameters
    ----------
    dag:
        The generated DAG the item was built from.
    treatment, outcome:
        The causal query. Must be distinct nodes of *dag*.
    adjustment:
        The proposed set. May be empty — the empty set is valid whenever no backdoor
        path exists (e.g. a pure mediation graph).

    Raises
    ------
    ValueError
        If treatment/outcome coincide, are missing from the graph, or *adjustment*
        contains the treatment, the outcome, or nodes not in the graph — a malformed
        proposal is a grading error to surface, not a False to bury.
    """
    if treatment == outcome:
        raise ValueError("treatment and outcome must be distinct")
    dag._require_nodes(treatment, outcome, *adjustment)
    if treatment in adjustment or outcome in adjustment:
        raise ValueError("adjustment set may not contain the treatment or outcome")

    # Condition 1: no descendant of the treatment.
    if adjustment & dag.descendants_of(treatment):
        return False

    # Condition 2: adjustment d-separates X from Y in the graph minus X's out-edges.
    # Built over dag.nodes, not dag.parents.keys(): a node that appears only as a
    # parent (e.g. the treatment in a pure mediation chain) would otherwise vanish
    # from the trimmed graph entirely and misgrade as "unknown node".
    trimmed = Dag(
        {node: tuple(p for p in dag.parents_of(node) if p != treatment) for node in dag.nodes}
    )
    return trimmed.d_separated(treatment, outcome, adjustment)


def is_minimal_adjustment_set(
    dag: Dag, *, treatment: str, outcome: str, adjustment: frozenset[str]
) -> bool:
    """Is *adjustment* valid with no removable element? (Subscore only, never the grade:
    a valid non-minimal set is a correct answer — merely a less efficient one.)"""
    if not is_valid_adjustment_set(
        dag, treatment=treatment, outcome=outcome, adjustment=adjustment
    ):
        return False
    return all(
        not is_valid_adjustment_set(
            dag, treatment=treatment, outcome=outcome, adjustment=adjustment - {member}
        )
        for member in adjustment
    )
