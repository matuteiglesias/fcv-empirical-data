# ADR 0002: Preserve natural grain

**Status:** Accepted

## Decision

Durable ingestion and normalized products preserve each source's natural grain. Events, projects,
project locations, administrative aggregates, and respondents are not coerced into a common
`GID × period` schema by generic code.

Geographic membership and period annotations may be derived explicitly through shared spatial/time
infrastructure, but they are annotations rather than permission to discard source-native keys.

## Consequences

Later experiments may construct the lattice they need without making ingestion irreversible. A source
with multiple legitimate grains may emit multiple data products instead of a lossy universal record.
