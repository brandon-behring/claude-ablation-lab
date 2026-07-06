\section{B-Trees}

A B-tree is a balanced search tree whose nodes hold \emph{many} keys — often hundreds — rather than one.

\subsection{Why not a binary search tree?}

Databases read storage in fixed-size pages (4--16 KB); the dominant cost is the number of page reads, not comparisons. A binary tree does one comparison per page touched — catastrophic when each touch is an I/O. A B-tree packs a whole node into one page, so a single read narrows the search among hundreds of children. The tree is also kept perfectly balanced by construction: nodes split when full and merge/borrow when underfull, so every leaf sits at the same depth.

\subsection{Branching factor and height}

With branching factor $m$, a B-tree of height $h$ indexes on the order of $m^h$ keys, so
\[
h \approx \log_m N .
\]
For $m = 500$ and $N = 10^9$: $h \approx \log_{500} 10^9 \approx 3.6$ — a billion rows in three to four page reads, and the top levels are invariably cached in memory, leaving roughly one \emph{actual} disk read per lookup.

\subsection{The variant databases actually use}

Production engines use B\textsuperscript{+}-trees: internal nodes hold only routing keys, all values live in the leaves, and leaves are linked for efficient range scans — the access pattern indexes exist to serve.
