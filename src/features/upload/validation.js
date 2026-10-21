import { ALLOWED_EXTENSIONS, MAX_SIZE_MB } from './config';

// Returns an error message, or null if the file is fine.
export function validateFile(file) {
  if (!file) return 'Choose a file to upload.';
  const ext = file.name.split('.').pop().toLowerCase();
  if (!ALLOWED_EXTENSIONS.includes(ext)) {
    return `Unsupported file type. Upload a ${ALLOWED_EXTENSIONS.map((e) => `.${e}`).join(', ')} file.`;
  }
  if (file.size === 0) return 'This file is empty.';
  if (file.size > MAX_SIZE_MB * 1024 * 1024) return `This file is larger than ${MAX_SIZE_MB} MB.`;
  return null;
}
