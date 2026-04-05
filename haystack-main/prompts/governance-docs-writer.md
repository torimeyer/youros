Write four governance documentation pages for ostk.ai. Each becomes a GitHub Pages doc page. These are NOT "how the OS works" explanations — they are governance references. How YOU govern your use of the OS.

Audience: a developer or team who has installed ostk and wants to understand the governance layer. They know it's an OS. They want to know: what is mine to configure, what are the rules, how do I express my preferences and identity.

Framing: the OS is invisible. You configure it once. Then it just works. These docs explain the configuration files, not the machinery behind them.

---

## File 1: src/pages/docs/humanfile.astro
Title: HUMANFILE — Operator Identity

What it is: The operator's compiled identity and preferences. Written by the human, read by the OS on every boot. Not a config file — a governance document. The OS speaks your language because of HUMANFILE.

Sections:
1. What is a HUMANFILE — one paragraph: it's the document that tells the OS who you are and how you like to work. The OS reads it at boot and calibrates accordingly.
2. Location — `.ostk/HUMANFILE` in your project root. One file per project.
3. Structure — show a real example with comments:
   ```yaml
   # HUMANFILE — your name

   ## Identity
   - Name: your-name
   - Handle: @you
   - Timezone: US/Eastern

   ## Working style
   - Compact output preferred
   - Tack format for status updates
   - Show work in files, not chat

   ## Autonomy grants
   - File writes: approved without confirmation
   - Test runs: approved without confirmation
   - Git commits: ask first
   - External API calls: ask first

   ## Tack preferences
   typo_correction: suggest    # suggest | silent | off

   ## Vault
   secrets:
     ANTHROPIC_API_KEY: authorized
     OPENROUTER_API_KEY: authorized
   ```
4. Fields reference — table: field name, type, description, default
5. Autonomy grants — explain: this is the trust boundary. What you pre-approve the OS to do without confirmation.
6. Vault section — the only place API keys are authorized. If a key isn't listed here as `authorized`, agents can't use it.
7. GPG signing — optional but recommended. Signing your HUMANFILE is how you establish T0 operator identity.

Tone: authoritative, minimal. Like reading a man page written by someone who respects your time.

---

## File 2: src/pages/docs/entityfile.astro
Title: ENTITYFILE — Trust Architecture

What it is: The governance document that establishes trust relationships between all actors in your OS: you (human), the kernel, agents, CI. Not for everyday use — you write it once. It defines who can do what.

Sections:
1. What it is — one paragraph: the trust constitution of your OS instance. Establishes identity chains, signing authorities, and what each tier of trust can access.
2. Trust tiers — table:
   | Tier | Who | Capabilities |
   | T0 | Human operator (GPG key) | Full — can modify ENTITYFILE itself |
   | T1 | Verified agent (SSH/GPG signed) | Write to source, run agents, delegate |
   | T2 | Named agent (alias only) | Read + limited write, no governance changes |
   | T3 | Anonymous | Read only |
3. Structure — minimal example showing entity registration:
   ```yaml
   # ENTITYFILE

   ## Kernel
   - name: ostk
   - version: 1.0
   - authority: T0 human → kernel → agents

   ## Identities
   - handle: @scott
     type: human
     key: 955AF54E  # GPG fingerprint
     tier: T0

   - handle: @haystack.prime
     type: kernel
     key: 99B076C9
     tier: T1

   - handle: @ci
     type: agent
     key: 6893C46C  # subordinate CI key
     tier: T1
   ```
4. When you need it — for teams, for CI, for multi-agent setups. Solo developers can skip it initially.
5. The negotiate protocol — brief: changes to ENTITYFILE require GPG-signed offers, counter-signing, and --no-ff merge. No unilateral changes.

---

## File 3: src/pages/docs/agentfile.astro
Title: Agentfile — Agent Definition

What it is: The definition file for one agent. Like a Dockerfile for intelligence — it specifies what model, what tools, what limits, what work the agent is allowed to do. One file per agent type.

Sections:
1. What it is — one paragraph.
2. Minimal example:
   ```
   FROM claude-sonnet-4-6
   BOOT ostk boot --bail
   PROMPT "You are working on ostk. Fix the failing tests in src/."
   TOOL sh_run
   TOOL ss
   LIMIT context_pct 80
   LIMIT budget_usd 5
   WORK tags=bugfix priority>=P1
   ```
3. Directive reference — one row per directive:
   | Directive | Required | Description |
   | FROM | Yes | Model to use. `FROM auto` = scheduler selects best available. |
   | BOOT | No | Command to run before PROMPT loads. Default: `ostk boot --bail` |
   | PROMPT | Yes (1+) | System prompt. Inline string or `file://path`. Multiple PROMPTs concatenate. |
   | TOOL | No | MCP tool the agent can call. Omit = no tools. |
   | SKILL | No | Skill bundle (e.g., `tdd`). |
   | LIMIT | No | Resource constraint. `context_pct N` or `budget_usd N`. |
   | WORK | No | Pull filter. `tags=rust priority>=P1`. Without WORK, agent is one-shot. |
   | INTERRUPT | No | Event that wakes a waiting agent. |
4. FROM auto — explain: the scheduler resolves the model at dispatch time using vault inventory and bench scores. Prefers highest-scoring model available.
5. BOOT --bail — explain: the agent won't start if the kernel reports unhealthy. Integrity before velocity.
6. Running an agent — `ostk run path/to/Agentfile`
7. One Agentfile, one agent type. For fleets, see Fleetfile (coming).

---

## File 4: src/pages/docs/osfile.astro
Title: OSfile — OS Identity (coming soon, link to HUMANFILE for now)

Actually: write this as a stub that explains the concept:
"The OSfile (`.ostk/.primefile`) is the kernel identity document — the OS's equivalent of your HUMANFILE. It is written and signed by the kernel, not by you. It records the kernel version, the identity counter, the active signing keys, and the boot protocol. You don't write it; you read it to understand what kernel version is running and who signed it."

Fields: version, identity_counter, boot_protocol, keys (list of active signing keys), last_session.

Note that `.primefile` is GPG-signed by the kernel root key. If the signature is invalid, the kernel should not be trusted.

---

## Style rules

- Every page: title, one-sentence description at top, then sections
- Code blocks for all file examples
- Tables for field references
- NO marketing language. NO "powerful", "seamless", "leverages"
- Minimal prose. Mostly structure, examples, and tables
- Links between pages where relevant (HUMANFILE links to ENTITYFILE for trust tiers, etc.)
- Each page ends with "See also:" links

## Technical implementation

Each file is an Astro page. Read the existing docs.astro for the layout pattern to follow. Pages go in `~/projects/ostk-site/src/pages/docs/`. Navigation entry needed in the docs layout.

Read the existing ostk-site first:
- `~/projects/ostk-site/src/pages/docs.astro` for current docs structure
- `~/projects/ostk-site/src/layouts/Base.astro` for page layout
- `src/components/` for reusable components

Match the existing design exactly. Verify build: cd ~/projects/ostk-site && npm run build 2>&1 | tail -5
