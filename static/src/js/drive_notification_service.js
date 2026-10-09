/** @odoo-module **/

import { registry } from "@web/core/registry";

// SERVICIO GLOBAL: Escucha la señal de Python y muestra el cuadro bonito
export const driveNotificationService = {
    dependencies: ["bus_service"],
    
    start(env, { bus_service }) {
        console.log("✅✅✅ SERVICIO DE NOTIFICACIONES DRIVE INICIADO ✅✅✅");

        // --- CORRECCIÓN PARA ODOO 16/17 ---
        // No usamos .subscribe(), usamos .addEventListener("notification")
        bus_service.addEventListener("notification", ({ detail: notifications }) => {
            
            // Odoo nos envía un array de notificaciones, las recorremos
            for (const { type, payload } of notifications) {
                
                // Si la notificación es la nuestra (la que mandamos desde Python)
                if (type === "drive_custom_delete_alert") {
                    console.log("⚡ SEÑAL RECIBIDA DESDE PYTHON:", payload);
                    showPrettyToast(payload.title, payload.message);
                }
            }
        });
    }
};

// Registramos el servicio
registry.category("services").add("driveNotificationService", driveNotificationService);


// --- FUNCIÓN DE DISEÑO (EL CUADRO BONITO - IGUAL QUE ANTES) ---
function showPrettyToast(title, subtitle) {
    console.log("showPrettyToast")
    // ELIMINÉ: const $ = window.$; (ESTO ERA LO QUE ROMPÍA PRODUCCIÓN)
    
    const existing = document.getElementById('drive_pretty_toast');
    if (existing) { 
        existing.remove(); 
    }

    const toastHtml = `
        <div id="drive_pretty_toast" style="
            position: fixed; 
            bottom: 30px; 
            right: 30px; 
            width: 360px; 
            background: #ffffff; 
            border-radius: 12px; 
            box-shadow: 0 10px 40px rgba(0,0,0,0.15); 
            z-index: 99999; 
            border-left: 6px solid #F44336; 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; 
            overflow: hidden;
            animation: slideIn 0.4s cubic-bezier(0.2, 0.8, 0.2, 1);
            display: flex;
            flex-direction: column;
        ">
            <style>
                @keyframes slideIn { from { transform: translateX(120%); } to { transform: translateX(0); } }
                @keyframes progress { from { width: 0%; } to { width: 100%; } }
                @keyframes fadeOut { from { opacity: 1; } to { opacity: 0; } }
                @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
            </style>

            <div style="padding: 16px;">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom: 6px;">
                    <strong style="font-size: 15px; color:#2c3e50; font-weight: 600;">${title}</strong>
                    <div class="drive-spinner" style="
                        width: 16px; 
                        height: 16px; 
                        border: 2px solid #f3f3f3; 
                        border-top: 2px solid #F44336; 
                        border-radius: 50%; 
                        animation: spin 1s linear infinite;">
                    </div>
                </div>
                
                <div style="font-size: 13px; color: #7f8c8d; line-height: 1.4;">
                    ${subtitle}
                </div>
            </div>

            <div style="height: 4px; background: #fce4ec; width: 100%;">
                <div class="drive-bar" style="
                    height: 100%; 
                    background: #F44336; 
                    width: 0%; 
                    animation: progress 2s ease-in-out forwards;
                "></div>
            </div>
        </div>
    `;

    document.body.insertAdjacentHTML('beforeend', toastHtml);

    // Animación de éxito a los 2 segundos
    setTimeout(() => {
        const toast = document.getElementById('drive_pretty_toast');
        if (toast) {
            toast.style.borderLeftColor = '#4CAF50';
            
            const bar = toast.querySelector('.drive-bar');
            if(bar) bar.style.backgroundColor = '#4CAF50';
            
            const spinner = toast.querySelector('.drive-spinner');
            if(spinner) {
                spinner.style.animation = 'none';
                spinner.style.border = 'none';
                spinner.innerHTML = '✅';
                spinner.style.fontSize = '14px';
                spinner.style.marginTop = '-4px'; // Ajuste visual
            }
            
            // Desaparecer
            setTimeout(() => {
                toast.style.animation = 'fadeOut 0.5s forwards';
                setTimeout(() => {
                    // Usamos una doble verificación antes de remover para evitar errores
                    const elementToRemove = document.getElementById('drive_pretty_toast');
                    if(elementToRemove) elementToRemove.remove();
                }, 500);
            }, 2500);
        }
    }, 2000);
}