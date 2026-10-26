/**
 * Browser download helpers.
 *
 * The API answers some endpoints with binary bodies (receipt PDFs, DICOM
 * previews, analytics exports) rather than JSON, so pages fetch the bytes and
 * hand them to the browser via an object URL.
 */

/** Trigger a download for a Blob and revoke the object URL afterwards. */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

/** True when a value resolved to a binary body we can save. */
export function isBlob(value: unknown): value is Blob {
  return typeof Blob !== "undefined" && value instanceof Blob;
}
