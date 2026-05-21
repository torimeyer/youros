import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import React from 'react'
import { renderMarkdown, renderTextWithMarkdown } from './markdown'

function renderNodes(nodes: React.ReactNode[]) {
  return render(<div>{nodes}</div>)
}

describe('renderMarkdown', () => {
  it('renders **bold** as a <strong> tag', () => {
    const { container } = renderNodes(renderMarkdown('**Hi**'))
    const strong = container.querySelector('strong')
    expect(strong).not.toBeNull()
    expect(strong?.textContent).toBe('Hi')
  })

  it('renders *italic* as an <em> tag', () => {
    const { container } = renderNodes(renderMarkdown('*hello*'))
    const em = container.querySelector('em')
    expect(em).not.toBeNull()
    expect(em?.textContent).toBe('hello')
  })

  it('renders `code` as a <code> tag', () => {
    const { container } = renderNodes(renderMarkdown('`snippet`'))
    const code = container.querySelector('code')
    expect(code).not.toBeNull()
    expect(code?.textContent).toBe('snippet')
  })

  it('renders inline bold inside a sentence', () => {
    const { container } = renderNodes(renderMarkdown('This is **bold** text'))
    const strong = container.querySelector('strong')
    expect(strong?.textContent).toBe('bold')
    expect(container.textContent).toContain('This is')
    expect(container.textContent).toContain('text')
  })

  it('renders bullet list items as <li> elements', () => {
    const md = '- First\n- Second\n- Third'
    const { container } = renderNodes(renderMarkdown(md))
    const items = container.querySelectorAll('li')
    expect(items.length).toBe(3)
    expect(items[0].textContent).toBe('First')
    expect(items[1].textContent).toBe('Second')
    expect(items[2].textContent).toBe('Third')
  })

  it('renders numbered list items as <li> elements inside <ol>', () => {
    const md = '1. One\n2. Two\n3. Three'
    const { container } = renderNodes(renderMarkdown(md))
    const ol = container.querySelector('ol')
    expect(ol).not.toBeNull()
    const items = ol!.querySelectorAll('li')
    expect(items.length).toBe(3)
  })

  it('renders fenced code block as <pre><code>', () => {
    const md = '```\nconsole.log("hello")\n```'
    const { container } = renderNodes(renderMarkdown(md))
    const pre = container.querySelector('pre')
    expect(pre).not.toBeNull()
    expect(pre?.textContent).toContain('console.log')
  })

  it('renders fenced code block with language hint', () => {
    const md = '```python\nprint("hi")\n```'
    const { container } = renderNodes(renderMarkdown(md))
    const pre = container.querySelector('pre')
    expect(pre).not.toBeNull()
    expect(pre?.getAttribute('data-lang')).toBe('python')
  })

  it('renders ## heading as <h2>', () => {
    const { container } = renderNodes(renderMarkdown('## Section Title'))
    const h2 = container.querySelector('h2')
    expect(h2?.textContent).toBe('Section Title')
  })

  it('renders # heading as <h1>', () => {
    const { container } = renderNodes(renderMarkdown('# Big Title'))
    const h1 = container.querySelector('h1')
    expect(h1?.textContent).toBe('Big Title')
  })

  it('renders ### heading as <h3>', () => {
    const { container } = renderNodes(renderMarkdown('### Small Heading'))
    const h3 = container.querySelector('h3')
    expect(h3?.textContent).toBe('Small Heading')
  })

  it('handles bold inside heading', () => {
    const { container } = renderNodes(renderMarkdown('## **Bold** Heading'))
    const h2 = container.querySelector('h2')
    expect(h2).not.toBeNull()
    const strong = h2!.querySelector('strong')
    expect(strong?.textContent).toBe('Bold')
  })

  it('handles bold inside bullet list item', () => {
    const md = '- **Important** item'
    const { container } = renderNodes(renderMarkdown(md))
    const li = container.querySelector('li')
    const strong = li?.querySelector('strong')
    expect(strong?.textContent).toBe('Important')
  })

  it('renders a realistic assistant message about Sophisticated Adversarial Attacks', () => {
    const md = [
      '## Sophisticated Adversarial Attacks',
      '',
      '**Key Techniques:**',
      '- Gradient-based attacks',
      '- Transfer attacks',
      '',
      'Use `model.predict()` carefully.',
    ].join('\n')
    const { container } = renderNodes(renderMarkdown(md))

    const h2 = container.querySelector('h2')
    expect(h2?.textContent).toBe('Sophisticated Adversarial Attacks')

    const strong = container.querySelector('strong')
    expect(strong?.textContent).toBe('Key Techniques:')

    const items = container.querySelectorAll('li')
    expect(items.length).toBe(2)

    const code = container.querySelector('code')
    expect(code?.textContent).toBe('model.predict()')
  })

  it('trims leading newlines so the first node is not a <br>', () => {
    // Model responses often start with \n\n before the actual text.
    // renderMarkdown must strip those so no blank gap appears at the bubble top.
    const nodes = renderMarkdown('\n\nHey! Doing good.')
    // The first node should NOT be a <br> element
    const { container } = renderNodes(nodes)
    const children = Array.from(container.childNodes)
    const firstChild = children[0] as HTMLElement
    expect(firstChild.nodeName).not.toBe('BR')
    expect(container.textContent).toContain('Hey! Doing good.')
  })

  it('trims trailing newlines so no trailing <br> is rendered', () => {
    const nodes = renderMarkdown('Hello\n\n')
    const { container } = renderNodes(nodes)
    const children = Array.from(container.childNodes)
    const lastChild = children[children.length - 1] as HTMLElement
    expect(lastChild.nodeName).not.toBe('BR')
  })

  // Regression: an assistant bubble that returned a markdown link after a
  // tool-use block used to render the link as raw text like
  // "[View in Calendar](https://...)". Links must become real <a href> tags.
  it('renders a markdown link as a clickable anchor with target blank', () => {
    const md = '[View in Calendar](https://www.google.com/calendar/event?eid=abc123)'
    const { container } = renderNodes(renderMarkdown(md))
    const anchor = container.querySelector('a')
    expect(anchor).not.toBeNull()
    expect(anchor?.getAttribute('href')).toBe('https://www.google.com/calendar/event?eid=abc123')
    expect(anchor?.getAttribute('target')).toBe('_blank')
    expect(anchor?.getAttribute('rel')).toBe('noopener noreferrer')
    expect(anchor?.textContent).toBe('View in Calendar')
    // No leftover raw markdown bracket text should be visible anywhere.
    expect(container.textContent).not.toContain('](')
    expect(container.textContent).not.toContain('[View in Calendar]')
  })

  it('renders a markdown link embedded inside a sentence of prose', () => {
    const md = 'I created it. [View in Calendar](https://cal.example/e/1) when ready.'
    const { container } = renderNodes(renderMarkdown(md))
    const anchor = container.querySelector('a')
    expect(anchor?.textContent).toBe('View in Calendar')
    expect(container.textContent).toContain('I created it.')
    expect(container.textContent).toContain('when ready.')
    expect(container.textContent).not.toContain('](')
  })

  it('renders a markdown link inside a bullet list item', () => {
    const md = '- See [the docs](https://example.com/docs) for details'
    const { container } = renderNodes(renderMarkdown(md))
    const li = container.querySelector('li')
    const anchor = li?.querySelector('a')
    expect(anchor?.getAttribute('href')).toBe('https://example.com/docs')
    expect(anchor?.textContent).toBe('the docs')
  })

  it('does not create an anchor for javascript: URLs', () => {
    // Safety: refuse unsafe schemes so a malicious model cannot inject
    // clickable javascript links into a chat bubble.
    const md = '[click me](javascript:alert(1))'
    const { container } = renderNodes(renderMarkdown(md))
    const anchor = container.querySelector('a')
    expect(anchor).toBeNull()
    expect(container.textContent).toContain('click me')
  })

  it('allows relative links like /tasks/123', () => {
    const md = 'See [task 123](/tasks/123)'
    const { container } = renderNodes(renderMarkdown(md))
    const anchor = container.querySelector('a')
    expect(anchor?.getAttribute('href')).toBe('/tasks/123')
  })

  // Regression: chat bubble collapsed two sentences separated by a single
  // newline onto one visible line (e.g. "...now.Added as..."). A single
  // newline inside a paragraph must render as a soft break so GitHub/Slack
  // style prose stays readable.
  it('renders a single newline as a soft break (<br>)', () => {
    const { container } = renderNodes(renderMarkdown('Line one.\nLine two.'))
    const paragraph = container.querySelector('p')
    expect(paragraph).not.toBeNull()
    const br = paragraph!.querySelector('br')
    expect(br).not.toBeNull()
    // Both sentences must be present.
    expect(paragraph!.textContent).toContain('Line one.')
    expect(paragraph!.textContent).toContain('Line two.')
    // The <br> must sit between the two sentences (not before the first,
    // not after the second).
    const children = Array.from(paragraph!.childNodes)
    const brIdx = children.findIndex((n) => (n as Element).nodeName === 'BR')
    expect(brIdx).toBeGreaterThan(0)
    expect(brIdx).toBeLessThan(children.length - 1)
  })

  it('renders a blank line as a new paragraph with no double <br>', () => {
    const { container } = renderNodes(renderMarkdown('Para one.\n\nPara two.'))
    const paragraphs = container.querySelectorAll('p')
    expect(paragraphs.length).toBe(2)
    expect(paragraphs[0].textContent).toContain('Para one.')
    expect(paragraphs[1].textContent).toContain('Para two.')
    // The paragraph boundary must not be a <br> at all. Spacing comes from
    // the <p> margin, not from a stacked break between paragraphs.
    const topLevelBrs = Array.from(container.children[0]?.children || []).filter(
      (el) => el.tagName === 'BR',
    )
    expect(topLevelBrs.length).toBe(0)
  })

  it('reproduces the calendar bubble scenario with a soft break between sentences', () => {
    // Exact shape from the screenshot: prose, single newline, more prose.
    // The two sentences must be visibly separated in the DOM.
    const md =
      "You're right, it reconnected. Loading the calendar tool now.\n" +
      'Added as an all-day event on April 28, 2026: "Pepper field trip - magic show."'
    const { container } = renderNodes(renderMarkdown(md))
    const paragraph = container.querySelector('p')
    expect(paragraph).not.toBeNull()
    const br = paragraph!.querySelector('br')
    expect(br).not.toBeNull()
    // Verify the <br> sits between the two sentences so they are visibly
    // separated. textContent collapses <br> to empty, so we walk childNodes.
    const children = Array.from(paragraph!.childNodes)
    const brIdx = children.findIndex((n) => (n as Element).nodeName === 'BR')
    expect(brIdx).toBeGreaterThan(0)
    const before = children.slice(0, brIdx).map((n) => n.textContent).join('')
    const after = children.slice(brIdx + 1).map((n) => n.textContent).join('')
    expect(before).toContain('Loading the calendar tool now.')
    expect(after).toContain('Added as an all-day event')
  })

  it('does not insert a <br> between list items (single newline inside a list)', () => {
    // Single newline between bullets means a list-item boundary, not a soft
    // break. The list renderer owns that newline, so no stray <br> should
    // appear inside the <ul>.
    const md = '- First\n- Second'
    const { container } = renderNodes(renderMarkdown(md))
    const ul = container.querySelector('ul')
    expect(ul).not.toBeNull()
    expect(ul!.querySelector('br')).toBeNull()
    expect(ul!.querySelectorAll('li').length).toBe(2)
  })
})

describe('renderTextWithMarkdown', () => {
  it('renders bold text correctly', () => {
    const { container } = render(
      <div>{renderTextWithMarkdown('**Hello**')}</div>
    )
    const strong = container.querySelector('strong')
    expect(strong?.textContent).toBe('Hello')
  })

  it('handles @model mentions alongside markdown', () => {
    const { container } = render(
      <div>
        {renderTextWithMarkdown('@claude **explains** things', { claude: 'text-blue-400' })}
      </div>
    )
    expect(container.textContent).toContain('@claude')
    const strong = container.querySelector('strong')
    expect(strong?.textContent).toBe('explains')
  })
})

// Probe evidence for the streaming jitter bug. renderMarkdown is a block
// parser: a partial fenced code block or an unfinished [link](url) produces
// a DIFFERENT tree than the closed version. That means if we parse markdown
// on every token during streaming, the rendered tree shifts shape as
// structural delimiters arrive. ChatPanel avoids this by rendering plain
// text during streaming; the tests below document the underlying instability
// so a future refactor does not quietly reintroduce the jitter.
describe('renderMarkdown is not safe to call per-token during streaming', () => {
  it('toggles <pre> on and off as an unclosed code fence becomes closed', () => {
    const duringStream = renderMarkdown('Here is code:\n```js\nconst x = 1;\n')
    const { container: midContainer } = render(<div>{duringStream}</div>)
    // The parser consumed the opening fence and wrapped the rest in a <pre>,
    // even though the stream has not produced the closing fence yet.
    expect(midContainer.querySelector('pre')).not.toBeNull()

    const afterClose = renderMarkdown('Here is code:\n```js\nconst x = 1;\n```\nDone.')
    const { container: doneContainer } = render(<div>{afterClose}</div>)
    // The closed fence still produces a <pre>, but the paragraph below it
    // ("Done.") now renders as a sibling paragraph. In a live stream this
    // is the exact moment where layout snaps, which is why the chat panel
    // uses plain text during streaming.
    expect(doneContainer.querySelector('pre')).not.toBeNull()
    expect(doneContainer.textContent).toContain('Done.')
  })

  it('produces an <a> tag only once the closing paren of a link arrives', () => {
    const partial = renderMarkdown('See [my doc](https://example.com')
    const { container: partialContainer } = render(<div>{partial}</div>)
    expect(partialContainer.querySelector('a')).toBeNull()

    const complete = renderMarkdown('See [my doc](https://example.com)')
    const { container: completeContainer } = render(<div>{complete}</div>)
    expect(completeContainer.querySelector('a')).not.toBeNull()
  })
})

// ─── →1355 / →1354 regression suite ─────────────────────────────────────────
// Both bugs share the same root cause: the Anthropic SDK's `stream.text_stream`
// yields text from every text block in sequence with NO separator between
// blocks.  When a tool-use response contains [text] [tool_use] [text], the two
// text blocks are concatenated with no space or newline and the combined string
// is sent to the frontend.
//
// The backend fix (stream_anthropic in chat_providers.py) switches to raw event
// iteration and emits a "\n\n" token whenever a new text block starts after a
// previous text block ended.  That makes the accumulated frontend string look
// like "sentence1.\n\nsentence2.\n\nsentence3." — which renderMarkdown already
// handles correctly.
//
// These tests are written in "desired-state" form: they pass the FIXED input
// (what the backend will send after the fix) and assert the correct output.
// They also contain a `broken` constant that reproduces the pre-fix bug so the
// exact regression is documented.
describe('→1355: text blocks from separate tool-use turns must render as separate paragraphs', () => {
  it('renders three properly-separated sentences as three distinct paragraphs', () => {
    // After the backend fix the frontend accumulates:
    //   "block1\n\nblock2\n\nblock3"
    // renderMarkdown must turn each paragraph-separated chunk into a <p>.
    const fixed =
      'Pushing the redesign on top of it.\n\n' +
      "Setting up todos for the brainstorming flow so we don't lose track.\n\n" +
      "Okay, here's what I learned from verifying ostk recall:"
    const { container } = renderNodes(renderMarkdown(fixed))
    const paras = container.querySelectorAll('p')
    expect(paras.length).toBeGreaterThanOrEqual(3)
    expect(paras[0].textContent).toContain('redesign on top of it')
    expect(paras[1].textContent).toContain('brainstorming flow')
    expect(paras[2].textContent).toContain('ostk recall')
  })

  it('produces only ONE paragraph when the block separator is absent (documents the pre-fix bug)', () => {
    // This is the exact content the backend currently sends:
    // three separate text blocks smashed together with no separator.
    // The single-paragraph result is the bug.  Once the backend is fixed,
    // this input string will never appear in practice.
    const broken =
      "redesign on top of it.Setting up todos for the brainstorming flow so we don't lose track.Okay, here's what I learned from verifying ostk recall:"
    const { container } = renderNodes(renderMarkdown(broken))
    const paras = container.querySelectorAll('p')
    // Documents broken behavior: everything collapses to 1 paragraph.
    expect(paras.length).toBe(1)
  })

  it('→1355 GREEN: backend now emits \\n\\n between text blocks so sentences render as paragraphs', () => {
    // After the backend fix, stream_anthropic injects "\n\n" between
    // consecutive text blocks.  The frontend accumulates the separator
    // as a normal token, so the stored message content looks like:
    //   "sentence1.\n\nsentence2.\n\nsentence3."
    // renderMarkdown turns each \n\n-separated chunk into a <p>.
    const fixed =
      "redesign on top of it.\n\n" +
      "Setting up todos for the brainstorming flow so we don't lose track.\n\n" +
      "Okay, here's what I learned from verifying ostk recall:"
    const { container } = renderNodes(renderMarkdown(fixed))
    const paras = container.querySelectorAll('p')
    expect(paras.length).toBeGreaterThanOrEqual(3)
    expect(paras[0].textContent).toContain('redesign on top of it')
    expect(paras[1].textContent).toContain('brainstorming flow')
    expect(paras[2].textContent).toContain('ostk recall')
  })
})

describe('→1354: code blocks and screenshots must preserve internal newlines', () => {
  it('renders a fenced code block with multiple lines intact', () => {
    // After the backend fix, a code block inside a second text block arrives
    // with a "\n\n" prefix that properly separates it from preceding prose.
    const fixed =
      'Here is the relevant code:\n\n' +
      '```python\ndef greet(name):\n    return f"Hello, {name}"\n```\n\n' +
      'Call it with `greet("world")`.'
    const { container } = renderNodes(renderMarkdown(fixed))
    const pre = container.querySelector('pre')
    expect(pre).not.toBeNull()
    // Both lines inside the code block must be present.
    expect(pre!.textContent).toContain('def greet(name):')
    expect(pre!.textContent).toContain('return f"Hello, {name}"')
    // Prose before and after the block must also appear.
    expect(container.textContent).toContain('Here is the relevant code')
    expect(container.textContent).toContain('Call it with')
  })

  it('→1354 GREEN: code fence preceded by \\n\\n is correctly parsed as a <pre> block', () => {
    // After the backend fix, prose from one text block and code from the
    // next arrive separated by "\n\n", putting the fence on its own line.
    const fixed =
      'Here is the relevant code:\n\n' +
      '```python\ndef greet(name):\n    return f"Hello, {name}"\n```'
    const { container } = renderNodes(renderMarkdown(fixed))
    const pre = container.querySelector('pre')
    expect(pre).not.toBeNull()
    expect(pre!.textContent).toContain('def greet')
    expect(pre!.textContent).toContain('return f"Hello, {name}"')
    expect(container.textContent).toContain('Here is the relevant code')
  })
})
// ─────────────────────────────────────────────────────────────────────────────

describe('light mode contrast: code elements use amber classes overridden in index.css', () => {
  it('inline code element has text-amber-300 class (overridden to dark in light mode)', () => {
    const { container } = renderNodes(renderMarkdown('`snippet`'))
    const code = container.querySelector('code')
    expect(code).not.toBeNull()
    expect(code!.className).toContain('text-amber-300')
  })

  it('fenced code block pre has text-amber-200 class (overridden to dark in light mode)', () => {
    const md = '```\nconsole.log(1)\n```'
    const { container } = renderNodes(renderMarkdown(md))
    const pre = container.querySelector('pre')
    expect(pre).not.toBeNull()
    expect(pre!.className).toContain('text-amber-200')
  })
})

// →1578: code blocks must not overflow the chat bubble
// JSDOM does not compute CSS layout, so we verify class intent rather than pixel widths.
// The max-w-full class ensures the <pre> is bounded by its parent's width, which in
// ChatPanel.tsx is now a block element with a definite max-width (85% of the panel).
// Combined with overflow-x-auto, this allows long code lines to scroll within the
// bubble instead of pushing the bubble wider than the chat panel.
describe('→1578: fenced code blocks are constrained to parent width', () => {
  it('fenced code block <pre> has max-w-full class so it cannot overflow its parent', () => {
    const md = '```js\nconst x = "' + 'a'.repeat(200) + '";\n```'
    const { container } = renderNodes(renderMarkdown(md))
    const pre = container.querySelector('pre')
    expect(pre).not.toBeNull()
    expect(pre!.className).toContain('max-w-full')
  })

  it('fenced code block <pre> has overflow-x-auto so long lines scroll instead of wrapping', () => {
    const md = '```\n' + 'x'.repeat(300) + '\n```'
    const { container } = renderNodes(renderMarkdown(md))
    const pre = container.querySelector('pre')
    expect(pre).not.toBeNull()
    expect(pre!.className).toContain('overflow-x-auto')
  })

  it('fenced code block preserves whitespace-pre so indentation is not collapsed', () => {
    const md = '```python\ndef f():\n    return 1\n```'
    const { container } = renderNodes(renderMarkdown(md))
    const pre = container.querySelector('pre')
    expect(pre).not.toBeNull()
    expect(pre!.className).toContain('whitespace-pre')
  })
})
