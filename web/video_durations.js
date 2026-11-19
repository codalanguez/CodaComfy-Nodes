// Keep the backend INT socket and its serialized value, including when a trailer
// shares that integer with the song trimmer. Only the editor is model-dependent.
const videos = new Set();
const menus = new Map();
let pending = false;
const providerDefault = "Model default";
const widget = (node, name) => node.widgets?.find((item) => item.name === name);

function hasPairedGuides(node) {
    if (!widget(node, "use_last_frame")?.value) return false;
    if (node.inputs?.some((input) => input.name === "last_frame" && input.link != null)) return true;
    let slot = node.inputs?.findIndex((input) => input.name === "frame_stack") ?? -1;
    let card = slot < 0 ? null : node.getInputNode(slot);
    let count = 0;
    const seen = new Set();
    while (card?.type === "CodaFrameStack" && !seen.has(card)) {
        seen.add(card);
        if (widget(card, "enabled")?.value && card.inputs?.some((i) => i.name === "frame" && i.link != null)) count++;
        if (count >= 2) return true;
        slot = card.inputs.findIndex((input) => input.name === "previous_frames");
        card = slot < 0 ? null : card.getInputNode(slot);
    }
    return false;
}

function offered(node) {
    const model = widget(node, "model")?.value;
    const row = node._codaVideoModels?.find((item) => item.id === model);
    const times = [...new Set(row?.supported_durations ?? [])]
        .filter((n) => Number.isInteger(n) && n > 0).sort((a, b) => a - b);
    const ending = hasPairedGuides(node);
    return ending && model?.startsWith("google/veo-3.1") ? times.filter((n) => n === 8) : times;
}

function closest(times, value) {
    // Prefer the longer shot when both adjacent durations are equally close.
    return times.reduce((best, n) => Math.abs(n - value) <= Math.abs(best - value) ? n : best);
}

function setNumber(owner, target, value) {
    if (Number(target.value) === value) return;
    target.value = value;
    target.callback?.call(target, value);
    owner.setDirtyCanvas(true, true);
}

function removeMenu(owner, menu) {
    owner.removeWidget(menu.picker);
    if (menu.hiddenTarget) menu.hiddenTarget.hidden = menu.wasHidden;
    menus.delete(owner);
    owner.setDirtyCanvas(true, true);
}

function showTimingInfo(node, times, explanation) {
    let menu = menus.get(node);
    if (menu?.target) {
        removeMenu(node, menu);
        menu = null;
    }
    const values = times.length ? times.map(String) : [explanation];
    if (!menu) {
        const picker = node.addCustomWidget({
            type: "combo", name: "Accepted times (seconds)", value: values[0], serialize: false,
            options: { values, serialize: false }, callback() {},
        });
        picker.serializeValue = () => undefined;
        menu = { picker };
        menus.set(node, menu);
    }
    menu.picker.options.values = values;
    menu.picker.value = values[0];
    menu.picker.tooltip = explanation;
    node.setDirtyCanvas(true, true);
}

function installMenu(owner, targetOwner, target, times, allowDefault, hideTarget) {
    let menu = menus.get(owner);
    if (menu && menu.target !== target) {
        removeMenu(owner, menu);
        menu = null;
    }
    if (!menu) {
        const picker = owner.addCustomWidget({
            type: "combo", name: "Shot length (seconds)", value: "", serialize: false,
            options: { values: [], serialize: false },
            callback(value) {
                setNumber(targetOwner, target, value === providerDefault ? 0 : Number(value));
                updateDurations();
            },
        });
        picker.serializeValue = () => undefined;
        menu = { picker, target, hiddenTarget: hideTarget ? target : null, wasHidden: target.hidden };
        menus.set(owner, menu);
        if (hideTarget) target.hidden = true;
    }
    const current = Number(target.value);
    const value = allowDefault && current === 0 ? 0 : closest(times, current);
    setNumber(targetOwner, target, value);
    menu.picker.options.values = [...(allowDefault ? [providerDefault] : []), ...times.map(String)];
    menu.picker.value = value === 0 ? providerDefault : String(value);
    owner.setDirtyCanvas(true, true);
}

function sync() {
    pending = false;
    const controllers = new Map();
    const active = new Set();
    for (const node of videos) {
        if (!node.graph) continue;
        const times = offered(node);
        if (node.type === "CodaOnScreenDialogue") {
            const seconds = widget(node, "seconds");
            if (!seconds) continue;
            seconds.options.values = times.length ? times.map(String) : [String(seconds.value)];
            seconds.label = times.length ? "Shot length (seconds)" : "Seconds (no list available)";
            seconds.tooltip = times.length ? "Supported seconds for this model and frame setup." : "No duration list available for this model.";
            if (times.length) seconds.value = String(closest(times, Number(seconds.value)));
            node.setDirtyCanvas(true, true);
            continue;
        }
        const input = node.inputs?.findIndex((slot) => slot.name === "duration_seconds") ?? -1;
        const wired = input >= 0 && node.inputs[input].link != null;
        const source = wired ? node.getInputNode(input) : node;
        const target = source && widget(source, wired ? "value" : "duration_seconds");
        // Computed timing links remain authoritative. Never overwrite an arbitrary upstream expression.
        if (!target || (wired && source.type !== "PrimitiveInt")) {
            active.add(node);
            showTimingInfo(node, times, times.length ? "Duration is supplied by the connected node." : "No fixed times listed");
            continue;
        }
        const shared = controllers.get(source);
        if (shared) {
            shared.nodes.push(node);
            if (times.length) shared.limits.push(times);
        } else controllers.set(source, { target, wired, nodes: [node], limits: times.length ? [times] : [] });
    }
    for (const [source, { target, wired, nodes, limits }] of controllers) {
        const times = (limits[0] ?? []).filter((n) => limits.every((list) => list.includes(n)));
        if (!times.length) {
            for (const node of nodes) {
                active.add(node);
                showTimingInfo(node, [], limits.length ? "No shared duration" : "No fixed times listed");
            }
            continue;
        }
        active.add(source);
        installMenu(source, source, target, times, !wired, true);
        if (wired) for (const node of nodes) {
            active.add(node);
            installMenu(node, source, target, times, false, false);
        }
    }
    for (const [owner, menu] of menus) if (!active.has(owner)) removeMenu(owner, menu);
}

export function updateDurations() {
    if (!pending) {
        pending = true;
        queueMicrotask(sync);
    }
}

export function registerVideo(node) {
    videos.add(node);
    for (const name of ["model", "use_last_frame"]) {
        const w = widget(node, name);
        if (!w) continue;
        const previous = w.callback;
        w.callback = function (...args) {
            const result = previous?.apply(this, args);
            updateDurations();
            return result;
        };
    }
    updateDurations();
}

export function unregisterVideo(node) {
    videos.delete(node);
    updateDurations();
}
