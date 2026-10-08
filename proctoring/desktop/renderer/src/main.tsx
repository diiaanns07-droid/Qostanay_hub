// Renderer entry (A07). The renderer never opens a camera and never learns the backend port/token:
// everything goes through window.qorgau (A06 preload) or the explicitly labelled FixtureBridge (dev/demo builds).
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { selectBridge } from "./bridge/selectBridge";
import "./styles.css";

const root = document.getElementById("root");
if (!root) throw new Error("#root missing");
void selectBridge().then((choice) =>
  createRoot(root).render(
    <StrictMode>
      <App choice={choice} />
    </StrictMode>,
  ),
);
