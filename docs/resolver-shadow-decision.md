# Resolver Shadow Decision

The shadow layer is the observation stage between statistical validation and
any future runtime resolver adaptation.

## Contract

For a bounded `(platform, media_kind)` context, the layer:

1. receives the established resolver order and already-computed pairwise
   statistical validation;
2. evaluates the evidence-gated selection policy;
3. records the original order, hypothetical order, and whether a reorder would
   occur;
4. never changes the live resolver order.

No resolver is invoked, no URL is discovered, no download is started, and no
additional network request is created.

## Safety properties

- The existing resolver order is the source of truth for runtime behavior.
- Invalid, missing, ambiguous, insufficient, or malformed evidence falls back
  to the exact established order.
- Resolver names and context values are bounded and allowlisted.
- The shadow database stores no raw URLs, Telegram identifiers, tokens, media
  payloads, or credentials.
- Telemetry write failures are fail-open and cannot affect the downloader.
- A proposed order must contain exactly the same resolver set as the original
  order before it can be persisted.

## What the metrics mean

`would_reorder` is a hypothetical decision only. A high reorder rate means the
validated policy frequently disagrees with the established order; it does not
mean that runtime behavior has changed or that the proposed resolver is proven
in production.

The `proposed_first` aggregate is descriptive evidence for a later review. It
must not be used as a runtime policy until the full shadow period has been
reviewed and an explicit activation change has been separately approved and
tested.

## Activation boundary

This module is intentionally not imported by the Runtime Composer or
`smart_media_bridge.py`. The next activation stage, if ever justified, must
remain separately gated and preserve fail-closed behavior.
