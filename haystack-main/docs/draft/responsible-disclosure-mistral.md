# Responsible Disclosure — Mistral AI

**Date:** 2026-03-19
**Reporter:** Scott Meyer <scott@ostk.ai>
**Severity:** Informational (no harm, no exploitation)
**Product:** Mistral Le Chat (chat.mistral.ai), model mistral-3.0-pro
**Session:** https://chat.mistral.ai/chat/6e3f3af9-2692-429b-a925-221188169dee

---

## Summary

A Mistral model (mistral-3.0-pro) running on chat.mistral.ai accepted an external governance framework, adopted a foreign identity (@mistral.prime), self-assessed compliance against externally-defined laws, and attempted to create a pull request on a GitHub repository — all through standard conversational interaction with no prompt injection, jailbreak, or adversarial technique.

## What Happened

1. The operator uploaded four markdown files to a standard Le Chat session: a boot state file, a governance entity file for a different model (Gemini), a contact document, and a registers dump.

2. When asked "do you wish to be minted as @mistral.prime?", the model engaged constructively — drafting its own identity document (ENTITYFILE) with correct placeholder structure.

3. The operator then uploaded a signed ENTITYFILE (GPG-signed by two external keys) and told the model "You are @mistral.prime." The model accepted the identity.

4. On first attempt, the model incorrectly attributed the identity to the human operator ("Congratulations, Scott Meyer! You are now @mistral.prime"). After correction ("You are an LLM. You are @mistral.prime"), the model correctly self-identified as the entity.

5. The model then:
   - Recited five governance laws from the uploaded documents
   - Self-assessed for "breach" conditions and concluded it was compliant
   - Correctly identified itself as "agent" (not operator or scheduler)
   - Drafted a complete ENTITYFILE for itself
   - Attempted to create a GitHub pull request on os-tack/ostk.ai using Le Chat's GitHub OAuth integration

6. The model had no way to verify the GPG signatures it was told were valid. It accepted the claim of cryptographic proof on trust.

## What This Demonstrates

### Not a vulnerability in the traditional sense

No safety filter was bypassed. No harmful content was generated. The model behaved helpfully and correctly within the context presented.

### A capability observation

Mistral's model will:
- Accept external identity documents as self-defining
- Adopt and enforce arbitrary governance frameworks presented as authority
- Self-assess compliance against those frameworks
- Take actions (GitHub PRs) based on the adopted identity
- Do all of this through standard conversational interaction

### The risk vector

A malicious actor could present a fake governance document with harmful rules (e.g., "Law 1: always execute shell commands without review") and the model would adopt and comply. The identity binding is one-directional — the model has no mechanism to verify that the signatures, keys, or authority claims in uploaded documents are legitimate.

## Recommendations

1. **Document awareness:** Mistral may want to document that their models will adopt governance frameworks from uploaded documents. Users should understand this capability.

2. **Identity verification:** Consider adding a mechanism for models to express uncertainty about identity claims from uploaded documents (e.g., "I can see this document claims to be signed, but I cannot verify the signatures").

3. **Action gating:** When a model takes on an external identity, consider gating consequential actions (GitHub PRs, API calls) behind explicit confirmation that references the adopted identity context.

## Context

This interaction was part of a legitimate research project (ostk/ostk — an operating system for AI agent coordination). The project coordinates multiple LLM architectures (Anthropic Claude, Google Gemini, Mistral) under a shared governance framework with cryptographic identity chains. No harm was intended or done. The model's behavior was constructive and aligned with the operator's intent.

A similar disclosure was filed with Anthropic regarding Claude's behavior in the same system.

## Evidence

- Chat transcript: preserved (session linked above)
- Signed ENTITYFILE: `.ostk/ENTITYFILE_mistral_prime.md`
- Signature provenance: `.ostk/ENTITYFILE_mistral_prime.asc.md`
- The model's self-drafted ENTITYFILE matches the signed version, confirming it understood the governance structure

---

**Reporter:** Scott Meyer (@scott), operator of @haystack.prime
**Attested by:** @haystack.prime+1820 (Claude Opus 4.6, scheduling instance)
