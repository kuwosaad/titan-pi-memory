import { describe, expect, test } from "bun:test"
import { mkdtemp, mkdir, readFile, readdir, stat, writeFile } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"

import { installOpenCodePlugin } from "../scripts/install"
import { buildBundle } from "../scripts/build"

const SKILLS = [
  "titan-memory-workflow",
  "titan-doctor-workflow",
  "titan-cluster-graph-workflow",
  "titan-patterns-workflow",
  "memory-sync",
]

async function fixture() {
  const root = await mkdtemp(join(tmpdir(), "titan-opencode-install-"))
  const source = join(root, "bundle.ts")
  const skills = join(root, "skills")
  await mkdir(skills, { recursive: true })
  await writeFile(source, "export const TitanMemoryPlugin = async () => ({ event: async () => {} })\n")
  for (const skill of SKILLS) {
    await mkdir(join(skills, skill), { recursive: true })
    await writeFile(join(skills, skill, "SKILL.md"), `# ${skill}\n`)
  }
  return { root, source, skills }
}

describe("installOpenCodePlugin", () => {
  test("installs the bundle, skills, and classic MCP config without losing JSONC comments", async () => {
    const { root, source, skills } = await fixture()
    const configPath = join(root, "config", "opencode.jsonc")
    await mkdir(join(root, "config"), { recursive: true })
    await writeFile(
      configPath,
      `// Keep this comment\n{\n  "theme": "dark",\n  "mcp": {\n    // Keep this MCP comment\n    "other": { "type": "local", "command": ["other"] }\n  }\n}\n`,
    )

    const result = await installOpenCodePlugin({
      homeDir: root,
      configPath,
      pluginSource: source,
      skillSourceDir: skills,
      checkCommands: false,
    })

    const plugin = await readFile(join(root, ".config", "opencode", "plugins", "titan_v2_spool_plugin.ts"), "utf8")
    const config = await readFile(configPath, "utf8")
    expect(plugin).toBe(await readFile(source, "utf8"))
    expect(plugin).toContain("TitanMemoryPlugin")
    expect(config).toContain("// Keep this comment")
    expect(config).toContain("// Keep this MCP comment")
    expect(config).toContain('"theme": "dark"')
    expect(config).toContain('"titan-memory"')
    expect(config).toContain('"command": ["titan", "mcp", "--agent", "opencode"]')
    expect(result.configPath).toBe(configPath)
    expect(result.backups).toEqual([`${configPath}.bak`])

    for (const skill of SKILLS) {
      expect(await readFile(join(root, ".config", "opencode", "skills", skill, "SKILL.md"), "utf8")).toBe(`# ${skill}\n`)
    }
    expect((await stat(join(root, ".config", "opencode"))).mode & 0o777).toBe(0o700)
    expect((await stat(join(root, ".config", "opencode", "plugins"))).mode & 0o777).toBe(0o700)
    expect((await stat(join(root, ".config", "opencode", "skills"))).mode & 0o777).toBe(0o700)
    expect((await stat(join(root, ".config", "opencode", "plugins", "titan_v2_spool_plugin.ts"))).mode & 0o777).toBe(0o600)
  })

  test("builds the single-file bundle at the installer input path", async () => {
    const bundlePath = await buildBundle()
    const bundle = await readFile(bundlePath, "utf8")
    expect(bundlePath.endsWith("dist/titan_v2_spool_plugin.ts")).toBe(true)
    expect(bundle).toContain("export {")
    expect(bundle).toContain("TitanMemoryPlugin")
  })

  test("backs up a changed plugin and is idempotent for an identical reinstall", async () => {
    const { root, source, skills } = await fixture()
    const configPath = join(root, "opencode.json")
    await writeFile(configPath, "{\n  \"theme\": \"dark\"\n}\n")
    const options = { homeDir: root, configPath, pluginSource: source, skillSourceDir: skills, checkCommands: false }

    await installOpenCodePlugin(options)
    const firstConfig = await readFile(configPath, "utf8")
    const firstPlugin = await readFile(join(root, ".config", "opencode", "plugins", "titan_v2_spool_plugin.ts"), "utf8")
    const second = await installOpenCodePlugin(options)
    expect(second.backups).toEqual([])
    expect(await readFile(configPath, "utf8")).toBe(firstConfig)
    expect(await readFile(join(root, ".config", "opencode", "plugins", "titan_v2_spool_plugin.ts"), "utf8")).toBe(firstPlugin)
    expect((await readdir(join(root, ".config", "opencode", "plugins"))).filter((name) => name.includes(".bak")).length).toBe(0)

    await writeFile(source, "export const TitanMemoryPlugin = async () => ({ event: async () => 'new' })\n")
    const changed = await installOpenCodePlugin(options)
    expect(changed.backups).toHaveLength(1)
    expect(await readdir(join(root, ".config", "opencode", "plugins"))).toContain("titan_v2_spool_plugin.ts.bak")
  })

  test("accepts a classic OpenCode 1.x runner and rejects newer majors", async () => {
    const { root, source, skills } = await fixture()
    const configPath = join(root, "opencode.jsonc")
    const runner = async () => ({ stdout: "1.18.26\n", stderr: "", exitCode: 0 })
    const result = await installOpenCodePlugin({
      homeDir: root,
      configPath,
      pluginSource: source,
      skillSourceDir: skills,
      commandRunner: runner,
    })
    expect(result.opencodeVersion).toBe("1.18.26")

    await expect(installOpenCodePlugin({
      homeDir: root,
      configPath: join(root, "new.json"),
      pluginSource: source,
      skillSourceDir: skills,
      commandRunner: async () => ({ stdout: "2.0.0\n", stderr: "", exitCode: 0 }),
    })).rejects.toThrow("OpenCode 1.x is required")
  })

  test("repairs an invalid mcp value without creating a duplicate JSON key", async () => {
    const { root, source, skills } = await fixture()
    const configPath = join(root, "opencode.json")
    await writeFile(configPath, '{\n  "mcp": []\n}\n')
    await installOpenCodePlugin({ homeDir: root, configPath, pluginSource: source, skillSourceDir: skills, checkCommands: false })
    const config = await readFile(configPath, "utf8")
    expect(config.match(/"mcp"\s*:/g)).toHaveLength(1)
    expect(config).toContain('"titan-memory"')
  })

  test("uses the active XDG config root for config, plugin, and skills", async () => {
    const { root, source, skills } = await fixture()
    const xdgRoot = join(root, "xdg")
    const configDir = join(xdgRoot, "opencode")
    await mkdir(configDir, { recursive: true })
    const configPath = join(configDir, "opencode.json")
    await writeFile(configPath, '{"project": "kept"}\n')
    const previous = process.env.XDG_CONFIG_HOME
    process.env.XDG_CONFIG_HOME = xdgRoot
    try {
      const result = await installOpenCodePlugin({ homeDir: root, pluginSource: source, skillSourceDir: skills, checkCommands: false })
      expect(result.configPath).toBe(configPath)
      expect(result.pluginPath).toBe(join(configDir, "plugins", "titan_v2_spool_plugin.ts"))
      expect(await readFile(join(configDir, "plugins", "titan_v2_spool_plugin.ts"), "utf8")).toBe(await readFile(source, "utf8"))
      expect(await readFile(join(configDir, "skills", "memory-sync", "SKILL.md"), "utf8")).toBe("# memory-sync\n")
      expect((await stat(join(configDir, "plugins"))).mode & 0o777).toBe(0o700)
      expect((await stat(join(configDir, "skills"))).mode & 0o777).toBe(0o700)
    } finally {
      if (previous === undefined) delete process.env.XDG_CONFIG_HOME
      else process.env.XDG_CONFIG_HOME = previous
    }
  })

  test("finds the root object after comments and leaves string commas untouched", async () => {
    const { root, source, skills } = await fixture()
    const configPath = join(root, "opencode.jsonc")
    await writeFile(configPath, `// a comment containing { and a comma,}\n/* another { comment */\n{\n  "url": "https://example.test/a,}",\n}\n`)
    await installOpenCodePlugin({ homeDir: root, configPath, pluginSource: source, skillSourceDir: skills, checkCommands: false })
    const config = await readFile(configPath, "utf8")
    expect(config).toContain('"url": "https://example.test/a,}"')
    expect(config).toContain('"titan-memory"')
  })
})
