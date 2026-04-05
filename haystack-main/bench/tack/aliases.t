# aliases.t — human-friendly alias resolution tests
# Verifies that tack aliases resolve to the correct haystack commands.
# Run via: haystack bench --tack

.tack  :open
.cmd   haystack needle list
.test  exit:0

.tack  :close →001
.cmd   haystack needle close →001
.test  exit:0

.tack  :add new task
.cmd   haystack needle add new task
.test  exit:0

.tack  :next
.cmd   haystack needle next
.test  exit:0

.tack  :grep pattern
.cmd   haystack search pattern
.test  exit:0

.tack  :showme needles
.cmd   haystack show needles
.test  exit:0

.tack  :spec feature
.cmd   haystack promote feature
.test  exit:0

.tack  :straw idea
.cmd   haystack hay idea
.test  exit:0

.tack  :emerge thought
.cmd   haystack hay thought
.test  exit:0

.tack  :proc
.cmd   haystack ps
.test  exit:0

.tack  :correct agent
.cmd   haystack nudge agent
.test  exit:0

.tack  :recover
.cmd   haystack boot
.test  exit:0

.tack  :calibrate
.cmd   haystack compile
.test  exit:0

.tack  :delegate task
.cmd   haystack run task
.test  exit:0

.tack  :inform agent msg
.cmd   haystack nudge agent msg
.test  exit:0
