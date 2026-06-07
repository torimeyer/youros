# Privacy

yourOS runs on your computer. It is not a cloud service. There is no
yourOS-operated backend that receives your data.

## What yourOS stores, and where

Everything yourOS keeps about you lives in two places on your own disk:

* `~/.youros/` - tasks, notes, chat history, agent transcripts,
  preferences, and any files you drop into yourOS.
* The yourOS application folder - the program itself, never your
  personal content.

Nothing in those folders is sent to yourOS. If you delete `~/.youros/`,
your yourOS data is gone.

## What leaves your computer

yourOS talks to the internet only in the ways you choose to turn on.
Each one is optional, and each one goes directly from your machine to
the third party. yourOS is not in the middle.

* **Anthropic (Claude)** - when you chat or run an agent that uses
  Claude, the prompt and response go to Anthropic's API using the API
  key you configured. See Anthropic's privacy policy for what they do
  with it.
* **Google (Gemini)** - prompts and responses go directly to Google.
  **Note on Free Tier:** If you use the Gemini API free tier, Google
  may use your data to improve its products. For strict privacy, use
  a paid tier or Vertex AI. See the [Gemini API Privacy Notice](https://ai.google.dev/gemini-api/terms#data-use-unpaid)
  for details.
* **Google (Gmail, Calendar, Drive)** - only if you connect these in
  Settings. Requests go straight from your machine to Google.
* **GitHub, Slack, and other integrations** - same pattern. Only
  active if you connect them. yourOS does not receive a copy.
* **Update checks** - yourOS checks GitHub for new releases. Only the
  release metadata is fetched. Nothing about you is sent.

There is no telemetry. yourOS does not phone home. We do not collect
crash reports or usage analytics.

## Your data, your call

* **Export** - everything is already a plain file under `~/.youros/`.
  Copy that folder and you have your data.
* **Delete** - remove `~/.youros/` and disconnect any integrations in
  their respective account settings (Google, GitHub, etc.). Optionally
  revoke the API keys you issued to yourOS.
* **Move to another computer** - copy `~/.youros/` across. yourOS will
  pick it up on launch.

## Questions

yourOS is open source. If you want to verify any of the above, read
the code. Every network request is in the `api/` folder. If something
here is out of date or wrong, please open an issue on GitHub.
