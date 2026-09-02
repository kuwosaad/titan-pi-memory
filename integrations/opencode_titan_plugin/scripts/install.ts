import { chmod, mkdir, readFile, readdir, rename, stat, unlink, writeFile } from "node:fs/promises"
import { homedir } from "node:os"
import { dirname, join, basename } from "node:path"
import { randomUUID } from "node:crypto"

export const PLUGIN_FILENAME = "titan_v2_spool_plugin.ts"
export const REQUIRED_SKILLS = [
  "titan-memory-workflow",
  "titan-doctor-workflow",
  "titan-cluster-graph-workflow",
  "titan-patterns-workflow",
  "memory-sync",
] as const

export type CommandResult = { stdout: string; stderr?: string; exitCode?: number }

export type InstallOptions = {
  homeDir?: string
  configPath?: string
  pluginSource?: string
  skillSourceDir?: string
  pluginTargetDir?: string
  skillsTargetDir?: string
  checkCommands?: boolean
  commandRunner?: (command: string, args: string[]) => Promise<CommandResult>
}

export type InstallResult = {
  configPath: string
  pluginPath: string
  backups: string[]
  skills: string[]
  opencodeVersion?: string
}

type Property = { key: string; keyStart: number; valueStart: number; valueEnd: number }

function skipTrivia(text: string, index: number, end = text.length): number {
  while (index < end) {
    if (/\s/.test(text[index])) {
      index++
      continue
    }
    if (text.startsWith("//", index)) {
      const newline = text.indexOf("\n", index + 2)
      index = newline < 0 ? end : newline + 1
      continue
    }
    if (text.startsWith("/*", index)) {
      const close = text.indexOf("*/", index + 2)
      index = close < 0 ? end : close + 2
      continue
    }
    break
  }
  return index
}

function stringEnd(text: string, start: number): number {
  let escaped = false
  for (let index = start + 1; index < text.length; index++) {
    const character = text[index]
    if (escaped) {
      escaped = false
    } else if (character === "\\") {
      escaped = true
    } else if (character === '"') {
      return index + 1
    }
  }
  throw new Error("Unterminated JSON string")
}

function matchingEnd(text: string, start: number): number {
  const opening = text[start]
  const closing = opening === "{" ? "}" : "]"
  let depth = 1
  let index = start + 1
  while (index < text.length) {
    if (text[index] === '"') {
      index = stringEnd(text, index)
      continue
    }
    if (text.startsWith("//", index)) {
      const newline = text.indexOf("\n", index + 2)
      index = newline < 0 ? text.length : newline + 1
      continue
    }
    if (text.startsWith("/*", index)) {
      const close = text.indexOf("*/", index + 2)
      index = close < 0 ? text.length : close + 2
      continue
    }
    if (text[index] === opening) depth++
    if (text[index] === closing && --depth === 0) return index
    index++
  }
  throw new Error("Unterminated JSON object")
}

function valueEnd(text: string, start: number, end: number): number {
  if (text[start] === '"') return stringEnd(text, start)
  if (text[start] === "{" || text[start] === "[") return matchingEnd(text, start) + 1
  let index = start
  while (index < end && text[index] !== "," && text[index] !== "}") index++
  return index
}

function properties(text: string, objectStart: number, objectEnd: number): Property[] {
  const result: Property[] = []
  let index = skipTrivia(text, objectStart + 1, objectEnd)
  while (index < objectEnd) {
    if (text[index] === ",") {
      index = skipTrivia(text, index + 1, objectEnd)
      continue
    }
    if (text[index] !== '"') {
      index++
      continue
    }
    const keyStart = index
    const keyEnd = stringEnd(text, index)
    let key: string
    try {
      key = JSON.parse(text.slice(keyStart, keyEnd)) as string
    } catch {
      index = keyEnd
      continue
    }
    index = skipTrivia(text, keyEnd, objectEnd)
    if (text[index] !== ":") {
      index = keyEnd
      continue
    }
    const valueStart = skipTrivia(text, index + 1, objectEnd)
    const valueEndIndex = valueEnd(text, valueStart, objectEnd)
    result.push({ key, keyStart, valueStart, valueEnd: valueEndIndex })
    index = skipTrivia(text, valueEndIndex, objectEnd)
    if (text[index] === ",") index = skipTrivia(text, index + 1, objectEnd)
  }
  return result
}

function stripJsoncComments(text: string): string {
  let output = ""
  let index = 0
  while (index < text.length) {
    if (text[index] === '"') {
      const end = stringEnd(text, index)
      output += text.slice(index, end)
      index = end
    } else if (text.startsWith("//", index)) {
      const newline = text.indexOf("\n", index + 2)
      output += newline < 0 ? " ".repeat(text.length - index) : " ".repeat(newline - index) + "\n"
      index = newline < 0 ? text.length : newline + 1
    } else if (text.startsWith("/*", index)) {
      const close = text.indexOf("*/", index + 2)
      const end = close < 0 ? text.length : close + 2
      output += text.slice(index, end).replace(/[^\n]/g, " ")
      index = end
    } else {
      output += text[index++]
    }
  }
  let withoutTrailingCommas = ""
  index = 0
  while (index < output.length) {
    if (output[index] === '"') {
      const end = stringEnd(output, index)
      withoutTrailingCommas += output.slice(index, end)
      index = end
      continue
    }
    if (output[index] === ",") {
      const next = skipTrivia(output, index + 1)
      if (next < output.length && (output[next] === "}" || output[next] === "]")) {
        index++
        continue
      }
    }
    withoutTrailingCommas += output[index++]
  }
  return withoutTrailingCommas
}

function parseJsonc(text: string): Record<string, unknown> {
  const parsed: unknown = JSON.parse(stripJsoncComments(text))
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("OpenCode config must be a JSON object")
  return parsed as Record<string, unknown>
}

function indentationAt(text: string, position: number): string {
  const lineStart = text.lastIndexOf("\n", position - 1) + 1
  const match = text.slice(lineStart, position).match(/^[ \t]*/)
  return match?.[0] ?? ""
}

function hasCommaAfter(text: string, position: number, objectEnd: number): boolean {
  return skipTrivia(text, position, objectEnd) < objectEnd && text[skipTrivia(text, position, objectEnd)] === ","
}

function entryText(indent: string): string {
  const child = `${indent}  `
  return `"titan-memory": {\n${child}"type": "local",\n${child}"command": ["titan", "mcp", "--agent", "opencode"],\n${child}"enabled": true\n${indent}}`
}

function mcpText(indent: string): string {
  return `"mcp": {\n${indent}  ${entryText(`${indent}  `)}\n${indent}}`
}

function firstObjectStart(text: string): number {
  let index = 0
  while (index < text.length) {
    if (/\s/.test(text[index])) {
      index++
      continue
    }
    if (text.startsWith("//", index)) {
      const newline = text.indexOf("\n", index + 2)
      index = newline < 0 ? text.length : newline + 1
      continue
    }
    if (text.startsWith("/*", index)) {
      const close = text.indexOf("*/", index + 2)
      index = close < 0 ? text.length : close + 2
      continue
    }
    if (text[index] === '"') {
      index = stringEnd(text, index)
      continue
    }
    if (text[index] === "{") return index
    index++
  }
  return -1
}

function patchConfig(text: string): string {
  const rootStart = firstObjectStart(text)
  if (rootStart < 0) throw new Error("OpenCode config must contain a JSON object")
  const rootEnd = matchingEnd(text, rootStart)
  const rootProps = properties(text, rootStart, rootEnd)
  const mcp = rootProps.find((property) => property.key === "mcp")
  if (mcp && text[mcp.valueStart] !== "{") {
    return text.slice(0, mcp.valueStart) + `{\n${indentationAt(text, mcp.keyStart)}  ${entryText(`${indentationAt(text, mcp.keyStart)}  `)}\n${indentationAt(text, mcp.keyStart)}}` + text.slice(mcp.valueEnd)
  }
  if (mcp && text[mcp.valueStart] === "{") {
    const mcpEnd = matchingEnd(text, mcp.valueStart)
    const mcpProps = properties(text, mcp.valueStart, mcpEnd)
    const titan = mcpProps.find((property) => property.key === "titan-memory")
    const keyIndent = indentationAt(text, titan?.keyStart ?? mcpEnd)
    if (titan) {
      const replacement = entryText(keyIndent)
      return text.slice(0, titan.keyStart) + replacement + text.slice(titan.valueEnd)
    }
    const childIndent = `${indentationAt(text, mcp.keyStart)}  `
    const last = mcpProps[mcpProps.length - 1]
    const separator = last && !hasCommaAfter(text, last.valueEnd, mcpEnd) ? "," : ""
    const withSeparator = last ? text.slice(0, last.valueEnd) + separator + text.slice(last.valueEnd) : text
    const shiftedMcpEnd = mcpEnd + separator.length
    return withSeparator.slice(0, shiftedMcpEnd) + `\n${childIndent}${entryText(childIndent)}\n${indentationAt(text, mcp.keyStart)}` + withSeparator.slice(shiftedMcpEnd)
  }

  const rootIndent = rootProps.length > 0 ? indentationAt(text, rootProps[0].keyStart) : "  "
  const last = rootProps[rootProps.length - 1]
  const separator = last && !hasCommaAfter(text, last.valueEnd, rootEnd) ? "," : ""
  const withSeparator = last ? text.slice(0, last.valueEnd) + separator + text.slice(last.valueEnd) : text
  const shiftedRootEnd = rootEnd + separator.length
  return withSeparator.slice(0, shiftedRootEnd) + `\n${rootIndent}${mcpText(rootIndent)}\n` + withSeparator.slice(shiftedRootEnd)
}

async function exists(path: string): Promise<boolean> {
  try {
    await stat(path)
    return true
  } catch {
    return false
  }
}

async function atomicWrite(path: string, content: string | Uint8Array, mode = 0o600): Promise<void> {
  await mkdir(dirname(path), { recursive: true, mode: 0o700 })
  const temporary = join(dirname(path), `.${basename(path)}.${randomUUID()}.tmp`)
  try {
    await writeFile(temporary, content, { encoding: "utf8", mode })
    await chmod(temporary, mode)
    await rename(temporary, path)
  } catch (error) {
    await unlink(temporary).catch(() => {})
    throw error
  }
}

async function backup(path: string): Promise<string> {
  let candidate = `${path}.bak`
  let suffix = 1
  while (await exists(candidate)) candidate = `${path}.bak.${suffix++}`
  await rename(path, candidate)
  return candidate
}

async function copyTree(source: string, target: string): Promise<void> {
  await mkdir(target, { recursive: true, mode: 0o700 })
  await chmod(target, 0o700)
  for (const entry of await readdir(source, { withFileTypes: true })) {
    const sourcePath = join(source, entry.name)
    const targetPath = join(target, entry.name)
    if (entry.isDirectory()) await copyTree(sourcePath, targetPath)
    else if (entry.isFile()) {
      const incoming = await readFile(sourcePath)
      const current = await Bun.file(targetPath).arrayBuffer().catch(() => null)
      const same = current && Buffer.from(current).equals(incoming)
      if (!same) {
        await mkdir(dirname(targetPath), { recursive: true, mode: 0o700 })
        await writeFile(targetPath, incoming, { mode: 0o600 })
      }
      await chmod(targetPath, 0o600)
    }
  }
}

async function runCommand(command: string, args: string[]): Promise<CommandResult> {
  const process = Bun.spawn([command, ...args], { stdout: "pipe", stderr: "pipe" })
  const stdout = await new Response(process.stdout).text()
  const stderr = await new Response(process.stderr).text()
  return { stdout, stderr, exitCode: await process.exited }
}

function commandPath(name: string): string | undefined {
  return Bun.which(name) ?? undefined
}

async function verifyCommands(options: InstallOptions): Promise<string> {
  const runner = options.commandRunner ?? runCommand
  for (const command of ["titan", "opencode"]) {
    if (!options.commandRunner && !commandPath(command)) throw new Error(`${command} is required but was not found on PATH`)
  }
  const versionResult = await runner("opencode", ["--version"])
  if (versionResult.exitCode && versionResult.exitCode !== 0) throw new Error(`Unable to determine OpenCode version: ${versionResult.stderr ?? ""}`.trim())
  const match = versionResult.stdout.match(/(?:^|\s)(\d+)(?:\.\d+){0,2}(?:\s|$)/)
  if (!match) throw new Error(`Unable to determine OpenCode version from: ${versionResult.stdout.trim()}`)
  const version = match[1]
  if (version !== "1") throw new Error(`OpenCode 1.x is required; found ${version}`)
  return versionResult.stdout.trim()
}

function defaultConfigPath(homeDir: string): string {
  const configRoot = process.env.XDG_CONFIG_HOME || join(homeDir, ".config")
  return join(configRoot, "opencode", "opencode.json")
}

async function resolveConfigPath(homeDir: string, explicit?: string): Promise<string> {
  if (explicit) return explicit
  const root = process.env.XDG_CONFIG_HOME || join(homeDir, ".config")
  for (const candidate of [join(root, "opencode", "opencode.json"), join(root, "opencode", "opencode.jsonc")]) {
    if (await exists(candidate)) return candidate
  }
  return defaultConfigPath(homeDir)
}

export async function installOpenCodePlugin(options: InstallOptions = {}): Promise<InstallResult> {
  const homeDir = options.homeDir ?? homedir()
  const integrationDir = join(import.meta.dir, "..")
  const pluginSource = options.pluginSource ?? join(integrationDir, "dist", PLUGIN_FILENAME)
  const skillSourceDir = options.skillSourceDir ?? join(integrationDir, "skills")
  const configRoot = process.env.XDG_CONFIG_HOME || join(homeDir, ".config")
  const pluginTargetDir = options.pluginTargetDir ?? join(configRoot, "opencode", "plugins")
  const skillsTargetDir = options.skillsTargetDir ?? join(configRoot, "opencode", "skills")
  const configPath = await resolveConfigPath(homeDir, options.configPath)
  if (!(await exists(pluginSource))) throw new Error(`Plugin bundle not found: ${pluginSource}. Run bun run build first.`)
  if (!(await exists(skillSourceDir))) throw new Error(`Skill source directory not found: ${skillSourceDir}`)

  const opencodeVersion = options.checkCommands === false ? undefined : await verifyCommands(options)
  const backups: string[] = []
  const pluginPath = join(pluginTargetDir, PLUGIN_FILENAME)
  await mkdir(pluginTargetDir, { recursive: true, mode: 0o700 })
  await chmod(pluginTargetDir, 0o700)
  const incomingPlugin = await readFile(pluginSource)
  if (await exists(pluginPath)) {
    const currentPlugin = await readFile(pluginPath)
    if (!currentPlugin.equals(incomingPlugin)) backups.push(await backup(pluginPath))
    else await chmod(pluginPath, 0o600)
  }
  if (!(await exists(pluginPath))) await atomicWrite(pluginPath, incomingPlugin)

  const originalConfig = (await exists(configPath)) ? await readFile(configPath, "utf8") : "{}\n"
  const nextConfig = patchConfig(originalConfig)
  parseJsonc(nextConfig)
  if (nextConfig !== originalConfig) {
    if (await exists(configPath)) backups.push(await backup(configPath))
    await atomicWrite(configPath, nextConfig)
  }

  const installedSkills: string[] = []
  await mkdir(skillsTargetDir, { recursive: true, mode: 0o700 })
  await chmod(skillsTargetDir, 0o700)
  for (const skill of REQUIRED_SKILLS) {
    const source = join(skillSourceDir, skill)
    if (!(await exists(source))) throw new Error(`Required skill source not found: ${source}`)
    await copyTree(source, join(skillsTargetDir, skill))
    installedSkills.push(skill)
  }
  return { configPath, pluginPath, backups, skills: installedSkills, opencodeVersion }
}

if (import.meta.main) {
  await installOpenCodePlugin()
  console.log("Installed Titan Memory for OpenCode 1.x")
}
