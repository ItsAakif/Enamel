import { useEffect, useState } from "react";

/**
 * PWA helpers: service-worker registration, cache clearing and online status.
 *
 * The service worker (``public/sw.js``) caches the app shell and immutable
 * build assets only. It never caches ``/api`` or ``/media`` responses, so no
 * patient data is ever written to Cache Storage. See ``docs/PWA.md``.
 */

export const APP_CACHE_PREFIX = "enamel-shell";

/** Register the app-shell service worker. Best-effort: offline support is optional. */
export function registerServiceWorker(): void {
  if (typeof window === "undefined" || typeof navigator === "undefined") return;
  if (!("serviceWorker" in navigator)) return;
  const base = import.meta.env.BASE_URL || "/";
  const url = `${base.replace(/\/$/, "")}/sw.js`;
  navigator.serviceWorker.register(url).catch(() => {
    // A failed registration must never break the app.
  });
}

/**
 * Delete every app-shell cache and ask the active worker to do the same.
 *
 * Called on portal logout so a shared device cannot serve a previously cached
 * shell to the next patient.
 */
export async function clearAppCaches(): Promise<void> {
  if (typeof caches !== "undefined") {
    const keys = await caches.keys();
    await Promise.all(
      keys.filter((key) => key.startsWith(APP_CACHE_PREFIX)).map((key) => caches.delete(key)),
    );
  }
  const controller =
    typeof navigator !== "undefined" && "serviceWorker" in navigator
      ? navigator.serviceWorker.controller
      : null;
  if (controller) {
    controller.postMessage({ type: "ENAMEL_CLEAR_CACHES" });
  }
}

/** True while the browser reports a network connection. */
export function useOnlineStatus(): boolean {
  const [online, setOnline] = useState<boolean>(
    typeof navigator === "undefined" ? true : navigator.onLine,
  );

  useEffect(() => {
    function goOnline() {
      setOnline(true);
    }
    function goOffline() {
      setOnline(false);
    }
    window.addEventListener("online", goOnline);
    window.addEventListener("offline", goOffline);
    return () => {
      window.removeEventListener("online", goOnline);
      window.removeEventListener("offline", goOffline);
    };
  }, []);

  return online;
}
