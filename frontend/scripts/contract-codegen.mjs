import { compile } from "json-schema-to-typescript";
import openapiTS, { astToString } from "openapi-typescript";
import ts from "typescript";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

export const generatedDirectory = fileURLToPath(
  new URL("../src/generated/", import.meta.url),
);

const openApiSource = new URL("../../contracts/openapi/v1.json", import.meta.url);
const eventSource = new URL(
  "../../contracts/events/v1.schema.json",
  import.meta.url,
);

const generatedHeader =
  "// 此文件由 frontend/scripts/generate-contracts.mjs 生成，请勿手工修改。\n";

async function readJson(url) {
  return JSON.parse(await readFile(url, "utf8"));
}

function binaryFormatToBlob(schemaObject) {
  if (
    schemaObject.type === "string" &&
    (schemaObject.format === "binary" ||
      schemaObject.contentMediaType === "application/octet-stream")
  ) {
    return ts.factory.createTypeReferenceNode("Blob");
  }
  return undefined;
}

async function renderOpenApiTypes() {
  const schema = await readJson(openApiSource);
  const ast = await openapiTS(schema, {
    alphabetize: true,
    transform: binaryFormatToBlob,
  });
  return `${generatedHeader}${astToString(ast)}`;
}

async function compileEventSchema(schema, rootName) {
  return compile(schema, rootName, {
    bannerComment: "",
    format: false,
    style: {
      singleQuote: false,
      semi: true,
      tabWidth: 2,
      trailingComma: "all",
    },
    unreachableDefinitions: true,
  });
}

async function renderEventTypes() {
  const schema = await readJson(eventSource);
  const combinedSchema = {
    $schema: schema.$schema,
    $defs: {
      ...schema.persisted.$defs,
      ...schema.transient.$defs,
      PersistedEvent: { oneOf: schema.persisted.oneOf },
      TransientEvent: { oneOf: schema.transient.oneOf },
    },
    oneOf: [
      { $ref: "#/$defs/PersistedEvent" },
      { $ref: "#/$defs/TransientEvent" },
    ],
  };
  const eventTypes = await compileEventSchema(combinedSchema, "EventContract");
  return `${generatedHeader}${eventTypes.trim()}\n`;
}

export async function renderContracts() {
  return new Map([
    ["openapi-v1.ts", await renderOpenApiTypes()],
    ["events-v1.ts", await renderEventTypes()],
  ]);
}
