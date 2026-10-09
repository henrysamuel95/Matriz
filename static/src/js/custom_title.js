/** @odoo-module **/

import { WebClient } from "@web/webclient/webclient";
import { patch } from "web.utils";

patch(WebClient.prototype, "matriz_custom_title.WebClient", {
    setup() {
        this._super();
        this.title.setParts({ zopenerp: "SGO" });
    },
});