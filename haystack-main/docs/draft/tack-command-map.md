# Tack → ostk Command Map

status: draft
source: audit trail data mine (1085 events, 48 hay.filed, 8 threads, 30 tack verbs)
date: 2026-03-09

## The evidence chain

```
Human types tack verb → hay.filed in audit → ostk compile → needle → commit → command
```

48 hay events filed. 11 compiled to needles. 37 stayed as hay.
The compilation ratio (23%) means most human thinking stays thinking.
Only the sharpest thoughts become executable.

## Tack verb → ostk command mapping (audit-verified)

| Tack verb | ostk command | Status | Evidence |
|-----------|-----------------|--------|----------|
| `:compile` | `ostk compile` | shipped | 48 hay.compiled events |
| `:show`/`:showme` | `ostk show <target>` | shipped | hay.filed: "universal query command" |
| `:draft` | `ostk draft` | shipped | 13 draft.created events |
| `:spec` | `ostk promote` | shipped | 4 spec.promoted events |
| `:needle` | `ostk needle add` | shipped | 334 task.added events |
| `:hay`/`:straw` | `ostk hay` | shipped | 48 hay.filed events |
| `:emerge` | `ostk hay` → compile | shipped | hay: "emergence = uncompiled observations" |
| `:spawn` | `ostk spawn` | shipped | 2 agent.spawned events |
| `:proc` | `ostk ps` | shipped | agent fleet in TUI |
| `:reap` | `ostk reap` | shipped | 9 reap events |
| `:bench` | `ostk bench` | shipped | 98 bench.docker + 13 bench.run events |
| `:verify` | `ostk verify` | shipped | used this session, integrity OK |
| `:audit` | `ostk audit check` | shipped | 1085 events tracked |
| `:correct`/`:nudge` | `ostk nudge` | shipped | nudge delivery wired in boot (→542) |
| `:boot`/`:BOOT` | `ostk boot` | shipped | 2 project.installed events |
| `:recover` | `ostk boot` (re-read state) | shipped | boot reads boot.md = recovery |
| `:calibrate` | `ostk compile` (re-triage) | partial | hay: "calibrate = telescope on thread" |
| `:delegate` | `ostk run`/`spawn` | shipped | Agentfile + run command |
| `:milestone` | — | **not shipped** | →553 filed, point = needle resolved |
| `:insight` | — | **not shipped** | hay stays hay (no command yet) |
| `:inform` | `ostk nudge` | shipped | cross-OS nudge = inform |
| `:boost` | — | **not shipped** | intensity signal, no command equivalent |

22 tack verbs map to shipped commands. 3 not shipped. 1 partial.

## Threads (audit-codified)

8 threads in audit trail:

| Thread | Needles | What it proved |
|--------|---------|----------------|
| **ostk-intelligence** | →386→407 (11 needles) | Tack IS training data for fcp-ostk. The language compiled itself into the OS. |
| **ostk-test-bench** | →415,→383,→408 | Silent shim beat prompted beat control. Invisible infrastructure wins. |
| **human-nudge** | →416,→415,→417 | Human corrections = the MMU. The arg IS the correction that triggers thinking. |
| **humanfile-agentfile** | →430,→431,→422,→423,→429,→420,→419 | Agentfile DECLARES, Humanfile PROVIDES, kernel MEDIATES. Ring 0/Ring 3 boundary. |
| **ostk-os-bin** | →429,→416,→420,→415 | The binary IS the OS. One binary, symlinks, transparent. |
| **shutdown-sequence** | →407,→430,→429,→419 | boot.md regenerated, verified, committed. Forward recovery only. |
| **fcp-ostk** | →437,→404,→407,→432,→435 | The compiler compiles the human into the OS. fcp-ostk = the device driver for human intent. |
| **tori-boot** | (empty) | First external user. 40% success rate → TORI-MODE gate. |

## Emergence (audit-measured)

### ID format evolution
```
bd-NNN: 237 events (Sprint 1-4, bead era)
nd-NNN:  17 events (transition period)
→NNN:   144 events (current, arrow era)
```
Three formats. The arrow won because it's the shortest and most intentional.

### Compilation ratio
```
48 hay.filed → 11 compiled to needle (23%)
                37 stayed as hay (77%)
```
Most human thinking stays thinking. The OS preserves it all (audit trail) but only sharpens 23% into executable work. This IS the intelligence — knowing what NOT to compile.

### Communication cost
```
Events per day:
  2026-03-08: ~800 events (build day, heavy)
  2026-03-09: ~285 events (sprint day, focused)
```
Focus compounds. The second day produced less noise and more signal.

### Tack verb density
30 unique tack verbs emerged from natural conversation. 22 map 1:1 to shipped commands. The human didn't learn ostk's command surface — ostk's command surface grew to match the human's vocabulary. The audit proves the direction of compilation.
