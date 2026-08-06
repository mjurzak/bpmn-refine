import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// jsdom implements no layout, so scrollIntoView is missing entirely
Element.prototype.scrollIntoView = () => {};

// jsdom keeps the document between tests, so a query could match a stale node
afterEach(() => {
  cleanup();
});
