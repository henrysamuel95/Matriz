/** @odoo-module **/

import { registry } from "@web/core/registry";
import { browser } from "@web/core/browser/browser";
import { session } from "@web/session";

const ACTIVITY_EVENTS = ["click", "keydown", "mousedown", "mousemove", "scroll", "touchstart"];
const CHECK_INTERVAL_MS = 60000;
const WRITE_THROTTLE_MS = 15000;

function storageKey() {
    return `matriz_session_last_activity_${session.uid || "public"}`;
}

function readStoredActivity(key, fallback) {
    try {
        const value = Number(window.localStorage.getItem(key));
        return Number.isFinite(value) && value > 0 ? value : fallback;
    } catch {
        return fallback;
    }
}

function writeStoredActivity(key, value) {
    try {
        window.localStorage.setItem(key, String(value));
    } catch {
        // Si el navegador bloquea localStorage, el timeout sigue funcionando en esta pestana.
    }
}

registry.category("services").add("matrizSessionTimeout", {
    start() {
        const timeoutSeconds = Number(session.matriz_session_timeout_seconds || 0);

        // Solo corre dentro del backend con usuario autenticado. No toca permisos,
        // roles ni metodos write; unicamente redirige a logout tras inactividad real.
        if (!session.uid || !Number.isFinite(timeoutSeconds) || timeoutSeconds <= 0) {
            return;
        }

        const timeoutMs = timeoutSeconds * 1000;
        const key = storageKey();
        let lastActivityAt = readStoredActivity(key, Date.now());
        let lastWriteAt = 0;
        let loggedOut = false;

        const markActivity = () => {
            const now = Date.now();
            lastActivityAt = now;

            // Evita escribir demasiado por eventos frecuentes como mousemove.
            if (now - lastWriteAt >= WRITE_THROTTLE_MS) {
                lastWriteAt = now;
                writeStoredActivity(key, now);
            }
        };

        for (const eventName of ACTIVITY_EVENTS) {
            window.addEventListener(eventName, markActivity, { passive: true });
        }

        // Sincroniza actividad entre pestanas del mismo usuario.
        window.addEventListener("storage", (event) => {
            if (event.key === key) {
                lastActivityAt = Math.max(lastActivityAt, readStoredActivity(key, lastActivityAt));
            }
        });

        markActivity();

        window.setInterval(() => {
            if (loggedOut) {
                return;
            }

            lastActivityAt = Math.max(lastActivityAt, readStoredActivity(key, lastActivityAt));
            if (Date.now() - lastActivityAt >= timeoutMs) {
                loggedOut = true;
                browser.location.href = "/web/session/logout?redirect=/web/login";
            }
        }, CHECK_INTERVAL_MS);
    },
});
