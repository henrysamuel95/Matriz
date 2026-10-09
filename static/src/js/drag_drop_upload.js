/** @odoo-module **/

import { FormController } from "@web/views/form/form_controller";
import { patch } from "@web/core/utils/patch";
import { onMounted, onWillUnmount } from "@odoo/owl";

patch(FormController.prototype, "Matriz.DriveDragDrop", {
    setup() {
        this._super(...arguments);

        const getModelName = () =>
            this.props.resModel || (this.model.config && this.model.config.resModel);

        const isDriveModel = () => getModelName() === 'drive';
        const isOrdenEspecialUploadMultiple = () =>
            getModelName() === 'ordenes_especiales.documento.carga.multiple';
        const isOrdenEspecialUploadSingle = () =>
            getModelName() === 'ordenes_especiales.documento.carga';
        const isOrdenEspecialUpload = () =>
            isOrdenEspecialUploadMultiple() || isOrdenEspecialUploadSingle();
        const isActaEntregaDocumentos = () =>
            getModelName() === 'acta_entrega_documentos';

        const ACTA_DROP_SELECTOR =
            ".sgo-acta-form-body .sgo-docs-reference .sgo-upload-dropzone";

        const getActaDropZone = (target) => {
            const element = target instanceof Element ? target : null;
            return element ? element.closest(ACTA_DROP_SELECTOR) : null;
        };

        const feedFileInput = (fileInput, file) => {
            if (!fileInput || !file) {
                return false;
            }
            const transfer = new DataTransfer();
            transfer.items.add(file);
            fileInput.files = transfer.files;
            fileInput.dispatchEvent(new Event('change', { bubbles: true }));
            return true;
        };

        const getDropZone = () => {
            if (isDriveModel()) {
                return document.getElementById("o_drive_drop_zone");
            }
            if (isOrdenEspecialUploadMultiple()) {
                return document.getElementById("o_ordenes_especiales_drop_zone");
            }
            if (isOrdenEspecialUploadSingle()) {
                return document.getElementById("o_ordenes_especiales_single_drop_zone");
            }
            return null;
        };

        const getOrdenEspecialFileInput = () =>
            document.querySelector(
                ".fc-ordenes-upload-multiple input[type='file'], " +
                ".fc-ordenes-upload-single input[type='file']"
            );

        // --- ESTA ES LA CLAVE: Bloqueo Global Interceptor ---
        this._blockOdooChatter = (e) => {
            if (isDriveModel() || isOrdenEspecialUpload()) {
                const dropZone = getDropZone();
                // Si el evento NO ocurre dentro de nuestra zona de drop, 
                // detenemos la propagación para que el Chatter no se entere.
                if (dropZone && !dropZone.contains(e.target)) {
                    e.stopPropagation();
                    e.stopImmediatePropagation();
                }
            }
        };

        this._onDragOver = (e) => {
            e.preventDefault();
            e.stopPropagation(); 
            const dropZone = getDropZone();
            if (dropZone) {
                dropZone.style.backgroundColor = "#E6E6FA";
                dropZone.style.border = "2px dashed #4B0082";
            }
        };

        this._onDragLeave = (e) => {
            e.preventDefault();
            e.stopPropagation();
            const dropZone = getDropZone();
            if (dropZone) {
                dropZone.style.backgroundColor = "#F8F4FF";
                dropZone.style.border = "2px dashed #9370DB";
            }
        };

        this._onDrop = async (e) => {
            e.preventDefault();
            e.stopPropagation();

            const dropZone = getDropZone();
            if (!dropZone) {
                return;
            }

            dropZone.style.backgroundColor = "#F8F4FF";
            dropZone.style.border = "2px dashed #9370DB";

            const files = Array.from(e.dataTransfer.files || []);
            if (!files.length) {
                return;
            }

            if (isOrdenEspecialUploadMultiple()) {
                // many2many_binary de Odoo 16 agrega correctamente cada adjunto
                // cuando recibe cada archivo como un change independiente.
                // Esto conserva su lista visual y la X para quitar archivos.
                for (const file of files) {
                    const fileInput = getOrdenEspecialFileInput();
                    if (!fileInput) {
                        break;
                    }
                    const transfer = new DataTransfer();
                    transfer.items.add(file);
                    fileInput.files = transfer.files;
                    fileInput.dispatchEvent(new Event('change', { bubbles: true }));

                    // Dar tiempo al widget para crear el ir.attachment y volver
                    // a renderizar antes de entregar el siguiente archivo.
                    await new Promise((resolve) => setTimeout(resolve, 450));
                }
                return;
            }

            const fileInput = isOrdenEspecialUploadSingle()
                ? getOrdenEspecialFileInput()
                : dropZone.querySelector('input[type="file"]');
            if (fileInput) {
                const transfer = new DataTransfer();
                transfer.items.add(files[0]);
                fileInput.files = transfer.files;
                fileInput.dispatchEvent(new Event('change', { bubbles: true }));
            }
        };

        this._onActaDragOver = (e) => {
            if (!isActaEntregaDocumentos()) {
                return;
            }
            const dropZone = getActaDropZone(e.target);
            if (!dropZone) {
                return;
            }
            e.preventDefault();
            e.stopPropagation();
            e.stopImmediatePropagation();
            e.dataTransfer.dropEffect = 'copy';
            dropZone.classList.add('sgo-drag-active');
        };

        this._onActaDragLeave = (e) => {
            if (!isActaEntregaDocumentos()) {
                return;
            }
            const dropZone = getActaDropZone(e.target);
            if (!dropZone || (e.relatedTarget && dropZone.contains(e.relatedTarget))) {
                return;
            }
            dropZone.classList.remove('sgo-drag-active');
        };

        this._onActaDrop = (e) => {
            if (!isActaEntregaDocumentos()) {
                return;
            }
            const dropZone = getActaDropZone(e.target);
            if (!dropZone) {
                return;
            }
            e.preventDefault();
            e.stopPropagation();
            e.stopImmediatePropagation();
            dropZone.classList.remove('sgo-drag-active');

            const file = Array.from(e.dataTransfer.files || [])[0];
            if (!file) {
                return;
            }

            const fileInputs = Array.from(
                dropZone.querySelectorAll('input[type="file"]')
            );
            const fileInput = fileInputs.find(
                (input) => !input.closest('.o_invisible_modifier')
            ) || fileInputs[0];
            if (feedFileInput(fileInput, file)) {
                dropZone.classList.add('sgo-upload-loaded');
            }
        };

        onMounted(() => {
            if (isDriveModel() || isOrdenEspecialUpload()) {
                // Escuchadores globales en fase de captura (true) para interceptar antes que el Chatter
                window.addEventListener("dragenter", this._blockOdooChatter, true);
                window.addEventListener("dragover", this._blockOdooChatter, true);

                setTimeout(() => {
                    const dropZone = getDropZone();
                    if (dropZone) {
                        dropZone.addEventListener("dragover", this._onDragOver);
                        dropZone.addEventListener("dragleave", this._onDragLeave);
                        dropZone.addEventListener("drop", this._onDrop);
                    }
                }, 500);
            }
            if (isActaEntregaDocumentos()) {
                // Delegación en window: sigue funcionando aunque Odoo vuelva a
                // renderizar los widgets Binary al cambiar de estado.
                window.addEventListener('dragover', this._onActaDragOver, true);
                window.addEventListener('dragleave', this._onActaDragLeave, true);
                window.addEventListener('drop', this._onActaDrop, true);
            }
        });

        onWillUnmount(() => {
            window.removeEventListener("dragenter", this._blockOdooChatter, true);
            window.removeEventListener("dragover", this._blockOdooChatter, true);
            
            const dropZone = getDropZone();
            if (dropZone) {
                dropZone.removeEventListener("dragover", this._onDragOver);
                dropZone.removeEventListener("dragleave", this._onDragLeave);
                dropZone.removeEventListener("drop", this._onDrop);
            }
            window.removeEventListener('dragover', this._onActaDragOver, true);
            window.removeEventListener('dragleave', this._onActaDragLeave, true);
            window.removeEventListener('drop', this._onActaDrop, true);
        });
    }
});
