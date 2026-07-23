// 此文件由 frontend/scripts/generate-contracts.mjs 生成，请勿手工修改。
export interface paths {
    "/api/v1/artifacts/{artifact_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Artifact */
        get: operations["getArtifact"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/artifacts/{artifact_id}/content": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Artifact Content */
        get: operations["getArtifactContent"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/conversations": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Conversations */
        get: operations["listConversations"];
        put?: never;
        /** Create Conversation */
        post: operations["createConversation"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/conversations/{conversation_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Conversation */
        get: operations["getConversation"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/conversations/{conversation_id}/artifacts": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Artifacts */
        get: operations["listArtifacts"];
        put?: never;
        /** Upload Artifact */
        post: operations["uploadArtifact"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/conversations/{conversation_id}/history": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get History */
        get: operations["getConversationHistory"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/conversations/{conversation_id}/reviews": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Reviews */
        get: operations["listReviews"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/conversations/{conversation_id}/runs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Create Run */
        post: operations["createRun"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/health/live": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Liveness */
        get: operations["getLiveness"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/health/ready": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Readiness */
        get: operations["getReadiness"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/reviews/{review_id}/decision": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Decide Review */
        post: operations["decideReview"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/runs/{run_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Run */
        get: operations["getRun"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/runs/{run_id}/cancel": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Cancel Run */
        post: operations["cancelRun"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/runs/{run_id}/events": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Replay Events */
        get: operations["replayRunEvents"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/runs/{run_id}/events/stream": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Stream Events */
        get: operations["streamRunEvents"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/runs/{run_id}/resume": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Resume Run */
        post: operations["resumeRun"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
}
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        /** AgentTurnStartedEvent */
        AgentTurnStartedEvent: {
            /**
             * Conversation Id
             * Format: uuid
             */
            conversation_id: string;
            /**
             * Event Id
             * Format: uuid
             */
            event_id: string;
            /**
             * Occurred At
             * Format: date-time
             */
            occurred_at: string;
            payload: components["schemas"]["AgentTurnStartedPayload"];
            /**
             * Run Id
             * Format: uuid
             */
            run_id: string;
            /**
             * Schema Version
             * @default 1
             * @constant
             */
            schema_version: 1;
            /** Sequence */
            sequence: string;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "agent.turn_started";
        };
        /** AgentTurnStartedPayload */
        AgentTurnStartedPayload: {
            /** Remaining Turns */
            remaining_turns: number;
            /** Turn Index */
            turn_index: number;
        };
        /** ArtifactCreatedEvent */
        ArtifactCreatedEvent: {
            /**
             * Conversation Id
             * Format: uuid
             */
            conversation_id: string;
            /**
             * Event Id
             * Format: uuid
             */
            event_id: string;
            /**
             * Occurred At
             * Format: date-time
             */
            occurred_at: string;
            payload: components["schemas"]["ArtifactCreatedPayload"];
            /**
             * Run Id
             * Format: uuid
             */
            run_id: string;
            /**
             * Schema Version
             * @default 1
             * @constant
             */
            schema_version: 1;
            /** Sequence */
            sequence: string;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "artifact.created";
        };
        /** ArtifactCreatedPayload */
        ArtifactCreatedPayload: {
            /**
             * Artifact Id
             * Format: uuid
             */
            artifact_id: string;
            /** Kind */
            kind: string;
            /** Media Type */
            media_type?: string | null;
            /** Sha256 */
            sha256: string;
            /** Size Bytes */
            size_bytes: number;
        };
        /** ArtifactListResponse */
        ArtifactListResponse: {
            /**
             * Conversation Id
             * Format: uuid
             */
            conversation_id: string;
            /** Items */
            items: components["schemas"]["ArtifactRead"][];
            page: components["schemas"]["PageInfo"];
            /**
             * Schema Version
             * @default 1
             * @constant
             */
            schema_version: 1;
        };
        /** ArtifactRead */
        ArtifactRead: {
            /**
             * Artifact Id
             * Format: uuid
             */
            artifact_id: string;
            /**
             * Conversation Id
             * Format: uuid
             */
            conversation_id: string;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Kind */
            kind: string;
            /** Media Type */
            media_type?: string | null;
            /** Metadata */
            metadata?: {
                [key: string]: string | number | boolean | null;
            };
            /** Run Id */
            run_id?: string | null;
            /**
             * Schema Version
             * @default 1
             * @constant
             */
            schema_version: 1;
            /** Sha256 */
            sha256: string;
            /** Size Bytes */
            size_bytes: number;
            /** Source Event Id */
            source_event_id?: string | null;
        };
        /** Body_uploadArtifact */
        Body_uploadArtifact: {
            /**
             * File
             * @description 要导入 conversation workspace 的文件
             */
            file: Blob;
            /**
             * Kind
             * @default dataset
             */
            kind: string;
        };
        /** BudgetExhaustedEvent */
        BudgetExhaustedEvent: {
            /**
             * Conversation Id
             * Format: uuid
             */
            conversation_id: string;
            /**
             * Event Id
             * Format: uuid
             */
            event_id: string;
            /**
             * Occurred At
             * Format: date-time
             */
            occurred_at: string;
            payload: components["schemas"]["BudgetExhaustedPayload"];
            /**
             * Run Id
             * Format: uuid
             */
            run_id: string;
            /**
             * Schema Version
             * @default 1
             * @constant
             */
            schema_version: 1;
            /** Sequence */
            sequence: string;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "budget.exhausted";
        };
        /** BudgetExhaustedPayload */
        BudgetExhaustedPayload: {
            budget: components["schemas"]["BudgetKind"];
            /** Limit */
            limit: number;
            /** Unit */
            unit: string;
            /** Used */
            used: number;
        };
        /**
         * BudgetKind
         * @enum {string}
         */
        BudgetKind: "turn" | "wall_time" | "model_call" | "capability_call" | "retry";
        /** CapabilityCompletedEvent */
        CapabilityCompletedEvent: {
            /**
             * Conversation Id
             * Format: uuid
             */
            conversation_id: string;
            /**
             * Event Id
             * Format: uuid
             */
            event_id: string;
            /**
             * Occurred At
             * Format: date-time
             */
            occurred_at: string;
            payload: components["schemas"]["CapabilityCompletedPayload"];
            /**
             * Run Id
             * Format: uuid
             */
            run_id: string;
            /**
             * Schema Version
             * @default 1
             * @constant
             */
            schema_version: 1;
            /** Sequence */
            sequence: string;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "capability.completed";
        };
        /** CapabilityCompletedPayload */
        CapabilityCompletedPayload: {
            /** Artifact Ids */
            artifact_ids?: string[];
            /**
             * Attempt
             * @default 1
             */
            attempt: number;
            /**
             * Capability Call Id
             * Format: uuid
             */
            capability_call_id: string;
            /** Capability Name */
            capability_name: string;
            /** Result Status */
            result_status?: ("completed" | "aborted") | null;
            /** Summary */
            summary?: string | null;
            /** Task Id */
            task_id?: string | null;
        };
        /** CapabilityFailedEvent */
        CapabilityFailedEvent: {
            /**
             * Conversation Id
             * Format: uuid
             */
            conversation_id: string;
            /**
             * Event Id
             * Format: uuid
             */
            event_id: string;
            /**
             * Occurred At
             * Format: date-time
             */
            occurred_at: string;
            payload: components["schemas"]["CapabilityFailedPayload"];
            /**
             * Run Id
             * Format: uuid
             */
            run_id: string;
            /**
             * Schema Version
             * @default 1
             * @constant
             */
            schema_version: 1;
            /** Sequence */
            sequence: string;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "capability.failed";
        };
        /** CapabilityFailedPayload */
        CapabilityFailedPayload: {
            /**
             * Attempt
             * @default 1
             */
            attempt: number;
            /**
             * Capability Call Id
             * Format: uuid
             */
            capability_call_id: string;
            /** Capability Name */
            capability_name: string;
            /** Error Code */
            error_code: string;
            /** Error Summary */
            error_summary: string;
            /** Retryable */
            retryable: boolean;
            /** Task Id */
            task_id?: string | null;
        };
        /** CapabilityProgressEvent */
        CapabilityProgressEvent: {
            /**
             * Conversation Id
             * Format: uuid
             */
            conversation_id: string;
            /**
             * Event Id
             * Format: uuid
             */
            event_id: string;
            /**
             * Occurred At
             * Format: date-time
             */
            occurred_at: string;
            payload: components["schemas"]["CapabilityProgressPayload"];
            /**
             * Run Id
             * Format: uuid
             */
            run_id: string;
            /**
             * Schema Version
             * @default 1
             * @constant
             */
            schema_version: 1;
            /** Sequence */
            sequence: string;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "capability.progress";
        };
        /** CapabilityProgressPayload */
        CapabilityProgressPayload: {
            /**
             * Attempt
             * @default 1
             */
            attempt: number;
            /**
             * Capability Call Id
             * Format: uuid
             */
            capability_call_id: string;
            /** Capability Name */
            capability_name: string;
            /** Current */
            current: number;
            /** Message */
            message: string;
            /**
             * Stage
             * @default isolated_execution
             * @constant
             */
            stage: "isolated_execution";
            /** Task Id */
            task_id?: string | null;
            /** Total */
            total?: number | null;
        };
        /** CapabilityRetryingEvent */
        CapabilityRetryingEvent: {
            /**
             * Conversation Id
             * Format: uuid
             */
            conversation_id: string;
            /**
             * Event Id
             * Format: uuid
             */
            event_id: string;
            /**
             * Occurred At
             * Format: date-time
             */
            occurred_at: string;
            payload: components["schemas"]["CapabilityRetryingPayload"];
            /**
             * Run Id
             * Format: uuid
             */
            run_id: string;
            /**
             * Schema Version
             * @default 1
             * @constant
             */
            schema_version: 1;
            /** Sequence */
            sequence: string;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "capability.retrying";
        };
        /** CapabilityRetryingPayload */
        CapabilityRetryingPayload: {
            /**
             * Capability Call Id
             * Format: uuid
             */
            capability_call_id: string;
            /** Capability Name */
            capability_name: string;
            /** Delay Seconds */
            delay_seconds: number;
            /** Next Attempt */
            next_attempt: number;
            /** Reason */
            reason: string;
            /** Task Id */
            task_id?: string | null;
        };
        /** CapabilityStartedEvent */
        CapabilityStartedEvent: {
            /**
             * Conversation Id
             * Format: uuid
             */
            conversation_id: string;
            /**
             * Event Id
             * Format: uuid
             */
            event_id: string;
            /**
             * Occurred At
             * Format: date-time
             */
            occurred_at: string;
            payload: components["schemas"]["CapabilityStartedPayload"];
            /**
             * Run Id
             * Format: uuid
             */
            run_id: string;
            /**
             * Schema Version
             * @default 1
             * @constant
             */
            schema_version: 1;
            /** Sequence */
            sequence: string;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "capability.started";
        };
        /** CapabilityStartedPayload */
        CapabilityStartedPayload: {
            /**
             * Attempt
             * @default 1
             */
            attempt: number;
            /**
             * Capability Call Id
             * Format: uuid
             */
            capability_call_id: string;
            /** Capability Name */
            capability_name: string;
            /** Task Id */
            task_id?: string | null;
        };
        /** ConversationCreateRequest */
        ConversationCreateRequest: {
            /** Title */
            title?: string | null;
        };
        /** ConversationListResponse */
        ConversationListResponse: {
            /** Items */
            items: components["schemas"]["ConversationRead"][];
            page: components["schemas"]["PageInfo"];
            /**
             * Schema Version
             * @default 1
             * @constant
             */
            schema_version: 1;
        };
        /** ConversationRead */
        ConversationRead: {
            /**
             * Conversation Id
             * Format: uuid
             */
            conversation_id: string;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Dataset Artifact Id */
            dataset_artifact_id?: string | null;
            /**
             * Schema Version
             * @default 1
             * @constant
             */
            schema_version: 1;
            status: components["schemas"]["ConversationStatus"];
            /** Title */
            title?: string | null;
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
        };
        /**
         * ConversationStatus
         * @enum {string}
         */
        ConversationStatus: "active" | "archived";
        /** ErrorDetail */
        ErrorDetail: {
            /** Code */
            code: string;
            /** Field */
            field?: string | null;
            /** Message */
            message: string;
        };
        /** ErrorEnvelope */
        ErrorEnvelope: {
            error: components["schemas"]["ErrorInfo"];
            /**
             * Request Id
             * Format: uuid
             */
            request_id: string;
            /**
             * Schema Version
             * @default 1
             * @constant
             */
            schema_version: 1;
        };
        /** ErrorInfo */
        ErrorInfo: {
            /** Code */
            code: string;
            /** Details */
            details?: components["schemas"]["ErrorDetail"][];
            /** Message */
            message: string;
            /**
             * Retryable
             * @default false
             */
            retryable: boolean;
        };
        /** EventReplayResponse */
        EventReplayResponse: {
            /**
             * Conversation Id
             * Format: uuid
             */
            conversation_id: string;
            /** Events */
            events: (components["schemas"]["RunCreatedEvent"] | components["schemas"]["RunStartedEvent"] | components["schemas"]["AgentTurnStartedEvent"] | components["schemas"]["MessageCompletedEvent"] | components["schemas"]["TaskCreatedEvent"] | components["schemas"]["TaskUpdatedEvent"] | components["schemas"]["SkillLoadStartedEvent"] | components["schemas"]["SkillLoadCompletedEvent"] | components["schemas"]["SkillLoadFailedEvent"] | components["schemas"]["CapabilityStartedEvent"] | components["schemas"]["CapabilityCompletedEvent"] | components["schemas"]["CapabilityFailedEvent"] | components["schemas"]["CapabilityRetryingEvent"] | components["schemas"]["CapabilityProgressEvent"] | components["schemas"]["RuntimeCommandStartedEvent"] | components["schemas"]["RuntimeOutputEvent"] | components["schemas"]["RuntimeCommandCompletedEvent"] | components["schemas"]["ArtifactCreatedEvent"] | components["schemas"]["ReviewRequestedEvent"] | components["schemas"]["ReviewResolvedEvent"] | components["schemas"]["BudgetExhaustedEvent"] | components["schemas"]["RunCancelRequestedEvent"] | components["schemas"]["RunInterruptedEvent"] | components["schemas"]["RunCompletedEvent"] | components["schemas"]["RunFailedEvent"] | components["schemas"]["RunCancelledEvent"])[];
            /** Has More */
            has_more: boolean;
            /** Next Sequence */
            next_sequence: string;
            /**
             * Run Id
             * Format: uuid
             */
            run_id: string;
            /**
             * Schema Version
             * @default 1
             * @constant
             */
            schema_version: 1;
        };
        /** HealthComponentsRead */
        HealthComponentsRead: {
            api: components["schemas"]["HealthComponentStatus"];
            execution_backend: components["schemas"]["HealthComponentStatus"];
            postgres_application: components["schemas"]["HealthComponentStatus"];
            postgres_checkpointer: components["schemas"]["HealthComponentStatus"];
        };
        /**
         * HealthComponentStatus
         * @enum {string}
         */
        HealthComponentStatus: "healthy" | "unavailable";
        /** LivenessResponse */
        LivenessResponse: {
            /**
             * Schema Version
             * @default 1
             * @constant
             */
            schema_version: 1;
            /**
             * Status
             * @default alive
             * @constant
             */
            status: "alive";
        };
        /** MessageCompletedEvent */
        MessageCompletedEvent: {
            /**
             * Conversation Id
             * Format: uuid
             */
            conversation_id: string;
            /**
             * Event Id
             * Format: uuid
             */
            event_id: string;
            /**
             * Occurred At
             * Format: date-time
             */
            occurred_at: string;
            payload: components["schemas"]["MessageCompletedPayload"];
            /**
             * Run Id
             * Format: uuid
             */
            run_id: string;
            /**
             * Schema Version
             * @default 1
             * @constant
             */
            schema_version: 1;
            /** Sequence */
            sequence: string;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "message.completed";
        };
        /** MessageCompletedPayload */
        MessageCompletedPayload: {
            /** Content */
            content: string;
            /** Content Artifact Id */
            content_artifact_id?: string | null;
            /**
             * Has Tool Calls
             * @default false
             */
            has_tool_calls: boolean;
            /**
             * Message Id
             * Format: uuid
             */
            message_id: string;
            role: components["schemas"]["MessageRole"];
            /** Stop Reason */
            stop_reason?: string | null;
            /** Turn Index */
            turn_index?: number | null;
        };
        /**
         * MessageRole
         * @enum {string}
         */
        MessageRole: "user" | "assistant" | "system" | "tool";
        /** PageInfo */
        PageInfo: {
            /** Has More */
            has_more: boolean;
            /** Next Cursor */
            next_cursor?: string | null;
        };
        /** ReadinessResponse */
        ReadinessResponse: {
            components: components["schemas"]["HealthComponentsRead"];
            /** Ready */
            ready: boolean;
            /**
             * Schema Version
             * @default 1
             * @constant
             */
            schema_version: 1;
        };
        /**
         * ReviewDecision
         * @enum {string}
         */
        ReviewDecision: "approve" | "reject";
        /** ReviewDecisionRequest */
        ReviewDecisionRequest: {
            /** Comment */
            comment?: string | null;
            decision: components["schemas"]["ReviewDecision"];
        };
        /** ReviewDecisionResponse */
        ReviewDecisionResponse: {
            review: components["schemas"]["ReviewRead"];
            run: components["schemas"]["RunRead"];
            /**
             * Schema Version
             * @default 1
             * @constant
             */
            schema_version: 1;
        };
        /** ReviewListResponse */
        ReviewListResponse: {
            /**
             * Conversation Id
             * Format: uuid
             */
            conversation_id: string;
            /** Items */
            items: components["schemas"]["ReviewRead"][];
            page: components["schemas"]["PageInfo"];
            /**
             * Schema Version
             * @default 1
             * @constant
             */
            schema_version: 1;
        };
        /** ReviewRead */
        ReviewRead: {
            /** Comment */
            comment?: string | null;
            /**
             * Conversation Id
             * Format: uuid
             */
            conversation_id: string;
            decision?: components["schemas"]["ReviewDecision"] | null;
            /** Prompt */
            prompt: string;
            /**
             * Requested At
             * Format: date-time
             */
            requested_at: string;
            /** Resolved At */
            resolved_at?: string | null;
            /**
             * Review Id
             * Format: uuid
             */
            review_id: string;
            /**
             * Run Id
             * Format: uuid
             */
            run_id: string;
            /**
             * Schema Version
             * @default 1
             * @constant
             */
            schema_version: 1;
            status: components["schemas"]["ReviewStatus"];
            /** Task Id */
            task_id?: string | null;
        };
        /** ReviewRequestedEvent */
        ReviewRequestedEvent: {
            /**
             * Conversation Id
             * Format: uuid
             */
            conversation_id: string;
            /**
             * Event Id
             * Format: uuid
             */
            event_id: string;
            /**
             * Occurred At
             * Format: date-time
             */
            occurred_at: string;
            payload: components["schemas"]["ReviewRequestedPayload"];
            /**
             * Run Id
             * Format: uuid
             */
            run_id: string;
            /**
             * Schema Version
             * @default 1
             * @constant
             */
            schema_version: 1;
            /** Sequence */
            sequence: string;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "review.requested";
        };
        /** ReviewRequestedPayload */
        ReviewRequestedPayload: {
            /** Prompt */
            prompt: string;
            /**
             * Review Id
             * Format: uuid
             */
            review_id: string;
            /**
             * Status
             * @default pending
             * @constant
             */
            status: "pending";
            /** Task Id */
            task_id?: string | null;
        };
        /** ReviewResolvedEvent */
        ReviewResolvedEvent: {
            /**
             * Conversation Id
             * Format: uuid
             */
            conversation_id: string;
            /**
             * Event Id
             * Format: uuid
             */
            event_id: string;
            /**
             * Occurred At
             * Format: date-time
             */
            occurred_at: string;
            payload: components["schemas"]["ReviewResolvedPayload"];
            /**
             * Run Id
             * Format: uuid
             */
            run_id: string;
            /**
             * Schema Version
             * @default 1
             * @constant
             */
            schema_version: 1;
            /** Sequence */
            sequence: string;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "review.resolved";
        };
        /** ReviewResolvedPayload */
        ReviewResolvedPayload: {
            /** Comment */
            comment?: string | null;
            decision?: components["schemas"]["ReviewDecision"] | null;
            /**
             * Review Id
             * Format: uuid
             */
            review_id: string;
            /**
             * Status
             * @enum {string}
             */
            status: "approved" | "rejected" | "cancelled";
        };
        /**
         * ReviewStatus
         * @enum {string}
         */
        ReviewStatus: "pending" | "approved" | "rejected" | "cancelled";
        /** RunCancelledEvent */
        RunCancelledEvent: {
            /**
             * Conversation Id
             * Format: uuid
             */
            conversation_id: string;
            /**
             * Event Id
             * Format: uuid
             */
            event_id: string;
            /**
             * Occurred At
             * Format: date-time
             */
            occurred_at: string;
            payload: components["schemas"]["RunCancelledPayload"];
            /**
             * Run Id
             * Format: uuid
             */
            run_id: string;
            /**
             * Schema Version
             * @default 1
             * @constant
             */
            schema_version: 1;
            /** Sequence */
            sequence: string;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "run.cancelled";
        };
        /** RunCancelledPayload */
        RunCancelledPayload: {
            /** Reason */
            reason?: string | null;
            /**
             * Status
             * @default cancelled
             * @constant
             */
            status: "cancelled";
        };
        /** RunCancelRequest */
        RunCancelRequest: {
            /** Reason */
            reason?: string | null;
        };
        /** RunCancelRequestedEvent */
        RunCancelRequestedEvent: {
            /**
             * Conversation Id
             * Format: uuid
             */
            conversation_id: string;
            /**
             * Event Id
             * Format: uuid
             */
            event_id: string;
            /**
             * Occurred At
             * Format: date-time
             */
            occurred_at: string;
            payload: components["schemas"]["RunCancelRequestedPayload"];
            /**
             * Run Id
             * Format: uuid
             */
            run_id: string;
            /**
             * Schema Version
             * @default 1
             * @constant
             */
            schema_version: 1;
            /** Sequence */
            sequence: string;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "run.cancel_requested";
        };
        /** RunCancelRequestedPayload */
        RunCancelRequestedPayload: {
            /** Reason */
            reason?: string | null;
            /**
             * Status
             * @default cancelling
             * @constant
             */
            status: "cancelling";
        };
        /** RunCancelResponse */
        RunCancelResponse: {
            /** Accepted */
            accepted: boolean;
            run: components["schemas"]["RunRead"];
            /**
             * Schema Version
             * @default 1
             * @constant
             */
            schema_version: 1;
        };
        /** RunCompletedEvent */
        RunCompletedEvent: {
            /**
             * Conversation Id
             * Format: uuid
             */
            conversation_id: string;
            /**
             * Event Id
             * Format: uuid
             */
            event_id: string;
            /**
             * Occurred At
             * Format: date-time
             */
            occurred_at: string;
            payload: components["schemas"]["RunCompletedPayload"];
            /**
             * Run Id
             * Format: uuid
             */
            run_id: string;
            /**
             * Schema Version
             * @default 1
             * @constant
             */
            schema_version: 1;
            /** Sequence */
            sequence: string;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "run.completed";
        };
        /** RunCompletedPayload */
        RunCompletedPayload: {
            /** Artifact Ids */
            artifact_ids?: string[];
            /** Final Message Id */
            final_message_id?: string | null;
            /**
             * Status
             * @default completed
             * @constant
             */
            status: "completed";
        };
        /** RunCreatedEvent */
        RunCreatedEvent: {
            /**
             * Conversation Id
             * Format: uuid
             */
            conversation_id: string;
            /**
             * Event Id
             * Format: uuid
             */
            event_id: string;
            /**
             * Occurred At
             * Format: date-time
             */
            occurred_at: string;
            payload: components["schemas"]["RunCreatedPayload"];
            /**
             * Run Id
             * Format: uuid
             */
            run_id: string;
            /**
             * Schema Version
             * @default 1
             * @constant
             */
            schema_version: 1;
            /** Sequence */
            sequence: string;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "run.created";
        };
        /** RunCreatedPayload */
        RunCreatedPayload: {
            /**
             * Status
             * @default pending
             * @constant
             */
            status: "pending";
        };
        /** RunCreateRequest */
        RunCreateRequest: {
            /** Goal */
            goal: string;
            /** Input Artifact Ids */
            input_artifact_ids?: string[];
            /** Request Key */
            request_key?: string | null;
        };
        /** RunCreateResponse */
        RunCreateResponse: {
            run: components["schemas"]["RunRead"];
            /**
             * Schema Version
             * @default 1
             * @constant
             */
            schema_version: 1;
        };
        /** RunFailedEvent */
        RunFailedEvent: {
            /**
             * Conversation Id
             * Format: uuid
             */
            conversation_id: string;
            /**
             * Event Id
             * Format: uuid
             */
            event_id: string;
            /**
             * Occurred At
             * Format: date-time
             */
            occurred_at: string;
            payload: components["schemas"]["RunFailedPayload"];
            /**
             * Run Id
             * Format: uuid
             */
            run_id: string;
            /**
             * Schema Version
             * @default 1
             * @constant
             */
            schema_version: 1;
            /** Sequence */
            sequence: string;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "run.failed";
        };
        /** RunFailedPayload */
        RunFailedPayload: {
            /** Error Code */
            error_code: string;
            /** Error Summary */
            error_summary: string;
            /**
             * Retryable
             * @default false
             */
            retryable: boolean;
            /**
             * Status
             * @default failed
             * @constant
             */
            status: "failed";
        };
        /** RunHistoryResponse */
        RunHistoryResponse: {
            /**
             * Conversation Id
             * Format: uuid
             */
            conversation_id: string;
            /**
             * Items
             * @description 当前分页内从最新到最旧的 run。
             */
            items: components["schemas"]["RunRead"][];
            /**
             * Order
             * @description items 按 created_at 降序排列；时间相同时按 run_id 降序排列。分页 cursor 延续相同顺序。
             * @constant
             */
            order: "newest_first";
            page: components["schemas"]["PageInfo"];
            /**
             * Schema Version
             * @default 1
             * @constant
             */
            schema_version: 1;
        };
        /** RunInterruptedEvent */
        RunInterruptedEvent: {
            /**
             * Conversation Id
             * Format: uuid
             */
            conversation_id: string;
            /**
             * Event Id
             * Format: uuid
             */
            event_id: string;
            /**
             * Occurred At
             * Format: date-time
             */
            occurred_at: string;
            payload: components["schemas"]["RunInterruptedPayload"];
            /**
             * Run Id
             * Format: uuid
             */
            run_id: string;
            /**
             * Schema Version
             * @default 1
             * @constant
             */
            schema_version: 1;
            /** Sequence */
            sequence: string;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "run.interrupted";
        };
        /** RunInterruptedPayload */
        RunInterruptedPayload: {
            /** Reason */
            reason: string;
            /**
             * Resumable
             * @default true
             * @constant
             */
            resumable: true;
            /** Review Id */
            review_id?: string | null;
            /**
             * Status
             * @default review_required
             * @constant
             */
            status: "review_required";
        };
        /** RunRead */
        RunRead: {
            /** Completed At */
            completed_at?: string | null;
            /**
             * Conversation Id
             * Format: uuid
             */
            conversation_id: string;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Error Summary */
            error_summary?: string | null;
            /** Last Sequence */
            last_sequence: string;
            /**
             * Run Id
             * Format: uuid
             */
            run_id: string;
            /**
             * Schema Version
             * @default 1
             * @constant
             */
            schema_version: 1;
            /** Started At */
            started_at?: string | null;
            status: components["schemas"]["RunStatus"];
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
        };
        /** RunResumeRequest */
        RunResumeRequest: {
            /** Review Id */
            review_id?: string | null;
        };
        /** RunResumeResponse */
        RunResumeResponse: {
            /** Accepted */
            accepted: boolean;
            run: components["schemas"]["RunRead"];
            /**
             * Schema Version
             * @default 1
             * @constant
             */
            schema_version: 1;
        };
        /** RunStartedEvent */
        RunStartedEvent: {
            /**
             * Conversation Id
             * Format: uuid
             */
            conversation_id: string;
            /**
             * Event Id
             * Format: uuid
             */
            event_id: string;
            /**
             * Occurred At
             * Format: date-time
             */
            occurred_at: string;
            payload: components["schemas"]["RunStartedPayload"];
            /**
             * Run Id
             * Format: uuid
             */
            run_id: string;
            /**
             * Schema Version
             * @default 1
             * @constant
             */
            schema_version: 1;
            /** Sequence */
            sequence: string;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "run.started";
        };
        /** RunStartedPayload */
        RunStartedPayload: {
            /**
             * Status
             * @default running
             * @constant
             */
            status: "running";
        };
        /**
         * RunStatus
         * @enum {string}
         */
        RunStatus: "pending" | "running" | "review_required" | "cancelling" | "completed" | "failed" | "cancelled";
        /** RuntimeCommandCompletedEvent */
        RuntimeCommandCompletedEvent: {
            /**
             * Conversation Id
             * Format: uuid
             */
            conversation_id: string;
            /**
             * Event Id
             * Format: uuid
             */
            event_id: string;
            /**
             * Occurred At
             * Format: date-time
             */
            occurred_at: string;
            payload: components["schemas"]["RuntimeCommandCompletedPayload"];
            /**
             * Run Id
             * Format: uuid
             */
            run_id: string;
            /**
             * Schema Version
             * @default 1
             * @constant
             */
            schema_version: 1;
            /** Sequence */
            sequence: string;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "runtime.command_completed";
        };
        /** RuntimeCommandCompletedPayload */
        RuntimeCommandCompletedPayload: {
            /**
             * Attempt
             * @default 1
             */
            attempt: number;
            /**
             * Capability Call Id
             * Format: uuid
             */
            capability_call_id: string;
            /** Capability Name */
            capability_name: string;
            /** Duration Ms */
            duration_ms: number;
            /** Exit Code */
            exit_code?: number | null;
            /**
             * Outcome
             * @enum {string}
             */
            outcome: "completed" | "failed" | "timeout" | "cancelled";
            /**
             * Redacted
             * @default false
             */
            redacted: boolean;
            /**
             * Runtime Command Id
             * Format: uuid
             */
            runtime_command_id: string;
            /** Stderr Observed Bytes */
            stderr_observed_bytes: number;
            /** Stderr Published Bytes */
            stderr_published_bytes: number;
            /**
             * Stderr Truncated
             * @default false
             */
            stderr_truncated: boolean;
            /** Stdout Observed Bytes */
            stdout_observed_bytes: number;
            /** Stdout Published Bytes */
            stdout_published_bytes: number;
            /**
             * Stdout Truncated
             * @default false
             */
            stdout_truncated: boolean;
            /** Task Id */
            task_id?: string | null;
        };
        /** RuntimeCommandStartedEvent */
        RuntimeCommandStartedEvent: {
            /**
             * Conversation Id
             * Format: uuid
             */
            conversation_id: string;
            /**
             * Event Id
             * Format: uuid
             */
            event_id: string;
            /**
             * Occurred At
             * Format: date-time
             */
            occurred_at: string;
            payload: components["schemas"]["RuntimeCommandStartedPayload"];
            /**
             * Run Id
             * Format: uuid
             */
            run_id: string;
            /**
             * Schema Version
             * @default 1
             * @constant
             */
            schema_version: 1;
            /** Sequence */
            sequence: string;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "runtime.command_started";
        };
        /** RuntimeCommandStartedPayload */
        RuntimeCommandStartedPayload: {
            /**
             * Attempt
             * @default 1
             */
            attempt: number;
            /** Backend */
            backend: string;
            /**
             * Capability Call Id
             * Format: uuid
             */
            capability_call_id: string;
            /** Capability Name */
            capability_name: string;
            /** Code */
            code?: string | null;
            /** Command */
            command: string[];
            /**
             * Command Truncated
             * @default false
             */
            command_truncated: boolean;
            /**
             * Redacted
             * @default false
             */
            redacted: boolean;
            /**
             * Runtime Command Id
             * Format: uuid
             */
            runtime_command_id: string;
            /** Task Id */
            task_id?: string | null;
            /** Workdir */
            workdir: string;
        };
        /** RuntimeOutputEvent */
        RuntimeOutputEvent: {
            /**
             * Conversation Id
             * Format: uuid
             */
            conversation_id: string;
            /**
             * Event Id
             * Format: uuid
             */
            event_id: string;
            /**
             * Occurred At
             * Format: date-time
             */
            occurred_at: string;
            payload: components["schemas"]["RuntimeOutputPayload"];
            /**
             * Run Id
             * Format: uuid
             */
            run_id: string;
            /**
             * Schema Version
             * @default 1
             * @constant
             */
            schema_version: 1;
            /** Sequence */
            sequence: string;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "runtime.output";
        };
        /** RuntimeOutputPayload */
        RuntimeOutputPayload: {
            /**
             * Attempt
             * @default 1
             */
            attempt: number;
            /**
             * Capability Call Id
             * Format: uuid
             */
            capability_call_id: string;
            /** Capability Name */
            capability_name: string;
            /** Chunk */
            chunk: string;
            /**
             * Encoding
             * @default utf8
             * @enum {string}
             */
            encoding: "utf8" | "utf8_replacement";
            /** Index */
            index: number;
            /**
             * Redacted
             * @default false
             */
            redacted: boolean;
            /**
             * Runtime Command Id
             * Format: uuid
             */
            runtime_command_id: string;
            /**
             * Stream
             * @enum {string}
             */
            stream: "stdout" | "stderr";
            /** Task Id */
            task_id?: string | null;
            /**
             * Truncated
             * @default false
             */
            truncated: boolean;
        };
        /** SkillLoadCompletedEvent */
        SkillLoadCompletedEvent: {
            /**
             * Conversation Id
             * Format: uuid
             */
            conversation_id: string;
            /**
             * Event Id
             * Format: uuid
             */
            event_id: string;
            /**
             * Occurred At
             * Format: date-time
             */
            occurred_at: string;
            payload: components["schemas"]["SkillLoadCompletedPayload"];
            /**
             * Run Id
             * Format: uuid
             */
            run_id: string;
            /**
             * Schema Version
             * @default 1
             * @constant
             */
            schema_version: 1;
            /** Sequence */
            sequence: string;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "skill.load_completed";
        };
        /** SkillLoadCompletedPayload */
        SkillLoadCompletedPayload: {
            /** Content Bytes */
            content_bytes: number;
            /**
             * Outcome
             * @enum {string}
             */
            outcome: "loaded" | "already_loaded";
            /**
             * Purpose
             * @enum {string}
             */
            purpose: "domain_method" | "validation_rules" | "workflow_guidance" | "reference_lookup" | "example_lookup";
            /**
             * Resource Kind
             * @enum {string}
             */
            resource_kind: "body" | "reference" | "example";
            /** Resource Name */
            resource_name?: string | null;
            /**
             * Skill Load Id
             * Format: uuid
             */
            skill_load_id: string;
            /** Skill Name */
            skill_name: string;
        };
        /** SkillLoadFailedEvent */
        SkillLoadFailedEvent: {
            /**
             * Conversation Id
             * Format: uuid
             */
            conversation_id: string;
            /**
             * Event Id
             * Format: uuid
             */
            event_id: string;
            /**
             * Occurred At
             * Format: date-time
             */
            occurred_at: string;
            payload: components["schemas"]["SkillLoadFailedPayload"];
            /**
             * Run Id
             * Format: uuid
             */
            run_id: string;
            /**
             * Schema Version
             * @default 1
             * @constant
             */
            schema_version: 1;
            /** Sequence */
            sequence: string;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "skill.load_failed";
        };
        /** SkillLoadFailedPayload */
        SkillLoadFailedPayload: {
            /**
             * Error Code
             * @constant
             */
            error_code: "skill_resource_unavailable";
            /** Error Summary */
            error_summary: string;
            /**
             * Purpose
             * @enum {string}
             */
            purpose: "domain_method" | "validation_rules" | "workflow_guidance" | "reference_lookup" | "example_lookup";
            /**
             * Resource Kind
             * @enum {string}
             */
            resource_kind: "body" | "reference" | "example";
            /** Resource Name */
            resource_name?: string | null;
            /**
             * Skill Load Id
             * Format: uuid
             */
            skill_load_id: string;
            /** Skill Name */
            skill_name: string;
        };
        /** SkillLoadStartedEvent */
        SkillLoadStartedEvent: {
            /**
             * Conversation Id
             * Format: uuid
             */
            conversation_id: string;
            /**
             * Event Id
             * Format: uuid
             */
            event_id: string;
            /**
             * Occurred At
             * Format: date-time
             */
            occurred_at: string;
            payload: components["schemas"]["SkillLoadStartedPayload"];
            /**
             * Run Id
             * Format: uuid
             */
            run_id: string;
            /**
             * Schema Version
             * @default 1
             * @constant
             */
            schema_version: 1;
            /** Sequence */
            sequence: string;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "skill.load_started";
        };
        /** SkillLoadStartedPayload */
        SkillLoadStartedPayload: {
            /**
             * Purpose
             * @enum {string}
             */
            purpose: "domain_method" | "validation_rules" | "workflow_guidance" | "reference_lookup" | "example_lookup";
            /**
             * Resource Kind
             * @enum {string}
             */
            resource_kind: "body" | "reference" | "example";
            /** Resource Name */
            resource_name?: string | null;
            /**
             * Skill Load Id
             * Format: uuid
             */
            skill_load_id: string;
            /** Skill Name */
            skill_name: string;
        };
        /** TaskCreatedEvent */
        TaskCreatedEvent: {
            /**
             * Conversation Id
             * Format: uuid
             */
            conversation_id: string;
            /**
             * Event Id
             * Format: uuid
             */
            event_id: string;
            /**
             * Occurred At
             * Format: date-time
             */
            occurred_at: string;
            payload: components["schemas"]["TaskCreatedPayload"];
            /**
             * Run Id
             * Format: uuid
             */
            run_id: string;
            /**
             * Schema Version
             * @default 1
             * @constant
             */
            schema_version: 1;
            /** Sequence */
            sequence: string;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "task.created";
        };
        /** TaskCreatedPayload */
        TaskCreatedPayload: {
            /** Capability Name */
            capability_name?: string | null;
            /** Description */
            description?: string | null;
            /**
             * Status
             * @default pending
             * @constant
             */
            status: "pending";
            /**
             * Task Id
             * Format: uuid
             */
            task_id: string;
            /** Title */
            title: string;
        };
        /**
         * TaskStatus
         * @enum {string}
         */
        TaskStatus: "pending" | "in_progress" | "completed" | "failed" | "cancelled";
        /** TaskUpdatedEvent */
        TaskUpdatedEvent: {
            /**
             * Conversation Id
             * Format: uuid
             */
            conversation_id: string;
            /**
             * Event Id
             * Format: uuid
             */
            event_id: string;
            /**
             * Occurred At
             * Format: date-time
             */
            occurred_at: string;
            payload: components["schemas"]["TaskUpdatedPayload"];
            /**
             * Run Id
             * Format: uuid
             */
            run_id: string;
            /**
             * Schema Version
             * @default 1
             * @constant
             */
            schema_version: 1;
            /** Sequence */
            sequence: string;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "task.updated";
        };
        /** TaskUpdatedPayload */
        TaskUpdatedPayload: {
            status: components["schemas"]["TaskStatus"];
            /** Summary */
            summary?: string | null;
            /**
             * Task Id
             * Format: uuid
             */
            task_id: string;
        };
    };
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    getArtifact: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                artifact_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ArtifactRead"];
                };
            };
            /** @description 请求的资源不存在。 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 请求参数不符合 API 契约。 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    getArtifactContent: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                artifact_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description 返回经 workspace 边界校验的 artifact 内容。 */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/octet-stream": unknown;
                };
            };
            /** @description 请求语义或游标非法。 */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 请求的资源不存在。 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 请求参数不符合 API 契约。 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    listConversations: {
        parameters: {
            query?: {
                cursor?: string | null;
                limit?: number;
                status?: components["schemas"]["ConversationStatus"] | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ConversationListResponse"];
                };
            };
            /** @description 请求语义或游标非法。 */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 请求参数不符合 API 契约。 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    createConversation: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ConversationCreateRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ConversationRead"];
                };
            };
            /** @description 请求参数不符合 API 契约。 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    getConversation: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                conversation_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ConversationRead"];
                };
            };
            /** @description 请求的资源不存在。 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 请求参数不符合 API 契约。 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    listArtifacts: {
        parameters: {
            query?: {
                cursor?: string | null;
                kind?: string | null;
                limit?: number;
                run_id?: string | null;
            };
            header?: never;
            path: {
                conversation_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ArtifactListResponse"];
                };
            };
            /** @description 请求语义或游标非法。 */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 请求的资源不存在。 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 请求参数不符合 API 契约。 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    uploadArtifact: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                conversation_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "multipart/form-data": components["schemas"]["Body_uploadArtifact"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ArtifactRead"];
                };
            };
            /** @description 请求语义或游标非法。 */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 请求的资源不存在。 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 上传内容超过服务端上限。 */
            413: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 请求参数不符合 API 契约。 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    getConversationHistory: {
        parameters: {
            query?: {
                cursor?: string | null;
                limit?: number;
            };
            header?: never;
            path: {
                conversation_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RunHistoryResponse"];
                };
            };
            /** @description 请求语义或游标非法。 */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 请求的资源不存在。 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 请求参数不符合 API 契约。 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    listReviews: {
        parameters: {
            query?: {
                cursor?: string | null;
                limit?: number;
                run_id?: string | null;
                status?: components["schemas"]["ReviewStatus"] | null;
            };
            header?: never;
            path: {
                conversation_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ReviewListResponse"];
                };
            };
            /** @description 请求语义或游标非法。 */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 请求的资源不存在。 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 请求参数不符合 API 契约。 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    createRun: {
        parameters: {
            query?: never;
            header?: {
                "Idempotency-Key"?: string | null;
            };
            path: {
                conversation_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RunCreateRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RunCreateResponse"];
                };
            };
            /** @description 请求语义或游标非法。 */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 请求的资源不存在。 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 请求与当前生命周期状态冲突。 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 请求参数不符合 API 契约。 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    getLiveness: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["LivenessResponse"];
                };
            };
            /** @description 请求参数不符合 API 契约。 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    getReadiness: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ReadinessResponse"];
                };
            };
            /** @description 请求参数不符合 API 契约。 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 至少一个必要依赖尚未就绪。 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ReadinessResponse"];
                };
            };
        };
    };
    decideReview: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                review_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ReviewDecisionRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ReviewDecisionResponse"];
                };
            };
            /** @description 请求的资源不存在。 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 请求与当前生命周期状态冲突。 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 请求参数不符合 API 契约。 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    getRun: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                run_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RunRead"];
                };
            };
            /** @description 请求的资源不存在。 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 请求参数不符合 API 契约。 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    cancelRun: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                run_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RunCancelRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RunCancelResponse"];
                };
            };
            /** @description 请求的资源不存在。 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 请求参数不符合 API 契约。 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    replayRunEvents: {
        parameters: {
            query?: {
                after_sequence?: string;
                limit?: number;
            };
            header?: never;
            path: {
                run_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["EventReplayResponse"];
                };
            };
            /** @description 请求的资源不存在。 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 请求参数不符合 API 契约。 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    streamRunEvents: {
        parameters: {
            query?: {
                after_sequence?: string | null;
            };
            header?: {
                "Last-Event-ID"?: string | null;
            };
            path: {
                run_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Replay-first run event stream; disconnect does not cancel the run. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    /**
                     * @example id: 1
                     *     event: run.created
                     *     data: {...}
                     */
                    "text/event-stream": unknown;
                };
            };
            /** @description 请求语义或游标非法。 */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 请求的资源不存在。 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 请求参数不符合 API 契约。 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    resumeRun: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                run_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RunResumeRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RunResumeResponse"];
                };
            };
            /** @description 请求的资源不存在。 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 请求与当前生命周期状态冲突。 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 请求参数不符合 API 契约。 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
}
