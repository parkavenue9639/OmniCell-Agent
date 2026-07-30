import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ConversationWorkspace } from "./ConversationWorkspace";
import type { ConversationWorkspaceViewModel } from "./view-model";

const readyModel: ConversationWorkspaceViewModel = {
  viewState: "ready",
  connection: "reconnecting",
  connectionLabel: "正在重连",
  conversations: [
    {
      id: "conversation-1",
      title: "PBMC 免疫图谱",
      updatedAtLabel: "刚刚",
      runState: "review_required",
    },
  ],
  selectedConversationId: "conversation-1",
  datasets: [
    {
      artifactId: "dataset-1",
      name: "pbmc.h5ad",
      detail: "18,264 cells",
      sizeLabel: "42 MB",
    },
  ],
  selectedDatasetId: "dataset-1",
  title: "PBMC 免疫图谱",
  subtitle: "基于当前数据集的可恢复分析对话",
  run: {
    id: "run-1",
    state: "review_required",
    stateLabel: "等待审核",
    canCancel: true,
  },
  timeline: [
    {
      id: "message-1",
      kind: "message",
      role: "user",
      authorLabel: "你",
      content: "完成细胞分析并给出注释。",
      occurredAtLabel: "10:00",
    },
    {
      id: "tool-a",
      kind: "tool",
      toolName: "cluster_cells",
      family: "analyze",
      title: "降维与细胞聚类",
      purpose: "执行 PCA、邻居图与 Leiden 聚类",
      state: "completed",
      stateLabel: "调用完成",
      attempt: 1,
      process: [
        { label: "发起 Tool 调用", state: "completed" },
        { label: "Tool 返回结果", state: "completed" },
      ],
      resultSummary: "生成 11 个 cluster",
      artifactCount: 2,
      occurredAtLabel: "10:01",
    },
    {
      id: "skill-load-1",
      kind: "skill",
      skillName: "pca-clustering",
      resourceLabel: "Skill 正文",
      purposeLabel: "加载领域方法",
      state: "completed",
      stateLabel: "已加载",
      process: [
        { label: "读取 Skill 正文", state: "completed" },
        { label: "更新当前 Run 方法上下文", state: "completed" },
      ],
      resultSummary: "已加载 2.0 KiB 方法上下文",
      occurredAtLabel: "10:02",
    },
    {
      id: "tool-b",
      kind: "tool",
      toolName: "annotate_cell_clusters",
      family: "annotate",
      title: "细胞类型注释",
      purpose: "完成 cluster 注释、验证与评分",
      state: "review_required",
      stateLabel: "等待审核",
      attempt: 1,
      process: [
        { label: "发起 Tool 调用", state: "completed" },
        { label: "等待人工审核", state: "pending" },
      ],
      artifactCount: 0,
      occurredAtLabel: "10:03",
    },
    {
      id: "review-1",
      kind: "review",
      reviewId: "review-1",
      title: "确认继续深度注释",
      description: "该 Tool 需要人工确认。",
      state: "pending",
      decisionPending: false,
      occurredAtLabel: "10:04",
    },
  ],
  tasks: [
    {
      id: "task-1",
      runId: "run-1",
      title: "生成 marker",
      state: "completed",
      stateLabel: "已完成",
    },
  ],
  toolExecutions: [
    {
      id: "ca",
      runId: "run-1",
      name: "cluster_cells",
      family: "analyze",
      title: "降维与细胞聚类",
      description: "11 个 cluster",
      state: "completed",
      stateLabel: "已完成",
    },
    {
      id: "cb",
      runId: "run-1",
      name: "annotate_cell_clusters",
      family: "annotate",
      title: "细胞类型注释",
      description: "等待人工审核",
      state: "review_required",
      stateLabel: "等待审核",
    },
  ],
  reviews: [
    {
      id: "review-1",
      runId: "run-1",
      title: "继续执行细胞注释",
      description: "检查输入后决定。",
      capabilityLabel: "annotate_cell_clusters",
      state: "pending",
      decisionPending: false,
    },
  ],
  artifacts: [
    {
      id: "artifact-1",
      runId: "run-1",
      name: "markers.json",
      kindLabel: "Marker",
      sizeLabel: "12 KB",
      createdAtLabel: "10:03",
      canDownload: true,
      downloadPending: false,
    },
  ],
  events: [
    {
      id: "event-1",
      runId: "run-1",
      sequence: "9007199254740993",
      type: "review.requested",
      occurredAtLabel: "10:04",
      occurredAtIso: "2026-07-23T10:04:00Z",
      summary: "等待审核",
      context: "pending",
      tone: "warning",
      metadata: [
        { label: "event_id", value: "event-1" },
        { label: "review_id", value: "review-1" },
      ],
    },
  ],
  memory: {
    available: true,
    loading: false,
    useMemory: false,
    generateCandidates: false,
    enableAgentTools: false,
    providerConsentGranted: false,
    items: [],
    commandsPending: false,
  },
  commands: {
    createConversationPending: false,
    importDatasetPending: false,
    cancelRunPending: false,
  },
  composer: { placeholder: "继续提出分析目标…", disabled: false },
};

describe("ConversationWorkspace", () => {
  it("renders authoritative states and delegates user actions", () => {
    const onReviewDecision = vi.fn();
    const onCancelRun = vi.fn();
    const onSubmit = vi.fn().mockResolvedValue(true);
    const onDownloadArtifact = vi.fn();
    render(
      <ConversationWorkspace
        model={readyModel}
        actions={{
          onReviewDecision,
          onCancelRun,
          onSubmit,
          onDownloadArtifact,
        }}
      />,
    );

    expect(screen.getByText("正在恢复事件连接")).toBeInTheDocument();
    expect(screen.getByText("cluster_cells")).toBeInTheDocument();
    expect(screen.getAllByText("annotate_cell_clusters").length).toBeGreaterThan(0);
    expect(screen.getAllByText("TOOL")).toHaveLength(2);
    expect(screen.getByText("SKILL")).toBeInTheDocument();
    expect(screen.getByText("pca-clustering")).toBeInTheDocument();
    expect(screen.getByText("加载领域方法")).toBeInTheDocument();
    expect(screen.getByText("生成 11 个 cluster")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "批准并继续" }));
    expect(onReviewDecision).toHaveBeenCalledWith("review-1", "approve");

    fireEvent.click(screen.getByRole("button", { name: "取消运行" }));
    expect(onCancelRun).toHaveBeenCalledWith("run-1");

    fireEvent.change(screen.getByRole("textbox", { name: "分析指令" }), {
      target: { value: "比较不同 cluster" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送分析指令" }));
    expect(onSubmit).toHaveBeenCalledWith("比较不同 cluster", {
      mode: "off",
      refs: [],
    });

    fireEvent.click(screen.getByRole("tab", { name: /产物/ }));
    fireEvent.click(screen.getByRole("button", { name: "下载 markers.json" }));
    expect(onDownloadArtifact).toHaveBeenCalledWith(
      "artifact-1",
      "markers.json",
    );

    fireEvent.click(screen.getByRole("tab", { name: /事件/ }));
    fireEvent.click(screen.getByText("metadata · 2"));
    expect(screen.getByText("review_id")).toBeInTheDocument();
    expect(screen.getByText("review-1")).toBeInTheDocument();
  });

  it("shows loading, empty and error presentation without deriving a run state", () => {
    const { rerender } = render(
      <ConversationWorkspace
        model={{ ...readyModel, viewState: "loading", timeline: [] }}
      />,
    );
    expect(screen.getByLabelText("正在加载 conversation")).toBeInTheDocument();

    rerender(
      <ConversationWorkspace
        model={{
          ...readyModel,
          viewState: "empty",
          run: undefined,
          timeline: [],
        }}
      />,
    );
    expect(screen.getByText("从一个明确的分析目标开始")).toBeInTheDocument();

    rerender(
      <ConversationWorkspace
        model={{
          ...readyModel,
          viewState: "error",
          errorMessage: "无法读取事件",
          timeline: [],
        }}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("无法读取事件");
  });

  it("renders bounded assistant markdown without injecting raw HTML", () => {
    render(
      <ConversationWorkspace
        model={{
          ...readyModel,
          timeline: [
            {
              id: "assistant-result",
              kind: "message",
              role: "assistant",
              authorLabel: "OmniCell Agent",
              content:
                "**总聚类数**: 10\n- 注释产物: `artifact-1`\n<script>unsafe</script>",
              occurredAtLabel: "10:05",
            },
          ],
        }}
      />,
    );

    expect(screen.getByText("总聚类数").tagName).toBe("STRONG");
    expect(screen.getByText("artifact-1").tagName).toBe("CODE");
    expect(screen.getByText("<script>unsafe</script>")).toBeInTheDocument();
    expect(document.querySelector("script")).toBeNull();
  });

  it("renders plain-language memory activities with inline confirmation and no plaintext body", () => {
    const onApproveMemory = vi.fn().mockResolvedValue(true);
    const onForgetMemory = vi.fn().mockResolvedValue(true);
    const onPurgeMemory = vi.fn().mockResolvedValue(true);
    const proposalContent = `第一行偏好。\n${"长期内容".repeat(80)}\n最后一行。`;
    const identityWithIgnoredBody = {
      itemId: "memory-proposal",
      versionId: "version-proposal",
      version: 1,
      kind: "project_context" as const,
      source: "proposed",
      reason: "tool_search",
      body: "PRIVATE MEMORY BODY",
    };
    render(
      <ConversationWorkspace
        model={{
          ...readyModel,
          timeline: [
            {
              id: "memory-snapshot",
              kind: "memory",
              operation: "snapshot",
              mode: "default",
              outcome: "loaded",
              title: "检查相关记忆",
              description: "回答当前问题前自动检查长期记忆",
              actionSummary: "选择与当前问题相关的记忆",
              stateLabel: "已加载",
              process: [
                {
                  label: "冻结当前 Run 的记忆选择",
                  state: "completed",
                },
              ],
              resultSummary: "已加载 1 个精确记忆版本",
              identities: [
                {
                  itemId: "memory-snapshot",
                  versionId: "version-snapshot",
                  version: 2,
                  kind: "response_preference",
                  source: "explicit",
                  reason: "default",
                },
              ],
              occurredAtLabel: "10:01",
            },
            {
              id: "memory-search",
              kind: "memory",
              operation: "search",
              outcome: "empty",
              title: "继续查找历史背景",
              description: "Agent 发现还需要更多背景",
              actionSummary: "继续查找相关内容",
              stateLabel: "无匹配记忆",
              process: [
                { label: "发起记忆身份搜索", state: "completed" },
                { label: "保持当前记忆上下文不变", state: "completed" },
              ],
              resultSummary: "没有找到可用记忆",
              identities: [],
              occurredAtLabel: "10:02",
            },
            {
              id: "memory-proposal",
              kind: "memory",
              operation: "proposal",
              outcome: "proposed",
              title: "有一条记忆待确认",
              description: "Agent 整理了一条可供未来使用的记忆",
              actionSummary: "确认后才会在未来对话中使用",
              stateLabel: "待确认",
              process: [
                { label: "登记不可变候选版本", state: "completed" },
                { label: "等待用户确认", state: "pending" },
              ],
              resultSummary: "候选记忆已创建，但尚未成为 active 记忆",
              identities: [identityWithIgnoredBody],
              occurredAtLabel: "10:03",
            },
            {
              id: "memory-forget",
              kind: "memory",
              operation: "forget",
              outcome: "confirmation_required",
              title: "确认忘记这条内容",
              description: "确认后未来对话不再使用",
              actionSummary: "先确认目标，避免误删",
              stateLabel: "等待确认",
              process: [
                { label: "定位精确记忆版本", state: "completed" },
                { label: "等待用户选择撤销或彻底删除", state: "pending" },
              ],
              resultSummary: "当前记忆尚未被撤销或清除",
              identities: [
                {
                  itemId: "memory-forget",
                  versionId: "version-forget",
                  version: 4,
                  kind: "scientific_observation",
                  source: "corrected",
                  reason: "tool_search",
                },
              ],
              occurredAtLabel: "10:04",
            },
          ],
          memory: {
            ...readyModel.memory,
            items: [
              {
                id: "memory-proposal",
                stableKey: "project.proposal",
                kind: "project_context",
                kindLabel: "项目上下文",
                status: "proposed",
                statusLabel: "待确认",
                version: 1,
                versionId: "version-proposal",
                content: proposalContent,
                sourceLabel: "Agent 提议",
                createdAtLabel: "10:03",
                updatedAtLabel: "10:03",
                canApprove: true,
                canCorrect: false,
                canForget: false,
                canPurge: true,
              },
              {
                id: "memory-forget",
                stableKey: "scientific.forget",
                kind: "scientific_observation",
                kindLabel: "科研观察",
                status: "active",
                statusLabel: "已生效",
                version: 4,
                versionId: "version-forget",
                content: "仅用于测试当前遗忘状态",
                sourceLabel: "已纠正",
                createdAtLabel: "09:00",
                updatedAtLabel: "10:04",
                canApprove: false,
                canCorrect: true,
                canForget: true,
                canPurge: true,
              },
            ],
          },
        }}
        actions={{ onApproveMemory, onForgetMemory, onPurgeMemory }}
      />,
    );

    expect(screen.getAllByText("BACKEND")).toHaveLength(1);
    expect(screen.getAllByText("TOOL")).toHaveLength(3);
    expect(screen.getByText("Tool · search_memory")).toBeVisible();
    expect(screen.getByText("Tool · propose_memory")).toBeVisible();
    expect(screen.getByText("Tool · forget_memory")).toBeVisible();
    expect(screen.getByRole("heading", { name: "检查相关记忆" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "继续查找历史背景" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "有一条记忆待确认" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "确认忘记这条内容" })).toBeVisible();
    expect(screen.getByText("等待用户确认")).toBeVisible();
    expect(screen.getByText("当前记忆尚未被撤销或清除")).toBeVisible();
    expect(screen.getAllByText("动作")).toHaveLength(4);
    expect(screen.getAllByText("过程")).toHaveLength(4);
    expect(screen.getAllByText("结果")).toHaveLength(4);
    expect(screen.queryByText("PRIVATE MEMORY BODY")).not.toBeInTheDocument();
    expect(screen.getByText("将完整保存的来源消息")).toBeVisible();
    expect(
      screen.getByText(
        `共 ${Array.from(proposalContent).length} 字 · 当前预览前 280 字`,
      ),
    ).toBeVisible();
    const proposalPreview = document.querySelector(
      '[data-operation="proposal"] .oc-memory-confirm-preview > p',
    );
    expect(proposalPreview?.textContent).toContain("\n");
    expect(proposalPreview?.textContent).toHaveLength(281);
    fireEvent.click(screen.getByText("查看完整原文"));
    expect(
      document.querySelector(
        '[data-operation="proposal"] .oc-memory-full-content',
      )?.textContent,
    ).toBe(proposalContent);
    expect(screen.getByText("要忘记的内容")).toBeVisible();
    expect(screen.getByText("仅用于测试当前遗忘状态")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "不采用并清除" }));
    expect(onPurgeMemory).not.toHaveBeenCalled();
    expect(
      screen.getByText("候选正文会被清除，原始对话仍会保留。"),
    ).toBeVisible();
    fireEvent.click(
      screen.getByRole("button", { name: "确认不采用并清除" }),
    );
    expect(onPurgeMemory).toHaveBeenCalledWith("memory-proposal", 1);
    fireEvent.click(screen.getByRole("button", { name: "确认记住" }));
    fireEvent.click(screen.getByRole("button", { name: "确认忘记" }));
    expect(onApproveMemory).toHaveBeenCalledWith("memory-proposal", 1);
    expect(onForgetMemory).toHaveBeenCalledWith("memory-forget", 4);
  });

  it("reconciles historical memory requests with the current resource state", () => {
    render(
      <ConversationWorkspace
        model={{
          ...readyModel,
          timeline: [
            {
              id: "proposal-resolved",
              kind: "memory",
              operation: "proposal",
              outcome: "proposed",
              title: "有一条记忆待确认",
              description: "历史提议",
              actionSummary: "等待确认",
              stateLabel: "待确认",
              process: [
                { label: "创建候选", state: "completed" },
                { label: "等待你的确认", state: "pending" },
              ],
              resultSummary: "尚未保存",
              identities: [
                {
                  itemId: "proposal-current",
                  versionId: "proposal-version",
                  version: 1,
                  kind: "response_preference",
                  source: "proposed",
                  reason: "tool_search",
                },
              ],
              occurredAtLabel: "10:00",
            },
            {
              id: "forget-resolved",
              kind: "memory",
              operation: "forget",
              outcome: "confirmation_required",
              title: "确认忘记这条内容",
              description: "历史遗忘请求",
              actionSummary: "等待确认",
              stateLabel: "等待确认",
              process: [
                { label: "定位记忆", state: "completed" },
                { label: "等待你的确认", state: "pending" },
              ],
              resultSummary: "尚未忘记",
              identities: [
                {
                  itemId: "forget-current",
                  versionId: "forget-version",
                  version: 2,
                  kind: "project_context",
                  source: "explicit",
                  reason: "tool_search",
                },
              ],
              occurredAtLabel: "10:01",
            },
          ],
          memory: {
            ...readyModel.memory,
            items: [
              {
                id: "proposal-current",
                stableKey: "preference.current",
                kind: "response_preference",
                kindLabel: "回复偏好",
                status: "active",
                statusLabel: "已生效",
                version: 1,
                versionId: "proposal-version",
                content: "回答时先给结论",
                sourceLabel: "Agent 提议",
                createdAtLabel: "10:00",
                updatedAtLabel: "10:02",
                canApprove: false,
                canCorrect: true,
                canForget: true,
                canPurge: true,
              },
              {
                id: "forget-current",
                stableKey: "project.forgotten",
                kind: "project_context",
                kindLabel: "项目上下文",
                status: "revoked",
                statusLabel: "已遗忘",
                version: 2,
                versionId: "forget-version",
                content: "旧项目背景",
                sourceLabel: "显式创建",
                createdAtLabel: "09:00",
                updatedAtLabel: "10:02",
                canApprove: false,
                canCorrect: true,
                canForget: false,
                canPurge: true,
              },
            ],
          },
        }}
      />,
    );

    expect(
      screen.getByRole("heading", { name: "已记住这条内容" }),
    ).toBeVisible();
    expect(
      screen.getByRole("heading", { name: "已忘记这条内容" }),
    ).toBeVisible();
    expect(screen.getByText("已确认记住")).toBeVisible();
    expect(screen.getByText("已确认忘记")).toBeVisible();
    expect(
      screen.getByText("已确认；未来相关对话可以使用这条记忆"),
    ).toBeVisible();
    expect(
      screen.getByText("已确认；未来对话不再使用这条记忆"),
    ).toBeVisible();
    expect(screen.queryByRole("button", { name: "确认记住" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "确认忘记" })).not.toBeInTheDocument();
  });

  it("renders purged proposals as rejected and missing resources as unverifiable", () => {
    const proposal = (
      id: string,
      itemId: string,
      versionId: string,
    ) =>
      ({
        id,
        kind: "memory",
        operation: "proposal",
        outcome: "proposed",
        title: "有一条记忆待确认",
        description: "历史提议",
        actionSummary: "等待确认",
        stateLabel: "待确认",
        process: [
          { label: "创建候选", state: "completed" },
          { label: "等待你的确认", state: "pending" },
        ],
        resultSummary: "尚未采用",
        identities: [
          {
            itemId,
            versionId,
            version: 1,
            kind: "response_preference",
            source: "proposed",
            reason: "tool_search",
          },
        ],
        occurredAtLabel: "10:00",
      }) as const;

    render(
      <ConversationWorkspace
        model={{
          ...readyModel,
          timeline: [
            proposal("proposal-rejected", "memory-rejected", "version-old"),
            proposal("proposal-missing", "memory-missing", "version-missing"),
          ],
          memory: {
            ...readyModel.memory,
            items: [
              {
                id: "memory-rejected",
                stableKey: "preference.rejected",
                kind: "response_preference",
                kindLabel: "回复偏好",
                status: "purged",
                statusLabel: "已清除",
                sourceLabel: "Agent 候选",
                createdAtLabel: "10:00",
                updatedAtLabel: "10:01",
                canApprove: false,
                canCorrect: false,
                canForget: false,
                canPurge: false,
              },
            ],
          },
        }}
      />,
    );

    expect(
      screen.getByRole("heading", { name: "已拒绝这条记忆候选" }),
    ).toBeVisible();
    expect(
      screen.getByText("已拒绝并清除候选"),
    ).toBeVisible();
    expect(
      screen.getByRole("heading", { name: "当前无法核对记忆状态" }),
    ).toBeVisible();
    expect(
      screen.getByText("无法读取对应的记忆资源；请稍后重试记忆服务"),
    ).toBeVisible();
  });

  it("scopes memory command progress and failures to the target card", () => {
    const timeline: ConversationWorkspaceViewModel["timeline"] = [
      {
        id: "proposal-other",
        kind: "memory",
        operation: "proposal",
        outcome: "proposed",
        title: "有一条记忆待确认",
        description: "候选",
        actionSummary: "等待确认",
        stateLabel: "待确认",
        process: [{ label: "等待你的确认", state: "pending" }],
        resultSummary: "尚未采用",
        identities: [
          {
            itemId: "memory-other",
            versionId: "version-other",
            version: 1,
            kind: "profile_fact",
            source: "proposed",
            reason: "tool_search",
          },
        ],
        occurredAtLabel: "09:59",
      },
      {
        id: "proposal-target",
        kind: "memory",
        operation: "proposal",
        outcome: "proposed",
        title: "有一条记忆待确认",
        description: "候选",
        actionSummary: "等待确认",
        stateLabel: "待确认",
        process: [{ label: "等待你的确认", state: "pending" }],
        resultSummary: "尚未采用",
        identities: [
          {
            itemId: "memory-target",
            versionId: "version-target",
            version: 1,
            kind: "profile_fact",
            source: "proposed",
            reason: "tool_search",
          },
        ],
        occurredAtLabel: "10:00",
      },
    ];
    const items: ConversationWorkspaceViewModel["memory"]["items"] = [
      {
        id: "memory-other",
        stableKey: "profile.other",
        kind: "profile_fact",
        kindLabel: "个人事实",
        status: "proposed",
        statusLabel: "待确认",
        version: 1,
        versionId: "version-other",
        content: "候选 A",
        sourceLabel: "Agent 候选",
        createdAtLabel: "09:59",
        updatedAtLabel: "09:59",
        canApprove: true,
        canCorrect: true,
        canForget: true,
        canPurge: true,
      },
      {
        id: "memory-target",
        stableKey: "profile.target",
        kind: "profile_fact",
        kindLabel: "个人事实",
        status: "proposed",
        statusLabel: "待确认",
        version: 1,
        versionId: "version-target",
        content: "候选 B",
        sourceLabel: "Agent 候选",
        createdAtLabel: "10:00",
        updatedAtLabel: "10:00",
        canApprove: true,
        canCorrect: true,
        canForget: true,
        canPurge: true,
      },
    ];
    const { rerender } = render(
      <ConversationWorkspace
        model={{
          ...readyModel,
          timeline,
          memory: {
            ...readyModel.memory,
            commandsPending: true,
            command: {
              kind: "purge",
              memoryId: "memory-target",
              pending: true,
            },
            items,
          },
        }}
      />,
    );

    const otherPendingCard = screen.getByText("候选 A", { exact: true }).closest(
      "article",
    )!;
    const targetPendingCard = screen.getByText("候选 B", { exact: true }).closest(
      "article",
    )!;
    expect(
      within(otherPendingCard).queryByText("正在拒绝并清除", { exact: true }),
    ).not.toBeInTheDocument();
    expect(
      within(targetPendingCard).getByText("正在拒绝并清除", { exact: true }),
    ).toBeVisible();
    expect(
      within(otherPendingCard).getByRole("button", {
        name: "确认记住",
      }),
    ).toBeDisabled();
    expect(
      within(targetPendingCard).getByRole("button", {
        name: "不采用并清除",
      }),
    ).toBeDisabled();

    rerender(
      <ConversationWorkspace
        model={{
          ...readyModel,
          timeline,
          memory: {
            ...readyModel.memory,
            commandErrorMessage: "版本已经变化，请刷新后重试",
            commandsPending: false,
            command: {
              kind: "purge",
              memoryId: "memory-target",
              pending: false,
              errorMessage: "版本已经变化，请刷新后重试",
            },
            items,
          },
        }}
      />,
    );

    const otherFailedCard = screen.getByText("候选 A", { exact: true }).closest(
      "article",
    )!;
    const targetFailedCard = screen.getByText("候选 B", { exact: true }).closest(
      "article",
    )!;
    expect(screen.getByRole("alert")).toHaveTextContent(
      "记忆操作未完成版本已经变化，请刷新后重试",
    );
    expect(
      within(otherFailedCard).queryByText(
        "错误：版本已经变化，请刷新后重试",
      ),
    ).not.toBeInTheDocument();
    expect(
      within(targetFailedCard).getByText(
        "错误：版本已经变化，请刷新后重试",
      ),
    ).toBeVisible();
    expect(
      within(targetFailedCard).getByRole("button", {
        name: "不采用并清除",
      }),
    ).toBeEnabled();
  });

  it("renders a later revoked proposal as forgotten instead of rejected", () => {
    render(
      <ConversationWorkspace
        model={{
          ...readyModel,
          timeline: [
            {
              id: "proposal-later-forgotten",
              kind: "memory",
              operation: "proposal",
              outcome: "proposed",
              title: "有一条记忆待确认",
              description: "候选",
              actionSummary: "等待确认",
              stateLabel: "待确认",
              process: [{ label: "等待你的确认", state: "pending" }],
              resultSummary: "尚未采用",
              identities: [
                {
                  itemId: "memory-later-forgotten",
                  versionId: "version-later-forgotten",
                  version: 1,
                  kind: "response_preference",
                  source: "proposed",
                  reason: "tool_search",
                },
              ],
              occurredAtLabel: "10:00",
            },
          ],
          memory: {
            ...readyModel.memory,
            items: [
              {
                id: "memory-later-forgotten",
                stableKey: "preference.later-forgotten",
                kind: "response_preference",
                kindLabel: "回复偏好",
                status: "revoked",
                statusLabel: "已遗忘",
                version: 1,
                versionId: "version-later-forgotten",
                content: "回答时先给结论",
                sourceLabel: "Agent 候选",
                createdAtLabel: "10:00",
                updatedAtLabel: "10:05",
                canApprove: false,
                canCorrect: true,
                canForget: false,
                canPurge: true,
              },
            ],
          },
        }}
      />,
    );

    expect(
      screen.getByRole("heading", { name: "已忘记这条内容" }),
    ).toBeVisible();
    expect(
      screen.queryByRole("heading", { name: "已拒绝这条记忆候选" }),
    ).not.toBeInTheDocument();
  });

  it("uses command kind to target only the matching card for one item", () => {
    const sharedIdentity = {
      itemId: "memory-shared",
      versionId: "version-shared",
      version: 1,
      kind: "response_preference" as const,
      source: "proposed",
      reason: "tool_search",
    };
    render(
      <ConversationWorkspace
        model={{
          ...readyModel,
          timeline: [
            {
              id: "proposal-shared",
              kind: "memory",
              operation: "proposal",
              outcome: "proposed",
              title: "有一条记忆待确认",
              description: "候选",
              actionSummary: "等待确认",
              stateLabel: "待确认",
              process: [{ label: "等待你的确认", state: "pending" }],
              resultSummary: "尚未采用",
              identities: [sharedIdentity],
              occurredAtLabel: "10:00",
            },
            {
              id: "forget-shared",
              kind: "memory",
              operation: "forget",
              outcome: "confirmation_required",
              title: "确认忘记这条内容",
              description: "遗忘请求",
              actionSummary: "等待确认",
              stateLabel: "等待确认",
              process: [{ label: "等待你的确认", state: "pending" }],
              resultSummary: "尚未忘记",
              identities: [sharedIdentity],
              occurredAtLabel: "10:05",
            },
          ],
          memory: {
            ...readyModel.memory,
            commandsPending: true,
            command: {
              kind: "forget",
              memoryId: "memory-shared",
              pending: true,
            },
            items: [
              {
                id: "memory-shared",
                stableKey: "preference.shared",
                kind: "response_preference",
                kindLabel: "回复偏好",
                status: "active",
                statusLabel: "已生效",
                version: 1,
                versionId: "version-shared",
                content: "回答时先给结论",
                sourceLabel: "Agent 候选",
                createdAtLabel: "10:00",
                updatedAtLabel: "10:01",
                canApprove: false,
                canCorrect: true,
                canForget: true,
                canPurge: true,
              },
            ],
          },
        }}
      />,
    );

    const proposalCard = screen
      .getByRole("heading", { name: "已记住这条内容" })
      .closest("article")!;
    const forgetCard = screen
      .getByRole("heading", { name: "确认忘记这条内容" })
      .closest("article")!;
    expect(
      within(proposalCard).queryByText("正在确认忘记", { exact: true }),
    ).not.toBeInTheDocument();
    expect(
      within(forgetCard).getByText("正在确认忘记", { exact: true }),
    ).toBeVisible();
  });

  it("automatically uses default memory for every submission after one-time enablement", async () => {
    const onSubmit = vi.fn().mockResolvedValue(true);
    render(
      <ConversationWorkspace
        model={{
          ...readyModel,
          memory: {
            ...readyModel.memory,
            useMemory: true,
            generateCandidates: true,
            enableAgentTools: true,
            providerConsentGranted: true,
          },
        }}
        actions={{ onSubmit }}
      />,
    );

    expect(screen.queryByLabelText("本次运行记忆模式")).not.toBeInTheDocument();
    expect(screen.getByText("记忆已开启")).toBeVisible();
    fireEvent.change(screen.getByRole("textbox", { name: "分析指令" }), {
      target: { value: "第一轮问题" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送分析指令" }));

    expect(onSubmit).toHaveBeenCalledWith("第一轮问题", {
      mode: "default",
      refs: [],
    });
    await waitFor(() =>
      expect(screen.getByRole("textbox", { name: "分析指令" })).toHaveValue(""),
    );
    fireEvent.change(screen.getByRole("textbox", { name: "分析指令" }), {
      target: { value: "第二轮问题" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送分析指令" }));
    expect(onSubmit).toHaveBeenLastCalledWith("第二轮问题", {
      mode: "default",
      refs: [],
    });
  });

  it("exposes one memory switch while keeping internal gates out of the normal UI", () => {
    render(
      <ConversationWorkspace
        model={{
          ...readyModel,
          memory: {
            ...readyModel.memory,
            useMemory: true,
            generateCandidates: true,
            enableAgentTools: true,
            providerConsentGranted: true,
          },
        }}
      />,
    );

    fireEvent.click(screen.getByRole("tab", { name: /记忆/ }));
    expect(screen.getAllByRole("checkbox")).toHaveLength(1);
    expect(screen.getByRole("checkbox", { name: /跨会话记忆/ })).toBeChecked();
    expect(
      screen.getByText(/主动提出记忆候选/),
    ).toBeVisible();
    expect(
      screen.getByText(/主动提议值得长期保留的信息/),
    ).toBeVisible();
    expect(
      screen.queryByText(/直接对 Agent 说“记住/),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("生成记忆候选")).not.toBeInTheDocument();
    expect(screen.queryByText("启用 Agent 记忆 Tool")).not.toBeInTheDocument();
  });

  it("unmounts correction plaintext when a memory becomes purged", () => {
    const secretBody = "只应存在于旧版本的敏感正文";
    const actions = { onCorrectMemory: vi.fn().mockResolvedValue(true) };
    const activeItem = {
      id: "memory-purge",
      stableKey: "profile.private",
      kind: "profile_fact",
      kindLabel: "个人事实",
      status: "active",
      statusLabel: "已生效",
      version: 1,
      versionId: "version-before-purge",
      content: secretBody,
      sourceLabel: "显式创建",
      createdAtLabel: "10:00",
      updatedAtLabel: "10:00",
      canApprove: false,
      canCorrect: true,
      canForget: true,
      canPurge: true,
    } as const;
    const { rerender } = render(
      <ConversationWorkspace
        model={{
          ...readyModel,
          memory: { ...readyModel.memory, items: [activeItem] },
        }}
        actions={actions}
      />,
    );
    fireEvent.click(screen.getByRole("tab", { name: /记忆/ }));
    fireEvent.click(screen.getByRole("button", { name: "纠正" }));
    expect(screen.getByRole("textbox", { name: "新版本正文" })).toHaveValue(
      secretBody,
    );

    rerender(
      <ConversationWorkspace
        model={{
          ...readyModel,
          memory: {
            ...readyModel.memory,
            items: [
              {
                ...activeItem,
                status: "purged",
                statusLabel: "已清除",
                version: undefined,
                versionId: undefined,
                content: undefined,
                contentSha256: undefined,
                canCorrect: false,
                canForget: false,
                canPurge: false,
              },
            ],
          },
        }}
        actions={actions}
      />,
    );

    expect(
      screen.queryByRole("textbox", { name: "新版本正文" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(secretBody)).not.toBeInTheDocument();
    expect(screen.getByText("已清除记录 · 1")).toBeVisible();
    expect(
      screen.queryByText("这条记忆已被清除，正文不可用。"),
    ).not.toBeVisible();
    fireEvent.click(screen.getByText("已清除记录 · 1"));
    expect(
      screen.getByText("这条记忆已被清除，正文不可用。"),
    ).toBeVisible();
  });

  it("blocks run submission while a memory command is being persisted", () => {
    const onSubmit = vi.fn().mockResolvedValue(true);
    render(
      <ConversationWorkspace
        model={{
          ...readyModel,
          memory: {
            ...readyModel.memory,
            commandsPending: true,
          },
        }}
        actions={{ onSubmit }}
      />,
    );

    fireEvent.change(screen.getByRole("textbox", { name: "分析指令" }), {
      target: { value: "不要与正在提交的授权发生竞态" },
    });

    expect(
      screen.getByRole("button", { name: "发送分析指令" }),
    ).toBeDisabled();
    expect(screen.getByText("记忆设置正在确认，完成后才能发送")).toBeVisible();
    fireEvent.submit(screen.getByRole("textbox", { name: "分析指令" }).closest("form")!);
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("requires explicit provider consent before enabling memory", () => {
    const onGrantMemoryConsentAndEnable = vi.fn().mockResolvedValue(true);
    render(
      <ConversationWorkspace
        model={readyModel}
        actions={{ onGrantMemoryConsentAndEnable }}
      />,
    );

    fireEvent.click(screen.getByRole("tab", { name: /记忆/ }));
    fireEvent.click(
      screen.getByRole("checkbox", { name: /跨会话记忆/ }),
    );
    expect(
      screen.getByRole("dialog", { name: "开启长期记忆" }),
    ).toBeInTheDocument();
    expect(onGrantMemoryConsentAndEnable).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "开启" }));
    expect(onGrantMemoryConsentAndEnable).toHaveBeenCalledTimes(1);
  });

  it("fails closed to memory off when the Memory API is unavailable", () => {
    const onSubmit = vi.fn().mockResolvedValue(true);
    render(
      <ConversationWorkspace
        model={{
          ...readyModel,
          memory: {
            ...readyModel.memory,
            available: false,
            errorMessage: "connection refused",
            useMemory: true,
            providerConsentGranted: true,
          },
        }}
        actions={{ onSubmit }}
      />,
    );

    expect(
      screen.getByText("记忆未启用").closest(".oc-composer-memory"),
    ).toHaveTextContent("服务不可用，本次按普通会话执行");
    fireEvent.change(screen.getByRole("textbox", { name: "分析指令" }), {
      target: { value: "普通问答" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送分析指令" }));
    expect(onSubmit).toHaveBeenCalledWith("普通问答", {
      mode: "off",
      refs: [],
    });
  });

  it.each([
    ["读取门禁关闭", { useMemory: false }],
    ["候选门禁关闭", { generateCandidates: false }],
    ["Agent Tool 门禁关闭", { enableAgentTools: false }],
    ["provider 授权缺失", { providerConsentGranted: false }],
  ])("fails closed to off when %s", (_, override) => {
    const onSubmit = vi.fn().mockResolvedValue(true);
    render(
      <ConversationWorkspace
        model={{
          ...readyModel,
          memory: {
            ...readyModel.memory,
            useMemory: true,
            generateCandidates: true,
            enableAgentTools: true,
            providerConsentGranted: true,
            ...override,
          },
        }}
        actions={{ onSubmit }}
      />,
    );

    fireEvent.change(screen.getByRole("textbox", { name: "分析指令" }), {
      target: { value: "保持普通会话" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送分析指令" }));
    expect(onSubmit).toHaveBeenCalledWith("保持普通会话", {
      mode: "off",
      refs: [],
    });
  });

  it("renders runtime transcript and loads a bounded artifact preview", async () => {
    const onLoadArtifactContent = vi
      .fn()
      .mockResolvedValue(new Blob(['{"clusters": 11}'], { type: "application/json" }));
    render(
      <ConversationWorkspace
        model={{
          ...readyModel,
          timeline: [
            {
              id: "runtime-1",
              kind: "runtime",
              runtimeCommandId: "runtime-1",
              toolName: "cluster_cells",
              backend: "local-docker-cli",
              command: ["python", "/app/data/request.py"],
              code: "print('clustered')",
              workdir: "/app/data",
              state: "completed",
              stdout: "clustered\n",
              stderr: "",
              exitCode: 0,
              durationLabel: "1.20 s",
              commandTruncated: false,
              stdoutTruncated: false,
              stderrTruncated: false,
              redacted: false,
              process: [
                { label: "启动 Backend 命令", state: "completed" },
                { label: "接收执行输出", state: "completed" },
                { label: "Backend 操作完成", state: "completed" },
              ],
              occurredAtLabel: "10:05",
            },
            {
              id: "artifact-event-1",
              kind: "artifact",
              artifactId: "artifact-json",
              name: "summary.json",
              artifactKind: "analysis_metadata",
              mediaType: "application/json",
              sizeLabel: "18 B",
              previewMode: "json",
              occurredAtLabel: "10:06",
            },
          ],
        }}
        actions={{ onLoadArtifactContent }}
      />,
    );

    expect(screen.getByText("BACKEND")).toBeInTheDocument();
    expect(screen.getByText("执行 cluster_cells")).toBeInTheDocument();
    expect(
      screen.getByText("在 local-docker-cli 中执行 cluster_cells"),
    ).toBeInTheDocument();
    expect(screen.getByText("Backend 操作完成")).toBeInTheDocument();
    expect(screen.getByText("命令执行成功，退出码 0")).toBeInTheDocument();
    expect(screen.getByText("print('clustered')").closest("details")).not.toHaveAttribute(
      "open",
    );
    expect(screen.getByText("clustered").closest(".oc-backend-output")).toBeVisible();
    expect(await screen.findByText(/"clusters": 11/)).toBeInTheDocument();
    expect(onLoadArtifactContent).toHaveBeenCalledWith("artifact-json");
  });

  it("renders cluster annotation artifacts as a bounded domain table", async () => {
    const onLoadArtifactContent = vi.fn().mockResolvedValue(
      new Blob(
        [
          JSON.stringify({
            cluster_annotations: {
              "0": {
                general_type: "Immune cells",
                sub_type: "B cells",
                cs_score: 92,
                flags: [],
                reasoning_chain: "bounded detail",
              },
              "1": {
                general_type: "Immune cells",
                sub_type: "Ambiguous (NeedsReview)",
                cs_score: 55,
                flags: ["low_self_consistency"],
              },
            },
          }),
        ],
        { type: "application/json" },
      ),
    );
    render(
      <ConversationWorkspace
        model={{
          ...readyModel,
          timeline: [
            {
              id: "annotation-artifact",
              kind: "artifact",
              artifactId: "annotation-artifact",
              name: "annotations.json",
              artifactKind: "cluster_annotations",
              mediaType: "application/json",
              sizeLabel: "1.2 KiB",
              previewMode: "json",
              occurredAtLabel: "10:06",
            },
          ],
        }}
        actions={{ onLoadArtifactContent }}
      />,
    );

    expect(await screen.findByText("B cells")).toBeInTheDocument();
    expect(screen.getByText("Ambiguous (NeedsReview)")).toBeInTheDocument();
    expect(screen.getByText("low_self_consistency")).toBeInTheDocument();
    expect(screen.getAllByText("需要")).toHaveLength(1);
    expect(screen.getByText("查看有界 JSON 证据")).toBeInTheDocument();
  });

  it("defaults the inspector to the current run and can switch scope", () => {
    render(
      <ConversationWorkspace
        model={{
          ...readyModel,
          toolExecutions: [
            {
              id: "current-tool",
              runId: "run-1",
              name: "cluster_cells",
              family: "analyze",
              title: "降维与细胞聚类",
              description: "生成 11 个 cluster",
              state: "failed",
              stateLabel: "失败",
            },
            {
              id: "old-tool",
              runId: "run-old",
              name: "inspect_dataset",
              family: "inspect",
              title: "检查当前数据集",
              description: "Human PBMC",
              state: "completed",
              stateLabel: "已完成",
            },
          ],
        }}
      />,
    );

    fireEvent.click(screen.getByRole("tab", { name: /Tool1/ }));
    expect(screen.getByText("cluster_cells")).toBeInTheDocument();
    expect(screen.queryByText("inspect_dataset")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "全部会话" }));
    expect(screen.getByRole("tab", { name: /Tool2/ })).toBeInTheDocument();
    expect(screen.getByText(/inspect_dataset/)).toBeInTheDocument();
  });

  it("does not fetch artifact content when preview policy selects fallback", () => {
    const onLoadArtifactContent = vi.fn();
    render(
      <ConversationWorkspace
        model={{
          ...readyModel,
          timeline: [
            {
              id: "artifact-large",
              kind: "artifact",
              artifactId: "artifact-large",
              name: "matrix.csv",
              artifactKind: "dataset",
              mediaType: "text/csv",
              sizeLabel: "12.0 MiB",
              previewMode: "none",
              previewReason: "内容较大，仅提供 metadata 与下载",
              occurredAtLabel: "10:06",
            },
          ],
        }}
        actions={{ onLoadArtifactContent }}
      />,
    );

    expect(
      screen.getByText("内容较大，仅提供 metadata 与下载"),
    ).toBeInTheDocument();
    expect(onLoadArtifactContent).not.toHaveBeenCalled();
  });
});
