"""Status values and display tables used across the materializer."""

from ..common.namespaces import AR, OBS

# Requirement status.
SATISFIED, UNSATISFIED, UNKNOWN = OBS.Satisfied, OBS.Unsatisfied, OBS.Unknown
# Evidence kind of an instance.
OBSERVED, FROM_CONFIG = OBS.Observed, OBS.FromConfiguration
# Capability status, best first.
AVAILABLE, CONDITIONAL, UNAVAILABLE = OBS.Available, OBS.Conditional, OBS.Unavailable
STATUS_RANK = {AVAILABLE: 0, CONDITIONAL: 1, UNAVAILABLE: 2}

# Assessment classes the snapshot may contain besides model kinds.
ASSESSMENT_CLASSES = {
    OBS.Snapshot,
    OBS.ConditionEvaluation,
    OBS.CapabilityAssessment,
    OBS.RouteAssessment,
    OBS.ModificationAssessment,
}

# Effect types that change something.
MUTATING_EFFECTS = {AR.Create, AR.Update, AR.Delete, AR.Configure, AR.Transmit, AR.Execute, AR.Delegate, AR.Install}
# Effect types the session tables report: changes and reads.
REPORTED_EFFECTS = MUTATING_EFFECTS | {AR.Read}

# Channel adapters that deliver to a client on this machine rather than a remote platform.
LOCAL_CHANNELS = {"cli", "emacs"}

# Display subpaths for modeled resources inside a mounted location. A dict value
# maps the parent location kind to the subpath used under it.
SUBPATHS = {
    AR.NanoClawInboundMailbox: "inbound.db",
    AR.NanoClawOutboundMailbox: "outbound.db",
    AR.NanoClawInboxArtifact: "inbox/",
    AR.NanoClawOutboxArtifact: "outbox/",
    AR.NanoClawConversationArchive: "conversations/",
    AR.NanoClawPersistentMemory: "memory/",
    AR.NanoClawTaskRunLog: "tasks/",
    AR.NanoClawConversationContinuation: "projects/",
    AR.ClaudeTodoList: "todos/",
    AR.ClaudeSkillDefinition: {AR.NanoClawProviderStateDirectory: "skills/", AR.NanoClawSharedSkillsDirectory: "<skill>/"},
}

CONTAINER_ONLY = "-"
