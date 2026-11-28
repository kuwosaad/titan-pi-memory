import { mkdir } from "node:fs/promises"
import { join } from "node:path"

export const BUNDLE_FILENAME = "titan_v2_spool_plugin.ts"

async function buildFile(source: string, filename: string): Promise<string> {
  const root = join(import.meta.dir, "..")
  const entrypoint = join(root, "src", source)
  const outfile = join(root, "dist", filename)
  await mkdir(join(root, "dist"), { recursive: true })
  const result = await Bun.build({
    entrypoints: [entrypoint],
    outfile,
    target: "bun",
    format: "esm",
    minify: false,
    sourcemap: "none",
  })
  if (!result.success) {
    const details = result.logs.map((log) => log.message).join("\n")
    throw new Error(`Unable to build OpenCode plugin${details ? `:\n${details}` : ""}`)
  }
  // Bun 1.3 returns an in-memory output when invoked through its JS API.
  // Write it explicitly so the installer always consumes the generated bundle.
  await Bun.write(outfile, result.outputs[0])
  return outfile
}

export async function buildBundle(): Promise<string> {
  await buildFile("server.ts", "server.js")
  return buildFile("index.ts", BUNDLE_FILENAME)
}

if (import.meta.main) {
  console.log(await buildBundle())
}
