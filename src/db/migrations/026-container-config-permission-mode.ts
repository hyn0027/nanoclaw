import type { Migration } from './index.js';

/**
 * Per-agent-group safeguard settings on `container_configs`, so the runtime ontology can be
 * compared against Claude Code's own permission modes:
 *
 *   - `permission_mode`: 'auto' (Claude Code's classifier decides) | 'manual' (each risky
 *     tool call is approved in a DM card). NULL = bypass, the default: no per-tool checks.
 *   - `ontology`: 'off' leaves the group out of the runtime ontology (no gate, no policy
 *     groups, no questions). NULL = on.
 *
 * No backfill: NULL keeps every existing group as it was.
 */
export const migration026: Migration = {
  version: 26,
  name: 'container-config-permission-mode',
  async up(db) {
    await db.exec(`ALTER TABLE container_configs ADD COLUMN permission_mode TEXT;`);
    await db.exec(`ALTER TABLE container_configs ADD COLUMN ontology TEXT;`);
  },
};
