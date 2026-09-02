import { describe, expect, test } from "bun:test"
import { readFileSync, readdirSync, statSync } from "node:fs"
import { join, resolve } from "node:path"

const skillsRoot = resolve(import.meta.dir, "../skills")
const expectedSkills = [
  "memory-sync",
  "titan-cluster-graph-workflow",
  "titan-doctor-workflow",
  "titan-memory-workflow",
  "titan-patterns-workflow",
]

function frontmatter(text: string): string {
  expect(text.startsWith("---\n")).toBe(true)
  const end = text.indexOf("\n---\n", 4)
  expect(end).toBeGreaterThan(0)
  return text.slice(4, end)
}

describe("OpenCode skills", () => {
  test("has exactly the five discoverable skill directories", () => {
    const actual = readdirSync(skillsRoot)
      .filter((name) => statSync(join(skillsRoot, name)).isDirectory())
      .sort()
    expect(actual).toEqual([...expectedSkills].sort())
  })

  for (const skillName of expectedSkills) {
    test(`${skillName} has classic OpenCode frontmatter and namespaced tools`, () => {
      const skillPath = join(skillsRoot, skillName, "SKILL.md")
      const text = readFileSync(skillPath, "utf8")
      const fm = frontmatter(text)
      expect(fm).toMatch(new RegExp(`^name: ${skillName}$`, "m"))
      expect(fm).toMatch(/^description: \S.+$/m)
      expect(text).toContain("titan-memory_")
    })
  }

  test("contains no stale Codex UI or event-hook language", () => {
    const stale = /\/mcp\b|\/hooks\b|\bhooks?\b/i
    for (const skillName of expectedSkills) {
      const text = readFileSync(join(skillsRoot, skillName, "SKILL.md"), "utf8")
      expect(text).not.toMatch(stale)
    }
  })

  test("foreign recall guidance preserves source_agent", () => {
    const allText = expectedSkills
      .map((skillName) => readFileSync(join(skillsRoot, skillName, "SKILL.md"), "utf8"))
      .join("\n")
    expect(allText).toContain("source_agent")
    expect(allText).toContain("foreign")
  })
})
