---
name: walkingpad-next
description: Rank the next useful repository work by evidence, dependency, and hardware requirements.
---

# Choose the next slice

Read every plan under `docs/plans/`, the open-questions section of
`docs/design-spec.md`, and every item marked unresolved or provisional in
`AGENTS.md`. Treat completed historical plans as evidence, not as unfinished work;
current code, `AGENTS.md` and measured evidence take precedence over stale plan
text.

Produce a short ranked list. For each item state one of:

- **Hardware-free:** captured fixtures and no hardware are sufficient.
- **Hardware required:** the pad must be awake and a human must stand clear.

Rank hardware-free work first when it unblocks the same amount. Identify
dependencies between slices, and call out work blocked on an unresolved
measurement rather than effort. Never close an open question by reasoning alone;
leave it blocked until the required hardware procedure is performed and recorded.
