import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { ComfyWidgets } from "../../scripts/widgets.js";
import { registerVideo, unregisterVideo, updateDurations } from "./video_durations.js";

const DEFAULT_MODEL = "(OpenRouter default)";
const catalogRequests = new Map();
const mediaKinds = { CodaOpenRouterVideo: "video", CodaOpenRouterSound: "audio", CodaOpenRouterDialogue: "dialogue", CodaOnScreenDialogue: "speaking_video" };
const balanceWidgets = new Map();
const dollars = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
let balanceRequest;

function refreshBalance() {
    if (!balanceWidgets.size || balanceRequest) return balanceRequest;
    balanceRequest = (async () => {
        let label;
        let tip = "Click to refresh the remaining OpenRouter credit.";
        try {
            const response = await api.fetchApi("/coda-tools/openrouter/balance", { cache: "no-store" });
            if (!response.ok) throw new Error(response.status === 403 ? "Balance is visible in local ComfyUI." : "Check the server key and connection, then click to retry.");
            const { scope, remaining_usd } = await response.json();
            if (!Number.isFinite(remaining_usd)) throw new Error("OpenRouter did not report a dollar amount.");
            const title = scope === "account" ? "Account credit" : "Key allowance";
            label = title + " remaining: " + dollars.format(remaining_usd);
        } catch (error) {
            label = "Balance unavailable · click to retry";
            tip = error.message;
        }
        for (const [node, widget] of balanceWidgets) {
            widget.label = label;
            widget.name = label;
            widget.tooltip = tip;
            node.setDirtyCanvas(true, true);
        }
    })().finally(() => { balanceRequest = undefined; });
    return balanceRequest;
}

for (const event of ["execution_success", "execution_error", "execution_interrupted"]) {
    api.addEventListener(event, refreshBalance);
}

function catalog(refresh, kind) {
    if (!catalogRequests.has(kind) || refresh) {
        const request = api.fetchApi(`/coda-tools/openrouter/models?kind=${kind}&refresh=${refresh ? 1 : 0}`)
            .then(async (response) => {
                const body = await response.json();
                if (!response.ok) throw new Error(body.error || "Could not load models.");
                return body.models;
            }).catch((error) => { catalogRequests.delete(kind); throw error; });
        catalogRequests.set(kind, request);
    }
    return catalogRequests.get(kind);
}

async function loadModels(node, refresh = false, kind = "text") {
    try {
        const models = await catalog(refresh, kind);
        const ids = models.map((model) => model.id);
        const widget = node.widgets.find((item) => item.name === "model");
        if (!widget) return;
        // Preserve an older saved model, even if it is no longer in the catalog.
        widget.options.values = [...new Set([kind === "text" && DEFAULT_MODEL, ...ids, widget.value].filter(Boolean))];
        if (kind === "video" || kind === "speaking_video") {
            node._codaVideoModels = models;
            updateDurations();
        }
        node.setDirtyCanvas(true, true);
        if (refresh) app.extensionManager?.toast?.add({ severity: "success", summary: "OpenRouter models refreshed", detail: `${ids.length} ${kind} models available.` });
    } catch (error) {
        app.extensionManager?.toast?.add({ severity: "warn", summary: "OpenRouter model list unavailable", detail: error.message });
    }
}

app.registerExtension({
    name: "CodaComfyTools.OpenRouter",
    afterConfigureGraph: updateDurations,
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name === "CodaFrameStack") {
            for (const hook of ["onWidgetChanged", "onConnectionsChange", "onRemoved"]) {
                const original = nodeType.prototype[hook];
                nodeType.prototype[hook] = function (...args) {
                    const result = original?.apply(this, args);
                    updateDurations();
                    return result;
                };
            }
            return;
        }
        const kind = mediaKinds[nodeData.name];
        const timed = kind === "video" || kind === "speaking_video";
        const balanceOnly = nodeData.name === "CodaOpenRouterBalance";
        if (!kind && !balanceOnly && !["CodaOpenRouterPrompt", "CodaOpenRouterModels"].includes(nodeData.name)) return;
        const onAdded = nodeType.prototype.onAdded;
        nodeType.prototype.onAdded = function () {
            const result = onAdded?.apply(this, arguments);
            if (timed) registerVideo(this);
            const widget = this._codaBalance ??= this.addWidget("button", "Checking OpenRouter balance…", null, refreshBalance, { serialize: false });
            widget.serializeValue = () => undefined;
            balanceWidgets.set(this, widget);
            void refreshBalance();
            return result;
        };
        const onRemoved = nodeType.prototype.onRemoved;
        nodeType.prototype.onRemoved = function () {
            balanceWidgets.delete(this);
            if (timed) unregisterVideo(this);
            return onRemoved?.apply(this, arguments);
        };
        if (balanceOnly) return;
        if (timed) {
            const onConnectionsChange = nodeType.prototype.onConnectionsChange;
            nodeType.prototype.onConnectionsChange = function (...args) {
                const result = onConnectionsChange?.apply(this, args);
                updateDurations();
                return result;
            };
        }
        if (nodeData.name === "CodaOnScreenDialogue") {
            const onConfigure = nodeType.prototype.onConfigure;
            nodeType.prototype.onConfigure = function () {
                const result = onConfigure?.apply(this, arguments);
                const model = this.widgets?.find((w) => w.name === "model");
                if (model && !model.value) model.value = "google/veo-3.1-fast";
                if (["Veo 3.1 Fast | On-screen dialogue", "Veo 3.1 Fast - On-Screen Dialogue"].includes(this.title)) {
                    this.title = "OpenRouter On-Screen Dialogue";
                }
                return result;
            };
        }
        const onCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = onCreated?.apply(this, arguments);
            if (kind) {
                this.addWidget("button", "Refresh available models", null, () => loadModels(this, true, kind), { serialize: false });
                void loadModels(this, false, kind);
                return result;
            }
            if (nodeData.name === "CodaOpenRouterPrompt") {
                const systemPrompt = this.widgets?.find((widget) => widget.name === "system_prompt");
                if (systemPrompt) {
                    systemPrompt.label = "System Prompt";
                    if (systemPrompt.inputEl) {
                        systemPrompt.inputEl.placeholder = "System Prompt: role, rules, style, and output format";
                        systemPrompt.inputEl.title = "System Prompt — system instructions sent separately from your user prompt";
                        systemPrompt.inputEl.setAttribute("aria-label", "System Prompt (system instructions)");
                    }
                }
                const control = this.widgets?.find((widget) => widget.name === "control_after_generate");
                if (control) control.value = "fixed";
                this.addWidget("button", "Refresh available models", null, () => loadModels(this, true), { serialize: false });
                void loadModels(this);
            }
            const preview = ComfyWidgets.STRING(this, "response", ["STRING", { multiline: true }], app).widget;
            preview.inputEl.readOnly = true;
            preview.options.serialize = false;
            preview.serializeValue = () => undefined;
            this._codaPreview = preview;
            this.setSize([Math.max(this.size[0], 440), Math.max(this.size[1], 520)]);
            return result;
        };
        const onExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (message) {
            onExecuted?.apply(this, arguments);
            if (this._codaPreview) this._codaPreview.value = (message.text || []).join("\n");
            this.setDirtyCanvas(true, true);
        };
    },
});
