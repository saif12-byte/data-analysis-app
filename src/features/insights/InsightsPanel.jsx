import { useEffect, useRef, useState } from 'react';
import Card from '../../components/Card';
import {
  askDatasetQuestion,
  generateDatasetAIInsights,
  getDatasetInsights,
} from '../../services/api';

const numberFormat = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 });

function formatNumber(value) {
  return value == null ? '—' : numberFormat.format(value);
}

function formatNumberOrText(value) {
  return typeof value === 'number' ? formatNumber(value) : String(value);
}

function Stat({ label, value }) {
  return (
    <div className="insight-stat">
      <span>{label}</span>
      <strong>{formatNumber(value)}</strong>
    </div>
  );
}

export default function InsightsPanel({ datasetId }) {
  const [result, setResult] = useState(null);
  const [loadError, setLoadError] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [aiOptIn, setAiOptIn] = useState(false);
  const [aiResult, setAiResult] = useState(null);
  const [aiError, setAiError] = useState('');
  const [isGenerating, setIsGenerating] = useState(false);
  const aiRequestId = useRef(0);
  const [question, setQuestion] = useState('');
  const [questionResult, setQuestionResult] = useState(null);
  const [questionError, setQuestionError] = useState('');
  const [isAsking, setIsAsking] = useState(false);
  const questionRequestId = useRef(0);

  useEffect(() => {
    let active = true;
    aiRequestId.current += 1;
    questionRequestId.current += 1;
    setResult(null);
    setLoadError('');
    setAiOptIn(false);
    setAiResult(null);
    setAiError('');
    setIsGenerating(false);
    setQuestion('');
    setQuestionResult(null);
    setQuestionError('');
    setIsAsking(false);

    if (!datasetId) {
      setIsLoading(false);
      return () => { active = false; };
    }

    setIsLoading(true);
    getDatasetInsights(datasetId)
      .then((response) => {
        if (active) setResult(response);
      })
      .catch((error) => {
        if (active) setLoadError(error.message);
      })
      .finally(() => {
        if (active) setIsLoading(false);
      });

    return () => { active = false; };
  }, [datasetId]);

  async function generateInsights() {
    if (!datasetId || !aiOptIn || !result?.ai_enabled) return;
    const requestId = aiRequestId.current + 1;
    aiRequestId.current = requestId;
    setIsGenerating(true);
    setAiError('');
    try {
      const response = await generateDatasetAIInsights(datasetId);
      if (aiRequestId.current === requestId) setAiResult(response);
    } catch (error) {
      if (aiRequestId.current === requestId) {
        setAiResult(null);
        setAiError(error.message);
      }
    } finally {
      if (aiRequestId.current === requestId) setIsGenerating(false);
    }
  }

  async function askQuestion(event) {
    event.preventDefault();
    if (!datasetId || !question.trim() || isAsking) return;
    const requestId = questionRequestId.current + 1;
    questionRequestId.current = requestId;
    setIsAsking(true);
    setQuestionError('');
    setQuestionResult(null);
    try {
      const response = await askDatasetQuestion(datasetId, question.trim());
      if (questionRequestId.current === requestId) setQuestionResult(response);
    } catch (error) {
      if (questionRequestId.current === requestId) setQuestionError(error.message);
    } finally {
      if (questionRequestId.current === requestId) setIsAsking(false);
    }
  }

  const pack = result?.pack;
  const overview = pack?.overview;
  const categories = Object.entries(pack?.top_categories ?? {}).slice(0, 4);
  const trend = pack?.monthly_trend ?? [];
  const largestTrendValue = Math.max(1, ...trend.map((point) => Math.abs(point.value ?? 0)));

  return (
    <Card
      className="insights-panel"
      title="Data insights"
      subtitle="Pandas computes these summaries from the full dataset."
    >
      {!datasetId && (
        <p className="analysis-privacy">Upload and analyse a CSV to see its statistics, charts, and findings here.</p>
      )}
      {isLoading && <p className="insights-state" role="status">Calculating dataset insights…</p>}
      {loadError && <p className="msg-error" role="alert">{loadError}</p>}

      {pack && (
        <>
          <div className="insights-stat-grid" aria-label="Dataset summary">
            <Stat label="Rows" value={overview.rows} />
            <Stat label="Columns" value={overview.columns} />
            <Stat label="Missing values" value={overview.missing_values} />
            <Stat label="Duplicate rows" value={overview.duplicate_rows} />
          </div>

          <div className="insights-chart-grid">
            {categories.length > 0 && (
              <section className="insights-chart" aria-label="Top categories">
                <h3>Top categories</h3>
                {categories.map(([column, values]) => (
                  <div className="insights-series" key={column}>
                    <h4>{column}</h4>
                    {values.slice(0, 4).map((item) => (
                      <div className="insights-bar-row" key={`${column}-${item.category}`}>
                        <span title={item.category}>{item.category}</span>
                        <div className="insights-bar-track">
                          <div
                            className="insights-bar-fill"
                            style={{ width: `${Math.min(100, item.share_percent)}%` }}
                          />
                        </div>
                        <strong>{formatNumber(item.share_percent)}%</strong>
                      </div>
                    ))}
                  </div>
                ))}
              </section>
            )}

            {trend.length > 0 && (
              <section className="insights-chart" aria-label="Monthly trend">
                <h3>Monthly trend</h3>
                <div className="insights-trend">
                  {trend.map((point) => (
                    <div className="insights-trend-row" key={point.month}>
                      <span>{point.month}</span>
                      <div className="insights-bar-track">
                        <div
                          className="insights-bar-fill is-trend"
                          style={{ width: `${Math.max(1, Math.abs(point.value ?? 0) / largestTrendValue * 100)}%` }}
                        />
                      </div>
                      <strong>{formatNumber(point.value)}</strong>
                      <small>
                        {point.change_percent == null ? '—' : `${formatNumber(point.change_percent)}%`}
                      </small>
                    </div>
                  ))}
                </div>
              </section>
            )}
          </div>

          {Object.keys(pack.numeric_statistics).length > 0 && (
            <section className="insights-numeric">
              <h3>Numeric summaries</h3>
              <div className="insights-numeric-grid">
                {Object.entries(pack.numeric_statistics).slice(0, 6).map(([column, stats]) => (
                  <div className="insights-numeric-card" key={column}>
                    <strong>{column}</strong>
                    <span>Mean {formatNumber(stats.mean)} · Median {formatNumber(stats.median)}</span>
                    <span>Range {formatNumber(stats.min)}–{formatNumber(stats.max)}</span>
                    <span>{formatNumber(pack.outlier_counts[column])} outliers</span>
                  </div>
                ))}
              </div>
            </section>
          )}

          {pack.rule_findings.length > 0 && (
            <section className="insights-findings">
              <h3>Rule-based findings</h3>
              <ul>
                {pack.rule_findings.map((item, index) => (
                  <li key={`${index}-${item.finding}`}>{item.finding}</li>
                ))}
              </ul>
            </section>
          )}

          {pack.correlations.length > 0 && (
            <section className="insights-findings">
              <h3>Strongest correlations</h3>
              <ul>
                {pack.correlations.map((item) => (
                  <li key={`${item.column_a}-${item.column_b}`}>
                    {item.column_a} and {item.column_b}: {formatNumber(item.correlation)}
                  </li>
                ))}
              </ul>
            </section>
          )}

          <section className="dataset-question">
            <h3>Ask a question about this CSV</h3>
            <p className="insights-state">
              Ask for a count, sum, or average and use the column names from your CSV header. Pandas computes the result on the server; CSV rows are not sent to Gemini.
            </p>
            <form onSubmit={askQuestion}>
              <label className="insights-focus">
                <span>Use the column names from your CSV header.</span>
                <textarea
                  value={question}
                  maxLength={500}
                  onChange={(event) => setQuestion(event.target.value)}
                  placeholder="How many indexes have the same first name, grouped by country?"
                />
              </label>
              <button
                className="btn"
                type="submit"
                disabled={!question.trim() || isAsking}
              >
                {isAsking ? 'Analyzing your data…' : 'Ask about this data'}
              </button>
            </form>
            {questionError && <p className="msg-error" role="alert">{questionError}</p>}
            {questionResult && (
              <div className="question-results" aria-live="polite">
                <p>
                  {questionResult.metric.aggregation === 'nunique'
                    ? `Distinct ${questionResult.metric.column} values`
                    : questionResult.metric.aggregation === 'count'
                      ? 'Matching row count'
                      : `${questionResult.metric.aggregation} of ${questionResult.metric.column}`}
                  {questionResult.group_by.length > 0
                    ? ` grouped by ${questionResult.group_by.join(' and ')}`
                    : ''}
                  : {numberFormat.format(questionResult.total_groups)} group(s).
                </p>
                <div className="question-table-wrap">
                  <table className="question-table">
                    <thead>
                      <tr>
                        {[...questionResult.group_by, Object.keys(questionResult.results[0] ?? {}).find((key) => !questionResult.group_by.includes(key))]
                          .filter(Boolean)
                          .map((column) => <th key={column}>{column}</th>)}
                      </tr>
                    </thead>
                    <tbody>
                      {questionResult.results.map((row, index) => (
                        <tr key={`${index}-${JSON.stringify(row)}`}>
                          {[...questionResult.group_by, Object.keys(questionResult.results[0] ?? {}).find((key) => !questionResult.group_by.includes(key))]
                            .filter(Boolean)
                            .map((column) => (
                              <td key={column}>{row[column] == null ? '—' : formatNumberOrText(row[column])}</td>
                            ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {questionResult.truncated && (
                  <p className="insights-state">
                    Showing the top {numberFormat.format(questionResult.results.length)} of {numberFormat.format(questionResult.total_groups)} groups.
                  </p>
                )}
              </div>
            )}
          </section>

          <section className="ai-insights">
            <h3>AI-generated insights</h3>
            {!result.ai_feature_enabled && (
              <p className="insights-state">AI insights are disabled by the server configuration. Rule-based analysis remains available.</p>
            )}
            {result.ai_feature_enabled && !result.ai_key_configured && (
              <p className="insights-state">Gemini API key is missing on the server. Rule-based analysis remains available.</p>
            )}
            <label className="ai-opt-in">
              <input
                type="checkbox"
                checked={aiOptIn}
                disabled={!result.ai_enabled}
                onChange={(event) => {
                  setAiOptIn(event.target.checked);
                  setAiResult(null);
                  setAiError('');
                }}
              />
              <span>Allow Gemini to summarize this aggregate insight pack</span>
            </label>
            <button
              className="btn"
              type="button"
              onClick={generateInsights}
              disabled={!result.ai_enabled || !aiOptIn || isGenerating}
            >
              {isGenerating ? 'Generating insights…' : 'Generate AI insights'}
            </button>
            {aiError && <p className="msg-error" role="alert">{aiError}</p>}
            {aiResult?.message && <p className="insights-state" role="status">{aiResult.message}</p>}
            {aiResult?.insufficient_data && (
              <p className="insights-state" role="status">{aiResult.insufficient_data}</p>
            )}
            {aiResult?.findings?.length > 0 && (
              <div className="insights-results" aria-live="polite">
                {aiResult.source !== 'rules' && (
                  <p className="ai-summary-label">AI-generated summary. All numbers come from your data.</p>
                )}
                <ul>
                  {aiResult.findings.map((item, index) => (
                    <li key={`${index}-${item.finding}`}>
                      <span>{item.finding}</span>
                      <small>Supporting numbers: {item.supporting_numbers.join(', ')}</small>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </section>
        </>
      )}
    </Card>
  );
}
