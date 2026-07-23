import { createBrowserRouter, Navigate } from "react-router";

import { ConversationRoute } from "./ConversationRoute";

export const router = createBrowserRouter([
  { path: "/", element: <ConversationRoute /> },
  { path: "/conversation/:conversationId", element: <ConversationRoute /> },
  { path: "*", element: <Navigate replace to="/" /> },
]);
