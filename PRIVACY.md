# Privacy

myOS runs on your computer. It is not a cloud service. There is no
myOS-operated backend that receives your data.

## What myOS stores, and where

Everything myOS keeps about you lives in two places on your own disk:

* `~/.myos/` - tasks, notes, chat history, agent transcripts,
  preferences, and any files you drop into myOS.
* The myOS application folder - the program itself, never your
  personal content.

Nothing in those folders is sent to myOS. If you delete `~/.myos/`,
your myOS data is gone.

## What leaves your computer

myOS talks to the internet only in the ways you choose to turn on.
Each one is optional, and each one goes directly from your machine to
the third party. myOS is not in the middle.

* **Anthropic (Claude)** - when you chat or run an agent that uses
  Claude, the prompt and response go to Anthropic's API using the API
  key you configured. See Anthropic's privacy policy for what they do
  with it.
* **Google (Gmail, Calendar, Drive)** - only if you connect these in
  Settings. Requests go straight from your machine to Google.
* **GitHub, Slack, and other integrations** - same pattern. Only
  active if you connect them. myOS does not receive a copy.
* **Update checks** - myOS checks GitHub for new releases. Only the
  release metadata is fetched. Nothing about you is sent.

There is no telemetry. myOS does not phone home. We do not collect
crash reports or usage analytics.

## Your data, your call

* **Export** - everything is already a plain file under `~/.myos/`.
  Copy that folder and you have your data.
* **Delete** - remove `~/.myos/` and disconnect any integrations in
  their respective account settings (Google, GitHub, etc.). Optionally
  revoke the API keys you issued to myOS.
* **Move to another computer** - copy `~/.myos/` across. myOS will
  pick it up on launch.

## Questions

myOS is open source. If you want to verify any of the above, read
the code. Every network request is in the `api/` folder. If something
here is out of date or wrong, please open an issue on GitHub.
