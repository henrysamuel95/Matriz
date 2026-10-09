/** @odoo-module **/

import { registry } from "@web/core/registry";

const maintenanceEmailAction = async (env, action) => {
    const orm = env.services.orm;
    const notification = env.services.notification;
    const { subject, message, email_from } = action.params;

    // --- FUNCIONES VISUALES DEL TOAST ---
    const showToast = (title, subtitle) => {
        const $ = window.$;
        if ($('#maintenance_toast').length === 0) {
            $('body').append(`
                <div id="maintenance_toast" style="position: fixed; bottom: 20px; right: 20px; width: 320px; background: white; border-radius: 8px; box-shadow: 0 4px 15px rgba(0,0,0,0.2); z-index: 9999; display: none; border-left: 5px solid #ff9800; font-family: Roboto, sans-serif;">
                    <div style="padding: 12px 15px;">
                        <div style="display:flex; justify-content:space-between; align-items:center;">
                            <strong id="mt_title" style="font-size: 14px; color:#333;">${title}</strong>
                            <i id="mt_icon" class="fa fa-envelope-o fa-spin text-warning"></i>
                        </div>
                        <div id="mt_subtitle" style="font-size: 12px; color: #666; margin-top:4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${subtitle}</div>
                        <div class="progress" style="height: 4px; margin-top: 10px; background: #f1f1f1;">
                            <div id="mt_bar" class="progress-bar bg-warning" style="width: 0%; transition: width 0.3s;"></div>
                        </div>
                    </div>
                </div>
            `);
        }
        $('#maintenance_toast').fadeIn();
        $('#mt_title').text(title);
        $('#mt_subtitle').text(subtitle);
    };

    const updateProgress = (current, total, currentEmail) => {
        const $ = window.$;
        const percent = (current / total) * 100;
        $('#mt_title').text(`Enviando ${current} de ${total}`);
        $('#mt_subtitle').text(currentEmail);
        $('#mt_bar').css('width', percent + '%');
    };

    const finishToast = () => {
        const $ = window.$;
        $('#maintenance_toast').css('border-left-color', '#4CAF50');
        $('#mt_title').text("¡Avisos Enviados!");
        $('#mt_subtitle').text("Todos los usuarios notificados.");
        $('#mt_icon').removeClass().addClass('fa fa-check text-success');
        $('#mt_bar').removeClass('bg-warning').addClass('bg-success').css('width', '100%');
        setTimeout(() => { $('#maintenance_toast').fadeOut(); }, 4000);
    };

    // --- LÓGICA DE PROCESAMIENTO EN SEGUNDO PLANO ---
    try {
        const users = await orm.call("maintenance.control.wizard", "js_get_users_to_email", []);
        
        if (!users || users.length === 0) {
            notification.add("No hay usuarios activos con correo electrónico configurado.", { type: "warning" });
            return; // ANTES ESTABA EL CÓDIGO DE CERRAR VENTANA AQUÍ. Ya lo quitamos.
        }

        showToast("Iniciando envíos...", `Preparando ${users.length} correos`);

        const processQueue = async (index) => {
            if (index >= users.length) {
                finishToast();
                return;
            }

            const user = users[index];
            updateProgress(index + 1, users.length, user.email);

            try {
                await orm.call("maintenance.control.wizard", "js_send_single_email", [user.email, subject, message, email_from]);
            } catch (e) {
                console.error("Error enviando correo a", user.email, e);
            }

            setTimeout(() => {
                processQueue(index + 1);
            }, 50); 
        };

        processQueue(0);

        // ANTES ESTABA EL CÓDIGO DE CERRAR VENTANA AQUÍ. Lo reemplazamos por un return vacío.
        // Esto le dice a Odoo que la acción terminó, pero que no debe alterar la pantalla actual.
        return; 

    } catch (err) {
        console.error("Error inicializando envíos", err);
        notification.add("Error al iniciar el envío masivo.", { type: "danger" });
        return; // Igual aquí, quitamos el cierre de ventana.
    }
};

registry.category("actions").add("matriz.start_maintenance_emails", maintenanceEmailAction);