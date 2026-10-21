import Papa from 'papaparse';
import { PREVIEW_ROWS } from './config';

export function previewCsv(file) {
  return new Promise((resolve, reject) => {
    Papa.parse(file, {
      header: true,
      skipEmptyLines: true,
      preview: PREVIEW_ROWS,
      complete: ({ data, meta }) => {
        if (!meta.fields?.length) return reject(new Error('No columns found in this file.'));
        resolve({ columns: meta.fields, rows: data });
      },
      error: () => reject(new Error('Could not read this file.')),
    });
  });
}
