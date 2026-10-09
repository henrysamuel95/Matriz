/** @odoo-module **/

import { ListController } from "@web/views/list/list_controller";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { onMounted, onWillUnmount } from "@odoo/owl";

const PANEL_ID = "sgo_mass_job_monitor";
const ACK_KEY = "sgo_mass_job_acknowledged";

function stateLabel(state) {
    return {pending: "Pendiente", running: "Procesando", done: "Terminado",
        done_errors: "Terminado con errores", failed: "Fallido"}[state] || state;
}

function acknowledgedIds() {
    try {
        return new Set(JSON.parse(window.sessionStorage.getItem(ACK_KEY) || "[]"));
    } catch (_error) {
        return new Set();
    }
}

function acknowledge(jobs) {
    const ids = acknowledgedIds();
    jobs.forEach((job) => ids.add(job.id));
    window.sessionStorage.setItem(ACK_KEY, JSON.stringify([...ids].slice(-50)));
}

function ensurePanel() {
    let panel = document.getElementById(PANEL_ID);
    if (panel) return panel;
    panel = document.createElement("aside");
    panel.id = PANEL_ID;
    panel.className = "sgo-mass-job-panel";
    panel.style.zIndex = "10000";
    panel.innerHTML = `
        <div class="sgo-mass-job-panel__header">
            <div><strong>Procesos masivos</strong><span>El proceso continúa aunque cierres la ventana.</span></div>
            <button type="button" data-role="close" aria-label="Cerrar">×</button>
        </div>
        <div class="sgo-mass-job-panel__body" data-role="jobs"></div>`;
    document.body.appendChild(panel);
    return panel;
}

function renderJobs(jobs) {
    const panel = ensurePanel();
    const body = panel.querySelector('[data-role="jobs"]');
    body.replaceChildren();
    for (const job of jobs) {
        const percent = job.total ? Math.round((job.processed / job.total) * 100) : 0;
        const card = document.createElement("section");
        card.className = `sgo-mass-job-card sgo-mass-job-card--${job.state}`;
        card.innerHTML = `
            <div class="sgo-mass-job-card__top"><strong></strong><span class="sgo-mass-job-card__status"></span></div>
            <div class="sgo-mass-job-card__count"></div>
            <div class="sgo-mass-job-card__track"><div class="sgo-mass-job-card__bar"></div></div>
            <div class="sgo-mass-job-card__error" style="display:none"></div>`;
        card.querySelector("strong").textContent = job.name;
        card.querySelector(".sgo-mass-job-card__status").textContent = stateLabel(job.state);
        card.querySelector(".sgo-mass-job-card__count").textContent =
            `${job.processed} de ${job.total} procesadas · ${job.success_count} correctas · ${job.error_count} con error`;
        card.querySelector(".sgo-mass-job-card__bar").style.width = `${Math.max(2, Math.min(percent, 100))}%`;
        if (job.last_error) {
            const error = card.querySelector(".sgo-mass-job-card__error");
            error.textContent = job.last_error;
            error.style.display = "block";
        }
        body.appendChild(card);
    }
    panel.classList.add("is-visible");
}

patch(ListController.prototype, "Matriz.SgoMassJobMonitor", {
    setup() {
        this._super(...arguments);
        this._sgoMassOrm = useService("orm");
        this._sgoMassPollTimer = null;
        this._sgoMassFinishTimer = null;
        this._sgoMassClosedSignature = "";
        onMounted(() => {
            const model = this.props.resModel || this.model?.config?.resModel;
            if (model !== "acta_entrega_documentos") return;
            const panel = ensurePanel();
            panel.querySelector('[data-role="close"]').onclick = () => {
                this._sgoMassClosedSignature = this._sgoMassLastSignature || "";
                panel.classList.remove("is-visible");
            };
            this._pollSgoMassJobs();
            this._sgoMassPollTimer = window.setInterval(() => this._pollSgoMassJobs(), 700);
        });
        onWillUnmount(() => {
            if (this._sgoMassPollTimer) window.clearInterval(this._sgoMassPollTimer);
            if (this._sgoMassFinishTimer) window.clearTimeout(this._sgoMassFinishTimer);
        });
    },

    async _pollSgoMassJobs() {
        try {
            const jobs = await this._sgoMassOrm.silent.call("sgo.mass.job", "get_my_job_progress", [], {});
            const ack = acknowledgedIds();
            const visible = jobs.filter((job) => ["pending", "running"].includes(job.state) ||
                (job.is_recent && !ack.has(job.id)));
            const signature = visible.map((job) => `${job.id}:${job.state}:${job.processed}:${job.error_count}`).join("|");
            this._sgoMassLastSignature = signature;
            const panel = document.getElementById(PANEL_ID);
            if (!visible.length) {
                panel?.classList.remove("is-visible");
                return;
            }
            if (signature !== this._sgoMassClosedSignature) renderJobs(visible);
            const active = visible.filter((job) => ["pending", "running"].includes(job.state));
            const finished = visible.filter((job) => ["done", "done_errors", "failed"].includes(job.state));
            if (active.length && this._sgoMassFinishTimer) {
                window.clearTimeout(this._sgoMassFinishTimer);
                this._sgoMassFinishTimer = null;
            }
            if (!active.length && finished.length && !this._sgoMassFinishTimer) {
                this._sgoMassFinishTimer = window.setTimeout(() => {
                    acknowledge(finished);
                    document.getElementById(PANEL_ID)?.classList.remove("is-visible");
                    window.location.reload();
                }, 3500);
            }
        } catch (error) {
            console.error("[Procesos masivos] No fue posible consultar el progreso", error);
        }
    },
});
