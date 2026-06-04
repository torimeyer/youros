import { describe, it, expect } from 'vitest'
import { TOP_LEVEL_ROUTES, ARCADE_NAV_ITEM } from './Sidebar'

describe('Sidebar Arcade placement', () => {
  it('Arcade route is not in primary nav', () => {
    expect(TOP_LEVEL_ROUTES.has('/break')).toBe(false)
  })

  it('ARCADE_NAV_ITEM is defined for bottom cluster with correct route and icon', () => {
    expect(ARCADE_NAV_ITEM.to).toBe('/break')
    expect(ARCADE_NAV_ITEM.icon).toBe('sports_esports')
    expect(ARCADE_NAV_ITEM.label).toBe('The Arcade')
  })
})
