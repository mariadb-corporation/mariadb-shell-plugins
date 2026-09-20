import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

// Unit tests run in plain Node, so the `vscode` module - which only exists
// inside the extension host - is resolved to a hand written test double.
const vscodeMock = fileURLToPath(
    new URL("./src/test/mocks/vscode.ts", import.meta.url),
);

export default defineConfig({
    resolve: {
        alias: [{ find: /^vscode$/, replacement: vscodeMock }],
    },
    test: {
        environment: "node",
        include: ["src/**/*.test.ts"],
        coverage: {
            provider: "v8",
            include: ["src/**/*.ts"],
            exclude: ["src/test/**"],
        },
    },
});
