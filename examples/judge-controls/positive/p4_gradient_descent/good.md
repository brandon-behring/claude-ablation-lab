\section{Gradient Descent}

Gradient descent minimizes a differentiable loss $L(\theta)$ by stepping \emph{against} the gradient:
\[
\theta_{t+1} = \theta_t - \eta \, \nabla L(\theta_t)
\]
where $\eta > 0$ is the learning rate. The negative sign is the whole algorithm: the gradient points uphill, so we move the other way.

\subsection{The learning rate}

$\eta$ trades progress against stability. Too small and convergence crawls; too large and iterates overshoot the minimum and diverge — for a quadratic with curvature $L$-Lipschitz gradients, $\eta < 2/L$ is the classical stability bound. Schedules (decay, warmup) and adaptive methods (Adam, RMSProp) exist precisely because one fixed $\eta$ is rarely right for the whole trajectory.

\subsection{What convexity buys}

On a \textbf{convex} loss, every local minimum is global, and gradient descent with a suitable $\eta$ converges to it. On the \textbf{non-convex} losses of deep learning, no such guarantee exists: the method finds a stationary point, which may be a local minimum or a saddle. In practice, stochastic gradient noise helps escape saddles, and the empirical mystery is that the minima found generalize well — a property of the landscape and the data, not of the optimizer's guarantees.
