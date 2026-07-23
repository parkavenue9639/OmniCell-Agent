import type { components, operations } from "../generated/openapi-v1";
import {
  clientFor,
  type ApiCallOptions,
  unwrapApiResponse,
} from "./client";

export type Review = components["schemas"]["ReviewRead"];
export type ReviewListResponse = components["schemas"]["ReviewListResponse"];
export type ReviewListQuery = NonNullable<
  operations["listReviews"]["parameters"]["query"]
>;
export type ReviewDecisionRequest =
  components["schemas"]["ReviewDecisionRequest"];
export type ReviewDecisionResponse =
  components["schemas"]["ReviewDecisionResponse"];

export async function listReviews(
  conversationId: string,
  query: ReviewListQuery = {},
  options?: ApiCallOptions,
): Promise<ReviewListResponse> {
  return unwrapApiResponse(
    await clientFor(options).GET(
      "/api/v1/conversations/{conversation_id}/reviews",
      {
        params: {
          path: { conversation_id: conversationId },
          query,
        },
        signal: options?.signal,
      },
    ),
  );
}

export async function decideReview(
  reviewId: string,
  body: ReviewDecisionRequest,
  options?: ApiCallOptions,
): Promise<ReviewDecisionResponse> {
  return unwrapApiResponse(
    await clientFor(options).POST("/api/v1/reviews/{review_id}/decision", {
      params: { path: { review_id: reviewId } },
      body,
      signal: options?.signal,
    }),
  );
}
