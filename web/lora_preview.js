import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

let previewVersion = Date.now();
const imageURL = (name, index = 0, size = 512) => api.apiURL(`/coda/loras/preview?${new URLSearchParams({name, index, size, v: previewVersion})}`);
const collator = new Intl.Collator(undefined, {numeric: true, sensitivity: "base"});
let catalogNames = [], catalogExpires = 0, catalogRequest;
const detailRequests = new Map();
const requestTimeout = 15000;
async function fetchJSON(path, timeout = requestTimeout) {
    const controller = new AbortController();
    let timer;
    try {
        return await Promise.race([
            (async () => {
                const response = await api.fetchApi(path, {signal: controller.signal, priority: "high"});
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                return response.json();
            })(),
            new Promise((_, reject) => {
                timer = setTimeout(() => {
                    reject(new Error("Request timed out. Please retry."));
                    controller.abort();
                }, timeout);
            }),
        ]);
    } finally { clearTimeout(timer); }
}
function loadCatalog(refresh = false) {
    if (catalogRequest) return catalogRequest;
    if (!refresh && Date.now() < catalogExpires) return Promise.resolve(catalogNames);
    catalogRequest = fetchJSON(`/coda/loras${refresh ? "?refresh=1" : ""}`, 60000)
        .then(data => {
            if (refresh) {
                detailRequests.clear();
                previewVersion = Math.max(Date.now(), previewVersion + 1);
            }
            catalogNames = data.names;
            catalogExpires = Date.now() + 60000;
            return catalogNames;
        }).finally(() => { catalogRequest = null; });
    return catalogRequest;
}
function loadDetails(name) {
    const cached = detailRequests.get(name);
    if (cached && Date.now() < cached.expires) return cached.promise;
    const entry = {expires: Date.now() + 86400000};
    entry.promise = fetchJSON(`/coda/loras/details?${new URLSearchParams({name})}`)
        .catch(error => { if (detailRequests.get(name) === entry) detailRequests.delete(name); throw error; });
    detailRequests.delete(name);
    detailRequests.set(name, entry);
    if (detailRequests.size > 512) detailRequests.delete(detailRequests.keys().next().value);
    return entry.promise;
}
let slotContentId = 0;
function element(tag, className, text) {
    const el = document.createElement(tag);
    el.className = className;
    if (text) el.textContent = text;
    return el;
}
function button(text, action) {
    const el = element("button", "coda-lora-button", text);
    el.type = "button";
    el.onclick = action;
    return el;
}
function imagePanel() {
    const root = element("div", "coda-lora-image-panel");
    let image = element("img", "coda-lora-image");
    image.alt = "LoRA preview";
    image.hidden = true;
    const status = element("div", "coda-lora-status", "Choose a LoRA to see its preview");
    const caption = element("div", "coda-lora-caption");
    const nav = element("div", "coda-lora-nav");
    nav.hidden = true;
    const counter = element("span", "");
    const triggers = element("div", "coda-lora-triggers");
    const triggerLabel = element("strong", "", "Trigger words");
    const triggerText = element("textarea", "coda-lora-trigger-text");
    triggerText.readOnly = true;
    triggerText.setAttribute("aria-label", "Trigger words");
    const copy = button("Copy triggers", async () => {
        try { await navigator.clipboard.writeText(triggerText.value); copy.textContent = "Copied"; }
        catch { triggerText.focus(); triggerText.select(); copy.textContent = "Press Ctrl+C to copy"; }
    });
    copy.disabled = true;
    triggers.append(triggerLabel, triggerText, copy);
    let current = "", index = 0, count = 0, revision = 0, imageRevision = 0, imageTimer;
    const retry = button("Retry preview and triggers", () => panel.show(current));
    retry.hidden = true;
    const previous = button("‹", () => showIndex(index - 1));
    const next = button("›", () => showIndex(index + 1));
    previous.title = "Previous attached image";
    next.title = "Next attached image";
    nav.append(previous, counter, next);
    root.append(image, status, retry, caption, nav, triggers);
    function cancelImage() {
        ++imageRevision;
        clearTimeout(imageTimer);
        image.onload = image.onerror = null;
        image.removeAttribute("src");
    }
    function showIndex(value) {
        cancelImage();
        const token = imageRevision;
        index = (value + Math.max(1, count)) % Math.max(1, count);
        // Each request owns its element/events; an old response cannot reveal
        // the previous LoRA or clear the current request's loading state.
        const nextImage = element("img", "coda-lora-image");
        nextImage.alt = "LoRA preview";
        nextImage.loading = "eager";
        nextImage.fetchPriority = "high";
        image.replaceWith(nextImage);
        image = nextImage;
        image.hidden = true;
        status.hidden = false;
        status.textContent = "Loading preview…";
        counter.textContent = count ? `${index + 1} / ${count}` : "";
        image.onload = () => {
            if (token !== imageRevision) return;
            clearTimeout(imageTimer);
            image.hidden = false;
            status.hidden = true;
        };
        image.onerror = () => {
            if (token !== imageRevision) return;
            clearTimeout(imageTimer);
            image.hidden = true;
            status.hidden = false;
            status.textContent = "Preview unavailable or no readable image attached";
            retry.hidden = false;
        };
        imageTimer = setTimeout(() => {
            if (token !== imageRevision) return;
            cancelImage();
            status.textContent = "Preview request timed out. Please retry.";
            retry.hidden = false;
        }, requestTimeout);
        image.src = imageURL(current, index);
    }
    const panel = {
        root,
        dispose() { ++revision; cancelImage(); },
        async show(name) {
            const token = ++revision;
            current = name;
            triggerText.value = "";
            triggerText.placeholder = "Loading trigger words…";
            copy.disabled = true;
            copy.textContent = "Copy triggers";
            count = 0;
            caption.textContent = name;
            caption.title = name;
            nav.hidden = true;
            retry.hidden = true;
            if (!name) { cancelImage(); image.hidden = true; status.hidden = false; status.textContent = "No LoRA selected"; triggerText.placeholder = "No LoRA selected"; return; }
            showIndex(0);
            try {
                const data = await loadDetails(name);
                if (token !== revision) return;
                count = data.count;
                triggerText.value = (data.triggers || []).join(", ");
                triggerText.placeholder = "No trigger words recorded";
                copy.disabled = !triggerText.value;
                nav.hidden = count < 2;
                counter.textContent = count ? `1 / ${count}` : "";
            } catch (error) {
                if (token !== revision) return;
                triggerText.placeholder = `Trigger metadata unavailable: ${error.message}`;
                retry.hidden = false;
            }
        },
    };
    return panel;
}

function openPicker(widget, commit) {
    const dialog = element("dialog", "coda-lora-dialog");
    const header = element("div", "coda-lora-toolbar");
    const title = element("strong", "", "Browse LoRAs");
    const search = element("input", "coda-lora-search");
    search.type = "search";
    search.placeholder = "Search in the selected folder…";
    search.setAttribute("aria-label", "Search LoRAs");
    let closed = false;
    const close = () => { closed = true; cleanup(); dialog.close(); dialog.remove(); };
    const body = element("div", "coda-lora-browser");
    const list = element("div", "coda-lora-list");
    list.tabIndex = 0;
    list.setAttribute("role", "listbox");
    list.setAttribute("aria-label", "LoRAs; arrow keys browse, Enter selects");
    const spacer = element("div", "coda-lora-spacer");
    list.append(spacer);
    const panel = imagePanel();
    // Native lazy loading can start every thumbnail on a page in advance.
    // Only load rows near the viewport so the selected preview gets a turn.
    const thumbnails = new IntersectionObserver(entries => {
        for (const entry of entries) {
            if (!entry.isIntersecting) continue;
            entry.target.src = entry.target.dataset.src;
            thumbnails.unobserve(entry.target);
        }
    }, {root: list, rootMargin: "84px"});
    function clearThumbnails() {
        thumbnails.disconnect();
        for (const thumb of spacer.querySelectorAll("img")) thumb.removeAttribute("src");
    }
    function cleanup() { clearTimeout(previewTimer); panel.dispose(); clearThumbnails(); }
    const footer = element("div", "coda-lora-toolbar");
    const summary = element("span", "coda-lora-summary");
    const use = button("Use this LoRA", () => select());
    let names = Array.from(widget.options?.values || []).filter(n => typeof n === "string");
    const directoryOf = name => name.replaceAll("\\", "/").split("/").slice(0, -1).join("/");
    let directory = widget.value ? directoryOf(widget.value) : "";
    let filtered = [], active = 0, page = 0;
    const folderBar = element("div", "coda-lora-folder-bar");
    const folderLevels = element("div", "coda-lora-folder-levels");
    const recursiveLabel = element("label", "coda-lora-recursive");
    const recursive = element("input", "");
    recursive.type = "checkbox";
    recursive.checked = true;
    recursiveLabel.append(recursive, document.createTextNode("Include subfolders"));
    folderBar.append(element("strong", "", "Folder"), folderLevels, recursiveLabel);
    const pagination = element("div", "coda-lora-pagination");
    const pageLabel = element("span", "coda-lora-page-label");
    pageLabel.setAttribute("aria-live", "polite");
    const pageSize = element("select", "coda-lora-select");
    pageSize.setAttribute("aria-label", "LoRAs per page");
    for (const size of [12, 24, 48]) {
        const option = element("option", "", `${size} per page`);
        option.value = String(size);
        pageSize.append(option);
    }
    pageSize.value = "24";
    const previousPage = button("Previous", () => changePage(page - 1));
    const nextPage = button("Next", () => changePage(page + 1));
    previousPage.setAttribute("aria-label", "Previous results page");
    nextPage.setAttribute("aria-label", "Next results page");
    pagination.append(previousPage, pageLabel, nextPage, pageSize);
    let hovered = "", previewTimer;
    const rowHeight = 84;
    function folderControls() {
        const directories = [...new Set(names.map(directoryOf))];
        if (names.length && directory && !directories.some(path => path === directory || path.startsWith(directory + "/"))) directory = "";
        const selectedParts = directory ? directory.split("/") : [];
        folderLevels.replaceChildren();
        for (let depth = 0; depth <= selectedParts.length; depth++) {
            const parent = selectedParts.slice(0, depth).join("/");
            const prefix = parent ? parent + "/" : "";
            const children = [...new Set(directories.filter(path => path.startsWith(prefix))
                .map(path => path.slice(prefix.length).split("/")[0]).filter(Boolean))]
                .sort(collator.compare);
            if (!children.length && depth) break;
            const select = element("select", "coda-lora-select");
            select.setAttribute("aria-label", depth ? `Subfolder of ${parent}` : "LoRA folder");
            const all = element("option", "", depth ? "This folder" : "All folders");
            all.value = "";
            select.append(all);
            for (const child of children) {
                const option = element("option", "", child);
                option.value = child;
                select.append(option);
            }
            select.value = selectedParts[depth] || "";
            select.onchange = () => {
                directory = [...selectedParts.slice(0, depth), ...(select.value ? [select.value] : [])].join("/");
                folderControls();
                filter();
            };
            if (depth) folderLevels.append(element("span", "", "›"));
            folderLevels.append(select);
        }
    }
    function changePage(value) {
        const total = Math.max(1, Math.ceil(filtered.length / Number(pageSize.value)));
        page = Math.max(0, Math.min(total - 1, value));
        active = page * Number(pageSize.value);
        list.scrollTop = 0;
        preview(filtered[active] || "");
        render();
    }
    function preview(name) {
        if (hovered === name) return;
        hovered = name;
        clearTimeout(previewTimer);
        previewTimer = setTimeout(() => panel.show(name), 80);
    }
    function select() {
        if (!filtered[active]) return;
        commit(filtered[active]);
        close();
    }
    function render() {
        clearThumbnails();
        const size = Number(pageSize.value);
        const first = page * size;
        const end = Math.min(filtered.length, first + size);
        spacer.style.height = `${(end - first) * rowHeight}px`;
        const rows = [];
        for (let i = first; i < end; i++) {
            const name = filtered[i];
            const row = element("div", `coda-lora-row${i === active ? " is-active" : ""}`);
            row.style.top = `${(i - first) * rowHeight}px`;
            row.id = `coda-lora-option-${i}`;
            row.setAttribute("role", "option");
            row.setAttribute("aria-selected", String(i === active));
            row.title = name;
            const thumb = element("img", "coda-lora-thumb");
            thumb.alt = "";
            thumb.loading = "lazy";
            thumb.fetchPriority = "low";
            thumb.dataset.src = imageURL(name, 0, 128);
            const empty = element("span", "coda-lora-no-thumb", "No preview");
            empty.hidden = true;
            thumb.onerror = () => { thumb.hidden = true; empty.hidden = false; };
            const text = element("div", "coda-lora-row-text");
            const parts = name.replaceAll("\\", "/").split("/");
            text.append(element("strong", "", parts.pop()), element("small", "", parts.join(" / ")));
            row.append(thumb, empty, text);
            row.onpointerenter = () => preview(name);
            row.onclick = () => {
                active = i;
                preview(name);
                for (const item of spacer.children) {
                    const selected = item === row;
                    item.classList.toggle("is-active", selected);
                    item.setAttribute("aria-selected", String(selected));
                }
                list.setAttribute("aria-activedescendant", row.id);
                list.focus();
            };
            row.ondblclick = () => { active = i; select(); };
            rows.push(row);
        }
        spacer.replaceChildren(...rows);
        for (const thumb of spacer.querySelectorAll("img")) thumbnails.observe(thumb);
        if (filtered.length) list.setAttribute("aria-activedescendant", `coda-lora-option-${active}`);
        else list.removeAttribute("aria-activedescendant");
        summary.textContent = filtered.length ? `${first + 1}–${end} of ${filtered.length.toLocaleString()} LoRAs · ${directory || "All folders"}` : `No matching LoRAs in ${directory || "All folders"}`;
        const pages = Math.ceil(filtered.length / size);
        pageLabel.textContent = pages ? `Page ${page + 1} of ${pages}` : "Page 0 of 0";
        previousPage.disabled = page === 0;
        nextPage.disabled = page + 1 >= pages;
        use.disabled = !filtered.length;
    }
    function filter(keepSelection = false) {
        const query = search.value.toLocaleLowerCase().trim();
        filtered = names.filter(name => {
            const path = directoryOf(name);
            const inFolder = recursive.checked ? (!directory || path === directory || path.startsWith(directory + "/")) : path === directory;
            return inFolder && name.toLocaleLowerCase().includes(query);
        }).sort(collator.compare);
        active = keepSelection ? Math.max(0, filtered.indexOf(widget.value)) : 0;
        page = Math.floor(active / Number(pageSize.value));
        preview(filtered[active] || "");
        render();
        list.scrollTop = (active % Number(pageSize.value)) * rowHeight;
    }
    async function updateCatalog(force = false) {
        refresh.disabled = true;
        if (!names.length) summary.textContent = "Loading LoRAs…";
        try {
            const updated = await loadCatalog(force);
            if (closed) return;
            if (!force && updated === widget.options.values) return;
            names = updated;
            widget.options.values = names;
            hovered = "";
            folderControls();
            filter(true);
        } catch (error) { summary.textContent = `Could not refresh LoRAs: ${error.message}`; }
        finally { refresh.disabled = false; }
    }
    const refresh = button("Refresh", () => updateCatalog(true));
    header.append(title, search, refresh, button("Close", close));
    footer.append(summary, use);
    body.append(list, panel.root);
    dialog.append(header, folderBar, body, pagination, footer);
    search.oninput = () => filter();
    recursive.onchange = () => filter();
    pageSize.onchange = () => changePage(0);
    dialog.addEventListener("keydown", event => {
        if (event.target !== search && event.target !== list) return;
        if (["PageDown", "PageUp"].includes(event.key)) {
            event.preventDefault();
            changePage(page + (event.key === "PageDown" ? 1 : -1));
        } else if (["ArrowDown", "ArrowUp"].includes(event.key)) {
            event.preventDefault();
            const delta = event.key === "ArrowDown" ? 1 : -1;
            active = Math.max(0, Math.min(filtered.length - 1, active + delta));
            page = Math.floor(active / Number(pageSize.value));
            const y = (active % Number(pageSize.value)) * rowHeight;
            render();
            if (y < list.scrollTop) list.scrollTop = y;
            if (y + rowHeight > list.scrollTop + list.clientHeight) list.scrollTop = y + rowHeight - list.clientHeight;
            preview(filtered[active] || "");
        } else if (event.key === "Enter" && (event.target === search || event.target === list)) {
            event.preventDefault(); select();
        }
    });
    dialog.addEventListener("close", () => { closed = true; cleanup(); dialog.remove(); }, {once: true});
    document.body.append(dialog);
    dialog.showModal();
    folderControls();
    filter(true);
    updateCatalog();
    search.focus();
    return close;
}

app.registerExtension({
    name: "Coda.LoRAPreview",
    setup() {
        const css = document.createElement("link");
        css.rel = "stylesheet";
        css.href = new URL("./lora_preview.css", import.meta.url).href;
        document.head.append(css);
    },
    beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== "CodaLoraStackPreview") return;
        const original = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = original?.apply(this, arguments);
            const node = this;
            const state = this.widgets.find(w => w.name === "loras");
            const mode = this.widgets.find(w => w.name === "mode");
            const toggle = this.widgets.find(w => w.name === "toggle");
            if (!state) return result;
            // Keep the JSON as a normal serialized STRING input for API workflows.
            state.type = "converted-widget";
            state.hidden = true;
            state.computeSize = () => [0, -4];
            state.draw = () => {};
            const root = element("div", "coda-lora-stack");
            const toolbar = element("div", "coda-lora-toolbar");
            const summary = element("span", "coda-lora-summary");
            const slots = element("div", "coda-lora-slots");
            let entries = [], closePicker, revision = 0;
            const catalog = {value: "", options: {values: []}};
            function save() {
                node.graph?.beforeChange?.();
                state.value = JSON.stringify(entries);
                node.graph?.afterChange?.();
                node.setDirtyCanvas(true, true);
            }
            function browse(entry) {
                closePicker?.();
                catalog.value = entry.name;
                catalog.options.values = catalogNames;
                closePicker = openPicker(catalog, name => { entry.name = name; save(); render(); });
            }
            const add = button("+ Add LoRA", () => {
                if (entries.length >= 10) return;
                entries.push({name: "", enabled: true, strength: 1, clip_strength: 1});
                save(); render();
            });
            toolbar.append(add, summary);
            root.append(toolbar, slots);
            function render() {
                const token = ++revision;
                summary.textContent = `${entries.length} / 10 slots${toggle.value ? "" : " · Stack disabled"}`;
                add.disabled = entries.length >= 10;
                slots.replaceChildren();
                entries.forEach((entry, index) => {
                    const card = element("div", "coda-lora-slot");
                    const top = element("div", "coda-lora-toolbar");
                    const enabled = element("input", "");
                    enabled.type = "checkbox";
                    enabled.checked = entry.enabled !== false;
                    enabled.setAttribute("aria-label", `Enable LoRA ${index + 1}`);
                    enabled.onchange = () => { entry.enabled = enabled.checked; save(); render(); };
                    const choose = button(entry.name || "Choose LoRA…", () => browse(entry));
                    choose.classList.add("coda-lora-choose");
                    choose.title = entry.name || "Browse with previews and triggers";
                    const remove = button("×", () => { entries.splice(index, 1); save(); render(); });
                    remove.setAttribute("aria-label", `Remove LoRA ${index + 1}`);
                    const content = element("div", "coda-lora-slot-content");
                    content.id = `coda-lora-slot-content-${++slotContentId}`;
                    const fold = button("", () => {
                        entry.collapsed = !entry.collapsed;
                        updateFold();
                        if (!entry.collapsed) loadContent();
                        save();
                    });
                    fold.classList.add("coda-lora-fold");
                    fold.setAttribute("aria-controls", content.id);
                    function updateFold() {
                        const collapsed = entry.collapsed === true;
                        content.hidden = collapsed;
                        fold.textContent = collapsed ? "▸" : "▾";
                        fold.setAttribute("aria-expanded", String(!collapsed));
                        fold.setAttribute("aria-label", `${collapsed ? "Expand" : "Collapse"} LoRA ${index + 1}`);
                        fold.title = `${collapsed ? "Show" : "Hide"} preview, strengths, and triggers`;
                    }
                    updateFold();
                    top.append(fold, enabled, element("strong", "", `${index + 1}`), choose, remove);
                    const thumb = element("img", "coda-lora-slot-thumb");
                    thumb.alt = "Attached LoRA preview";
                    thumb.title = "Click to browse full previews";
                    thumb.onclick = () => browse(entry);
                    thumb.hidden = !entry.name;
                    thumb.onerror = () => { thumb.hidden = true; };
                    const controls = element("div", "coda-lora-slot-controls");
                    const weights = element("div", "coda-lora-toolbar");
                    function weight(labelText, key) {
                        const label = element("label", "coda-lora-weight", labelText);
                        const input = element("input", "");
                        input.type = "number";
                        input.min = "-10"; input.max = "10"; input.step = "0.01";
                        input.value = entry[key] ?? 1;
                        input.setAttribute("aria-label", `${labelText} for LoRA ${index + 1}`);
                        input.onchange = () => {
                            const value = Number(input.value);
                            if (input.value.trim() === "" || !Number.isFinite(value)) { input.value = entry[key] ?? 1; return; }
                            entry[key] = Math.max(-10, Math.min(10, value));
                            input.value = entry[key]; save();
                        };
                        label.append(input);
                        return label;
                    }
                    weights.append(weight(mode.value === "simple" ? "Strength" : "Model", "strength"));
                    if (mode.value === "advanced") weights.append(weight("CLIP", "clip_strength"));
                    const triggers = element("textarea", "coda-lora-trigger-text");
                    triggers.readOnly = true;
                    triggers.setAttribute("aria-label", `Triggers for LoRA ${index + 1}`);
                    triggers.placeholder = entry.name ? "Loading trigger words…" : "Choose a LoRA to see trigger words";
                    const copy = button("Copy triggers", async () => {
                        try { await navigator.clipboard.writeText(triggers.value); copy.textContent = "Copied"; }
                        catch { triggers.focus(); triggers.select(); copy.textContent = "Press Ctrl+C"; }
                    });
                    copy.disabled = true;
                    let contentLoaded = false;
                    function loadContent() {
                        if (!entry.name || contentLoaded) return;
                        contentLoaded = true;
                        thumb.src = imageURL(entry.name, 0, 128);
                        loadDetails(entry.name)
                            .then(data => {
                                if (token !== revision) return;
                                triggers.value = (data.triggers || []).join(", ");
                                triggers.placeholder = "No trigger words recorded";
                                copy.disabled = !triggers.value;
                            }).catch(() => { if (token === revision) triggers.placeholder = "Trigger metadata unavailable"; });
                    }
                    if (!entry.collapsed) loadContent();
                    controls.append(weights, triggers, copy);
                    content.append(thumb, controls);
                    card.append(top, content);
                    card.classList.toggle("is-disabled", entry.enabled === false || !toggle.value);
                    slots.append(card);
                });
            }
            function restore() {
                try {
                    const parsed = JSON.parse(state.value || "[]");
                    if (!Array.isArray(parsed) || parsed.length > 10 || parsed.some(v => !v || typeof v !== "object" || Array.isArray(v))) throw new Error();
                    entries = parsed;
                    render();
                } catch { summary.textContent = "Saved stack data is invalid. Rebuild the slots to replace it."; }
            }
            for (const widget of [mode, toggle]) {
                const callback = widget.callback;
                widget.callback = function () { const result = callback?.apply(this, arguments); render(); return result; };
            }
            root.addEventListener("pointerdown", e => e.stopPropagation());
            root.addEventListener("wheel", e => e.stopPropagation());
            this.addDOMWidget("lora_stack_preview", "custom", root, {
                serialize: false, hideOnZoom: false,
                getMinHeight: () => 290,
                getMaxHeight: () => Math.max(290, node.size[1] - 110),
            });
            const configure = this.onConfigure;
            this.onConfigure = function () { const result = configure?.apply(this, arguments); restore(); return result; };
            const removed = this.onRemoved;
            this.onRemoved = function () { closePicker?.(); return removed?.apply(this, arguments); };
            this.setSize([Math.max(460, this.size[0]), Math.max(540, this.size[1])]);
            restore();
            return result;
        };
    },
});
