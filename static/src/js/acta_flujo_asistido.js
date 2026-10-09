odoo.define('matriz.acta_flujo_asistido', function (require) {
    "use strict";

    function isVisible(element) {
        return !!(
            element &&
            element.offsetParent !== null &&
            !element.closest('.o_invisible_modifier') &&
            window.getComputedStyle(element).display !== 'none'
        );
    }

    function markActive(step) {
        document.querySelectorAll('.js-acta-flow-step').forEach(function (item) {
            item.style.boxShadow = '';
            item.style.outline = '';
        });
        step.style.boxShadow = '0 0 0 3px rgba(4, 52, 132, 0.12)';
        step.style.outline = '1px solid rgba(4, 52, 132, 0.18)';
    }

    let activeStep = null;
    let scrollTimer = null;
    let activeCertificacionTarget = null;
    let certificacionScrollTimer = null;
    let activeDeTarget = null;
    let deScrollTimer = null;
    let deAdvanceTimer = null;

    function isActiveMarker(marker) {
        return !!(
            marker &&
            !marker.classList.contains('o_invisible_modifier') &&
            !marker.closest('.o_invisible_modifier')
        );
    }

    function getScrollParent(element) {
        let parent = element ? element.parentElement : null;
        while (parent && parent !== document.body) {
            const style = window.getComputedStyle(parent);
            const canScroll = /(auto|scroll)/.test(style.overflowY) && parent.scrollHeight > parent.clientHeight;
            if (canScroll || parent.classList.contains('modal-body')) {
                return parent;
            }
            parent = parent.parentElement;
        }
        return document.scrollingElement || document.documentElement;
    }

    function parsePercentValue(value) {
        if (!value) {
            return 0;
        }
        const normalized = String(value).replace(/\./g, '').replace(',', '.').replace(/[^\d.-]/g, '');
        return Number(normalized) || 0;
    }

    function certificacionTargetHasValue(target) {
        if (!target) {
            return false;
        }
        const field = target.querySelector('input, textarea, select');
        if (!field) {
            return false;
        }
        if (target.id === 'certificacion_campo_porcentaje') {
            return parsePercentValue(field.value) !== 0;
        }
        return String(field.value || '').trim() !== '';
    }

    function clearCertificacionHighlight() {
        document.querySelectorAll('.js-certificacion-highlighted').forEach(function (item) {
            item.classList.remove('js-certificacion-highlighted');
            item.style.boxShadow = '';
            item.style.outline = '';
        });
    }

    function shouldSkipFilledCertificacionTarget(target) {
        const skippableIds = [
            'certificacion_campo_actividad',
            'certificacion_campo_porcentaje',
            'certificacion_campo_coordinador_nombre',
            'certificacion_campo_coordinador_cargo',
            'certificacion_campo_coordinador_fecha',
            'certificacion_campo_ventanilla_nombre',
            'certificacion_campo_ventanilla_fecha',
            'certificacion_campo_financiera_nombre',
            'certificacion_campo_financiera_fecha',
        ];
        return target && skippableIds.indexOf(target.id) !== -1 && certificacionTargetHasValue(target);
    }

    function hasActiveMarkerFor(modal, fragment) {
        return Array.from(modal.querySelectorAll('.js-certificacion-cumplimiento-marker')).some(function (marker) {
            const selector = marker.getAttribute('data-target') || '';
            return isActiveMarker(marker) && selector.indexOf(fragment) !== -1;
        });
    }

    function findCertificacionTarget(modal) {
        const isAsistenteCoordinacion = (
            hasActiveMarkerFor(modal, '#certificacion_campo_actividad') ||
            hasActiveMarkerFor(modal, '#certificacion_campo_porcentaje')
        );

        if (isAsistenteCoordinacion) {
            const actividadTarget = modal.querySelector('#certificacion_campo_actividad');
            if (actividadTarget && isVisible(actividadTarget) && !certificacionTargetHasValue(actividadTarget)) {
                return actividadTarget;
            }

            const porcentajeTarget = modal.querySelector('#certificacion_campo_porcentaje');
            if (
                porcentajeTarget &&
                isVisible(porcentajeTarget) &&
                certificacionTargetHasValue(actividadTarget) &&
                !certificacionTargetHasValue(porcentajeTarget)
            ) {
                return porcentajeTarget;
            }
        }

        const markers = Array.from(modal.querySelectorAll('.js-certificacion-cumplimiento-marker')).filter(isActiveMarker);
        for (const marker of markers) {
            const selector = marker.getAttribute('data-target');
            const target = selector ? modal.querySelector(selector) : null;
            if (!target) {
                continue;
            }
            if (shouldSkipFilledCertificacionTarget(target)) {
                continue;
            }
            return target;
        }
        return null;
    }

    function highlightCertificacionTarget(target) {
        clearCertificacionHighlight();
        target.classList.add('js-certificacion-highlighted');
        target.style.boxShadow = '0 0 0 4px rgba(4, 52, 132, 0.22)';
        target.style.outline = '2px solid rgba(4, 52, 132, 0.45)';
    }

    function scrollToCertificacionTarget(force) {
        const modal = Array.from(document.querySelectorAll('.modal.show, .modal.in, .o_dialog')).find(function (item) {
            return item.textContent && item.textContent.indexOf('CERTIFICACIÓN CONJUNTA') !== -1;
        });
        if (!modal) {
            activeCertificacionTarget = null;
            return;
        }

        const target = findCertificacionTarget(modal);
        if (!target) {
            activeCertificacionTarget = null;
            clearCertificacionHighlight();
            return;
        }

        if (!force && activeCertificacionTarget === target) {
            return;
        }

        activeCertificacionTarget = target;
        highlightCertificacionTarget(target);

        clearTimeout(certificacionScrollTimer);
        certificacionScrollTimer = setTimeout(function () {
            const scrollParent = getScrollParent(target);
            const parentRect = scrollParent.getBoundingClientRect();
            const targetRect = target.getBoundingClientRect();
            const offset = targetRect.top - parentRect.top - (scrollParent.clientHeight / 3);
            scrollParent.scrollTo({
                top: Math.max(scrollParent.scrollTop + offset, 0),
                behavior: 'smooth'
            });
        }, 450);
    }

    function scrollToDeTarget(force) {
        const marker = Array.from(document.querySelectorAll('.js-acta-de-marker, .js-acta-de-marker-regalias')).find(isVisible);
        if (!marker) {
            activeDeTarget = null;
            return;
        }

        const selector = marker.getAttribute('data-target');
        const target = selector ? document.querySelector(selector) : null;
        if (!target || !isVisible(target)) {
            activeDeTarget = null;
            return;
        }

        // No volver a marcar ni regresar al campo si el usuario ya digitó un valor válido.
        const input = target.querySelector('input, textarea, select');
        const rawValue = input ? String(input.value || '').trim() : '';
        const numericValue = Number(rawValue.replace(',', '.'));
        if (rawValue && numericValue > 0) {
            activeDeTarget = null;
            clearTimeout(deScrollTimer);
            return;
        }

        if (!force && activeDeTarget === target) {
            return;
        }

        activeDeTarget = target;

        clearTimeout(deScrollTimer);
        deScrollTimer = setTimeout(function () {
            scrollToActaTarget(target);
        }, 900);
    }

    function bindDeFieldProgression() {
        document.addEventListener('input', function (event) {
            const targetCell = event.target && event.target.closest ? event.target.closest('#sgo-campo-de') : null;
            if (!targetCell) {
                return;
            }

            const input = targetCell.querySelector('input, textarea, select');
            const rawValue = input ? String(input.value || '').trim() : '';
            const numericValue = Number(rawValue.replace(',', '.'));

            clearTimeout(deAdvanceTimer);

            if (!rawValue || !numericValue || numericValue <= 0) {
                return;
            }

            activeDeTarget = null;
            clearTimeout(deScrollTimer);
            targetCell.style.boxShadow = '';
            targetCell.style.outline = '';
            targetCell.style.background = '';

            // No esperamos a que Odoo recalcule los modifiers:
            // tras una breve pausa al escribir, vamos directamente al Paso 2.
            deAdvanceTimer = setTimeout(function () {
                const regaliasMarker = Array.from(document.querySelectorAll('.js-acta-de-marker-regalias')).find(isVisible);
                let nextStep = null;

                if (regaliasMarker) {
                    nextStep = Array.from(document.querySelectorAll('.js-acta-flow-step')).find(function (step) {
                        return isVisible(step) && !step.closest('#sgo-campo-de');
                    });
                } else {
                    nextStep = document.querySelector('#cc-asignar-coordinador');
                }

                if (nextStep && isVisible(nextStep)) {
                    const stepBox = nextStep.classList && nextStep.classList.contains('js-acta-flow-step')
                        ? nextStep
                        : (nextStep.querySelector ? nextStep.querySelector('.js-acta-flow-step') : null) || nextStep;

                    markActive(stepBox);
                    const scrollParent = getScrollParent(stepBox);
                    const parentRect = scrollParent.getBoundingClientRect();
                    const targetRect = stepBox.getBoundingClientRect();
                    const offset = targetRect.top - parentRect.top - (scrollParent.clientHeight / 4);
                    scrollParent.scrollTo({
                        top: Math.max(scrollParent.scrollTop + offset, 0),
                        behavior: 'smooth'
                    });
                }
            }, 850);
        });
    }

    function scrollToCurrentStep(force) {
        const marker = document.querySelector('.js-acta-flujo-cuenta-cobro');
        if (!marker) {
            activeStep = null;
            return;
        }

        const firstVisibleStep = Array.from(document.querySelectorAll('.js-acta-flow-step')).find(isVisible);
        if (!firstVisibleStep) {
            activeStep = null;
            return;
        }

        if (!force && activeStep === firstVisibleStep) {
            return;
        }

        activeStep = firstVisibleStep;
        markActive(firstVisibleStep);
        clearTimeout(scrollTimer);
        scrollTimer = setTimeout(function () {
            if (activeStep === firstVisibleStep && isVisible(firstVisibleStep)) {
                firstVisibleStep.scrollIntoView({ behavior: 'smooth', block: 'center' });
            }
        }, 350);
    }

    function scrollToActaTarget(target) {
        if (!target || !isVisible(target)) {
            return;
        }

        target.style.boxShadow = '0 0 0 4px rgba(4, 52, 132, 0.22)';
        target.style.outline = '2px solid rgba(4, 52, 132, 0.45)';
        target.style.background = '#eef6ff';

        const scrollParent = getScrollParent(target);
        const parentRect = scrollParent.getBoundingClientRect();
        const targetRect = target.getBoundingClientRect();
        const offset = targetRect.top - parentRect.top - (scrollParent.clientHeight / 3);

        scrollParent.scrollTo({
            top: Math.max(scrollParent.scrollTop + offset, 0),
            behavior: 'smooth'
        });

        setTimeout(function () {
            target.style.boxShadow = '';
            target.style.outline = '';
            target.style.background = '';
        }, 2600);

        const input = target.querySelector('input, textarea, select');
        if (input) {
            setTimeout(function () {
                input.focus({ preventScroll: true });
            }, 700);
        }
    }

    function bindActaTargetLinks() {
        document.addEventListener('click', function (event) {
            const trigger = event.target.closest('.js-acta-scroll-target');
            if (!trigger) {
                return;
            }
            event.preventDefault();
            event.stopPropagation();

            const selector = trigger.getAttribute('data-target');
            const target = selector ? document.querySelector(selector) : null;
            scrollToActaTarget(target);
        });
    }

    function init() {
        bindActaTargetLinks();
        bindDeFieldProgression();
        let attempts = 0;
        const timer = setInterval(function () {
            attempts += 1;
            scrollToCurrentStep(attempts === 1);
            scrollToCertificacionTarget(attempts === 1);
            scrollToDeTarget(attempts === 1);
            if (attempts >= 12) {
                clearInterval(timer);
            }
        }, 500);

        let mutationTimer = null;
        const observer = new MutationObserver(function () {
            clearTimeout(mutationTimer);
            mutationTimer = setTimeout(function () {
                scrollToCurrentStep(false);
                scrollToCertificacionTarget(false);
                scrollToDeTarget(false);
            }, 120);
        });
        observer.observe(document.body, {
            childList: true,
            subtree: true,
            attributes: true,
            attributeFilter: ['class']
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
});
