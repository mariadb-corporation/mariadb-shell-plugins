import { builtinModules } from "node:module";
import { defineConfig } from "vite";

// The extension host loads a single CommonJS file and provides the `vscode`
// module itself, so everything except `vscode` and the Node built-ins is
// bundled in.
const external = [
    "vscode",
    ...builtinModules,
    ...builtinModules.map((name) => `node:${name}`),
];

export default defineConfig({
    build: {
        target: "node20",
        outDir: "dist",
        sourcemap: true,
        minify: false,
        emptyOutDir: true,
        lib: {
            entry: "src/extension.ts",
            formats: ["cjs"],
            fileName: () => "extension.js",
        },
        rollupOptions: {
            external,
            output: {
                interop: "auto",
            },
        },
    },
});
