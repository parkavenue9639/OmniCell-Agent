import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router";

import { AppProviders } from "./app/providers";
import { router } from "./app/router";
import "./styles.css";

const root = document.getElementById("root");
if (root === null) {
  throw new Error("缺少 #root 挂载节点");
}

createRoot(root).render(
  <StrictMode>
    <AppProviders>
      <RouterProvider router={router} />
    </AppProviders>
  </StrictMode>,
);
