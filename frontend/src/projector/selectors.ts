import type { RunProjection } from "./model";

export const selectTasks = (state: RunProjection) => Object.values(state.tasks);

export const selectCapabilities = (state: RunProjection) =>
  Object.values(state.capabilities);

export const selectReviews = (state: RunProjection) => Object.values(state.reviews);

export const selectArtifacts = (state: RunProjection) =>
  Object.values(state.artifacts);

export const selectPendingReviews = (state: RunProjection) =>
  selectReviews(state).filter((review) => review.status === "pending");
