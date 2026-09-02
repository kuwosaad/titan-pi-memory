import type { Plugin } from "@opencode-ai/plugin"
import { CaptureCoordinator } from "./capture"

export const TitanMemoryPlugin: Plugin = async ({ client, directory }) => {
  const coordinator = new CaptureCoordinator(client, directory)
  return {
    event: async ({ event }) => {
      await coordinator.onEvent(event)
    },
  }
}
