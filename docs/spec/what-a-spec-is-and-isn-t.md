---
type: customer_docs
created_at: 2026-05-29T16:58:45Z
promoted_at: 2026-05-29T17:00:00Z
title: What a spec is (and isn't)
status: spec
---

## Acceptance Criteria

- [ ] All 7 FAQ questions are covered in plain language
- [ ] No jargon or em-dashes in any section
- [ ] Audience section explains who this is for
- [ ] Content matches what is displayed in the SpecFaq page in yourOS

## Audience

Anyone using yourOS who is about to write their first spec, or who has written a few and wants a plain-language refresher. No engineering background required.

## Outline

1. What is a spec?
2. What a spec is NOT
3. The kinds of specs
4. What the statuses mean
5. Required vs optional fields
6. When you do NOT need a spec
7. How yourOS treats specs

## Content

### What is a spec?

A spec describes what you want to build and why it matters. It is the shared source of truth for everyone involved in a piece of work, whether that is you, a teammate, or an agent. A spec does not say how to do the work. That part is up to the builder.

### What a spec is NOT

- Not code. A spec captures intent, not implementation.
- Not a step-by-step plan. Plans come after the spec, not before.
- Not a long prompt. A prompt is a one-time input. A spec is a living document that gets updated as the work evolves.

### The kinds of specs

- **Prototype:** an early idea that is not yet ready to build from.
- **Vision / Roadmap:** a direction for where the product is heading over time.
- **Customer docs:** material written for people using the product.
- **Engineering feature:** a description of a specific capability to build.

### What the statuses mean

- **Draft:** still being written. Not ready to act on.
- **Ready:** complete enough to start work from.
- **In Progress:** work is actively happening against this spec.
- **Done:** the work described in the spec is complete.

### Required vs optional fields

Required fields must be filled before a spec can move to Ready. Optional fields are not needed to start work, but they make the spec stronger by adding context, constraints, or examples.

### When you do NOT need a spec

Skip the spec for a tiny fix, a quick refactor, or a one-off task that takes under an hour and will not be revisited.

### How yourOS treats specs

yourOS is spec-first: important work starts with a spec. Specs are also kept and updated as work progresses. The point is that specs serve the work, not the other way around.
