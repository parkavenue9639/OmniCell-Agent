import createClient, {
  type Client,
  type ClientOptions,
} from "openapi-fetch";

import type { paths } from "../generated/openapi-v1";
import { normalizeApiError } from "./errors";

export type ApiClient = Client<paths>;

export interface ApiCallOptions {
  client?: ApiClient;
  signal?: AbortSignal;
}

export function createApiClient(options: ClientOptions = {}): ApiClient {
  return createClient<paths>({
    baseUrl: "",
    ...options,
  });
}

export const apiClient = createApiClient();

export function clientFor(options?: ApiCallOptions): ApiClient {
  return options?.client ?? apiClient;
}

export function unwrapApiResponse<T>(result: {
  data?: T;
  error?: unknown;
  response: Response;
}): T {
  if (result.data !== undefined) {
    return result.data;
  }
  throw normalizeApiError(result.error, result.response);
}
