import { buildBundle } from "./build"
import { installOpenCodePlugin } from "./install"

export async function setupOpenCodePlugin(): Promise<void> {
  await buildBundle()
  const result = await installOpenCodePlugin()
  console.log(`Installed Titan Memory for OpenCode 1.x at ${result.pluginPath}`)
}

if (import.meta.main) await setupOpenCodePlugin()
