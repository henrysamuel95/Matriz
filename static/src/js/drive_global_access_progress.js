/** @odoo-module **/

import { registry } from "@web/core/registry";

const PANEL_ID = "drive_global_access_progress_toast";

function getPanel() {
    let panel = document.getElementById(PANEL_ID);
    if (panel) {
        return panel;
    }

    panel = document.createElement("div");
    panel.id = PANEL_ID;
    panel.style.cssText = [
        "position:fixed",
        "right:24px",
        "bottom:24px",
        "width:340px",
        "background:#fff",
        "border-left:5px solid #043484",
        "border-radius:8px",
        "box-shadow:0 10px 28px rgba(15,23,42,.22)",
        "z-index:10000",
        "font-family:Roboto, Arial, sans-serif",
        "display:none",
    ].join(";");
    panel.innerHTML = `
        <div style="padding:14px 16px 13px;">
            <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;">
                <strong data-role="title" style="font-size:14px;color:#1f2937;">Sincronizando lectores</strong>
                <button type="button" data-role="close" aria-label="Cerrar"
                        style="border:0;background:transparent;color:#64748b;font-size:18px;line-height:1;cursor:pointer;">×</button>
            </div>
            <div data-role="label" style="font-size:13px;color:#334155;margin-top:10px;">Preparando lotes...</div>
            <div data-role="detail" style="font-size:12px;color:#64748b;margin-top:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">
                Iniciando sincronización
            </div>
            <div style="height:4px;background:#e5e7eb;border-radius:999px;margin-top:12px;overflow:hidden;">
                <div data-role="bar" style="height:100%;width:0%;background:#043484;transition:width .25s ease;"></div>
            </div>
        </div>
    `;
    panel.querySelector('[data-role="close"]').addEventListener("click", () => {
        panel.style.display = "none";
    });
    document.body.appendChild(panel);
    return panel;
}

function updatePanel(payload) {
    const panel = getPanel();
    const percent = Math.max(0, Math.min(Number(payload.percent || 0), 100));
    const title = payload.pending ? "Sincronizando lectores" : "Lectores sincronizados";
    const detail = [
        `Creados: ${payload.added || 0}`,
        `Existentes: ${payload.existing || 0}`,
        `Retirados: ${payload.removed || 0}`,
        `Sin carpeta: ${payload.missing || 0}`,
        `Errores: ${payload.errors || 0}`,
    ].join(" · ");

    panel.querySelector('[data-role="title"]').textContent = title;
    panel.querySelector('[data-role="label"]').textContent = payload.label || "Procesando...";
    panel.querySelector('[data-role="detail"]').textContent = detail;
    panel.querySelector('[data-role="bar"]').style.width = `${percent}%`;
    panel.style.display = "block";
}

function finishPanel(payload) {
    updatePanel(payload);
    setTimeout(() => {
        const panel = document.getElementById(PANEL_ID);
        if (panel) {
            panel.style.display = "none";
        }
    }, 3500);
}

async function driveGlobalAccessProgressAction(env, action) {
    const orm = env.services.orm;
    const notification = env.services.notification;
    const configId = action.params && action.params.config_id;
    const batchSize = (action.params && action.params.batch_size) || 5;

    if (!configId) {
        notification.add("No se encontró la configuración de lectores globales.", { type: "danger" });
        return;
    }

    updatePanel({
        pending: true,
        processed: 0,
        total: 0,
        percent: 0,
        label: "Preparando sincronización por lotes...",
    });

    const processNextBatch = async () => {
        let payload;
        try {
            payload = await orm.call(
                "drive.global.access",
                "action_process_drive_reader_batch",
                [[configId]],
                { batch_size: batchSize }
            );
        } catch (error) {
            const panel = getPanel();
            panel.querySelector('[data-role="title"]').textContent = "Error sincronizando lectores";
            panel.querySelector('[data-role="label"]').textContent = "Revisa el log o la conexión con Google Drive.";
            panel.querySelector('[data-role="detail"]').textContent = "";
            panel.querySelector('[data-role="bar"]').style.background = "#dc3545";
            notification.add("No fue posible continuar la sincronización de lectores globales.", { type: "danger" });
            throw error;
        }

        if (payload.pending) {
            updatePanel(payload);
            setTimeout(processNextBatch, 700);
            return;
        }
        finishPanel(payload);
    };

    await processNextBatch();
}

registry.category("actions").add("drive_global_access_progress", driveGlobalAccessProgressAction);
