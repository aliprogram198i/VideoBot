# Resolver evidence contract

The resolver chain is sequential, so ordinary fallback telemetry is selection-biased: later resolvers observe requests that earlier resolvers already failed. Resolver adaptation therefore requires paired evidence collected from the same request context.

## Contract

A future exploration runner may execute an explicitly selected set of existing public-media resolvers independently for the same request and submit one `sample_id` containing one outcome per resolver. The evidence store accepts only bounded platform/media-kind classes and technical outcome fields; it does not accept raw URLs, Telegram identifiers, credentials, or secrets.

The current implementation is storage and validation only. It does **not** invoke resolvers, change resolver order, or enable exploration.

## Promotion gate

Adaptive resolver ordering must remain disabled until evidence is sufficient for the target context and the statistical validation layer confirms a meaningful, robust advantage. A simple raw success-rate comparison is insufficient. The lower 95% confidence bound is retained as a conservative uncertainty signal; final selection should additionally use paired comparisons and minimum sample thresholds.
