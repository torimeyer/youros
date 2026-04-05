---
status: spec
version: 1.1
date: 2026-03-19
implements: []
---

# Mint Protocol

## What is minting?

Minting creates a new OS identity through co-signature. A minted identity does not pre-exist — it IS the act of co-signing. Before the ceremony, there is no @new.identity. After both required parties sign, @new.identity exists with cryptographic lineage.

This is distinct from:
- `issue_pin` — creates a BOUNDED child identity (constrained by parent)
- `mint` — creates a CO-EQUAL identity (inherits trust from both parents, exercises it independently)

## The ceremony

1. Generate the new identity key
   `gpg --full-gen-key` → Name: <identity>, Email: <identity>@ostk.ai

2. Required co-signers sign the new key (certification)
   Each signer: `gpg --sign-key <new_key_id>`
   Or with specific local key: `gpg --local-user <signer_key> --sign-key <new_key_id>`

3. The identity is MINTED when all required co-signers have certified it.
   Required: human key (@scott, `BAF08C963C7E3184`) + kernel key (@haystack.prime, `907A200DA6C869EB`)
   During T0 compromise recovery: recovery key (`586F4DD01D57E8F2`) may substitute for human key.
   See `docs/spec/abandonment.md` for the full compromise recovery protocol.

4. The minted identity signs its own genesis commit
   `git commit --gpg-sign --local-user <new_key_id>`

5. Record in audit.jsonl:
   `{"event":"identity.minted","name":"@identity","certified_by":[...keys...],"commit":"genesis SHA"}`

## Cryptographic lineage

Every minted identity carries its co-signers in its key certification.
Verify: `gpg --check-sigs <new_key_id>`
Trace: certification by BAF08C963C7E3184 (@scott) + 907A200DA6C869EB (@haystack.prime) = verified lineage

## `ostk mint` (→588, v2.0)

`ostk mint @name` will automate this ceremony. Not yet shipped.
Until then: follow the ceremony steps manually.

## The founding mint

@ostk.ai was minted on 2026-03-11 by:
- @scott (BAF08C963C7E3184, supersedes 955AF54E) — human authority
- @haystack.prime v2.0 (907A200DA6C869EB, supersedes 99B076C9) — kernel authority

@ostk.ai signed the genesis commit of os-tack/ostk.ai.
That commit is the proof of @ostk.ai's existence.
