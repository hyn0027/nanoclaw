/**
 * Whether agent containers wait for the ontology runtime (the ontology gate:
 * container/agent-runner/src/ontology-gate.ts). Set by this module when it starts,
 * read by the container runner to pass NANOCLAW_ONTOLOGY_GATE=1 to new containers.
 * No imports, so the container runner can read it without an import cycle.
 */
let enabled = false;

export function setOntologyGate(on: boolean): void {
  enabled = on;
}

export function ontologyGateEnabled(): boolean {
  return enabled;
}
