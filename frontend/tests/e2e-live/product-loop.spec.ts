import { expect, test } from "@playwright/test";

const fixture = "tests/fixtures/smoke.csv";

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
  await expect(page.getByRole("tab", { name: "能力 0" })).toBeVisible();

  const conversationUrl = page.url();
  await page.reload();
  await expect(page).toHaveURL(conversationUrl);
  await expect(
    page.getByText(/marker gene 可以揭示各群的特征表达/),
  ).toBeVisible();
  await expect(page.getByRole("tab", { name: "任务 0" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "能力 0" })).toBeVisible();
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
