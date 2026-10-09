/** @odoo-module **/

import { registry } from "@web/core/registry";

const STORAGE_PREFIX = "matriz.kanban.decorator.";
const CHATTER_FOCUS_PREFIX = "matriz.kanban.decorator.focus_chatter.";

function toInt(value) {
    const parsed = parseInt(value || "0", 10);
    return Number.isNaN(parsed) ? 0 : parsed;
}

function closeMenus(except = null) {
    document.querySelectorAll(".mtz-kanban-decorator.is-open").forEach((decorator) => {
        if (decorator !== except) {
            decorator.classList.remove("is-open");
        }
    });
}

function getDecorator(button) {
    return button.closest(".mtz-kanban-decorator");
}

function getDecoratorData(decorator) {
    return {
        model: decorator.dataset.model || "",
        resId: toInt(decorator.dataset.resId),
        displayName: decorator.dataset.displayName || "Registro",
        projectId: toInt(decorator.dataset.projectId),
        projectField: decorator.dataset.projectField || "",
    };
}

function getCurrentActionContextFromHash() {
    const hash = new URLSearchParams((window.location.hash || "").replace(/^#/, ""));
    return {
        model: hash.get("model") || "",
        resId: toInt(hash.get("id")),
    };
}

function focusRequestedChatter() {
    const { model, resId } = getCurrentActionContextFromHash();
    if (!model || !resId) {
        return;
    }

    const storageKey = `${CHATTER_FOCUS_PREFIX}${model}.${resId}`;
    if (!window.sessionStorage.getItem(storageKey)) {
        return;
    }

    const chatter = document.querySelector(".oe_chatter");
    if (!chatter) {
        return;
    }

    window.sessionStorage.removeItem(storageKey);
    chatter.scrollIntoView({ behavior: "smooth", block: "start" });
    chatter.classList.add("mtz-kanban-decorator__chatter-focus");

    window.setTimeout(() => {
        const candidates = Array.from(chatter.querySelectorAll("button, a"));
        const sendButton = candidates.find((element) => {
            const text = (element.textContent || "").trim().toLowerCase();
            return text.includes("enviar mensaje") || text.includes("send message");
        });
        if (sendButton) {
            sendButton.click();
        }
    }, 350);

    window.setTimeout(() => {
        chatter.classList.remove("mtz-kanban-decorator__chatter-focus");
    }, 2200);
}

function refreshLocalBadges() {
    document.querySelectorAll(".mtz-kanban-decorator").forEach((decorator) => {
        const model = decorator.dataset.model || "";
        const resId = decorator.dataset.resId || "";
        const writeDate = decorator.dataset.writeDate || "";
        const nativeCount = toInt(decorator.dataset.count);
        const badge = decorator.querySelector(".mtz-kanban-decorator__badge");

        if (!badge || !model || !resId) {
            return;
        }

        const storageKey = `${STORAGE_PREFIX}${model}.${resId}.write_date`;
        const previousWriteDate = window.localStorage.getItem(storageKey);
        let count = nativeCount;

        if (previousWriteDate && writeDate && previousWriteDate !== writeDate) {
            count += 1;
        }

        if (!previousWriteDate && writeDate) {
            window.localStorage.setItem(storageKey, writeDate);
        }

        if (count > 0) {
            badge.textContent = `+${count}`;
            badge.hidden = false;
        } else {
            badge.hidden = true;
        }
    });
}

export const kanbanDecoratorService = {
    dependencies: ["action", "notification"],

    start(env, { action, notification }) {
        let refreshTimer = null;

        const scheduleRefresh = () => {
            window.clearTimeout(refreshTimer);
            refreshTimer = window.setTimeout(refreshLocalBadges, 120);
        };

        const observer = new MutationObserver(scheduleRefresh);
        const chatterObserver = new MutationObserver(focusRequestedChatter);

        const observeBody = () => {
            if (!document.body) {
                window.setTimeout(observeBody, 50);
                return;
            }

            refreshLocalBadges();
            observer.observe(document.body, { childList: true, subtree: true });
            chatterObserver.observe(document.body, { childList: true, subtree: true });
            focusRequestedChatter();
        };

        observeBody();

        document.addEventListener("click", async (event) => {
            const toggleButton = event.target.closest(".mtz-kanban-decorator__toggle");
            const actionButton = event.target.closest(".mtz-kanban-decorator__action");

            if (!toggleButton && !actionButton && !event.target.closest(".mtz-kanban-decorator")) {
                closeMenus();
                return;
            }

            if (toggleButton) {
                event.preventDefault();
                event.stopPropagation();
                const decorator = getDecorator(toggleButton);
                const isOpen = decorator.classList.contains("is-open");
                closeMenus(decorator);
                decorator.classList.toggle("is-open", !isOpen);
                return;
            }

            if (!actionButton) {
                return;
            }

            event.preventDefault();
            event.stopPropagation();

            const decorator = getDecorator(actionButton);
            const data = getDecoratorData(decorator);
            const option = actionButton.dataset.option;

            if (!data.model || !data.resId) {
                notification.add("No se encontro el contexto del registro.", { type: "warning" });
                return;
            }

            const commonContext = {
                active_model: data.model,
                active_id: data.resId,
                default_model: data.model,
                default_res_id: data.resId,
                default_project_id: data.projectId || false,
                default_project_field: data.projectField || false,
            };

            const storageKey = `${STORAGE_PREFIX}${data.model}.${data.resId}.write_date`;
            if (decorator.dataset.writeDate) {
                window.localStorage.setItem(storageKey, decorator.dataset.writeDate);
            }
            const badge = decorator.querySelector(".mtz-kanban-decorator__badge");
            if (badge) {
                badge.hidden = true;
            }
            closeMenus();

            if (option === "trace" && data.model === "contrataciones_fuv") {
                await action.doAction({
                    type: "ir.actions.act_window",
                    name: `Trazabilidad interna - ${data.displayName}`,
                    res_model: "trazabilidad_interna",
                    view_mode: "tree,form",
                    views: [[false, "tree"], [false, "form"]],
                    target: "current",
                    domain: [["contratacion_fuv_id", "=", data.resId]],
                    context: {
                        ...commonContext,
                        search_default_group_usuario: 1,
                    },
                });
                return;
            }

            if (option === "trace") {
                await action.doAction({
                    type: "ir.actions.act_window",
                    name: `Mensajes y trazabilidad - ${data.displayName}`,
                    res_model: "mail.message",
                    view_mode: "tree,form",
                    views: [[false, "tree"], [false, "form"]],
                    target: "current",
                    domain: [["model", "=", data.model], ["res_id", "=", data.resId]],
                    context: commonContext,
                });
                return;
            }

            if (option === "activity") {
                await action.doAction({
                    type: "ir.actions.act_window",
                    name: `Actividades - ${data.displayName}`,
                    res_model: "mail.activity",
                    view_mode: "tree,form",
                    views: [[false, "tree"], [false, "form"]],
                    target: "current",
                    domain: [["res_model", "=", data.model], ["res_id", "=", data.resId]],
                    context: commonContext,
                });
                return;
            }

            if (option === "talk") {
                window.sessionStorage.setItem(`${CHATTER_FOCUS_PREFIX}${data.model}.${data.resId}`, "1");
            }

            await action.doAction({
                type: "ir.actions.act_window",
                name: data.displayName,
                res_model: data.model,
                res_id: data.resId,
                view_mode: "form",
                views: [[false, "form"]],
                target: "current",
                context: {
                    ...commonContext,
                    open_chatter: option === "talk",
                },
            });
        }, true);
    },
};

registry.category("services").add("matrizKanbanDecoratorService", kanbanDecoratorService);
