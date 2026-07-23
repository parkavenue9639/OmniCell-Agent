import { describe, expect, it, vi } from "vitest";

import type { Artifact } from "./artifacts";
import { uploadArtifact } from "./artifacts";
import { createApiClient } from "./client";

const artifactResponse: Artifact = {
  schema_version: 1,
  artifact_id: "c244088f-979a-482e-880a-9bfcbd36ba27",
  conversation_id: "13269a73-8a64-47f6-ad95-6d6063a3e5cc",
  kind: "dataset",
  size_bytes: 8,
  sha256: "f00d",
  created_at: "2026-07-23T08:00:00Z",
};

describe("uploadArtifact", () => {
  it("使用 FormData，并由运行时生成 multipart boundary", async () => {
    let capturedRequest: Request | undefined;
    const fetchMock = vi.fn(async (request: Request) => {
      capturedRequest = request.clone();
      return new Response(JSON.stringify(artifactResponse), {
        status: 201,
        headers: { "Content-Type": "application/json" },
      });
    });
    const client = createApiClient({
      baseUrl: "https://api.example.test",
      fetch: fetchMock,
    });
    const file = new File(["cell,data"], "cells.csv", {
      type: "text/csv",
    });

    await uploadArtifact(
      "13269a73-8a64-47f6-ad95-6d6063a3e5cc",
      { file, kind: "dataset" },
      { client },
    );

    expect(capturedRequest).toBeDefined();
    const request = capturedRequest!;
    expect(request.headers.get("content-type")).toMatch(
      /^multipart\/form-data; boundary=/,
    );
    const form = await request.formData();
    const uploadedFile = form.get("file");
    expect(uploadedFile).toMatchObject({ size: 9, type: "text/csv" });
    expect(form.get("kind")).toBe("dataset");
  });
});
