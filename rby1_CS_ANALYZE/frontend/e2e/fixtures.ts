import { test as base, expect } from "@playwright/test";

function isLoopback(rawURL: string): boolean {
  const url = new URL(rawURL);
  return url.hostname === "127.0.0.1" || url.hostname === "localhost" || url.hostname === "[::1]";
}

export const test = base.extend<{ denyNonLoopback: void }>({
  denyNonLoopback: [
    async ({ context }, use) => {
      await context.route("**/*", async (route) => {
        if (isLoopback(route.request().url())) await route.continue();
        else await route.abort("blockedbyclient");
      });
      await use();
    },
    { auto: true },
  ],
});

export { expect };
