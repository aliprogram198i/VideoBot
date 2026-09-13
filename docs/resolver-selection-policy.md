# Evidence-Gated Resolver Selection

The resolver selection policy is the safety boundary between statistical
validation and any future runtime adaptation.

## Required gates

A resolver can become the proposed first resolver only when all of the
following hold for the same `(platform, media_kind)` context:

1. It has a paired comparison against every other eligible resolver.
2. Each comparison has at least 30 paired samples.
3. Each comparison has at least 10 discordant outcomes.
4. The candidate's paired success-rate delta is at least 10 percentage points.
5. The candidate's 95% confidence-bound lower endpoint is strictly positive.
6. Every pairwise p-value passes Holm step-down multiple-comparison control at
   alpha 0.05.
7. Exactly one candidate satisfies all gates.

If any condition fails, the exact original resolver order is returned.

## Why this is stricter than the previous selector

The earlier adaptive selector used fallback success rates. Those observations
are selection-biased because later resolvers are only observed after earlier
resolvers fail. This policy consumes paired evidence from the same sample and
requires the candidate to beat every peer rather than only the current
baseline.

Holm correction is applied because one candidate may be compared against
multiple peers; using an unadjusted 0.05 threshold for every comparison would
increase false-positive selection risk.

## Runtime safety contract

This module is intentionally dormant. It does not:

- invoke resolvers;
- discover or download media;
- modify Runtime Composer;
- modify `smart_media_bridge.py`;
- enable exploration;
- add or remove resolvers;
- persist a runtime winner;
- deploy Production.

A future runtime integration must remain fail-open: malformed, missing,
ambiguous, stale, or insufficient evidence must preserve the established
resolver order exactly.
