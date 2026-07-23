import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { generatedDirectory, renderContracts } from "./contract-codegen.mjs";

await mkdir(generatedDirectory, { recursive: true });

for (const [fileName, contents] of await renderContracts()) {
  await writeFile(join(generatedDirectory, fileName), contents, "utf8");
  console.log(`generated src/generated/${fileName}`);
}
