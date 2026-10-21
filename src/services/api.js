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
