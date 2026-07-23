import { expect, test, type Page, type Route } from "@playwright/test";

const conversationId = "11111111-1111-4111-8111-111111111111";
const runId = "22222222-2222-4222-8222-222222222222";
const artifactId = "33333333-3333-4333-8333-333333333333";
const occurredAt = "2026-07-23T08:00:00Z";

function event(
  sequence: number,
  type: string,
  payload: Record<string, unknown>,
) {
  return {
    schema_version: 1,
    event_id: `44444444-4444-4444-8444-${String(sequence).padStart(12, "0")}`,
    conversation_id: conversationId,
    run_id: runId,
    sequence: String(sequence),
    occurred_at: occurredAt,
    type,
    payload,
  };
}

const events = [
  event(1, "run.created", { status: "pending" }),
  event(2, "run.started", { status: "running" }),
  event(3, "message.completed", {
    message_id: "55555555-5555-4555-8555-555555555555",
    role: "user",
    content: "比较两个细胞群并生成报告",
    stop_reason: null,
    content_artifact_id: null,
  }),
  event(4, "task.created", {
    task_id: "66666666-6666-4666-8666-666666666666",
    title: "执行单细胞分析",
    description: "生成可复查的分析产物",
    status: "pending",
    capability_name: "single_cell_analysis",
  }),
  event(5, "capability.started", {
    capability_call_id: "77777777-7777-4777-8777-777777777777",
    capability_name: "single_cell_analysis",
    task_id: "66666666-6666-4666-8666-666666666666",
    attempt: 1,
  }),
  event(6, "capability.completed", {
    capability_call_id: "77777777-7777-4777-8777-777777777777",
    capability_name: "single_cell_analysis",
    task_id: "66666666-6666-4666-8666-666666666666",
    artifact_ids: [artifactId],
    summary: "Graph A 已生成分析报告",
  }),
  event(7, "message.completed", {
    message_id: "88888888-8888-4888-8888-888888888888",
    role: "assistant",
    content: "分析完成，报告已经登记为可下载产物。",
    stop_reason: "finished",
    content_artifact_id: null,
  }),
  event(8, "run.completed", {
    status: "completed",
    final_message_id: "88888888-8888-4888-8888-888888888888",
    artifact_ids: [artifactId],
  }),
];

async function json(route: Route, body: unknown) {
  await route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

async function mockApi(page: Page) {
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path === "/api/v1/conversations") {
      await json(route, {
        schema_version: 1,
        items: [
          {
            schema_version: 1,
            conversation_id: conversationId,
            title: "免疫细胞比较",
            status: "active",
            dataset_artifact_id: artifactId,
            created_at: occurredAt,
            updated_at: occurredAt,
          },
        ],
        page: { next_cursor: null, has_more: false },
      });
      return;
    }
    if (path === `/api/v1/conversations/${conversationId}`) {
      await json(route, {
        schema_version: 1,
        conversation_id: conversationId,
        title: "免疫细胞比较",
        status: "active",
        dataset_artifact_id: artifactId,
        created_at: occurredAt,
        updated_at: occurredAt,
      });
      return;
    }
    if (path === `/api/v1/conversations/${conversationId}/history`) {
      await json(route, {
        schema_version: 1,
        conversation_id: conversationId,
        order: "newest_first",
        items: [
          {
            schema_version: 1,
            run_id: runId,
            conversation_id: conversationId,
            status: "completed",
            last_sequence: "8",
            created_at: occurredAt,
            started_at: occurredAt,
            updated_at: occurredAt,
            completed_at: occurredAt,
            error_summary: null,
          },
        ],
        page: { next_cursor: null, has_more: false },
      });
      return;
    }
    if (path === `/api/v1/conversations/${conversationId}/artifacts`) {
      await json(route, {
        schema_version: 1,
        conversation_id: conversationId,
        items: [
          {
            schema_version: 1,
            artifact_id: artifactId,
            conversation_id: conversationId,
            run_id: runId,
            source_event_id: null,
            kind: "dataset",
            media_type: "text/csv",
            size_bytes: 44,
            sha256: "a".repeat(64),
            metadata: { filename: "pbmc.csv" },
            created_at: occurredAt,
          },
        ],
        page: { next_cursor: null, has_more: false },
      });
      return;
    }
    if (path === `/api/v1/conversations/${conversationId}/reviews`) {
      await json(route, {
        schema_version: 1,
        conversation_id: conversationId,
        items: [],
        page: { next_cursor: null, has_more: false },
      });
      return;
    }
    if (path === `/api/v1/runs/${runId}/events`) {
      await json(route, {
        schema_version: 1,
        run_id: runId,
        conversation_id: conversationId,
        events,
        next_sequence: "8",
        has_more: false,
      });
      return;
    }
    if (path === `/api/v1/runs/${runId}/events/stream`) {
      const frames = events
        .map(
          (item) =>
            `id: ${item.sequence}\nevent: ${item.type}\ndata: ${JSON.stringify(item)}\n\n`,
        )
        .join("");
      await route.fulfill({
        status: 200,
        headers: { "content-type": "text/event-stream" },
        body: frames,
      });
      return;
    }
    await route.fulfill({ status: 404, body: "unexpected test route" });
  });
}

test.beforeEach(async ({ page }) => {
  await mockApi(page);
});

test("URL 刷新后恢复 conversation 与持久化事件投影", async ({ page }) => {
  await page.goto(`/conversation/${conversationId}`);

  await expect(page.getByRole("heading", { name: "免疫细胞比较" })).toBeVisible();
  await expect(page.getByText("分析完成，报告已经登记为可下载产物。")).toBeVisible();
  await expect(page.getByText("Graph A 已生成分析报告")).toBeVisible();
  await expect(page.getByRole("tab", { name: "事件 8" })).toBeVisible();

  await page.reload();
  await expect(page).toHaveURL(`/conversation/${conversationId}`);
  await expect(page.getByRole("heading", { name: "免疫细胞比较" })).toBeVisible();
});

test("窄屏提供导航与检查器抽屉入口", async ({ page }) => {
  await page.setViewportSize({ width: 720, height: 900 });
  await page.goto(`/conversation/${conversationId}`);

  await expect(page.getByRole("button", { name: "对话", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "检查器", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "检查器", exact: true }).click();
  await expect(page.getByRole("button", { name: "关闭运行检查器" })).toBeVisible();
});
