import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { generatedDirectory, renderContracts } from "./contract-codegen.mjs";

const drifted = [];

for (const [fileName, expected] of await renderContracts()) {
  let actual;
  try {
    actual = await readFile(join(generatedDirectory, fileName), "utf8");
  } catch (error) {
    if (error && error.code === "ENOENT") {
      drifted.push(`${fileName}（缺失）`);
      continue;
    }
    throw error;
  }

  if (actual !== expected) {
    drifted.push(fileName);
  }
}

if (drifted.length > 0) {
  console.error(
    `契约生成物已漂移：${drifted.join("、")}。请运行 npm run contracts:generate。`,
  );
  process.exitCode = 1;
} else {
  console.log("契约生成物与 ../../contracts 快照一致。");
}
