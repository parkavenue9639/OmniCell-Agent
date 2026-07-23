import type { PersistedEvent } from "../generated/events-v1";

export type RunTerminalStatus = "completed" | "failed" | "cancelled";

export interface TimelineMessage {
  readonly eventId: string;
  readonly messageId: string;
  readonly role: string;
  readonly content: string | null;
  readonly contentArtifactId: string | null;
  readonly occurredAt: string;
}
export interface TaskProjection {
  readonly taskId: string;
  readonly title: string;
  readonly description: string | null;
  readonly capabilityName: string | null;
  readonly status: string;
  readonly summary: string | null;
}

export interface CapabilityProjection {
  readonly capabilityCallId: string;
  readonly capabilityName: string;
  readonly taskId: string | null;
  readonly status:
    | "running"
    | "retrying"
    | "completed"
    | "failed"
    | "cancelled";
  readonly attempt: number;
  readonly summary: string | null;
  readonly errorSummary: string | null;
  readonly artifactIds: readonly string[];
  readonly progressCurrent: number | null;
  readonly progressTotal: number | null;
  readonly progressMessage: string | null;
}

export interface SkillLoadProjection {
  readonly skillLoadId: string;
  readonly skillName: string;
  readonly resourceKind: "body" | "reference" | "example";
  readonly resourceName: string | null;
  readonly purpose:
    | "domain_method"
    | "validation_rules"
    | "workflow_guidance"
    | "reference_lookup"
    | "example_lookup";
  readonly status: "running" | "completed" | "failed" | "cancelled";
  readonly outcome: "loaded" | "already_loaded" | null;
  readonly contentBytes: number | null;
  readonly errorCode: string | null;
  readonly errorSummary: string | null;
}

export interface RuntimeCommandProjection {
  readonly runtimeCommandId: string;
  readonly capabilityCallId: string;
  readonly capabilityName: string;
  readonly attempt: number;
  readonly backend: string;
  readonly command: readonly string[];
  readonly code: string | null;
  readonly workdir: string;
  readonly status: "running" | "completed" | "failed" | "timeout" | "cancelled";
  readonly stdout: string;
  readonly stderr: string;
  readonly exitCode: number | null;
  readonly durationMs: number | null;
  readonly commandTruncated: boolean;
  readonly stdoutTruncated: boolean;
  readonly stderrTruncated: boolean;
  readonly redacted: boolean;
}

export interface ReviewProjection {
  readonly reviewId: string;
  readonly taskId: string | null;
  readonly prompt: string;
  readonly status: string;
  readonly decision: string | null;
  readonly comment: string | null;
}

export interface ArtifactProjection {
  readonly artifactId: string;
  readonly kind: string;
  readonly mediaType: string | null;
  readonly sizeBytes: number;
  readonly sha256: string;
}

export interface RunProjection {
  readonly runId: string;
  readonly conversationId: string;
  readonly appliedSequence: string;
  readonly appliedEventIds: Readonly<Record<string, string>>;
  readonly status: string;
  readonly terminalStatus: RunTerminalStatus | null;
  readonly stopReason: string | null;
  readonly messages: readonly TimelineMessage[];
  readonly tasks: Readonly<Record<string, TaskProjection>>;
  readonly skillLoads: Readonly<Record<string, SkillLoadProjection>>;
  readonly capabilities: Readonly<Record<string, CapabilityProjection>>;
  readonly runtimeCommands: Readonly<Record<string, RuntimeCommandProjection>>;
  readonly reviews: Readonly<Record<string, ReviewProjection>>;
  readonly artifacts: Readonly<Record<string, ArtifactProjection>>;
  readonly events: readonly PersistedEvent[];
}

export function emptyRunProjection(
  runId: string,
  conversationId: string,
): RunProjection {
  return {
    runId,
    conversationId,
    appliedSequence: "0",
    appliedEventIds: {},
    status: "pending",
    terminalStatus: null,
    stopReason: null,
    messages: [],
    tasks: {},
    skillLoads: {},
    capabilities: {},
    runtimeCommands: {},
    reviews: {},
    artifacts: {},
    events: [],
  };
}
