/**
 * Multipart upload with real progress.
 *
 * `fetch` cannot report upload progress, so imaging uploads use
 * `XMLHttpRequest`. The request carries the same auth as the generated client:
 * `credentials: include`, the `X-Clinic-Id` scope header and the double-submit
 * CSRF header Django requires for unsafe methods.
 */
import { getActiveClinicId } from "@/lib/api";

export interface UploadProgress {
  loaded: number;
  total: number;
  /** 0..100, or `null` when the browser cannot compute the total. */
  percent: number | null;
}

function readCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  for (const part of document.cookie.split(";")) {
    const [key, ...rest] = part.trim().split("=");
    if (key === name) {
      const value = rest.join("=");
      return value === "" ? null : value;
    }
  }
  return null;
}

function errorMessage(status: number, body: string): string {
  try {
    const parsed = JSON.parse(body) as { error?: { message?: string } };
    if (parsed.error?.message) return parsed.error.message;
  } catch {
    // Not JSON: fall through to the status line.
  }
  return `Upload failed (HTTP ${status}).`;
}

/** Upload one or more DICOM files for a patient; resolves with the created studies. */
export function uploadImagingStudies(
  patientId: string,
  files: File[],
  source: string,
  onProgress: (progress: UploadProgress) => void,
): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append("patient", patientId);
    if (source.trim() !== "") form.append("source", source.trim());
    for (const file of files) form.append("files", file, file.name);

    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/v1/imaging/studies/");
    xhr.withCredentials = true;

    const clinicId = getActiveClinicId();
    if (clinicId) xhr.setRequestHeader("X-Clinic-Id", clinicId);
    const csrf = readCookie("csrftoken");
    if (csrf) xhr.setRequestHeader("X-CSRFToken", csrf);

    xhr.upload.onprogress = (event) => {
      onProgress({
        loaded: event.loaded,
        total: event.total,
        percent: event.lengthComputable && event.total > 0 ? Math.round((event.loaded / event.total) * 100) : null,
      });
    };

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(xhr.responseText ? JSON.parse(xhr.responseText) : null);
        } catch {
          resolve(null);
        }
        return;
      }
      reject(new Error(errorMessage(xhr.status, xhr.responseText)));
    };
    xhr.onerror = () => reject(new Error("Network error during upload."));
    xhr.onabort = () => reject(new Error("Upload cancelled."));

    xhr.send(form);
  });
}
