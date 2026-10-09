/** @odoo-module **/

const TYPE_CLASSES = [
    "sgo-file-type-pdf",
    "sgo-file-type-xlsx",
    "sgo-file-type-docx",
    "sgo-file-type-image",
];

const DOCUMENT_CONTAINERS = [
    ".sgo-acta-form-body .sgo-docs-reference",
    ".sgo-acta-form-body #cc-documentos-contabilidad",
    ".sgo-acta-form-body #cc-documentos-tesoreria",
];

function getFileType(filename) {
    const value = (filename || "").trim().toLowerCase();
    if (!value) {
        return null;
    }
    if (/\.(xlsx|xls|xlsm|csv)(?:\?|#|$)/i.test(value)) {
        return "sgo-file-type-xlsx";
    }
    if (/\.(docx|doc|odt|rtf)(?:\?|#|$)/i.test(value)) {
        return "sgo-file-type-docx";
    }
    if (/\.(png|jpe?g|gif|webp|bmp|svg)(?:\?|#|$)/i.test(value)) {
        return "sgo-file-type-image";
    }
    if (/\.pdf(?:\?|#|$)/i.test(value)) {
        return "sgo-file-type-pdf";
    }
    return null;
}

function fieldText(field) {
    if (!field) {
        return "";
    }
    const input = field.querySelector("input");
    if (input && input.value) {
        return input.value;
    }
    const titleNode = field.querySelector("[title]");
    if (titleNode && titleNode.getAttribute("title")) {
        return titleNode.getAttribute("title");
    }
    return field.textContent || "";
}

function findFilename(field) {
    const card = field.closest("td, .sgo-doc-multi-card, .o_group, .o_inner_group") || field.parentElement;
    let filename = "";

    if (card) {
        const hiddenNames = Array.from(
            card.querySelectorAll(
                '[name$="_name"], [name$="_documento_name"], [name$="_doc_name"], [name$="_fname"]'
            )
        );
        for (const hidden of hiddenNames) {
            filename = fieldText(hidden);
            if (getFileType(filename)) {
                return filename;
            }
        }
    }

    filename = fieldText(field);
    return filename;
}

function markLoadedUploaders(root = document) {
    const uploaders = root.querySelectorAll?.(".sgo-upload-dropzone-auto") || [];
    uploaders.forEach((dropzone) => {
        const widget = dropzone.querySelector(
            ".o_field_binary_file, .o_field_binary, .sgo-upload-field-auto"
        );
        if (!widget) {
            dropzone.classList.remove("sgo-upload-loaded");
            return;
        }

        const values = Array.from(widget.querySelectorAll("input"))
            .map((input) => (input.value || "").trim())
            .filter(Boolean);
        const hasSelectedFile = Array.from(
            widget.querySelectorAll('input[type="file"]')
        ).some((input) => Boolean(input.files?.length));
        const hasFilenameInput = Array.from(
            widget.querySelectorAll('input:not([type="file"])')
        ).some((input) => Boolean((input.value || "").trim()));
        const titled = Array.from(widget.querySelectorAll("[title]"))
            .map((node) => (node.getAttribute("title") || "").trim())
            .filter(Boolean);
        const visibleText = (widget.textContent || "").trim();

        const hasKnownFilename = [...values, ...titled, visibleText].some(
            (value) =>
                /\.(pdf|docx?|xlsx?|xlsm|csv|odt|rtf|png|jpe?g|gif|webp|bmp|svg)(?:\s|$|\?|#)/i.test(value)
        );
        const hasFilename = hasSelectedFile || hasFilenameInput || hasKnownFilename;

        dropzone.classList.toggle("sgo-upload-loaded", hasFilename);
    });
}

function applyIconTypes(root = document) {
    DOCUMENT_CONTAINERS.forEach((selector) => {
        const containers = root.querySelectorAll?.(selector) || [];
        containers.forEach((container) => {
            container.querySelectorAll(".readonly-field, .truncate-text").forEach((field) => {
                TYPE_CLASSES.forEach((cls) => field.classList.remove(cls));
                const typeClass = getFileType(findFilename(field));
                if (typeClass) {
                    field.classList.add(typeClass);
                }
            });
        });
    });
}

let scheduled = false;
function scheduleApply() {
    if (scheduled) {
        return;
    }
    scheduled = true;
    requestAnimationFrame(() => {
        scheduled = false;
        applyIconTypes();
        markLoadedUploaders();
    });
}

document.addEventListener("DOMContentLoaded", scheduleApply);
window.addEventListener("load", scheduleApply);
// El nombre de un Binary puede cambiar solo como propiedad del input, algo
// que MutationObserver no detecta. Escuchar change/input garantiza que la
// tarjeta pase al diseño compacto al seleccionar o arrastrar un archivo.
document.addEventListener("change", scheduleApply, true);
document.addEventListener("input", scheduleApply, true);

const observer = new MutationObserver(scheduleApply);
observer.observe(document.documentElement, {
    childList: true,
    subtree: true,
    characterData: true,
});
