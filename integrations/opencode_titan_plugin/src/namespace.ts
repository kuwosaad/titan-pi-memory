import { homedir } from "node:os"
import { basename, dirname, join, resolve } from "node:path"

export function namespaceEnvironment() {
  const env = process.env
  const inherited = env.TITAN_HOME?.trim() || ""
  const namedAgent = env.TITAN_AGENT_NAME?.trim()
  const inAgents = Boolean(inherited) && basename(dirname(inherited)) === "agents"
  const foreign = (namedAgent && namedAgent !== "opencode") || (inAgents && basename(inherited) !== "opencode")
  const shared = env.TITAN_SHARED_HOME || (inAgents ? dirname(dirname(inherited)) : join(homedir(), ".titan"))
  const home = resolve(inherited && !foreign ? inherited : join(shared, "agents", "opencode"))
  const spool = !foreign && env.TITAN_SPOOL_DIR?.trim()
  return {
    TITAN_AGENT_NAME: "opencode",
    TITAN_HOME: home,
    TITAN_BASE_DIR: home,
    TITAN_SPOOL_DIR: resolve(spool || join(home, "traces")),
  }
}
