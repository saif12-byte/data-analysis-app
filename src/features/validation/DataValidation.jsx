import { useRef, useState } from 'react';
import Card from '../../components/Card';
import { ALLOWED_EXTENSIONS, MAX_SIZE_MB } from '../upload/config';
import {
  downloadCleanedDataset,
  downloadValidationReport,
  previewCleanedDataset,
  validateDataset,
} from '../../services/api';
import {
  MAX_VALIDATION_ROWS,
  readCsvColumns,
  validateCsvFile,
} from './validation';

const RESULTS_PER_PAGE = 10;
const accept = ALLOWED_EXTENSIONS.map((extension) => `.${extension}`).join(',');
const conditions = [
  ['equals', 'Equals'],
  ['not_equals', 'Does not equal'],
  ['contains', 'Contains'],
  ['not_contains', 'Does not contain'],
  ['greater_than', 'Greater than'],
  ['less_than', 'Less than'],
  ['is_empty', 'Is empty'],
  ['is_not_empty', 'Is not empty'],
];
const valueNotRequired = new Set(['is_empty', 'is_not_empty']);

function conditionLabel(condition) {
  return conditions.find(([value]) => value === condition)?.[1] ?? condition;
}

function Metric({ label, value }) {
  return (
    <div className="validation-metric">
      <span>{label}</span>
      <strong>{value.toLocaleString()}</strong>
    </div>
  );
}

function getVisiblePages(currentPage, pageCount) {
  if (pageCount <= 7) {
    return Array.from({ length: pageCount }, (_, index) => index + 1);
  }
  if (currentPage <= 4) return [1, 2, 3, 4, 5, '...', pageCount];
  if (currentPage >= pageCount - 3) {
    return [1, '...', pageCount - 4, pageCount - 3, pageCount - 2, pageCount - 1, pageCount];
  }
  return [1, '...', currentPage - 1, currentPage, currentPage + 1, '...', pageCount];
}

function PaginatedTable({ headers, rows, label }) {
  const [page, setPage] = useState(1);
  const pageCount = Math.max(1, Math.ceil(rows.length / RESULTS_PER_PAGE));
  const currentPage = Math.min(page, pageCount);
  const start = (currentPage - 1) * RESULTS_PER_PAGE;
  const visibleRows = rows.slice(start, start + RESULTS_PER_PAGE);

  return (
    <>
      <div className="table-wrap">
        <table className="validation-table">
          <thead>
            <tr>{headers.map((header) => <th key={header}>{header}</th>)}</tr>
          </thead>
          <tbody>
            {visibleRows.map((row) => (
              <tr key={row.key}>
                {row.cells.map((cell, index) => <td key={`${row.key}-${index}`}>{cell}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {pageCount > 1 && (
        <nav className="validation-pagination" aria-label={label}>
          <span className="validation-page-summary">
            Showing {(start + 1).toLocaleString()}–{Math.min(start + RESULTS_PER_PAGE, rows.length).toLocaleString()} of {rows.length.toLocaleString()}
          </span>
          <div className="validation-page-buttons">
            <button
              className="btn btn-ghost"
              type="button"
              onClick={() => setPage(currentPage - 1)}
              disabled={currentPage === 1}
              aria-label="Previous page"
            >
              Previous
            </button>
            {getVisiblePages(currentPage, pageCount).map((pageNumber, index) => (
              pageNumber === '...'
                ? <span className="validation-page-ellipsis" key={`ellipsis-${index}`} aria-hidden="true">…</span>
                : (
                  <button
                    className={`btn btn-ghost validation-page-number ${pageNumber === currentPage ? 'is-current' : ''}`}
                    type="button"
                    key={pageNumber}
                    onClick={() => setPage(pageNumber)}
                    aria-label={`Page ${pageNumber}`}
                    aria-current={pageNumber === currentPage ? 'page' : undefined}
                  >
                    {pageNumber}
                  </button>
                )
            ))}
            <button
              className="btn btn-ghost"
              type="button"
              onClick={() => setPage(currentPage + 1)}
              disabled={currentPage === pageCount}
              aria-label="Next page"
            >
              Next
            </button>
          </div>
        </nav>
      )}
    </>
  );
}

export default function DataValidation() {
  const [file, setFile] = useState(null);
  const [columns, setColumns] = useState([]);
  const [rules, setRules] = useState([]);
  const [report, setReport] = useState(null);
  const [selectedGroups, setSelectedGroups] = useState([]);
  const [cleanImpact, setCleanImpact] = useState(null);
  const [previewingImpact, setPreviewingImpact] = useState(false);
  const [progress, setProgress] = useState(0);
  const [status, setStatus] = useState('idle');
  const [error, setError] = useState('');
  const [downloadError, setDownloadError] = useState('');
  const [downloading, setDownloading] = useState('');
  const nextRuleId = useRef(1);
  const reportSequence = useRef(0);
  const inputRef = useRef(null);
  const impactRequest = useRef(0);

  function updateRule(ruleId, updates) {
    setRules((current) => current.map((item) => (
      item.id === ruleId ? { ...item, ...updates } : item
    )));
    setReport(null);
    setSelectedGroups([]);
    setCleanImpact(null);
    setError('');
    setStatus('idle');
  }

  async function chooseFile(candidate) {
    impactRequest.current += 1;
    if (candidate && candidate.size > MAX_SIZE_MB * 1024 * 1024) {
      setFile(null);
      setColumns([]);
      setRules([]);
      setReport(null);
      setSelectedGroups([]);
      setCleanImpact(null);
      setProgress(0);
      setError(`This file is larger than ${MAX_SIZE_MB} MB.`);
      setStatus('error');
      return;
    }
    setFile(candidate ?? null);
    setColumns([]);
    setRules([]);
    setReport(null);
    setSelectedGroups([]);
    setCleanImpact(null);
    setProgress(0);
    setError('');
    setDownloadError('');
    setStatus(candidate ? 'loading' : 'idle');
    if (!candidate) return;
    try {
      const detectedColumns = await readCsvColumns(candidate);
      setColumns(detectedColumns);
      setRules([{
        id: `rule-${nextRuleId.current++}`,
        column: detectedColumns[0],
        condition: 'equals',
        value: '',
        severity: 'error',
      }]);
      setStatus('idle');
    } catch (readError) {
      setError(readError.message);
      setStatus('error');
    }
  }

  async function runValidation() {
    if (!file || !rules.length) return;
    setStatus('validating');
    setReport(null);
    setProgress(0);
    setError('');
    try {
      const automatic = await validateCsvFile(file, setProgress);
      const custom = await validateDataset(file, rules, automatic.csvIssues);
      setReport({ id: ++reportSequence.current, automatic, custom });
      setSelectedGroups([]);
      setCleanImpact({
        rows: custom.rows,
        removedRows: 0,
        remainingRows: custom.rows,
      });
      setProgress(automatic.rows);
      setStatus('done');
    } catch (validationError) {
      setError(validationError.message);
      setStatus('error');
    }
  }

  async function changeGroupSelection(groupId, isSelected) {
    const requestId = ++impactRequest.current;
    const nextGroups = isSelected
      ? [...selectedGroups, groupId]
      : selectedGroups.filter((id) => id !== groupId);
    setSelectedGroups(nextGroups);
    setCleanImpact(null);
    setDownloadError('');
    if (!file || !report || !nextGroups.length) {
      setPreviewingImpact(false);
      setCleanImpact(report ? {
        rows: report.custom.rows,
        removedRows: 0,
        remainingRows: report.custom.rows,
      } : null);
      return;
    }

    setPreviewingImpact(true);
    try {
      const impact = await previewCleanedDataset(file, rules, nextGroups, report.automatic.csvIssues);
      if (requestId === impactRequest.current) setCleanImpact(impact);
    } catch (previewError) {
      if (requestId === impactRequest.current) setDownloadError(previewError.message);
    } finally {
      if (requestId === impactRequest.current) setPreviewingImpact(false);
    }
  }

  async function downloadFile(download, suffix) {
    if (!report || !file || downloading) return;
    setDownloadError('');
    setDownloading(suffix);
    try {
      const blob = await download(file, rules, selectedGroups, report.automatic.csvIssues);
      saveDownload(blob, file, suffix);
    } catch (downloadError) {
      setDownloadError(downloadError.message);
    } finally {
      setDownloading('');
    }
  }

  function saveDownload(blob, sourceFile, suffix) {
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    const baseName = sourceFile.name.replace(/\.csv$/i, '').replace(/[<>:"/\\|?*\x00-\x1F]/g, '_');
    link.href = url;
    link.download = `${baseName || 'data'}-${suffix}.csv`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  }

  return (
    <Card
      className="validation-card"
      title="Data validation"
      subtitle={`Create rules for your data and check missing values, duplicates, and mixed types · up to ${MAX_VALIDATION_ROWS.toLocaleString()} rows`}
    >
      <p className="validation-private">Your file is read in this browser to find its columns. When you validate, the CSV and rules are sent to the API for processing and are not saved.</p>
      <label className="dropzone validation-dropzone">
        <input
          ref={inputRef}
          className="sr-only"
          type="file"
          accept={accept}
          onChange={(event) => {
            chooseFile(event.target.files[0]);
            event.target.value = '';
          }}
        />
        <strong>{file ? file.name : 'Choose a CSV file to validate'}</strong>
        {file && <span>{(file.size / 1024).toFixed(1)} KB</span>}
      </label>
      {error && <p className="msg-error" role="alert">{error}</p>}
      {status === 'loading' && <p className="validation-progress" role="status">Reading column names…</p>}
      {columns.length > 0 && (
        <section className="validation-rule-builder" aria-labelledby="validation-rules-heading">
          <div className="validation-rules-heading">
            <div>
              <h3 id="validation-rules-heading">Custom rules</h3>
              <p>Rules that fail are reported with their row and severity.</p>
            </div>
            <button
              className="btn btn-ghost"
              type="button"
              onClick={() => {
                setRules((current) => [
                  ...current,
                  {
                    id: `rule-${nextRuleId.current++}`,
                    column: columns[0],
                    condition: 'equals',
                    value: '',
                    severity: 'error',
                  },
                ]);
                setReport(null);
                setSelectedGroups([]);
                setCleanImpact(null);
                setError('');
                setStatus('idle');
              }}
              disabled={status === 'validating'}
            >
              Add rule
            </button>
          </div>
          {rules.map((rule, index) => (
            <div className="validation-rule" key={rule.id}>
              <label>
                <span>Column</span>
                <select
                  value={rule.column}
                  onChange={(event) => updateRule(rule.id, { column: event.target.value })}
                  disabled={status === 'validating'}
                >
                  {columns.map((column) => <option key={column} value={column}>{column}</option>)}
                </select>
              </label>
              <label>
                <span>Condition</span>
                <select
                  value={rule.condition}
                  onChange={(event) => updateRule(rule.id, { condition: event.target.value })}
                  disabled={status === 'validating'}
                >
                  {conditions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                </select>
              </label>
              {!valueNotRequired.has(rule.condition) && (
                <label>
                  <span>Value</span>
                  <input
                    value={rule.value}
                    onChange={(event) => updateRule(rule.id, { value: event.target.value })}
                    placeholder={['greater_than', 'less_than'].includes(rule.condition) ? 'Number' : 'Expected value'}
                    disabled={status === 'validating'}
                  />
                </label>
              )}
              <label>
                <span>Severity</span>
                <select
                  value={rule.severity}
                  onChange={(event) => updateRule(rule.id, { severity: event.target.value })}
                  disabled={status === 'validating'}
                >
                  <option value="error">Error</option>
                  <option value="warning">Warning</option>
                  <option value="info">Info (matching rows)</option>
                </select>
              </label>
              <button
                className="btn btn-ghost validation-remove-rule"
                type="button"
                aria-label={`Remove rule ${index + 1}`}
                onClick={() => {
                  setRules((current) => current.filter((item) => item.id !== rule.id));
                  setReport(null);
                  setSelectedGroups([]);
                  setCleanImpact(null);
                  setError('');
                  setStatus('idle');
                }}
                disabled={status === 'validating'}
              >
                Remove
              </button>
            </div>
          ))}
        </section>
      )}
      {status === 'validating' && (
        <p className="validation-progress" role="status">
          Checking {progress.toLocaleString()} rows…
        </p>
      )}
      {file && (
        <div className="actions">
          <button className="btn" type="button" onClick={runValidation} disabled={status === 'validating' || status === 'loading' || !rules.length || rules.some((rule) => !valueNotRequired.has(rule.condition) && !rule.value.trim())}>
            {status === 'validating' ? 'Validating…' : 'Validate data'}
          </button>
          <button className="btn btn-ghost" type="button" onClick={() => chooseFile(null)} disabled={status === 'validating' || status === 'loading'}>
            Remove file
          </button>
        </div>
      )}
      {report && (
        <div className="validation-results" aria-live="polite">
          {downloadError && <p className="msg-error" role="alert">{downloadError}</p>}
          <section className="validation-removal" aria-labelledby="validation-removal-heading">
            <div className="validation-rules-heading">
              <div>
                <h3 id="validation-removal-heading">Choose rows to remove</h3>
                <p>No groups are selected by default. Info groups show rows that match their rule and can’t be removed here.</p>
              </div>
            </div>
            {['error', 'warning', 'info'].map((severity) => {
              const groups = report.custom.issueGroups.filter((group) => group.severity === severity);
              if (!groups.length) return null;
              return (
                <section className={`validation-issue-groups is-${severity}`} key={severity} aria-labelledby={`issue-groups-${severity}`}>
                  <h4 id={`issue-groups-${severity}`}>
                    {severity === 'error' ? 'Errors' : severity === 'warning' ? 'Warnings' : 'Info · matching data'}
                  </h4>
                  {severity === 'info' && groups.map((group) => (
                    <div className="validation-issue-group is-not-removable" key={group.id}>
                      <span className="validation-issue-group-content">
                        <strong>{group.label}</strong>
                        <span>{group.affectedRows.toLocaleString()} matching rows</span>
                        <span>Sample spreadsheet rows: {group.sampleRows.join(', ') || 'None'}</span>
                      </span>
                    </div>
                  ))}
                  {groups.filter((group) => group.canRemove).map((group) => (
                    <label className="validation-issue-group" key={group.id}>
                      <input
                        type="checkbox"
                        checked={selectedGroups.includes(group.id)}
                        onChange={(event) => changeGroupSelection(group.id, event.target.checked)}
                      />
                      <span className="validation-issue-group-content">
                        <strong>{group.label}</strong>
                        <span>{group.affectedRows.toLocaleString()} affected rows · {group.issueCount.toLocaleString()} issues</span>
                        <span>Sample spreadsheet rows: {group.sampleRows.join(', ') || 'None'}</span>
                      </span>
                    </label>
                  ))}
                  {groups.filter((group) => severity !== 'info' && !group.canRemove).map((group) => (
                    <div className="validation-issue-group is-not-removable" key={group.id}>
                      <span className="validation-issue-group-content">
                        <strong>{group.label}</strong>
                        <span>{group.issueCount.toLocaleString()} parsing issue(s) found{group.sampleRows.length ? ` · sample spreadsheet rows: ${group.sampleRows.join(', ')}` : ''}</span>
                        <span>Repair the CSV structure and upload it again; these rows cannot be safely removed automatically.</span>
                      </span>
                    </div>
                  ))}
                </section>
              );
            })}
            <p className="validation-removal-impact" role="status">
              {previewingImpact
                ? 'Calculating the effect of your choices…'
                : cleanImpact
                  ? `Removing these groups will drop ${cleanImpact.removedRows.toLocaleString()} of ${cleanImpact.rows.toLocaleString()} rows (${cleanImpact.remainingRows.toLocaleString()} will remain).`
                  : 'Select an issue group to preview how many rows will be removed.'}
            </p>
            <p className="validation-download-note">
              The original file is unchanged. Download corrected CSV creates a separate copy using your selected row removals.
            </p>
            <div className="validation-download-actions">
              <button
                className="btn"
                type="button"
                onClick={() => downloadFile(downloadCleanedDataset, 'corrected')}
                disabled={previewingImpact || !cleanImpact || Boolean(downloading)}
              >
                {downloading === 'corrected'
                  ? 'Preparing corrected CSV…'
                  : `Download corrected CSV${cleanImpact ? ` (${cleanImpact.remainingRows.toLocaleString()} rows)` : ''}`}
              </button>
              <button
                className="btn btn-ghost"
                type="button"
                onClick={() => downloadFile(downloadValidationReport, 'validation-report')}
                disabled={Boolean(downloading)}
              >
                {downloading === 'validation-report' ? 'Preparing issue report…' : 'Download issue report (CSV)'}
              </button>
              <button
                className="btn btn-ghost"
                type="button"
                onClick={() => inputRef.current?.click()}
                disabled={Boolean(downloading)}
              >
                Upload corrected CSV
              </button>
            </div>
          </section>
          <div className="validation-custom-results">
            <div className="validation-summary">
              <div>
                <h3>Custom rule results</h3>
                <p>{report.custom.rows.toLocaleString()} rows checked · {rules.length} rules</p>
              </div>
              <span className={`validation-status ${report.custom.summary.failedRows ? 'has-issues' : ''}`}>
                {report.custom.summary.errorRows
                  ? 'Review recommended'
                  : report.custom.summary.warningRows
                    ? 'Warnings found'
                    : report.custom.summary.infoRows
                      ? 'Matching data found'
                      : 'All rules passed'}
              </span>
            </div>
            <div className="validation-metrics">
              <Metric label="Passed rows" value={report.custom.summary.passedRows} />
              <Metric label="Rows with errors" value={report.custom.summary.errorRows} />
              <Metric label="Rows with warnings" value={report.custom.summary.warningRows} />
              <Metric label="Rows matching info rules" value={report.custom.summary.infoRows} />
            </div>
            <PaginatedTable
              key={`${report.id}-rules`}
              label="Custom rule results pages"
              headers={['Column', 'Condition', 'Severity', 'Matching / failing rows']}
              rows={report.custom.ruleResults.map((result) => ({
                key: result.id,
                cells: [
                  result.column,
                  `${conditionLabel(result.condition)}${!valueNotRequired.has(result.condition) ? ` ${result.value}` : ''}`,
                  result.severity,
                  (result.severity === 'info' ? result.matchingRows : result.failedRows).toLocaleString(),
                ],
              }))}
            />
            {report.custom.failedRows.length > 0 && (
              <>
                <h4 className="validation-failures-heading">Rows that need review</h4>
                <PaginatedTable
                  key={`${report.id}-failures`}
                  label="Failing rows pages"
                  headers={['Row', 'Column', 'Actual value', 'Issue', 'Severity']}
                  rows={report.custom.failedRows.flatMap((failedRow) => failedRow.reasons.map((reason) => ({
                    key: `${failedRow.rowNumber}-${reason.ruleId}`,
                    cells: [
                      failedRow.rowNumber,
                      reason.column,
                      reason.actual || '(empty)',
                      `${conditionLabel(reason.condition)}${!valueNotRequired.has(reason.condition) ? ` ${reason.expected}` : ''}`,
                      reason.severity,
                    ],
                  })))}
                />
                {report.custom.failedRowsTruncated && (
                  <p className="validation-progress">Showing the first 1,000 failing rows. Counts include all rows.</p>
                )}
              </>
            )}
          </div>
          <div className="validation-summary">
            <div>
              <h3>Automatic checks</h3>
              <p>{report.automatic.rows.toLocaleString()} rows · {report.automatic.columns.toLocaleString()} columns</p>
            </div>
            <span className={`validation-status ${report.automatic.missingCells || report.automatic.duplicateRows || report.automatic.malformedRows || report.automatic.columnResults.some((column) => column.mixedTypes) ? 'has-issues' : ''}`}>
              {report.automatic.missingCells || report.automatic.duplicateRows || report.automatic.malformedRows || report.automatic.columnResults.some((column) => column.mixedTypes) ? 'Review recommended' : 'Looks good'}
            </span>
          </div>
          <div className="validation-metrics">
            <Metric label="Missing cells" value={report.automatic.missingCells} />
            <Metric label="Duplicate rows" value={report.automatic.duplicateRows} />
            <Metric label="CSV row issues" value={report.automatic.malformedRows} />
          </div>
          <PaginatedTable
            key={`${report.id}-columns`}
            label="Automatic check columns pages"
            headers={['Column', 'Detected type', 'Missing', 'Mixed-type values']}
            rows={report.automatic.columnResults.map((column, index) => ({
              key: `${index}-${column.name}`,
              cells: [
                column.name || `Column ${index + 1}`,
                column.inferredType,
                column.missing.toLocaleString(),
                column.mixedTypes.toLocaleString(),
              ],
            }))}
          />
        </div>
      )}
    </Card>
  );
}
