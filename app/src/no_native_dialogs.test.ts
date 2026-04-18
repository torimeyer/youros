import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

/**
 * Regression guard: no browser-native dialogs in app source.
 *
 * Browser-native confirm/alert/prompt popups are not allowed because
 * they show the app origin (like "localhost:3010 says") and break the
 * myOS visual style. Every destructive or informational dialog must
 * use the in-app <ConfirmModal> / toast pattern.
 *
 * This test walks the app/src tree and fails if any non-test .ts or
 * .tsx file calls window.confirm / window.alert / window.prompt.
 * If this test fails, replace the call with useConfirm() from
 * hooks/useConfirm or an in-app toast. See pages/Files.tsx for an
 * example.
 */

const here = path.dirname(fileURLToPath(import.meta.url))
const SRC_ROOT = here

const SKIP_DIRS = new Set(['node_modules', 'dist', 'build', '.vite', 'assets'])

// Filenames that are test scaffolding. These files may mock native
// dialogs to prove the app code does not call them, which is fine.
const TEST_NAME_RE = /\.(test|spec)\.(ts|tsx)$/

// Comments are allowed to mention the names (for docstrings like
// "replaces window.confirm"). We only flag actual call sites.
const FORBIDDEN_CALL_RE = /\bwindow\.(confirm|alert|prompt)\s*\(/

function* walk(dir: string): Generator<string> {
  for (const entry of readdirSync(dir)) {
    if (SKIP_DIRS.has(entry)) continue
    const full = path.join(dir, entry)
    const stat = statSync(full)
    if (stat.isDirectory()) {
      yield* walk(full)
    } else if (stat.isFile() && /\.(ts|tsx)$/.test(entry)) {
      yield full
    }
  }
}

describe('no browser-native dialogs', () => {
  it('app source does not call window.confirm, window.alert, or window.prompt', () => {
    const offenders: string[] = []

    for (const filePath of walk(SRC_ROOT)) {
      const base = path.basename(filePath)
      if (TEST_NAME_RE.test(base)) continue
      // The guard test itself (this file) is allowed to mention them.
      if (base === 'no_native_dialogs.test.ts') continue

      const source = readFileSync(filePath, 'utf8')
      const lines = source.split('\n')
      for (let i = 0; i < lines.length; i++) {
        const line = lines[i]
        // Strip line comments so "// replaces window.confirm" does
        // not count as an offender.
        const withoutLineComment = line.replace(/\/\/.*$/, '')
        if (FORBIDDEN_CALL_RE.test(withoutLineComment)) {
          const rel = path.relative(SRC_ROOT, filePath)
          offenders.push(`${rel}:${i + 1}: ${line.trim()}`)
        }
      }
    }

    if (offenders.length > 0) {
      const help =
        'Replace browser-native dialogs with the in-app <ConfirmModal> via useConfirm() or an in-app toast. ' +
        'See app/src/pages/Files.tsx for an example.'
      throw new Error(
        `Found ${offenders.length} browser-native dialog call(s):\n  ${offenders.join(
          '\n  '
        )}\n\n${help}`
      )
    }

    expect(offenders).toEqual([])
  })
})
