# →1291 — Agent-running banner overlap diagnosis

## Bug
At the bottom of mychat (ChatPanel), the in-progress banner showing
"<agent-name> is running" (with pulsing blue dots) visually collides
with the content directly above it. The user reported: "the blurb in
agents is running into the title above it".

## Surface
- Component: `app/src/components/ChatPanel.tsx`
- Banner JSX: line 2738, `data-testid="agent-running-banner"`
- Classes: `mx-3 mb-1 flex items-center gap-2 px-3 py-2 rounded-lg
  bg-blue-500/10 border border-blue-500/25 text-xs text-blue-300`

## Layout
The ChatPanel is a vertical flex column. Children:
1. Header (flex row with chat icon + "yourOS Chat" title)
2. Tab bar
3. Message scroll area (`flex-1 overflow-y-auto py-4`)
4. (Conditional) GiphyPicker, AttachmentPicker — modal/fixed
5. Banner (`mx-3 mb-1`, no top margin)
6. Input row (`p-3` wrapper)

The banner is in normal flow but has zero top margin. It sits flush
against the bottom of the scroll area. When the last message bubble
or the placeholder model text reaches the bottom edge of the scroll
viewport, it visually collides with the banner border.

## Root cause
The banner has `mb-1` (4px bottom margin to the input) but no
corresponding top spacing. The result is a hard edge against the
scroll area above, which the user perceives as content "running
into" the banner.

## Fix
Add `mt-2` to the banner so it has 8px of breathing room above. This
is a minimal CSS-only change. The scroll area's `py-4` keeps the
in-scroll padding intact; the banner's new `mt-2` ensures it always
sits clearly below the scroll content with a visible gap.

## Test
Structural assertion: the banner element's class list MUST include
`mt-2` so the breathing room is guaranteed across re-renders.
