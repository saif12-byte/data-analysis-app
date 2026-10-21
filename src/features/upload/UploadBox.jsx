import { useState } from 'react';
import Card from '../../components/Card';
import { ALLOWED_EXTENSIONS, MAX_SIZE_MB } from './config';
import PreviewTable from './PreviewTable';
import { useUpload } from './useUpload';

const accept = ALLOWED_EXTENSIONS.map((e) => `.${e}`).join(',');

export default function UploadBox({ onResult }) {
  const { file, preview, status, error, selectFile, analyse, reset } = useUpload(onResult);
  const [dragging, setDragging] = useState(false);
  const busy = status === 'uploading';

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

      {file && (
        <div className="actions">
          <button className="btn" onClick={analyse} disabled={busy}>
            {busy ? 'Analysing…' : 'Analyse data'}
          </button>
          <button className="btn btn-ghost" onClick={reset} disabled={busy}>Remove file</button>
        </div>
      )}
    </Card>
  );
}
