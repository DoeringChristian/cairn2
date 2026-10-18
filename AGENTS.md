# Project priorities

Stability > Performance > Features.

Before implementing any change to the Cairn contract, notify the user, explain the
proposed change and compatibility impact, and obtain explicit permission. This
includes public API or SDK behavior, persisted data formats, and documented
contract guarantees. Do not infer permission for contract changes from a general
request to optimize performance or add a feature. Existing explicit approval for
the specific contract change is sufficient.

Keep UI-only optimizations compatible with the existing logging API and storage
contract. When uncertain whether a change affects the contract, ask first.
