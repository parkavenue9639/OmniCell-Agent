import { fireEvent, render, screen } from "@testing-library/react";
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
    expect(onSubmit).toHaveBeenCalledWith("比较不同 cluster");

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
