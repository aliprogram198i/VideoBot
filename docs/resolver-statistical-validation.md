# Resolver statistical validation

The resolver evidence store records paired outcomes for the same opaque sample.
This layer evaluates those observations without changing production behavior.

## Why paired validation is required

Fallback telemetry is selection-biased: a later resolver is normally observed
only after an earlier resolver failed. Comparing those raw success rates can
therefore reward or punish a resolver for the traffic it receives rather than
for its intrinsic performance.

Paired evidence removes that specific bias by comparing two resolver outcomes
from the same sample. The statistical layer uses the discordant outcomes for an
exact two-sided McNemar test and reports a paired success-rate difference.

## Conservative gates

A pairwise advantage is marked `validated_advantage` only when all of these
conditions hold:

1. At least 30 paired samples are available by default.
2. At least 10 discordant paired outcomes are available by default.
3. The exact two-sided McNemar p-value is at or below 0.05.
4. The observed advantage is at least 10 percentage points.
5. The conservative 95% lower bound for the paired difference is above zero.

The thresholds are configurable for tests and future controlled analysis, but
there is no runtime configuration here that activates resolver reordering.

## Explicit non-goals

This layer does not invoke resolvers, discover media URLs, reorder resolver
execution, enable exploration, choose a production winner, or modify the
Runtime Composer. It is an evidence and validation primitive only.

A future adaptive-selection change must consume validated evidence plus an
independent safety policy and remain separately reviewable. Statistical
validation alone is not permission to alter production resolver order.
