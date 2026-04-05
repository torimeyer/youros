# core.t — boot-critical tack resolution tests + full verb coverage
# Run via: haystack bench --tack

# ── Boot-critical (original 5) ──

.tack  :compile
.cmd   haystack compile
.test  exit:0
.boot-critical  true

.tack  :boot
.cmd   haystack boot
.test  exit:0
.boot-critical  true

.tack  .? status
.cmd   haystack show status
.test  contains:needles
.boot-critical  true

.tack  :bench
.cmd   haystack bench
.test  contains:bench
.boot-critical  true

.tack  :verify
.cmd   haystack verify
.test  exit:0
.boot-critical  true

# ── Init sequence verbs ──

.tack  :shutdown
.cmd   haystack shutdown
.test  exit:0
.boot-critical  true

.tack  :status
.cmd   haystack status
.test  exit:0

.tack  :reap
.cmd   haystack reap
.test  exit:0

# ── Work verbs ──

.tack  :add test needle
.cmd   haystack needle add test needle
.test  exit:0

.tack  :hay test thought
.cmd   haystack hay test thought
.test  exit:0

.tack  :draft something
.cmd   haystack draft something
.test  exit:0

.tack  :promote spec
.cmd   haystack promote spec
.test  exit:0

.tack  :commit
.cmd   haystack commit
.test  exit:0

.tack  :decompose
.cmd   haystack decompose
.test  exit:0

.tack  :amend
.cmd   haystack amend
.test  exit:0

.tack  :shelve
.cmd   haystack shelve
.test  exit:0

.tack  :unshelve
.cmd   haystack unshelve
.test  exit:0

.tack  :trace
.cmd   haystack trace
.test  exit:0

.tack  :post
.cmd   haystack post
.test  exit:0

.tack  :merge
.cmd   haystack merge
.test  exit:0

.tack  :pull
.cmd   haystack pull
.test  exit:0

.tack  :purge
.cmd   haystack purge
.test  exit:0

.tack  :bail
.cmd   haystack bail
.test  exit:0

# ── Navigation ──

.tack  :diff
.cmd   haystack diff
.test  exit:0

.tack  :search pattern
.cmd   haystack search pattern
.test  exit:0

.tack  :history
.cmd   haystack history
.test  exit:0

.tack  :log
.cmd   haystack log
.test  exit:0

.tack  :ls
.cmd   haystack needle list
.test  exit:0

.tack  :find pattern
.cmd   haystack search pattern
.test  exit:0

# ── Process / system verbs (non-blocking only) ──

.tack  :ps
.cmd   haystack ps
.test  exit:0

.tack  :nudge agent fix
.cmd   haystack nudge agent fix
.test  exit:0

.tack  :thread
.cmd   haystack thread
.test  exit:0

.tack  :import data
.cmd   haystack import data
.test  exit:0

.tack  :secret
.cmd   haystack secret
.test  exit:0

.tack  :ask question
.cmd   haystack ask question
.test  exit:0

.tack  :audit
.cmd   haystack audit check
.test  exit:0

.tack  :clock
.cmd   haystack clock
.test  exit:0

.tack  :metrics
.cmd   haystack metrics
.test  exit:0

.tack  :tack
.cmd   haystack tack
.test  exit:0

.tack  :show
.cmd   haystack show
.test  exit:0

# NOTE: :serve, :listen, :scheduler, :spawn, :run, :init, :install
# omitted — these start long-running processes or mutate state.
# Resolution verified via unit tests in src/fcp/haystack.rs.

# ── Intent types (non-command) ──

.tack  →567
.cmd   haystack show →567
.test  exit:0

.tack  :: agent1 fix build
.cmd   haystack nudge agent1 fix build
.test  exit:0
