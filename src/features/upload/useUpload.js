import { useState } from 'react';
import { uploadDataset } from '../../services/api';
import { previewCsv } from './filePreview';
import { validateFile } from './validation';

// All upload logic lives here, so the UI component stays simple.
export function useUpload(onResult) {
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState(null);
  const [status, setStatus] = useState('idle'); // idle | ready | uploading | error | done
  const [error, setError] = useState('');

  function reset() {
    setFile(null);
    setPreview(null);
    setStatus('idle');
    setError('');
  }

  async function selectFile(candidate) {
    const problem = validateFile(candidate);
    if (problem) {
      setFile(null);
      setPreview(null);
      setStatus('error');
      setError(problem);
      return;
    }
    try {
      setPreview(await previewCsv(candidate));
      setFile(candidate);
      setStatus('ready');
      setError('');
    } catch (e) {
      setFile(null);
      setPreview(null);
      setStatus('error');
      setError(e.message);
    }
  }

  async function analyse() {
    setStatus('uploading');
    setError('');
    try {
      onResult?.(await uploadDataset(file));
      setStatus('done');
    } catch (e) {
      setStatus('error');
      setError(e.message);
    }
  }

  return { file, preview, status, error, selectFile, analyse, reset };
}
