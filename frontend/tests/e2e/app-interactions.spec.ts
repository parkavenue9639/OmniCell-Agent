import { expect, test, type Page, type Route } from "@playwright/test";

const conversationA = "11111111-1111-4111-8111-111111111111";
const conversationB = "22222222-2222-4222-8222-222222222222";
const runOld = "33333333-3333-4333-8333-333333333333";
const runNew = "44444444-4444-4444-8444-444444444444";
const datasetA = "55555555-5555-4555-8555-555555555555";
const uploadedA = "66666666-6666-4666-8666-666666666666";
const reviewId = "77777777-7777-4777-8777-777777777777";
const reportId = "88888888-8888-4888-8888-888888888888";
const occurredAt = "2026-07-23T08:00:00Z";

async function json(route: Route, body: unknown, status = 200) {
  await route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

function conversation(
  id: string,
  title: string,
  datasetArtifactId: string | null = null,
) {
  return {
    schema_version: 1,
    conversation_id: id,
    title,
    status: "active",
    dataset_artifact_id: datasetArtifactId,
    created_at: occurredAt,
    updated_at: occurredAt,
  };
}

function run(
  id: string,
  conversationId: string,
  status: "pending" | "running" | "review_required" | "completed",
  createdAt = occurredAt,
) {
  return {
    schema_version: 1,
    run_id: id,
    conversation_id: conversationId,
    status,
    last_sequence: status === "completed" ? "3" : "0",
    created_at: createdAt,
    started_at: createdAt,
    updated_at: createdAt,
    completed_at: status === "completed" ? createdAt : null,
    error_summary: null,
  };
}

function history(conversationId: string, items: readonly unknown[]) {
  return {
    schema_version: 1,
    conversation_id: conversationId,
    order: "newest_first",
    items,
    page: { next_cursor: null, has_more: false },
  };
}

function artifacts(conversationId: string, items: readonly unknown[]) {
  return {
    schema_version: 1,
    conversation_id: conversationId,
    items,
    page: { next_cursor: null, has_more: false },
  };
}

function reviews(conversationId: string, items: readonly unknown[]) {
  return {
    schema_version: 1,
    conversation_id: conversationId,
    items,
    page: { next_cursor: null, has_more: false },
  };
}

function event(
  sequence: number,
  type: string,
  payload: Record<string, unknown>,
  runId = runNew,
) {
  return {
    schema_version: 1,
    event_id: `99999999-9999-4999-8999-${String(sequence).padStart(12, "0")}`,
    conversation_id: conversationA,
    run_id: runId,
    sequence: String(sequence),
    occurred_at: occurredAt,
    type,
    payload,
  };
}

function eventReplay(runId: string, items: readonly unknown[]) {
  return {
    schema_version: 1,
    run_id: runId,
    conversation_id: conversationA,
    events: items,
    next_sequence: String(items.length),
    has_more: false,
  };
}

test("上传期间切换 conversation 不会泄漏 dataset 选择", async ({ page }) => {
  let releaseUpload!: () => void;
  let uploadStarted!: () => void;
  let uploadCompleted!: () => void;
  const uploadGate = new Promise<void>((resolve) => {
    releaseUpload = resolve;
  });
  const started = new Promise<void>((resolve) => {
    uploadStarted = resolve;
  });
  const completed = new Promise<void>((resolve) => {
    uploadCompleted = resolve;
  });
  let submitted: Record<string, unknown> | undefined;

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (path === "/api/v1/conversations") {
      await json(route, {
        schema_version: 1,
        items: [
          conversation(conversationA, "Conversation A", datasetA),
          conversation(conversationB, "Conversation B"),
        ],
        page: { next_cursor: null, has_more: false },
      });
      return;
    }
    if (path === `/api/v1/conversations/${conversationA}`) {
      await json(route, conversation(conversationA, "Conversation A", datasetA));
      return;
    }
    if (path === `/api/v1/conversations/${conversationB}`) {
      await json(route, conversation(conversationB, "Conversation B"));
      return;
    }
    if (path.endsWith("/history")) {
      const id = path.includes(conversationA) ? conversationA : conversationB;
      await json(route, history(id, []));
      return;
    }
    if (path === `/api/v1/conversations/${conversationA}/artifacts`) {
      if (request.method() === "POST") {
        uploadStarted();
        await uploadGate;
        await json(route, {
          schema_version: 1,
          artifact_id: uploadedA,
          conversation_id: conversationA,
          run_id: null,
          source_event_id: null,
          kind: "dataset",
          media_type: "text/csv",
          size_bytes: 44,
          sha256: "a".repeat(64),
          metadata: { filename: "uploaded-a.csv" },
          created_at: occurredAt,
        });
        uploadCompleted();
        return;
      }
      await json(
        route,
        artifacts(conversationA, [
          {
            schema_version: 1,
            artifact_id: datasetA,
            conversation_id: conversationA,
            run_id: null,
            source_event_id: null,
            kind: "dataset",
            media_type: "text/csv",
            size_bytes: 44,
            sha256: "b".repeat(64),
            metadata: { filename: "a.csv" },
            created_at: occurredAt,
          },
        ]),
      );
      return;
    }
    if (path === `/api/v1/conversations/${conversationB}/artifacts`) {
      await json(route, artifacts(conversationB, []));
      return;
    }
    if (
      path === `/api/v1/conversations/${conversationB}/runs` &&
      request.method() === "POST"
    ) {
      submitted = request.postDataJSON() as Record<string, unknown>;
      await json(
        route,
        { run: run(runNew, conversationB, "pending") },
        202,
      );
      return;
    }
    await route.fulfill({ status: 404, body: `unexpected ${request.method()} ${path}` });
  });

  await page.goto(`/conversation/${conversationA}`);
  await expect(page.getByRole("heading", { name: "Conversation A" })).toBeVisible();
  await page.getByRole("textbox", { name: "分析指令" }).fill("A 的未提交草稿");
  await page
    .locator('input[type="file"]')
    .setInputFiles("tests/fixtures/smoke.csv");
  await started;

  await page.getByRole("button", { name: /Conversation B/ }).click();
  await expect(page).toHaveURL(`/conversation/${conversationB}`);
  await expect(page.getByRole("textbox", { name: "分析指令" })).toHaveValue("");
  releaseUpload();
  await completed;
  await expect(page.getByText("尚未选择数据集")).toBeVisible();

  await page.getByRole("textbox", { name: "分析指令" }).fill("分析 B 数据");
  await page.getByRole("button", { name: "发送分析指令" }).click();
  await expect.poll(() => submitted).toBeTruthy();
  expect(submitted?.input_artifact_ids).toEqual([]);
});

test("conversation 绑定 dataset 不在 artifact 首分页时显示与提交一致", async ({
  page,
}) => {
  let submitted: Record<string, unknown> | undefined;
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (path === "/api/v1/conversations") {
      await json(route, {
        schema_version: 1,
        items: [conversation(conversationA, "Paged dataset", datasetA)],
        page: { next_cursor: null, has_more: false },
      });
      return;
    }
    if (path === `/api/v1/conversations/${conversationA}`) {
      await json(route, conversation(conversationA, "Paged dataset", datasetA));
      return;
    }
    if (path.endsWith("/history")) {
      await json(route, history(conversationA, []));
      return;
    }
    if (path.endsWith("/artifacts")) {
      await json(route, artifacts(conversationA, []));
      return;
    }
    if (
      path === `/api/v1/conversations/${conversationA}/runs` &&
      request.method() === "POST"
    ) {
      submitted = request.postDataJSON() as Record<string, unknown>;
      await json(
        route,
        { run: run(runNew, conversationA, "pending") },
        202,
      );
      return;
    }
    await route.fulfill({ status: 404, body: `unexpected ${request.method()} ${path}` });
  });

  await page.goto(`/conversation/${conversationA}`);
  await expect(page.getByText("已绑定当前数据集")).toBeVisible();
  await page.getByRole("textbox", { name: "分析指令" }).fill("使用绑定数据集");
  await page.getByRole("button", { name: "发送分析指令" }).click();
  await expect.poll(() => submitted).toBeTruthy();
  expect(submitted?.input_artifact_ids).toEqual([datasetA]);
});

test("连续 run 刷新后恢复最新 run 并按 run 查询 review", async ({ page }) => {
  const requestedStreams: string[] = [];
  const requestedReviewRuns: string[] = [];
  const skillLoadId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa0";
  const newestEvents = [
    event(1, "run.created", { status: "pending" }),
    event(2, "skill.load_started", {
      skill_load_id: skillLoadId,
      skill_name: "pca-clustering",
      resource_kind: "body",
      resource_name: null,
      purpose: "domain_method",
    }),
    event(3, "skill.load_completed", {
      skill_load_id: skillLoadId,
      skill_name: "pca-clustering",
      resource_kind: "body",
      resource_name: null,
      purpose: "domain_method",
      outcome: "loaded",
      content_bytes: 2048,
    }),
    event(4, "message.completed", {
      message_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      role: "assistant",
      content: "newest run restored",
      stop_reason: "finished",
      content_artifact_id: null,
    }),
    event(5, "run.completed", {
      status: "completed",
      final_message_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      artifact_ids: [],
    }),
  ];
  const oldestEvents = [
    event(1, "run.created", { status: "pending" }, runOld),
    event(2, "message.completed", {
      message_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
      role: "assistant",
      content: "old run restored",
      stop_reason: "finished",
      content_artifact_id: null,
    }, runOld),
    event(3, "run.completed", {
      status: "completed",
      final_message_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
      artifact_ids: [],
    }, runOld),
  ];

  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path === "/api/v1/conversations") {
      await json(route, {
        schema_version: 1,
        items: [conversation(conversationA, "Continuous runs")],
        page: { next_cursor: null, has_more: false },
      });
      return;
    }
    if (path === `/api/v1/conversations/${conversationA}`) {
      await json(route, conversation(conversationA, "Continuous runs"));
      return;
    }
    if (path.endsWith("/history")) {
      await json(
        route,
        history(conversationA, [
          run(runNew, conversationA, "completed", "2026-07-23T09:00:00Z"),
          run(runOld, conversationA, "completed", "2026-07-23T08:00:00Z"),
        ]),
      );
      return;
    }
    if (path.endsWith("/artifacts")) {
      await json(route, artifacts(conversationA, []));
      return;
    }
    if (path.endsWith("/reviews")) {
      requestedReviewRuns.push(url.searchParams.get("run_id") ?? "");
      await json(route, reviews(conversationA, []));
      return;
    }
    if (path === `/api/v1/runs/${runOld}/events`) {
      await json(route, eventReplay(runOld, oldestEvents));
      return;
    }
    if (path === `/api/v1/runs/${runNew}/events`) {
      await json(route, eventReplay(runNew, newestEvents));
      return;
    }
    if (path === `/api/v1/runs/${runNew}/events/stream`) {
      requestedStreams.push(runNew);
      const frames = newestEvents
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
    await route.fulfill({ status: 404, body: `unexpected ${path}` });
  });

  await page.goto(`/conversation/${conversationA}`);
  await expect(page.getByText("newest run restored")).toBeVisible();
  await expect(page.getByText("SKILL · 渐进加载")).toBeVisible();
  await expect(page.getByText("Skill 正文 · 加载领域方法")).toBeVisible();
  await expect.poll(() => requestedReviewRuns.at(-1)).toBe(runNew);
  expect(requestedStreams).not.toContain(runOld);

  await page.reload();
  await expect(page.getByText("newest run restored")).toBeVisible();
  await expect(page.getByText("SKILL · 渐进加载")).toBeVisible();
  await expect.poll(() => requestedReviewRuns.at(-1)).toBe(runNew);
  expect(requestedStreams).not.toContain(runOld);
});

test("review 与 cancel 防重入且下载保留原始文件名", async ({ page }) => {
  let releaseReview!: () => void;
  let reviewStarted!: () => void;
  let releaseCancel!: () => void;
  let cancelStarted!: () => void;
  const reviewGate = new Promise<void>((resolve) => {
    releaseReview = resolve;
  });
  const reviewRequestStarted = new Promise<void>((resolve) => {
    reviewStarted = resolve;
  });
  const cancelGate = new Promise<void>((resolve) => {
    releaseCancel = resolve;
  });
  const cancelRequestStarted = new Promise<void>((resolve) => {
    cancelStarted = resolve;
  });
  let reviewRequests = 0;
  let cancelRequests = 0;
  const review = {
    schema_version: 1,
    review_id: reviewId,
    conversation_id: conversationA,
    run_id: runNew,
    task_id: null,
    status: "pending",
    prompt: "确认继续执行",
    decision: null,
    comment: null,
    requested_at: occurredAt,
    resolved_at: null,
  };
  const report = {
    schema_version: 1,
    artifact_id: reportId,
    conversation_id: conversationA,
    run_id: runNew,
    source_event_id: null,
    kind: "report",
    media_type: "text/csv",
    size_bytes: 12,
    sha256: "c".repeat(64),
    metadata: { filename: "analysis-report.csv" },
    created_at: occurredAt,
  };

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (path === "/api/v1/conversations") {
      await json(route, {
        schema_version: 1,
        items: [conversation(conversationA, "Human review")],
        page: { next_cursor: null, has_more: false },
      });
      return;
    }
    if (path === `/api/v1/conversations/${conversationA}`) {
      await json(route, conversation(conversationA, "Human review"));
      return;
    }
    if (path.endsWith("/history")) {
      await json(
        route,
        history(conversationA, [run(runNew, conversationA, "review_required")]),
      );
      return;
    }
    if (path.endsWith("/artifacts")) {
      await json(route, artifacts(conversationA, [report]));
      return;
    }
    if (path.endsWith("/reviews")) {
      await json(route, reviews(conversationA, [review]));
      return;
    }
    if (path === `/api/v1/runs/${runNew}/events`) {
      await json(route, eventReplay(runNew, []));
      return;
    }
    if (path === `/api/v1/runs/${runNew}/events/stream`) {
      await route.fulfill({
        status: 200,
        headers: { "content-type": "text/event-stream" },
        body: ": heartbeat\n\n",
      });
      return;
    }
    if (path === `/api/v1/reviews/${reviewId}/decision`) {
      reviewRequests += 1;
      reviewStarted();
      await reviewGate;
      await json(route, {
        schema_version: 1,
        review: {
          ...review,
          status: "approved",
          decision: "approve",
          resolved_at: occurredAt,
        },
        run: run(runNew, conversationA, "running"),
      });
      return;
    }
    if (path === `/api/v1/runs/${runNew}/cancel`) {
      cancelRequests += 1;
      cancelStarted();
      await cancelGate;
      await json(route, {
        schema_version: 1,
        run: { ...run(runNew, conversationA, "running"), status: "cancelling" },
        accepted: true,
      });
      return;
    }
    if (path === `/api/v1/artifacts/${reportId}/content`) {
      await route.fulfill({
        status: 200,
        contentType: "text/csv",
        body: "cluster,label\n0,T cell\n",
      });
      return;
    }
    await route.fulfill({ status: 404, body: `unexpected ${request.method()} ${path}` });
  });

  await page.goto(`/conversation/${conversationA}`);
  await page.getByRole("tab", { name: /审核 1/ }).click();
  const approve = page.getByRole("button", { name: "批准", exact: true });
  await approve.evaluate((button) => {
    button.click();
    button.click();
  });
  await reviewRequestStarted;
  expect(reviewRequests).toBe(1);
  releaseReview();
  await expect(approve).toBeEnabled();

  const cancel = page.getByRole("button", { name: "取消运行" });
  await cancel.evaluate((button) => {
    button.click();
    button.click();
  });
  await cancelRequestStarted;
  expect(cancelRequests).toBe(1);
  releaseCancel();

  await page.getByRole("tab", { name: /产物 1/ }).click();
  const downloadPromise = page.waitForEvent("download");
  await page
    .getByRole("button", { name: "下载 analysis-report.csv" })
    .click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe("analysis-report.csv");
});
