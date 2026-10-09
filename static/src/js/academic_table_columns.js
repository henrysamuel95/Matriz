/** @odoo-module **/

import { registry } from "@web/core/registry";

function getAcademicType(row) {
    const selector = row.querySelector('select');
    if (selector) {
        return selector.value;
    }
    const firstCell = row.querySelector('td');
    return (firstCell?.textContent || '').trim().toLowerCase();
}

function toggleProfessionalColumns(table) {
    const rows = Array.from(table.querySelectorAll('tbody tr'));
    const hasPregrado = rows.some((row) => getAcademicType(row) === 'pregrado');

    table.querySelectorAll('tr').forEach((row) => {
        [3, 4].forEach((columnIndex) => {
            const cell = row.children[columnIndex];
            if (cell) {
                cell.style.display = hasPregrado ? '' : 'none';
            }
        });
    });
}

function refreshAcademicTables() {
    document.querySelectorAll('.academic-info-table table').forEach(toggleProfessionalColumns);
}

export const academicTableColumnsService = {
    start() {
        let refreshPending = false;
        const scheduleRefresh = () => {
            if (refreshPending) {
                return;
            }
            refreshPending = true;
            requestAnimationFrame(() => {
                refreshPending = false;
                refreshAcademicTables();
            });
        };

        const observer = new MutationObserver(scheduleRefresh);
        observer.observe(document.body, {
            childList: true,
            subtree: true,
            characterData: true,
        });
        document.addEventListener('change', (event) => {
            if (event.target.closest('.academic-info-table')) {
                scheduleRefresh();
            }
        });
        scheduleRefresh();

        return {
            stop() {
                observer.disconnect();
            },
        };
    },
};

registry.category('services').add('academicTableColumns', academicTableColumnsService);
