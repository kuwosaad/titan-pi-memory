import { REQUIRED_SKILLS } from "./skills"
import { namespaceEnvironment } from "./namespace"
import type { Plugin } from "@opencode/plugin"
import type { Skill } from "@opencode/schema/skill"
import { spawn } from "node:child_process"
import { readFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"
import { V2Capture, captureTypes } from "./v2-capture"

const root = join(dirname(fileURLToPath(import.meta.url)), "..")

const launcher = join(root, "scripts/titan_runtime.py")

function openProviderSetup(): Promise<void> {
  return new Promise((resolve, reject) => {
    const child = spawn("python3", [launcher, "configure", "--agent", "opencode"], {
      stdio: ["ignore", "pipe", "ignore"],
      env: { ...process.env, ...namespaceEnvironment() },
    })
    let line = ""
    child.once("error", reject)
    child.once("exit", code => reject(new Error(`Titan provider setup exited (${code}) before opening the form.`)))
    child.stdout.on("data", chunk => {
      line = (line + chunk.toString()).slice(-2048)
      if (line.includes("[titan] Open onboarding:")) {
        child.stdout.destroy()
        child.unref()
        resolve()
      }
    })
  })
}

export default {
  id: "titan-memory",
  async setup(ctx) {
    await ctx.mcp.transform(editor => {
      const configured = editor.get("titan-memory")
      // Replace the legacy Titan CLI entry while leaving a user-managed endpoint in place.
      if (configured) {
        if (configured.type !== "local") return
        const command = configured.command
        const args = command.slice(-3).join(" ")
        const legacy = command.length === 4 && command[0] === "titan"
        const bundled = command.length === 5 && command[1] === launcher
        if (args !== "mcp --agent opencode" || !(legacy || bundled)) return
      }
      editor.set("titan-memory", {
        ...configured, type: "local", command: ["python3", launcher, "mcp", "--agent", "opencode"],
        codemode: configured?.codemode ?? false, environment: { ...configured?.environment, ...namespaceEnvironment() },
        timeout: { startup: 180000, ...configured?.timeout },
      })
    })
    await ctx.command.transform(editor => editor.add({
      name: "titan-setup", description: "Open the local Titan provider configuration form", execute: openProviderSetup,
    }))
    // Skills are part of the plugin, so disabling it removes them from the host catalog.
    const skillsRoot = join(root, "skills")
    const skills = REQUIRED_SKILLS.map(name => {
      const path = join(skillsRoot, name, "SKILL.md")
      const source = readFileSync(path, "utf8")
      const [header, content] = source.slice(4).split(/\n---\n(.*)/s)
      return { id: name as Skill.ID, name: name as Skill.Name, path: path as Skill.Info["path"], content,
        description: header.match(/^description: (.+)$/m)?.[1] ?? name }
    })
    await ctx.skill.transform(editor => {
      for (const skill of skills) if (!editor.get(skill.id)) editor.add(skill)
    })
    const controller = new AbortController()
    const capture = new V2Capture()
    const run = async () => {
      for await (const event of ctx.event.subscribe({ signal: controller.signal, types: [...captureTypes] })) {
        if (event.location && event.location.directory !== ctx.location.directory) continue
        try { capture.accept(event) }
        catch (error) { console.warn("Titan could not write an OpenCode trace", error) }
      }
    }
    const running = run().catch(error => {
      if (!controller.signal.aborted) console.warn("Titan OpenCode capture stopped", error)
    })
    return () => { controller.abort(); return running }
  },
} satisfies Plugin.Plugin
