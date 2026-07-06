\section{L1 vs L2 Regularization}

Both penalties shrink model weights by adding a term to the loss: L1 adds $\lambda \sum_i |w_i|$, L2 adds $\lambda \sum_i w_i^2$. The geometric difference drives everything else: L1's constraint region has corners on the axes, so optima land \emph{on} axes and produce exact zeros — **sparsity and implicit feature selection** — while L2's spherical constraint shrinks all weights smoothly toward (but never exactly to) zero.

Equivalently, L1's gradient has constant magnitude $\lambda$ regardless of weight size (small weights get pushed to exactly zero), while L2's gradient $2\lambda w$ vanishes as weights shrink. L2 keeps correlated features together with shared small weights; L1 tends to pick one of a correlated group arbitrarily and zero the rest — a stability caveat when interpreting selected features.

Practically: use L2 (ridge, weight decay) as the default for prediction and stable optimization; use L1 (lasso) when sparsity itself is the goal; elastic net mixes both to get selection without the correlated-group instability. In Bayesian terms, L2 is a Gaussian prior on weights, L1 a Laplace prior.
