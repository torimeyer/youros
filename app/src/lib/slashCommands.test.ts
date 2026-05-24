/**
 * Unit tests for the slash command manifest (→1394 FR-008)
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createSlashCommands, filterCommands, type CommandContext } from './slashCommands'

function makeCtx(overrides: Partial<CommandContext> = {}): CommandContext {
  return {
    addSystemMessage: vi.fn(),
    clearChat: vi.fn(),
    openNeedleModal: vi.fn(),
    openSpecModal: vi.fn(),
    openGemPicker: vi.fn(),
    openGiphy: vi.fn(),
    togglePlanMode: vi.fn(),
    runSkill: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  }
}

describe('createSlashCommands', () => {
  it('returns 10 commands', () => {
    const cmds = createSlashCommands(makeCtx())
    expect(cmds).toHaveLength(10)
  })

  it('each command has a / prefix id', () => {
    const cmds = createSlashCommands(makeCtx())
    for (const cmd of cmds) {
      expect(cmd.id).toMatch(/^\//)
    }
  })

  it('includes the 9 spec commands + /giphy', () => {
    const cmds = createSlashCommands(makeCtx())
    const ids = cmds.map(c => c.id)
    const expected = [
      '/handoff', '/plan', '/review', '/init', '/help',
      '/clear', '/spec', '/needle', '/gem', '/giphy',
    ]
    for (const id of expected) {
      expect(ids).toContain(id)
    }
  })

  it('no description uses em-dashes', () => {
    const cmds = createSlashCommands(makeCtx())
    for (const cmd of cmds) {
      expect(cmd.description).not.toMatch(/—/)
    }
  })

  it('/clear trigger calls clearChat', () => {
    const ctx = makeCtx()
    const cmds = createSlashCommands(ctx)
    cmds.find(c => c.id === '/clear')!.trigger()
    expect(ctx.clearChat).toHaveBeenCalledOnce()
  })

  it('/needle trigger calls openNeedleModal', () => {
    const ctx = makeCtx()
    createSlashCommands(ctx).find(c => c.id === '/needle')!.trigger()
    expect(ctx.openNeedleModal).toHaveBeenCalledOnce()
  })

  it('/spec trigger calls openSpecModal', () => {
    const ctx = makeCtx()
    createSlashCommands(ctx).find(c => c.id === '/spec')!.trigger()
    expect(ctx.openSpecModal).toHaveBeenCalledOnce()
  })

  it('/gem trigger calls openGemPicker', () => {
    const ctx = makeCtx()
    createSlashCommands(ctx).find(c => c.id === '/gem')!.trigger()
    expect(ctx.openGemPicker).toHaveBeenCalledOnce()
  })

  it('/giphy trigger calls openGiphy', () => {
    const ctx = makeCtx()
    createSlashCommands(ctx).find(c => c.id === '/giphy')!.trigger()
    expect(ctx.openGiphy).toHaveBeenCalledOnce()
  })

  it('/plan trigger calls togglePlanMode', () => {
    const ctx = makeCtx()
    createSlashCommands(ctx).find(c => c.id === '/plan')!.trigger()
    expect(ctx.togglePlanMode).toHaveBeenCalledOnce()
  })

  it('/handoff trigger calls runSkill("handoff")', async () => {
    const ctx = makeCtx()
    await createSlashCommands(ctx).find(c => c.id === '/handoff')!.trigger()
    expect(ctx.runSkill).toHaveBeenCalledWith('handoff')
  })

  it('/review trigger calls runSkill("review")', async () => {
    const ctx = makeCtx()
    await createSlashCommands(ctx).find(c => c.id === '/review')!.trigger()
    expect(ctx.runSkill).toHaveBeenCalledWith('review')
  })

  it('/init trigger calls runSkill("init")', async () => {
    const ctx = makeCtx()
    await createSlashCommands(ctx).find(c => c.id === '/init')!.trigger()
    expect(ctx.runSkill).toHaveBeenCalledWith('init')
  })

  it('/help trigger adds a system message listing all commands', () => {
    const ctx = makeCtx()
    createSlashCommands(ctx).find(c => c.id === '/help')!.trigger()
    expect(ctx.addSystemMessage).toHaveBeenCalledOnce()
    const msg = (ctx.addSystemMessage as ReturnType<typeof vi.fn>).mock.calls[0][0] as string
    expect(msg).toContain('/handoff')
    expect(msg).toContain('/clear')
  })
})

describe('filterCommands', () => {
  const ctx = makeCtx()
  const cmds = createSlashCommands(ctx)

  it('empty query returns all commands', () => {
    expect(filterCommands(cmds, '')).toHaveLength(10)
  })

  it('"han" matches only /handoff', () => {
    const results = filterCommands(cmds, 'han')
    expect(results).toHaveLength(1)
    expect(results[0].id).toBe('/handoff')
  })

  it('"cl" matches /clear', () => {
    const results = filterCommands(cmds, 'cl')
    expect(results.some(c => c.id === '/clear')).toBe(true)
  })

  it('mid-word query returns no results for non-matching commands', () => {
    const results = filterCommands(cmds, 'xyz_nomatch')
    expect(results).toHaveLength(0)
  })

  it('case-insensitive: "HAND" matches /handoff', () => {
    const results = filterCommands(cmds, 'HAND')
    expect(results.some(c => c.id === '/handoff')).toBe(true)
  })
})
