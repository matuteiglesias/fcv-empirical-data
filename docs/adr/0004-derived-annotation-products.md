# ADR 0004: Derived annotation products remain derived

**Status:** Accepted

## Decision

Machine- or human-annotation outputs are derived empirical products, not replacements for the source.
They must retain source-native identifiers and record enough versioned configuration to reconstruct
how annotations were produced. A new annotation does not automatically receive L4 research authority.

## Consequences

The existing investment annotation flow may remain as legacy evidence. Future work can wrap or
supersede it with explicit manifests and contracts without moving or rewriting large source artifacts
in this kernel PR.
