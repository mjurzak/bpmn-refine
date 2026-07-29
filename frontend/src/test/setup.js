import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// jsdom implements no layout, so scrollIntoView is missing entirely. Components
// that keep a chat log pinned to the bottom call it on mount.
Element.prototype.scrollIntoView = () => {};

// jsdom keeps the document between tests; without this a query can match a node
// rendered by a previous case
afterEach(() => {
  cleanup();
});
