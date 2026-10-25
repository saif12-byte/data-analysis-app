import { useState } from 'react';
import Dashboard from './features/dashboard/Dashboard';
import DataValidation from './features/validation/DataValidation';
import UploadBox from './features/upload/UploadBox';

const startingSuggestions = [
  'How do I get started?',
  'What can I learn from my data?',
];

function getGuidance(question, hasAnalysis) {
  const prompt = question.toLowerCase();

  if (prompt.includes('chart') || prompt.includes('visual') || prompt.includes('trend')) {
    return 'Your dashboard includes a monthly revenue trend and a category performance chart. Upload and analyse a CSV to see those charts update with your data.';
  }
  if (prompt.includes('upload') || prompt.includes('file') || prompt.includes('start')) {
    return hasAnalysis
      ? 'Your data is already analysed. Explore the overview below, or upload another CSV from Tools to compare a different dataset.'
      : 'Start by uploading a CSV in Tools. You can preview the rows first, then choose Analyse data to build your dashboard.';
  }
  if (prompt.includes('column') || prompt.includes('format') || prompt.includes('csv')) {
    return 'Choose a CSV with a header row and columns containing the values you want to explore. The upload preview will help you check the data before analysis.';
  }
  if (prompt.includes('learn') || prompt.includes('insight') || prompt.includes('what')) {
    return hasAnalysis
      ? 'Check the overview cards for key metrics, then compare the revenue trend over time and the category performance bars for differences between groups.'
      : 'The dashboard starts with sample data. Upload your own CSV to see key metrics, a revenue trend, and category comparisons based on your data.';
  }
  return 'I can help you get started, prepare a CSV, or understand the dashboard charts. What would you like to explore?';
}

function DataGuide({ hasAnalysis }) {
  const [messages, setMessages] = useState([
    { from: 'guide', text: 'Hi! I can help you decide what to do next with your data.' },
  ]);
  const [question, setQuestion] = useState('');

  function sendQuestion(value) {
    const text = value.trim();
    if (!text) return;
    setMessages((current) => [
      ...current,
      { from: 'you', text },
      { from: 'guide', text: getGuidance(text, hasAnalysis) },
    ]);
    setQuestion('');
  }

  return (
    <aside className="guide" aria-labelledby="guide-title">
      <div className="guide-heading">
        <span className="guide-icon" aria-hidden="true">✦</span>
        <div>
          <h2 id="guide-title">Your data guide</h2>
          <p>Here to help you find a next step</p>
        </div>
      </div>
      <div className="guide-messages" aria-live="polite">
        {messages.map((message, index) => (
          <p className={`guide-message ${message.from === 'you' ? 'is-user' : ''}`} key={`${index}-${message.from}`}>
            {message.text}
          </p>
        ))}
      </div>
      <div className="guide-suggestions">
        {startingSuggestions.map((suggestion) => (
          <button key={suggestion} type="button" onClick={() => sendQuestion(suggestion)}>
            {suggestion}
          </button>
        ))}
        <a href="#tools">{hasAnalysis ? 'Explore your dashboard' : 'Upload a CSV'} <span aria-hidden="true">→</span></a>
      </div>
      <form
        className="guide-form"
        onSubmit={(event) => {
          event.preventDefault();
          sendQuestion(question);
        }}
      >
        <label className="sr-only" htmlFor="guide-question">Ask the data guide</label>
        <input
          id="guide-question"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="Ask a question..."
        />
        <button type="submit" aria-label="Send question" disabled={!question.trim()}>↑</button>
      </form>
    </aside>
  );
}

export default function App() {
  const [analysis, setAnalysis] = useState(null);
  const [accountNotice, setAccountNotice] = useState('');
  const [activeTool, setActiveTool] = useState('upload');

  return (
    <div className="app-shell">
      <header className="site-header">
        <a className="brand" href="#" aria-label="Data Story home">
          <span className="brand-mark" aria-hidden="true">d.</span>
          <span>data<span className="brand-light">story</span></span>
        </a>
        <nav className="site-nav" aria-label="Main navigation">
          <a href="#dashboard">Overview</a>
          <a href="#tools">Tools</a>
          <button type="button" onClick={() => setAccountNotice('Account access is not connected yet. You can explore all the tools without an account.')}>Sign in</button>
          <button className="nav-login" type="button" onClick={() => setAccountNotice('Account access is not connected yet. You can explore all the tools without an account.')}>Log in</button>
        </nav>
      </header>
      {accountNotice && (
        <div className="account-notice" role="status">
          <span>{accountNotice}</span>
          <button type="button" aria-label="Dismiss notice" onClick={() => setAccountNotice('')}>×</button>
        </div>
      )}
      <div className="content-layout">
        <main className="page">
          <section className="intro">
            <p className="eyebrow">YOUR DATA, MADE CLEAR</p>
            <h1 className="page-title">See the story in the numbers.</h1>
            <p className="page-sub">
              Upload a CSV file to analyse it. Until then, the dashboard shows fictional sample data.
            </p>
          </section>
          <section id="tools" aria-label="Data analysis tools">
            <div className="tool-tabs" role="tablist" aria-label="Data tools">
              <button
                id="upload-tab"
                className={`tool-tab ${activeTool === 'upload' ? 'is-active' : ''}`}
                type="button"
                role="tab"
                aria-selected={activeTool === 'upload'}
                aria-controls="upload-panel"
                onClick={() => setActiveTool('upload')}
              >
                Upload &amp; analyse
              </button>
              <button
                id="validation-tab"
                className={`tool-tab ${activeTool === 'validation' ? 'is-active' : ''}`}
                type="button"
                role="tab"
                aria-selected={activeTool === 'validation'}
                aria-controls="validation-panel"
                onClick={() => setActiveTool('validation')}
              >
                Data validation
              </button>
            </div>
            <div id="upload-panel" role="tabpanel" aria-labelledby="upload-tab" hidden={activeTool !== 'upload'}>
              <UploadBox onResult={setAnalysis} />
            </div>
            <div id="validation-panel" role="tabpanel" aria-labelledby="validation-tab" hidden={activeTool !== 'validation'}>
              <DataValidation />
            </div>
          </section>
          <section id="dashboard" aria-label="Data overview">
            <Dashboard data={analysis ?? undefined} isSample={!analysis} />
          </section>
        </main>
        <DataGuide hasAnalysis={Boolean(analysis)} />
      </div>
      <footer className="site-footer">
        <a className="footer-brand" href="#">data<span>story</span></a>
        <p>Make better sense of your data.</p>
        <span className="footer-note">Files are processed in memory and not saved</span>
        <a className="back-to-top" href="#">Back to top ↑</a>
      </footer>
    </div>
  );
}
