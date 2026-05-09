import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import TemplateCard from './TemplateCard'
import type { TemplateCapabilities } from './TemplateCard'
import { AGENT_MARKETPLACE } from '../data/agentMarketplace'

describe('TemplateCard', () => {
  it('renders name, description, and icon in installed context', () => {
    render(
      <TemplateCard
        name="Builder"
        description="Your go-to builder."
        icon="engineering"
        source="builtin"
        installed={true}
        onAction={vi.fn()}
        actionLabel="Use"
        testId="tc-builder"
      />
    )
    expect(screen.getByTestId('tc-builder')).toBeInTheDocument()
    expect(screen.getByText('Builder')).toBeInTheDocument()
    expect(screen.getByText('Your go-to builder.')).toBeInTheDocument()
    // Action button present
    expect(screen.getByTestId('tc-builder-action')).toBeInTheDocument()
    expect(screen.getByText('Use')).toBeInTheDocument()
  })

  it('renders alias chips when aliases are provided', () => {
    render(
      <TemplateCard
        name="Builder"
        description="Your go-to builder."
        icon="engineering"
        aliases={['saa', 'comprehensive']}
        source="builtin"
        installed={true}
        onAction={vi.fn()}
        testId="tc-builder-alias"
      />
    )
    expect(screen.getByTestId('alias-chip-saa')).toBeInTheDocument()
    expect(screen.getByText('alias: saa')).toBeInTheDocument()
    expect(screen.getByTestId('alias-chip-comprehensive')).toBeInTheDocument()
  })

  it('renders in marketplace context with add button when not installed', () => {
    render(
      <TemplateCard
        name="Summarizer"
        description="Summarize documents."
        icon="summarize"
        source="marketplace"
        installed={false}
        onAction={vi.fn()}
        testId="tc-summarizer"
      />
    )
    const card = screen.getByTestId('tc-summarizer')
    expect(card).toBeInTheDocument()
    expect(screen.getByText('Summarizer')).toBeInTheDocument()
    // Add button visible for uninstalled marketplace items
    expect(screen.getByTestId('tc-summarizer-action')).toBeInTheDocument()
  })

  it('installed and marketplace cards share the same root DOM structure', () => {
    const { container: c1 } = render(
      <TemplateCard
        name="Builder"
        description="Go-to builder."
        icon="engineering"
        source="builtin"
        installed={true}
        onAction={vi.fn()}
        testId="tc-inst"
      />
    )
    const { container: c2 } = render(
      <TemplateCard
        name="Summarizer"
        description="Summarize docs."
        icon="summarize"
        source="marketplace"
        installed={false}
        onAction={vi.fn()}
        testId="tc-mkt"
      />
    )
    // Both root elements are divs with the same base classes
    const root1 = c1.firstElementChild as HTMLElement
    const root2 = c2.firstElementChild as HTMLElement
    expect(root1.tagName).toBe(root2.tagName)
    // Both contain an icon container, a body div, and an action area
    expect(root1.children.length).toBe(root2.children.length)
  })

  it('shows delete button only for custom templates', () => {
    const onDelete = vi.fn()
    render(
      <TemplateCard
        name="My Custom"
        description="Custom template."
        icon="smart_toy"
        source="custom"
        installed={true}
        onAction={vi.fn()}
        onDelete={onDelete}
        testId="tc-custom"
      />
    )
    expect(screen.getByTestId('tc-custom-delete')).toBeInTheDocument()
  })

  it('does not show delete button when onDelete is not provided', () => {
    render(
      <TemplateCard
        name="Builder"
        description="Built-in."
        icon="engineering"
        source="builtin"
        installed={true}
        onAction={vi.fn()}
        testId="tc-no-delete"
      />
    )
    expect(screen.queryByTestId('tc-no-delete-delete')).not.toBeInTheDocument()
  })
})

describe('TemplateCard badge behavior', () => {
  it('builtin card does NOT render a built-in badge', () => {
    render(
      <TemplateCard
        name="Builder"
        description="Your go-to builder."
        icon="engineering"
        source="builtin"
        installed={true}
        onAction={vi.fn()}
        testId="tc-badge-builtin"
      />
    )
    const card = screen.getByTestId('tc-badge-builtin')
    expect(card.textContent).not.toContain('built-in')
  })

  it('marketplace card renders the catalog badge', () => {
    render(
      <TemplateCard
        name="Summarizer"
        description="Summarize documents."
        icon="summarize"
        source="marketplace"
        installed={false}
        onAction={vi.fn()}
        testId="tc-badge-mkt"
      />
    )
    const card = screen.getByTestId('tc-badge-mkt')
    expect(card.textContent).toContain('catalog')
  })

  it('catalog badge uses light-mode-readable colors (not gray-on-gray)', () => {
    render(
      <TemplateCard
        name="Summarizer"
        description="Summarize documents."
        icon="summarize"
        source="marketplace"
        installed={false}
        onAction={vi.fn()}
        testId="tc-badge-contrast"
      />
    )
    const badge = screen.getByText('catalog')
    expect(badge.className).toMatch(/bg-slate-200/)
    expect(badge.className).toMatch(/text-slate-700/)
    expect(badge.className).toMatch(/dark:bg-slate-700\/60/)
    expect(badge.className).toMatch(/dark:text-slate-400/)
  })

  it('custom card renders the custom badge', () => {
    render(
      <TemplateCard
        name="My Agent"
        description="Does things."
        icon="smart_toy"
        source="custom"
        installed={true}
        onAction={vi.fn()}
        testId="tc-badge-custom"
      />
    )
    const card = screen.getByTestId('tc-badge-custom')
    expect(card.textContent).toContain('custom')
  })
})

describe('TemplateCard layout', () => {
  const caps: TemplateCapabilities = {
    writes_to: 'no writes',
    cannot_touch: 'env',
    budget: 'no cap',
    time_limit: 'no limit',
    sandbox: 'on',
  }

  it('card root has p-5 padding class', () => {
    const { container } = render(
      <TemplateCard
        name="Builder"
        description="Your go-to builder."
        icon="engineering"
        source="builtin"
        installed={true}
        onAction={vi.fn()}
        testId="tc-padding"
      />
    )
    const root = container.firstElementChild as HTMLElement
    expect(root.className).toContain('p-5')
  })

  it('description is clamped to 2 lines via line-clamp-2', () => {
    const longDesc = 'A'.repeat(120)
    render(
      <TemplateCard
        name="Long"
        description={longDesc}
        icon="engineering"
        source="builtin"
        installed={true}
        onAction={vi.fn()}
        testId="tc-long"
      />
    )
    const desc = screen.getByText(longDesc)
    expect(desc.className).toContain('line-clamp-2')
  })

  it('short description also has line-clamp-2 class', () => {
    render(
      <TemplateCard
        name="Short"
        description="Brief."
        icon="engineering"
        source="builtin"
        installed={true}
        onAction={vi.fn()}
        testId="tc-short"
      />
    )
    const desc = screen.getByText('Brief.')
    expect(desc.className).toContain('line-clamp-2')
  })

  it('capabilities tooltip button appears when capabilities are provided', () => {
    render(
      <TemplateCard
        name="Builder"
        description="Your go-to builder."
        icon="engineering"
        source="builtin"
        installed={true}
        onAction={vi.fn()}
        capabilities={caps}
        testId="tc-caps-btn"
      />
    )
    expect(screen.getByTestId('template-caps-info-Builder')).toBeInTheDocument()
  })

  it('capabilities tooltip is NOT visible before hover', () => {
    render(
      <TemplateCard
        name="Builder"
        description="Your go-to builder."
        icon="engineering"
        source="builtin"
        installed={true}
        onAction={vi.fn()}
        capabilities={caps}
        testId="tc-caps-hover"
      />
    )
    expect(screen.queryByTestId('template-capabilities-Builder')).not.toBeInTheDocument()
  })

  it('capabilities tooltip appears on hover (mouseEnter)', () => {
    render(
      <TemplateCard
        name="Builder"
        description="Your go-to builder."
        icon="engineering"
        source="builtin"
        installed={true}
        onAction={vi.fn()}
        capabilities={caps}
        testId="tc-caps-hover"
      />
    )
    const infoBtn = screen.getByTestId('template-caps-info-Builder')
    fireEvent.mouseEnter(infoBtn)
    const tooltip = screen.getByTestId('template-capabilities-Builder')
    expect(tooltip).toBeInTheDocument()
    expect(tooltip.textContent).toContain('no writes')
    expect(tooltip.textContent).toContain('no cap')
    expect(tooltip.textContent).toContain('no limit')
  })

  it('capabilities tooltip disappears on mouse leave', () => {
    render(
      <TemplateCard
        name="Builder"
        description="Your go-to builder."
        icon="engineering"
        source="builtin"
        installed={true}
        onAction={vi.fn()}
        capabilities={caps}
        testId="tc-caps-leave"
      />
    )
    const infoBtn = screen.getByTestId('template-caps-info-Builder')
    fireEvent.mouseEnter(infoBtn)
    expect(screen.getByTestId('template-capabilities-Builder')).toBeInTheDocument()
    fireEvent.mouseLeave(infoBtn)
    expect(screen.queryByTestId('template-capabilities-Builder')).not.toBeInTheDocument()
  })

  it('capabilities info button absent when capabilities prop is null', () => {
    render(
      <TemplateCard
        name="No Caps"
        description="Template with no capabilities."
        icon="smart_toy"
        source="marketplace"
        installed={false}
        onAction={() => {}}
        capabilities={null}
        testId="tc-no-caps"
      />
    )
    expect(screen.queryByTestId('template-caps-info-No Caps')).not.toBeInTheDocument()
    expect(screen.queryByTestId('template-capabilities-No Caps')).not.toBeInTheDocument()
  })
})

describe('TemplateCard name prominence', () => {
  it('name element has text-lg and font-semibold classes', () => {
    render(
      <TemplateCard
        name="Builder"
        description="Your go-to builder."
        icon="engineering"
        source="builtin"
        installed={true}
        onAction={vi.fn()}
        testId="tc-name-size"
      />
    )
    const nameEl = screen.getByText('Builder')
    expect(nameEl.className).toContain('text-lg')
    expect(nameEl.className).toContain('font-semibold')
  })

  it('name element has truncate class for overflow protection', () => {
    const longName = 'A Very Long Template Name That Could Overflow The Card'
    render(
      <TemplateCard
        name={longName}
        description="Brief description."
        icon="engineering"
        source="builtin"
        installed={true}
        onAction={vi.fn()}
        testId="tc-name-truncate"
      />
    )
    const nameEl = screen.getByText(longName)
    expect(nameEl.className).toContain('truncate')
  })

  it('description still has line-clamp-2 after name size bump', () => {
    render(
      <TemplateCard
        name="Builder"
        description="Your go-to builder."
        icon="engineering"
        source="builtin"
        installed={true}
        onAction={vi.fn()}
        testId="tc-desc-clamp"
      />
    )
    const desc = screen.getByText('Your go-to builder.')
    expect(desc.className).toContain('line-clamp-2')
  })
})

describe('TemplateCard source badge correctness', () => {
  it('marketplace template shows "catalog" badge, NOT "custom"', () => {
    render(
      <TemplateCard
        name="Competitive Scan"
        description="Research what competitors are shipping."
        icon="monitor_heart"
        source="marketplace"
        installed={true}
        onAction={vi.fn()}
        testId="tc-mp-badge"
      />
    )
    const card = screen.getByTestId('tc-mp-badge')
    expect(card.textContent).toContain('catalog')
    expect(card.textContent).not.toContain('custom')
  })

  it('user-created custom template shows "custom" badge, NOT "marketplace"', () => {
    render(
      <TemplateCard
        name="My Custom Agent"
        description="Does something I created."
        icon="smart_toy"
        source="custom"
        installed={true}
        onAction={vi.fn()}
        testId="tc-custom-badge"
      />
    )
    const card = screen.getByTestId('tc-custom-badge')
    expect(card.textContent).toContain('custom')
    expect(card.textContent).not.toContain('marketplace')
  })
})

describe('Template name uniqueness', () => {
  it('no two marketplace templates share a normalized name', () => {
    const seen = new Set<string>()
    const dupes: string[] = []
    for (const cat of AGENT_MARKETPLACE) {
      for (const tpl of cat.templates) {
        const key = tpl.name.toLowerCase().trim()
        if (seen.has(key)) {
          dupes.push(tpl.name)
        }
        seen.add(key)
      }
    }
    expect(dupes).toEqual([])
  })
})

describe('TemplateCard capabilities panel (marketplace template)', () => {
  const marketplaceCaps: TemplateCapabilities = {
    writes_to: 'nothing',
    cannot_touch: 'no restrictions set',
    budget: 'no budget limit',
    time_limit: 'no time limit',
    sandbox: 'light sandbox',
  }

  it('renders capabilities on hover for an uninstalled marketplace template', () => {
    render(
      <TemplateCard
        name="Competitive Scan"
        description="Research what competitors are shipping in a product area."
        icon="monitor_heart"
        source="marketplace"
        installed={false}
        onAction={() => {}}
        capabilities={marketplaceCaps}
        testId="tc-mkt-caps"
      />
    )
    const infoBtn = screen.getByTestId('template-caps-info-Competitive Scan')
    fireEvent.mouseEnter(infoBtn)
    const capsDiv = screen.getByTestId('template-capabilities-Competitive Scan')
    expect(capsDiv).toBeInTheDocument()
    expect(capsDiv.textContent).toContain('nothing')
    expect(capsDiv.textContent).toContain('no budget limit')
    expect(capsDiv.textContent).toContain('light sandbox')
  })

  it('renders capabilities on hover for an installed custom template', () => {
    const customCaps: TemplateCapabilities = {
      writes_to: 'src/',
      cannot_touch: '.env',
      budget: '$2',
      time_limit: '30 minutes',
      sandbox: 'none',
    }
    render(
      <TemplateCard
        name="My Custom Agent"
        description="Does something custom."
        icon="smart_toy"
        source="custom"
        installed={true}
        onAction={() => {}}
        capabilities={customCaps}
        testId="tc-custom-caps"
      />
    )
    const infoBtn = screen.getByTestId('template-caps-info-My Custom Agent')
    fireEvent.mouseEnter(infoBtn)
    const capsDiv = screen.getByTestId('template-capabilities-My Custom Agent')
    expect(capsDiv).toBeInTheDocument()
    expect(capsDiv.textContent).toContain('src/')
    expect(capsDiv.textContent).toContain('$2')
  })

  it('shows no capabilities button when capabilities prop is null', () => {
    render(
      <TemplateCard
        name="No Caps"
        description="Template with no capabilities."
        icon="smart_toy"
        source="marketplace"
        installed={false}
        onAction={() => {}}
        capabilities={null}
        testId="tc-no-caps2"
      />
    )
    expect(screen.queryByTestId('template-caps-info-No Caps')).not.toBeInTheDocument()
  })
})
