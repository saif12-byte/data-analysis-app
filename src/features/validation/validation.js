import Papa from 'papaparse';

export const MAX_VALIDATION_ROWS = 150_000;

export function readCsvColumns(file) {
  return new Promise((resolve, reject) => {
    Papa.parse(file, {
      header: true,
      preview: 1,
      skipEmptyLines: 'greedy',
      complete(results) {
        const columns = results.meta.fields ?? [];
        if (!columns.length) {
          reject(new Error('No columns found. Add a header row and try again.'));
          return;
        }
        resolve(columns);
      },
      error(error) {
        reject(new Error(`Could not read this CSV: ${error.message}`));
      },
    });
  });
}

function getValueType(value) {
  const text = String(value ?? '').trim();
  if (!text) return null;
  if (/^(true|false)$/i.test(text)) return 'Boolean';
  if (/^\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:[T ].*)?$/.test(text)
    || /^\d{1,2}[-/]\d{1,2}[-/]\d{2,4}$/.test(text)) {
    return Number.isNaN(Date.parse(text)) ? 'Text' : 'Date';
  }
  const numeric = text.replace(/[,\s₹$€£%]/g, '');
  if (numeric && Number.isFinite(Number(numeric))) return 'Number';
  return 'Text';
}

export function validateCsvFile(file, onProgress = () => {}) {
  return new Promise((resolve, reject) => {
    let columns = null;
    let columnStats = [];
    let rowCount = 0;
    let duplicateRows = 0;
    let malformedRows = 0;
    const csvIssues = [];
    let exceededLimit = false;
    const seenRows = new Set();

    Papa.parse(file, {
      header: true,
      skipEmptyLines: 'greedy',
      worker: true,
      chunkSize: 1024 * 1024,
      chunk(results, parser) {
        if (!columns) {
          columns = results.meta.fields ?? [];
          if (!columns.length) {
            parser.abort();
            reject(new Error('No columns found. Add a header row and try again.'));
            return;
          }
          columnStats = columns.map(() => ({ missing: 0, types: new Map() }));
        }

        malformedRows += results.errors.length;
        for (const parseError of results.errors) {
          csvIssues.push({
            rowNumber: Number.isInteger(parseError.row) ? parseError.row + 2 : null,
            code: parseError.code ?? 'CSVParseError',
            description: parseError.message,
          });
        }
        for (const row of results.data) {
          rowCount += 1;
          if (rowCount > MAX_VALIDATION_ROWS) {
            exceededLimit = true;
            parser.abort();
            return;
          }

          const values = columns.map((column, index) => {
            const value = row[column] ?? '';
            const text = String(value).trim();
            if (!text) {
              columnStats[index].missing += 1;
            } else {
              const type = getValueType(text);
              const types = columnStats[index].types;
              types.set(type, (types.get(type) ?? 0) + 1);
            }
            return text;
          });

          const signature = JSON.stringify(values);
          if (seenRows.has(signature)) duplicateRows += 1;
          else seenRows.add(signature);
        }
        onProgress(rowCount);
      },
      complete() {
        if (exceededLimit) {
          reject(new Error(`This file has more than ${MAX_VALIDATION_ROWS.toLocaleString()} data rows. The validation limit is ${MAX_VALIDATION_ROWS.toLocaleString()} rows.`));
          return;
        }
        if (!columns?.length) {
          reject(new Error('No columns found. Add a header row and try again.'));
          return;
        }

        const columnResults = columns.map((name, index) => {
          const stats = columnStats[index];
          const typeEntries = [...stats.types.entries()].sort((a, b) => b[1] - a[1]);
          const expectedType = typeEntries[0]?.[0] ?? 'Empty';
          const populatedCount = typeEntries.reduce((total, [, count]) => total + count, 0);
          return {
            name,
            missing: stats.missing,
            inferredType: expectedType,
            mixedTypes: populatedCount - (typeEntries[0]?.[1] ?? 0),
          };
        });

        resolve({
          rows: rowCount,
          columns: columns.length,
          missingCells: columnResults.reduce((total, column) => total + column.missing, 0),
          duplicateRows,
          malformedRows,
          csvIssues,
          columnResults,
        });
      },
      error(error) {
        reject(new Error(`Could not read this CSV: ${error.message}`));
      },
    });
  });
}
