read the following prompt and then, add a CLAUDE.md file for ~/projects/ostk. After you write the perfect CLAUDE.md file, execute the discussion. I'll participate along the way, so keep me informed.



llmOS Review:
I'll comment specifc elements of your response. If I don't highlight or comment about it, I either don't have a strong opinion, it's already idiomatic and warrants no discussion, or you're probably right, but onlys testing proves. 

On Five Principals:
1. Yes. That's fcp principal. the LLM reasons about the shape/pattern that's even recognizable to human eyes (a key insight in model attention) creating complex xlsx, pptx, mid or .drawio files all have a familiar shape, applied to a different domain. 
2. LAW. This could be different in the future, but the limits imposed by the model trainers LIMIT what happens in MY terminal. llmOS is the agnostic solution with zero dependencies. it performs byte-for-byte passthrough for the commands you already use and condeses the output between what your eyes see and what the LLM sees (this is assume symlink mish->bash, etc, maybe not said exactly accurate, but sounded somewhere in the ballpark). 
3. I like this in concept. I worry about the user experience. Humans are already tired of LLMs required vast markdown documents litered in whatever folder the llm decided to resolve that turn. 
4. Exactly. 80% of users still have no idea the tool available at their finger tips(you). it will save turns, tokens and time, regardless. most of the time, the llm is already trying to coordinate agent dependency order on file change safety AND the user is prompting "change the color". The coordination problems we've experienced are pushing boundaries, but solving these problems make the experience bullet proof. 
5. Exactly. 'Device' drivers of whatever shape 'device' adapts to

On the Write Path:
Exaclty and incredibly elegant. Another consideration is the HUMAN operator here. if THEY issue one of these coammands, they should be notified the same way that their autonomous agents are busy on the same file. that's fantastic. a new OS errorcode space 

-The key: actually, a simple one binary install that symlinks over your regular tools, changes nothing you do AND doesn't interfere with changes or unrecognized new args (via passthrough) is compelling. never in the way, just added savings. 

On Agent Lifecycle:
- tool use failed, bad command, permission, user frustration or tool update...
I don't agree that ostk should attempt to recover agents at all. However, if the AGENT can recover itselff via the ambient context ostk provides, that seems like an achieveable goal. 
- That compressed summary: NO. Imagine a Human interacting with an operating system for the first time after a user left it open. Unsaved edited documents, applications open, etc. it's garbage to an LLM. A first MVP may leave a 'hanging session' of this sort of state in ostk for users. a new agent trying to make sense of that noise has no value. later, the kernel or agentic tooling will improve to provide accurate signals !BECAUSE ostk defines them.

On Awareness:
One instersitial command on start-up in an LLM exlusive environment should prompt the agent to run "--agents to fix." ERR 2. This is a strong signal that encourages the LLM to "fix" what is a non-issue. Subsequent runs of the same command don't nag and bloat context or distract from the goal. 


IMPORTANT: 'tool subscriptions' amongst agents feels wrong -- how does unix coordinate the same need?

What ships first?
The immediate scaffolding needed to coordinate the conversation we've tried to have, and allow further discovery of coordination patterns in this environment.
You should import and reuse the core elements of mish and slipstream - those are the "open-source" store front that draw users to create their own 

What we Kill:
Exactly. 



---
Write it.