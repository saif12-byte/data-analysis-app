import { useEffect, useState } from 'react';
import Card from '../../components/Card';
import { ALLOWED_EXTENSIONS, MAX_SIZE_MB } from './config';
import PreviewTable from './PreviewTable';
import { useUpload } from './useUpload';

const accept = ALLOWED_EXTENSIONS.map((e) => `.${e}`).join(',');

export default function UploadBox({ onResult }) {
  const { file, preview, status, error, selectFile, analyse, reset } = useUpload(onResult);
  const [dragging, setDragging] = useState(false);
  const [options, setOptions] = useState({
    measure_column: 'auto',
    date_column: 'auto',
    group_column: 'auto',
    aggregation: 'sum',
    trend_chart: 'area',
    category_chart: 'bar',
  });
  const busy = status === 'uploading';

  useEffect(() => {
    if (!preview) return;
    setOptions((current) => ({
      ...current,
      measure_column: current.measure_column === 'auto' || current.measure_column === 'count' || preview.columns.includes(current.measure_column)
        ? current.measure_column
        : 'auto',
      date_column: current.date_column === 'auto' || current.date_column === 'none' || preview.columns.includes(current.date_column)
        ? current.date_column
        : 'auto',
      group_column: current.group_column === 'auto' || current.group_column === 'none' || preview.columns.includes(current.group_column)
        ? current.group_column
        : 'auto',
    }));
  }, [preview]);

  function updateOption(name, value) {
    setOptions((current) => ({ ...current, [name]: value }));
  }

  function handleDrop(e) {
    e.preventDefault();
    setDragging(false);
    selectFile(e.dataTransfer.files[0]);
  }

  return (
    <Card className="upload" title="Upload data" subtitle={`${accept} files, up to ${MAX_SIZE_MB} MB`}>
      <label
        className={`dropzone ${dragging ? 'is-dragging' : ''}`}
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={handleDrop}
      >
        <input
          className="sr-only"
          type="file"
          accept={accept}
          onChange={(e) => { selectFile(e.target.files[0]); e.target.value = ''; }}
        />
        <strong>{file ? file.name : 'Drop a file here or click to browse'}</strong>
        {file && <span>{(file.size / 1024).toFixed(1)} KB</span>}
      </label>

      {error && <p className="msg-error" role="alert">{error}</p>}
      {preview && <PreviewTable columns={preview.columns} rows={preview.rows} />}

      {preview && (
        <div className="analysis-options">
          <div className="analysis-options-heading">
            <h3>Configure your analysis</h3>
            <p>Choose what to measure and how to visualize it.</p>
          </div>
          <div className="analysis-options-grid">
            <label>
              <span>Measure</span>
              <select value={options.measure_column} onChange={(event) => updateOption('measure_column', event.target.value)}>
                <option value="auto">Auto-detect numeric column</option>
                <option value="count">Count rows</option>
                {preview.columns.map((column) => <option key={column} value={column}>{column}</option>)}
              </select>
            </label>
            <label>
              <span>Aggregation</span>
              <select value={options.aggregation} onChange={(event) => updateOption('aggregation', event.target.value)}>
                <option value="sum">Sum</option>
                <option value="mean">Average</option>
                <option value="median">Median</option>
                <option value="min">Minimum</option>
                <option value="max">Maximum</option>
                <option value="count">Count</option>
              </select>
            </label>
            <label>
              <span>Date / time column</span>
              <select value={options.date_column} onChange={(event) => updateOption('date_column', event.target.value)}>
                <option value="auto">Auto-detect</option>
                <option value="none">No date grouping</option>
                {preview.columns.map((column) => <option key={column} value={column}>{column}</option>)}
              </select>
            </label>
            <label>
              <span>Group by</span>
              <select value={options.group_column} onChange={(event) => updateOption('group_column', event.target.value)}>
                <option value="auto">Auto-detect category</option>
                <option value="none">No grouping</option>
                {preview.columns.map((column) => <option key={column} value={column}>{column}</option>)}
              </select>
            </label>
            <label>
              <span>Trend chart</span>
              <select value={options.trend_chart} onChange={(event) => updateOption('trend_chart', event.target.value)}>
                <option value="area">Area</option>
                <option value="line">Line</option>
                <option value="bar">Bar</option>
              </select>
            </label>
            <label>
              <span>Group chart</span>
              <select value={options.category_chart} onChange={(event) => updateOption('category_chart', event.target.value)}>
                <option value="bar">Bar</option>
                <option value="pie">Pie</option>
              </select>
            </label>
          </div>
          <p className="analysis-privacy">
            Insight questions use the full CSV on the backend. Gemini receives only the column names and the aggregated results needed to answer; individual rows are not sent.
          </p>
        </div>
      )}

      {file && (
        <div className="actions">
          <button className="btn" onClick={() => analyse(options)} disabled={busy}>
            {busy ? 'Analysing…' : 'Analyse data'}
          </button>
          <button className="btn btn-ghost" onClick={reset} disabled={busy}>Remove file</button>
        </div>
      )}
    </Card>
  );
}
