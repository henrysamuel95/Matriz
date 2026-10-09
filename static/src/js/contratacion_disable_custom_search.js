/** @odoo-module **/

// Oculta la opcion nativa "Anadir filtro personalizado" solo en el modelo
// contratacion. El fields_get del modelo ya oculta los campos personalizados,
// pero Odoo agrega "ID" manualmente al filtro personalizado; por eso esta
// entrada seguia visible. Este archivo solo afecta la interfaz web, no cambia
// datos, dominios, permisos ni filtros predefinidos.
const CUSTOM_SEARCH_TEXTS = [
    "anadir filtro personalizado",
    "add custom filter",
];

function normalizeText(value) {
    return (value || "")
        .normalize("NFD")
        .replace(/[\u0300-\u036f]/g, "")
        .trim()
        .toLowerCase();
}

function isContratacionView() {
    const hash = new URLSearchParams((window.location.hash || "").replace(/^#/, ""));
    return hash.get("model") === "contratacion" || Boolean(document.querySelector(".mtz-contratacion-kanban"));
}

function hideCustomFilterEntry() {
    if (!document.body) {
        return;
    }

    if (!isContratacionView()) {
        return;
    }

    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) {
        nodes.push(walker.currentNode);
    }

    for (const node of nodes) {
        const text = normalizeText(node.nodeValue);
        if (!CUSTOM_SEARCH_TEXTS.some((label) => text.includes(label))) {
            continue;
        }

        const parent = node.parentElement;
        const target = parent && (
            parent.closest(".dropdown-item") ||
            parent.closest(".o-dropdown-item") ||
            parent.closest(".o_menu_item") ||
            parent.closest("[role='menuitem']") ||
            parent.closest("li") ||
            parent
        );

        if (target) {
            target.classList.add("d-none");
            target.setAttribute("aria-hidden", "true");
            target.style.display = "none";
        }
    }
}

function startCustomFilterGuard() {
    if (!document.body) {
        window.setTimeout(startCustomFilterGuard, 50);
        return;
    }

    const observer = new MutationObserver(hideCustomFilterEntry);

    observer.observe(document.body, {
        childList: true,
        subtree: true,
    });

    window.addEventListener("hashchange", hideCustomFilterEntry);
    document.addEventListener("click", () => window.setTimeout(hideCustomFilterEntry, 0), true);

    hideCustomFilterEntry();
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", startCustomFilterGuard, { once: true });
} else {
    startCustomFilterGuard();
}
