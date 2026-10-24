// The only file that talks to the backend. If the API changes, edit here.
const BASE_URL = import.meta.env.VITE_API_URL ?? '';

export async function uploadDataset(file) {
  const body = new FormData();
  body.append('file', file);

  let response;
  try {
    response = await fetch(`${BASE_URL}/api/upload`, { method: 'POST', body });
  } catch {
    throw new Error('Could not reach the analysis server. Start it with: npm run backend');
  }

  if (!response.ok) {
    let detail = `Upload failed (status ${response.status}).`;
    try {
      const payload = await response.json();
      if (payload.detail) detail = payload.detail;
    } catch {
      // Keep the HTTP status as the useful error when the server has no JSON response.
    }
    throw new Error(detail);
  }

  return response.json();
}

export async function validateDataset(file, rules, csvIssues = []) {
  const body = new FormData();
  body.append('file', file);
  body.append('rules', JSON.stringify(rules));
  body.append('csv_issues', JSON.stringify(csvIssues));

  let response;
  try {
    response = await fetch(`${BASE_URL}/api/validate`, { method: 'POST', body });
  } catch {
    throw new Error('Could not reach the validation server. Start it with: npm run backend');
  }

  if (!response.ok) {
    let detail = `Validation failed (status ${response.status}).`;
    try {
      const payload = await response.json();
      if (payload.detail) detail = payload.detail;
    } catch {
      // Keep the HTTP status as the useful error when the server has no JSON response.
    }
    throw new Error(detail);
  }

  return response.json();
}

async function requestValidationExport(endpoint, file, rules, selectedGroups, csvIssues) {
  const body = new FormData();
  body.append('file', file);
  body.append('rules', JSON.stringify(rules));
  body.append('selected_groups', JSON.stringify(selectedGroups));
  body.append('csv_issues', JSON.stringify(csvIssues));

  let response;
  try {
    response = await fetch(`${BASE_URL}/api/${endpoint}`, { method: 'POST', body });
  } catch {
    throw new Error('Could not reach the validation server. Start it with: npm run backend');
  }

  if (!response.ok) {
    let detail = `Could not prepare the download (status ${response.status}).`;
    try {
      const payload = await response.json();
      if (payload.detail) detail = payload.detail;
    } catch {
      // Keep the HTTP status as the useful error when the server has no JSON response.
    }
    throw new Error(detail);
  }

  return response.blob();
}

export async function previewCleanedDataset(file, rules, selectedGroups, csvIssues) {
  const body = new FormData();
  body.append('file', file);
  body.append('rules', JSON.stringify(rules));
  body.append('selected_groups', JSON.stringify(selectedGroups));
  body.append('csv_issues', JSON.stringify(csvIssues));

  let response;
  try {
    response = await fetch(`${BASE_URL}/api/clean-preview`, { method: 'POST', body });
  } catch {
    throw new Error('Could not reach the validation server. Start it with: npm run backend');
  }

  if (!response.ok) {
    let detail = `Could not calculate the removal impact (status ${response.status}).`;
    try {
      const payload = await response.json();
      if (payload.detail) detail = payload.detail;
    } catch {
      // Keep the HTTP status as the useful error when the server has no JSON response.
    }
    throw new Error(detail);
  }

  return response.json();
}

export function downloadCleanedDataset(file, rules, selectedGroups, csvIssues) {
  return requestValidationExport('cleaned-data', file, rules, selectedGroups, csvIssues);
}

export function downloadValidationReport(file, rules, selectedGroups, csvIssues) {
  return requestValidationExport('validation-report', file, rules, selectedGroups, csvIssues);
}
