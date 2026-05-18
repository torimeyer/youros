// scaffold(→1478): Backlog AllView kanban redesign - RED tests follow TDD order
// See plan: /Users/torimeyer/.claude/plans/this-isnt-very-user-twinkling-yeti.md

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Backlog from './Backlog'

// Tests added in TDD order (RED before GREEN) — see task brief →1478
