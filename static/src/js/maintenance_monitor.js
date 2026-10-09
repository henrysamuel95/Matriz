/** @odoo-module **/
import { registry } from "@web/core/registry";
import { browser } from "@web/core/browser/browser";
import { session } from "@web/session";

export const maintenanceService = {
    dependencies: ["bus_service", "notification"],

    start(env, { bus_service, notification }) {
        console.log("🛡️ SERVICIO DE MANTENIMIENTO INICIADO");

        // 1. CHEQUEO INICIAL (CONTRA F5) - Lee la sesión inyectada por Python
        // Esto es inmediato y no hace peticiones al servidor, por lo que NUNCA falla.
        if (session.maintenance_mode) {
            console.log("🚨 ALERTA: MODO MANTENIMIENTO DETECTADO AL CARGAR");
            blockScreen("MANTENIMIENTO EN CURSO", session.maintenance_message || "Actualización del sistema");
        }

        // 2. ESCUCHA EN VIVO (Para cuando oprimes el botón de activar/desactivar)
        bus_service.addChannel("maintenance_channel");
        bus_service.addEventListener("notification", ({ detail: notifications }) => {
            for (const { payload, type } of notifications) {
                if (type === "maintenance_signal") {
                    if (payload.type === 'maintenance_on') {
                        blockScreen(payload.title, payload.message);
                    } else if (payload.type === 'maintenance_off') {
                        browser.location.reload();
                    }
                }
            }
        });
    }
};

registry.category("services").add("maintenanceService", maintenanceService);

// FUNCIÓN DE BLOQUEO 
function blockScreen(title, message) {
    // EL ADMIN (ID 1 o 2) SIEMPRE TIENE PASE VIP (A ti nunca se te bloqueará)
    if (session.uid === 1 || session.uid === 2) {
        console.log("👮 Eres Admin, se omite el bloqueo de pantalla.");
        return;
    }

    // Si ya está la pantalla azul, no la duplicamos
    if (document.getElementById('maintenance-overlay')) return;

    const overlay = document.createElement('div');
    overlay.id = 'maintenance-overlay';
    overlay.style.cssText = `
        position: fixed; top: 0; left: 0; width: 100%; height: 100%;
        background: rgba(0,0,0,0.95); z-index: 9999999;
        display: flex; flex-direction: column; justify-content: center; align-items: center;
        color: white; font-family: sans-serif; text-align: center; user-select: none;
    `;

    overlay.innerHTML = `
        <div style="font-size: 5em;">🚧</div>
        <h1 style="font-size: 3em; color: #ff4444; margin: 20px 0;">${title}</h1>
        <p style="font-size: 1.5em; max-width: 600px;">${message}</p>
        <div style="margin-top: 50px;">
            <div class="spinner-border text-light" role="status" style="width: 3rem; height: 3rem;"></div>
        </div>
        <p style="margin-top: 20px; color: #aaa; font-size: 0.9em;">No recargue la página, el sistema volverá automáticamente.</p>
    `;

    document.body.appendChild(overlay);
    document.body.style.overflow = 'hidden'; 
}