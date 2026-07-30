import { expect, test } from "@playwright/test";

const fixture = "tests/fixtures/smoke.csv";
const scienceFixture = process.env.OMNICELL_LIVE_SCIENCE_FIXTURE;

async function createConversation(page: import("@playwright/test").Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "新建分析对话" }).click();
  await expect(page).toHaveURL(/\/conversation\/[0-9a-f-]{36}$/);
  await expect(page.getByRole("heading", { name: "新分析对话" })).toBeVisible();
}

test("真实 PostgreSQL/SSE 闭环支持上传、审核恢复、刷新重连与下载", async ({
  page,
}) => {
  const streamRequests: string[] = [];
  page.on("request", (request) => {
    if (new URL(request.url()).pathname.endsWith("/events/stream")) {
      streamRequests.push(request.url());
    }
  });

  await createConversation(page);
  await page.locator('input[type="file"]').setInputFiles(fixture);
  await expect(page.getByText("smoke.csv", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("已绑定当前数据集")).toBeVisible();

  await page
    .getByRole("textbox", { name: "分析指令" })
    .fill("基于上传的数据生成需要审核的真实分析报告");
  await page.getByRole("button", { name: "发送分析指令" }).click();

  const approveAndContinue = page.getByRole("button", {
    name: "批准并继续",
  });
  await expect(approveAndContinue).toBeVisible();
  await expect(page.getByRole("tab", { name: /事件 [1-9][0-9]*/ })).toBeVisible();
  await expect.poll(() => streamRequests.length).toBeGreaterThanOrEqual(1);

  const conversationUrl = page.url();
  await page.reload();
  await expect(page).toHaveURL(conversationUrl);
  await expect(page.getByRole("heading", { name: "新分析对话" })).toBeVisible();
  await expect(approveAndContinue).toBeVisible();
  await expect.poll(() => streamRequests.length).toBeGreaterThanOrEqual(2);

  await approveAndContinue.click();
  await expect(
    page.getByText("真实后端分析完成，报告已经持久化并可下载。"),
  ).toBeVisible();
  await expect(page.getByText("已完成", { exact: true }).last()).toBeVisible();

  await page.getByRole("tab", { name: /产物 1/ }).click();
  const downloadPromise = page.waitForEvent("download");
  await page
    .getByRole("button", { name: "下载 live-analysis-report.csv" })
    .click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe("live-analysis-report.csv");
  const path = await download.path();
  expect(path).not.toBeNull();
  expect(await import("node:fs/promises").then((fs) => fs.readFile(path!, "utf8")))
    .toBe("cluster,label\n0,T cell\n1,B cell\n");

  await page.getByRole("button", { name: "全部会话" }).click();
  await expect(page.getByRole("tab", { name: /产物 2/ })).toBeVisible();
});

test("普通问答以自然文本完成 Run 且不创建 Task 或 capability", async ({
  page,
}) => {
  await createConversation(page);
  await page
    .getByRole("textbox", { name: "分析指令" })
    .fill("为什么单细胞聚类后还需要 marker gene？请简单解释。");
  await page.getByRole("button", { name: "发送分析指令" }).click();

  await expect(
    page.getByText(/聚类只把表达模式相近的细胞分成群/),
  ).toBeVisible();
  await expect(page.getByText("已完成", { exact: true }).last()).toBeVisible();
  await expect(page.getByRole("tab", { name: "任务 0" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Tool 0" })).toBeVisible();

  const conversationUrl = page.url();
  await page.reload();
  await expect(page).toHaveURL(conversationUrl);
  await expect(
    page.getByText(/marker gene 可以揭示各群的特征表达/),
  ).toBeVisible();
  await expect(page.getByRole("tab", { name: "任务 0" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Tool 0" })).toBeVisible();
});

test("真实 Docker 原子 Tool 的科研证据会阻止错误总结先发布", async ({
  page,
}) => {
  if (!scienceFixture) {
    throw new Error("缺少 OMNICELL_LIVE_SCIENCE_FIXTURE");
  }
  await createConversation(page);
  await page.locator('input[type="file"]').setInputFiles(scienceFixture);
  await expect(page.getByText("science-fixture.h5ad", { exact: true }).first())
    .toBeVisible();
  await page
    .getByRole("textbox", { name: "分析指令" })
    .fill("请把当前表达矩阵做归一化和 log1p，并简要告诉我实际做了什么。");
  await page.getByRole("button", { name: "发送分析指令" }).click();

  await expect(
    page.getByText(/normalize_expression：executed/),
  ).toBeVisible();
  await expect(
    page.getByText("本次复用了归一化和 log1p，没有重新执行。", { exact: true }),
  ).toHaveCount(0);
  await expect(page.getByText("已完成", { exact: true }).last()).toBeVisible();
  await expect(page.getByRole("tab", { name: /Tool 1/ })).toBeVisible();

  const conversationUrl = page.url();
  await page.reload();
  await expect(page).toHaveURL(conversationUrl);
  await expect(
    page.getByText(/normalize_expression：executed/),
  ).toBeVisible();
  await expect(
    page.getByText("本次复用了归一化和 log1p，没有重新执行。", { exact: true }),
  ).toHaveCount(0);
});

test("真实运行可从 Web 提交取消并在刷新后恢复 cancelled 终态", async ({
  page,
}) => {
  await createConversation(page);
  await page
    .getByRole("textbox", { name: "分析指令" })
    .fill("启动受控阻塞运行，随后取消");
  await page.getByRole("button", { name: "发送分析指令" }).click();

  const cancel = page.getByRole("button", { name: "取消运行" });
  await expect(cancel).toBeVisible();
  await cancel.click();
  await expect(page.getByText("已取消", { exact: true }).last()).toBeVisible();
  await expect(page.getByText("运行已取消", { exact: true })).toBeVisible();

  const conversationUrl = page.url();
  await page.reload();
  await expect(page).toHaveURL(conversationUrl);
  await expect(page.getByText("已取消", { exact: true }).last()).toBeVisible();
  await expect(page.getByRole("tab", { name: /事件 [1-9][0-9]*/ })).toBeVisible();
});

test("跨会话记忆开启一次后可自然记住、自动召回并在忘记后停止命中", async ({
  page,
}) => {
  await createConversation(page);

  await page.getByRole("tab", { name: /记忆 0/ }).click();
  await page
    .getByRole("checkbox", { name: /跨会话记忆/ })
    .click();
  await expect(
    page.getByRole("dialog", { name: "开启长期记忆" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "开启", exact: true }).click();
  await expect(
    page.getByRole("checkbox", { name: /跨会话记忆/ }),
  ).toBeChecked();
  await expect(page.getByText("记忆已开启")).toBeVisible();

  const oneOffPrompt = page.getByRole("textbox", { name: "分析指令" });
  await oneOffPrompt.fill("这次只用一句话说明当前状态。");
  await page.getByRole("button", { name: "发送分析指令" }).click();
  await expect(
    page.getByText("这是一条仅适用于当前回答的临时要求。"),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "有一条记忆待确认" }),
  ).toHaveCount(0);

  await oneOffPrompt.fill(
    "以后和我打招呼时称我为“小木”，现在只用一句话说明当前状态。",
  );
  await page.getByRole("button", { name: "发送分析指令" }).click();
  await expect(
    page.getByText("已按当前要求简要回应，但混合消息不会被提议为长期记忆。"),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "有一条记忆待确认" }),
  ).toHaveCount(0);

  await oneOffPrompt.fill("当前数据集的这个聚类看起来像 T 细胞。");
  await page.getByRole("button", { name: "发送分析指令" }).click();
  await expect(
    page.getByText("这是当前数据相关的观察，不会被提议为跨会话记忆。"),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "有一条记忆待确认" }),
  ).toHaveCount(0);

  await oneOffPrompt.fill("今天阳光很好，随便聊聊吧。");
  await page.getByRole("button", { name: "发送分析指令" }).click();
  await expect(
    page.getByText("当然可以，普通闲聊不会被提议为长期记忆。"),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "有一条记忆待确认" }),
  ).toHaveCount(0);

  const rememberPrompt = page.getByRole("textbox", { name: "分析指令" });
  await rememberPrompt.fill("以后和我打招呼时都称我为“小木”。");
  await page.getByRole("button", { name: "发送分析指令" }).click();
  await expect(
    page.getByRole("heading", { name: "有一条记忆待确认" }),
  ).toBeVisible();
  await expect(
    page.getByText("好的，这条称呼偏好正在等待你确认。"),
  ).toBeVisible();
  const proposalConversationUrl = page.url();
  await page.reload();
  await expect(page).toHaveURL(proposalConversationUrl);
  await expect(
    page.getByRole("heading", { name: "有一条记忆待确认" }),
  ).toBeVisible();
  await expect(page.getByText("将完整保存的来源消息")).toBeVisible();
  await expect(
    page
      .locator(".oc-memory-confirm-preview")
      .getByText("以后和我打招呼时都称我为“小木”。", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "确认记住" }).click();
  await expect(page.getByRole("button", { name: "确认记住" })).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: "已记住这条内容" }),
  ).toBeVisible();

  const rejectedCandidate =
    "我长期在 macOS 上使用 OrbStack 作为本机 Docker 环境。";
  await rememberPrompt.fill(rejectedCandidate);
  await page.getByRole("button", { name: "发送分析指令" }).click();
  const rejectedProposalCard = page
    .locator('.oc-memory-activity[data-operation="proposal"]')
    .last();
  await expect(
    rejectedProposalCard.getByRole("heading", {
      name: "有一条记忆待确认",
    }),
  ).toBeVisible();
  await expect(
    rejectedProposalCard.getByText(rejectedCandidate, { exact: true }),
  ).toBeVisible();
  await rejectedProposalCard
    .getByRole("button", { name: "不采用并清除" })
    .click();
  await rejectedProposalCard
    .getByRole("button", { name: "确认不采用并清除" })
    .click();
  await expect(
    rejectedProposalCard.getByRole("heading", {
      name: "已拒绝这条记忆候选",
    }),
  ).toBeVisible();
  await expect(
    rejectedProposalCard.getByText(rejectedCandidate, { exact: true }),
  ).toHaveCount(0);

  const rejectedConversationUrl = page.url();
  await page.reload();
  await expect(page).toHaveURL(rejectedConversationUrl);
  const restoredRejectedCard = page
    .locator('.oc-memory-activity[data-operation="proposal"]')
    .last();
  await expect(
    restoredRejectedCard.getByRole("heading", {
      name: "已拒绝这条记忆候选",
    }),
  ).toBeVisible();
  await expect(
    restoredRejectedCard.getByText(rejectedCandidate, { exact: true }),
  ).toHaveCount(0);

  await createConversation(page);
  const rememberedPrompt = page.getByRole("textbox", { name: "分析指令" });
  const rememberedSubmit = page.getByRole("button", {
    name: "发送分析指令",
  });
  await expect(page.getByText("记忆已开启")).toBeVisible();
  await rememberedPrompt.fill("请按跨会话称呼偏好向我问好。");
  await expect(rememberedSubmit).toBeEnabled();
  await rememberedSubmit.click();

  await expect(page.getByText("你好，小木。很高兴继续和你一起工作。")).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "检查相关记忆" }),
  ).toBeVisible();
  await expect(
    page.getByText("当前回答使用了 1 条相关记忆"),
  ).toBeVisible();

  const rememberedConversationUrl = page.url();
  await page.reload();
  await expect(page).toHaveURL(rememberedConversationUrl);
  await expect(page.getByText("你好，小木。很高兴继续和你一起工作。")).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "检查相关记忆" }),
  ).toBeVisible();

  const stopUsingPrompt = page.getByRole("textbox", { name: "分析指令" });
  await stopUsingPrompt.fill("以后不要再使用这个称呼偏好了。");
  await page.getByRole("button", { name: "发送分析指令" }).click();
  await expect(
    page.getByRole("heading", { name: "确认忘记这条内容" }),
  ).toBeVisible();
  await expect(
    page.getByText("已发起停止使用该称呼偏好的确认请求。"),
  ).toBeVisible();
  await page.getByRole("button", { name: "确认忘记" }).click();
  await expect(
    page.getByRole("heading", { name: "已忘记这条内容" }),
  ).toBeVisible();

  await createConversation(page);
  const forgottenPrompt = page.getByRole("textbox", { name: "分析指令" });
  const forgottenSubmit = page.getByRole("button", {
    name: "发送分析指令",
  });
  await expect(page.getByText("记忆已开启")).toBeVisible();
  await forgottenPrompt.fill("请按跨会话称呼偏好向我问好。");
  await expect(forgottenSubmit).toBeEnabled();
  await forgottenSubmit.click();

  await expect(
    page.getByText("你好。我目前没有可用的跨会话称呼偏好。"),
  ).toBeVisible();
  await expect(
    page.getByText("没有匹配的长期记忆，按普通对话继续"),
  ).toBeVisible();
  await expect(page.getByText("你好，小木。很高兴继续和你一起工作。")).toHaveCount(
    0,
  );
});
