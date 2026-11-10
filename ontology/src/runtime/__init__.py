"""Per-session runtime ontology for NanoClaw.

Every NanoClaw session keeps its own ontology. It starts from the base model in
ontology/ (never edited at runtime) and grows by expansions:

- base changes: a skill, MCP server, or package added to (or removed from) the
  session's agent group. A skill or MCP server with a reviewed graph is simply
  loaded; one without gets a proposed graph (rt:ProposedFromSource);
- traffic: calls the OneCLI gateway recorded for the agent (GATEWAY_CAPTURE_DIR),
  read by an LLM that proposes new kinds, relations, reactions, and the concrete
  instances the payloads name (rt:ObservedTraffic).

State lives in data/v2-sessions/<group>/<session>/ontology/ (see store.py). The
ontology id is ``ont-<session id>``; each expansion is ``<ontology id>/<NNNN>``,
so a later run resumes from the last expansion and the last processed call id.
"""
