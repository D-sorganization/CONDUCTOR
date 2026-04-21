## 2024-05-18 - Optimize task list rendering with DocumentFragment and fast sort
**Learning:** `localeCompare` is ~3x slower than direct string comparison (`<` and `>`) for ISO 8601 timestamps. When combined with appending task rows directly to the DOM in WebSocket event handlers (which trigger reflows for each append), it creates UI jank under heavy task load.
**Action:** Always batch DOM insertions using `DocumentFragment` inside tight loops or rapid event handlers. Use simple `<`/`>` operators when sorting predictably-formatted strings like ISO dates instead of `localeCompare`.
