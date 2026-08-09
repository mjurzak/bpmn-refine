import React, { lazy, Suspense } from "react";
import ReactDOM from "react-dom/client";
import "./app.css";

const RootComponent = lazy(() =>
  window.location.pathname.startsWith("/dataset-compare")
    ? import("./components/DatasetComparePage.jsx")
    : import("./App.jsx"),
);

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <Suspense fallback={<div className="route-loading">Loading…</div>}>
      <RootComponent />
    </Suspense>
  </React.StrictMode>
);
