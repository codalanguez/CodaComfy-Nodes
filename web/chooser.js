import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { ChooserGallery } from "./gallery.js";

const pending = new Map();
const closed = new Set();
const panels = new Set();
let polling = false;
let revision = 0;

function sizeNode(node) {
    // Compact the previous release's default height when opening saved graphs.
    const height = node.size[1] === 800 ? 520 : Math.max(node.size[1], 520);
    node.setSize([Math.max(node.size[0], 560), height]);
}

function attachRequests() {
    for (const { node, gallery } of panels) {
        const request = [...pending.values()].find((item) => item.node_id === String(node.id));
        if (request) gallery.open(request);
    }
}

function closeRequest(token) {
    revision++;
    pending.delete(token);
    closed.add(token);
    if (closed.size > 100) closed.delete(closed.values().next().value);
    for (const { gallery } of panels) gallery.close(token);
}

function receive(request) {
    if (closed.has(request.token) || !Array.isArray(request.images) || request.images.length !== request.batch_size) return;
    pending.set(request.token, request);
    attachRequests();
}

async function recover() {
    if (polling || !api.clientId) return;
    polling = true;
    const before = revision;
    try {
        const response = await api.fetchApi(`/latent-chooser/pending?client_id=${encodeURIComponent(api.clientId)}`, {cache: "no-store"});
        if (!response.ok) return;
        const data = await response.json();
        if (before !== revision) return;
        const live = new Set(data.requests.map((request) => request.token));
        for (const token of pending.keys()) if (!live.has(token)) closeRequest(token);
        for (const request of data.requests) receive(request);
    } catch (error) {
        console.debug("Latent chooser recovery will retry:", error);
    } finally {
        polling = false;
    }
}

async function respond(request, index, cancel) {
    const response = await api.fetchApi("/latent-chooser/respond", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({token: request.token, client_id: api.clientId, index, cancel}),
    });
    if (!response.ok) {
        const body = await response.json();
        if (response.status === 404 || response.status === 409) closeRequest(request.token);
        throw new Error(body.error || "Could not send the selection.");
    }
    closeRequest(request.token);
}

app.registerExtension({
    name: "Coda.LatentImageChooser",
    async setup() {
        const stylesheet = document.createElement("link");
        stylesheet.rel = "stylesheet";
        stylesheet.href = new URL("./chooser.css?v=0.3.0", import.meta.url).href;
        document.head.append(stylesheet);
        api.addEventListener("latent-chooser-request", ({detail}) => {
            revision++;
            receive(detail);
        });
        api.addEventListener("latent-chooser-closed", ({detail}) => closeRequest(detail.token));
        api.addEventListener("status", recover);
        api.addEventListener("reconnected", recover);
        setInterval(recover, 5000);
        await recover();
    },
    afterConfigureGraph() {
        for (const { node } of panels) {
            sizeNode(node);
        }
        attachRequests();
        void recover();
    },
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== "LatentImageChooser") return;
        const original = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = original?.apply(this, arguments);
            const gallery = new ChooserGallery({
                imageUrl: ({ filename, subfolder, type }) => api.apiURL(`/view?${new URLSearchParams({ filename, subfolder, type })}`),
                onRespond: respond,
            });
            const node = this;
            const panel = { node, gallery };
            panels.add(panel);
            this.addDOMWidget("latent_chooser", "custom", gallery.root, {
                serialize: false,
                hideOnZoom: false,
                getMinHeight: () => 340,
                getMaxHeight: () => Math.max(340, node.size[1] - 150),
            });
            sizeNode(this);
            const removed = this.onRemoved;
            this.onRemoved = function () {
                panels.delete(panel);
                gallery.dispose();
                return removed?.apply(this, arguments);
            };
            attachRequests();
            return result;
        };
    },
});
