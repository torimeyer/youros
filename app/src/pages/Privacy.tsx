import { Link } from 'react-router-dom';
import TopBar from '../components/TopBar';
import { PageHeader } from '../components/ui';

// Keep this text in sync with PRIVACY.md at the repo root. The file is the
// source of truth and what people find on GitHub. This component is the
// in-app rendering so nobody has to leave the app to read it.
const PRIVACY_SECTIONS: { heading: string; body: string[] }[] = [
  {
    heading: 'What torios stores, and where',
    body: [
      'Everything torios keeps about you lives in two places on your own disk:',
      '\u2022 ~/.youros/ \u2014 tasks, notes, chat history, agent transcripts, preferences, and any files you drop into torios.',
      '\u2022 The torios application folder \u2014 the program itself, never your personal content.',
      'Nothing in those folders is sent to torios. If you delete ~/.youros/, your torios data is gone.',
    ],
  },
  {
    heading: 'What leaves your computer',
    body: [
      'torios talks to the internet only in the ways you choose to turn on. Each one is optional, and each one goes directly from your machine to the third party. torios is not in the middle.',
      'Anthropic (Claude) \u2014 when you chat or run an agent that uses Claude, the prompt and response go to Anthropic\u2019s API using the key you configured.',
      'Google (Gmail, Calendar, Drive) \u2014 only if you connect these in Settings. Requests go straight from your machine to Google.',
      'GitHub, Slack, and other integrations \u2014 same pattern. Only active if you connect them. torios does not receive a copy.',
      'Update checks \u2014 torios checks GitHub for new releases. Only release metadata is fetched. Nothing about you is sent.',
      'There is no telemetry. torios does not phone home. We do not collect crash reports or usage analytics.',
    ],
  },
  {
    heading: 'Your data, your call',
    body: [
      'Export \u2014 everything is already a plain file under ~/.youros/. Copy that folder and you have your data.',
      'Delete \u2014 remove ~/.youros/ and disconnect any integrations in their respective account settings. Optionally revoke the keys you issued to torios.',
      'Move to another computer \u2014 copy ~/.youros/ across. torios will pick it up on launch.',
    ],
  },
];

export default function Privacy() {
  return (
    <>
      <TopBar />
      <div className="page">
        <PageHeader title="Privacy" />
        <div style={{ maxWidth: 720, lineHeight: 1.6 }}>
          <p>
            torios runs on your computer. It is not a cloud service. There is
            no torios-operated backend that receives your data.
          </p>
          {PRIVACY_SECTIONS.map((section) => (
            <section key={section.heading} style={{ marginTop: 24 }}>
              <h2 style={{ fontSize: 18, marginBottom: 8 }}>{section.heading}</h2>
              {section.body.map((line, i) => (
                <p key={i} style={{ marginBottom: 8 }}>{line}</p>
              ))}
            </section>
          ))}
          <p style={{ marginTop: 32, opacity: 0.7, fontSize: 13 }}>
            <Link to="/settings">Back to Settings</Link>
          </p>
        </div>
      </div>
    </>
  );
}
