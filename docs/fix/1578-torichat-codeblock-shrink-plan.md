# →1578 Fix Plan: Torichat code block overflow

## Root cause

The assistant message bubble uses `display: inline-block` inside an outer container with
`max-w-[85%] w-fit`. When a `<pre>` with a long code line is rendered inside:

1. `<pre>` has no max-width → expands to full content width (e.g. 1000px)
2. `inline-block` bubble sizes to pre content → also 1000px
3. Outer div is capped at max-w-[85%] (e.g. 340px) but has `overflow: visible` (default)
4. The 1000px bubble visually overflows the 340px outer div
5. `overflow-x-auto` on the `<pre>` never fires because the pre has no width constraint
   (content width = pre width = no overflow from pre's perspective)

Result: code block stretches wider than the chat panel.

## Fix (3 targeted changes)

### 1. `app/src/lib/markdown.tsx` — `<pre>` element
Add `max-w-full` to the pre's className. Once the bubble has a definite width
(from fix #2), this constrains the pre to that width and lets `overflow-x-auto` work.

### 2. `app/src/components/ChatPanel.tsx` — outer container div (line ~2726)
For assistant non-broadcast messages, remove `w-fit` from the outer div
(`max-w-[85%] w-fit` → `max-w-[85%]`). This gives the outer div a definite width
(block element, up to 85% of panel) that child percentage widths can resolve against.
The outer div is transparent (no background/border), so visually no change.

### 3. `app/src/components/ChatPanel.tsx` — bubble div (line ~2731)
For assistant non-broadcast messages, change `inline-block border...` to
`block w-fit max-w-full border...`. Now the bubble:
- Is `display: block` with `width: fit-content` → shrinks for short messages (same visual)
- Is bounded by `max-w-full` (100% of the outer div's definite 85% width)
- Result: short messages stay narrow, code blocks are constrained to bubble width

### Why this preserves short-message appearance
`block w-fit` on the bubble means: "be as wide as content, up to parent's width." For
"hi", fit-content is small → bubble is narrow. For a 1000px code block, fit-content is
capped at 85% → bubble is 85% wide. The pre (max-w-full) then has a 340px constraint,
`overflow-x-auto` fires, horizontal scrollbar appears.

## Test
Add a vitest test in `app/src/lib/markdown.test.tsx` that renders a long-line code
block inside a constrained container and asserts the rendered `<pre>` has `max-w-full`
in its className (since JSDOM doesn't do layout, we verify the CSS class is applied).
