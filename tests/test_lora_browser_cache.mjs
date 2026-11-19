import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";
import {test} from "node:test";

const source = readFileSync(new URL("../web/lora_preview.js", import.meta.url), "utf8")
    .replace(/^import .*;\r?\n/gm, "").split("function openPicker")[0];

class Element {
    children = [];
    append(...items) { for (const item of items) { item.parent = this; this.children.push(item); } }
    replaceWith(item) { item.parent = this.parent; this.parent.children[this.parent.children.indexOf(this)] = item; }
    setAttribute(name, value) { this[name] = value; }
    removeAttribute(name) { delete this[name]; }
}

function setup() {
    let now = 1000;
    const requests = [];
    const timers = new Map();
    let timerId = 0;
    const context = vm.createContext({Intl, URLSearchParams, AbortController, Date: {now: () => now},
        document: {createElement: () => new Element()},
        setTimeout(fn) { timers.set(++timerId, fn); return timerId; },
        clearTimeout(id) { timers.delete(id); },
        api: {
        apiURL: path => path,
        fetchApi(path, options) {
            return new Promise(resolve => requests.push({path, options, resolve}));
        },
    }});
    vm.runInContext(source, context);
    return {context, requests, timers,
        expireRequests() { for (const [id, fn] of [...timers]) { timers.delete(id); fn(); } },
        advance(ms = 61000) { now += ms; },
        catalog: force => context.loadCatalog(force), details: name => context.loadDetails(name)};
}
const respond = (request, data, ok = true) => request.resolve({ok, status: ok ? 200 : 500, json: async () => data});

test("catalog requests share in-flight work, reuse fresh data, and explicitly refresh", async () => {
    const env = setup();
    const first = env.catalog(), second = env.catalog();
    assert.equal(first, second);
    assert.equal(env.requests.length, 1);
    respond(env.requests[0], {names: ["first"]});
    assert.deepEqual(await first, ["first"]);
    assert.deepEqual(await env.catalog(), ["first"]);
    assert.equal(env.requests.length, 1);
    const refresh = env.catalog(true);
    assert.equal(env.requests[1].path, "/coda/loras?refresh=1");
    respond(env.requests[1], {names: ["first", "new"]});
    await refresh;
    env.advance();
    const expired = env.catalog();
    assert.equal(env.requests.length, 3);
    respond(env.requests[2], {names: []});
    await expired;
});

test("a stalled trigger request times out, aborts, and can be retried", async () => {
    const env = setup();
    const stalled = env.details("test");
    const rejected = assert.rejects(stalled, /timed out/);
    env.expireRequests();
    await rejected;
    assert.equal(env.requests[0].options.signal.aborted, true);
    const retry = env.details("test");
    respond(env.requests[1], {count: 1, triggers: ["recovered"]});
    assert.deepEqual((await retry).triggers, ["recovered"]);
    assert.equal(env.timers.size, 0);
});

test("switching previews ignores stale images and metadata; closing cancels image work", async () => {
    const env = setup();
    const panel = env.context.imagePanel();
    const first = panel.show("first");
    const oldImage = panel.root.children[0];
    const oldLoad = oldImage.onload;
    const second = panel.show("second");
    const newImage = panel.root.children[0];
    const status = panel.root.children.find(el => el.className === "coda-lora-status");
    const text = panel.root.children.at(-1).children[1];
    oldLoad();
    assert.equal(status.hidden, false);
    assert.equal(newImage.hidden, true);
    assert.equal(oldImage.src, undefined);
    assert.equal(newImage.fetchPriority, "high");
    respond(env.requests[1], {count: 1, triggers: ["second"]});
    await second;
    newImage.onload();
    respond(env.requests[0], {count: 1, triggers: ["first"]});
    await first;
    assert.equal(text.value, "second");
    assert.equal(status.hidden, true);
    panel.dispose();
    assert.equal(newImage.src, undefined);
    assert.equal(env.timers.size, 0);
});

test("stalled preview and triggers leave loading state and offer a working retry", async () => {
    const env = setup();
    const panel = env.context.imagePanel();
    const stalled = panel.show("test");
    env.expireRequests();
    await stalled;
    const status = panel.root.children.find(el => el.className === "coda-lora-status");
    const retry = panel.root.children.find(el => el.textContent === "Retry preview and triggers");
    assert.match(status.textContent, /timed out/);
    assert.equal(retry.hidden, false);
    const retried = retry.onclick();
    respond(env.requests[1], {count: 1, triggers: ["recovered"]});
    await retried;
    panel.root.children[0].onload();
    assert.equal(status.hidden, true);
    assert.equal(retry.hidden, true);
    assert.equal(panel.root.children.at(-1).children[1].value, "recovered");
    assert.equal(env.timers.size, 0);
});

test("failed catalog requests can be retried", async () => {
    const env = setup();
    const first = env.catalog();
    respond(env.requests[0], null, false);
    await assert.rejects(first, /HTTP 500/);
    const retry = env.catalog();
    assert.equal(env.requests.length, 2);
    respond(env.requests[1], {names: []});
    await retry;
});

test("refresh invalidates trigger results even without an open dialog", async () => {
    const env = setup();
    const first = env.details("test");
    respond(env.requests[0], {count: 0, triggers: ["old"]});
    await first;
    const refresh = env.catalog(true);
    respond(env.requests[1], {names: ["test"]});
    await refresh;
    const updated = env.details("test");
    assert.equal(env.requests.length, 3);
    respond(env.requests[2], {count: 0, triggers: ["new"]});
    assert.deepEqual((await updated).triggers, ["new"]);
});

test("trigger cache shares requests, expires, and retries errors", async () => {
    const env = setup();
    const first = env.details("test"), second = env.details("test");
    assert.equal(first, second);
    respond(env.requests[0], {count: 1, triggers: ["word"]});
    await first;
    await env.details("test");
    assert.equal(env.requests.length, 1);
    env.advance();
    await env.details("test");
    assert.equal(env.requests.length, 1, "triggers stay cached beyond one minute");
    env.advance(86400001);
    const expired = env.details("test");
    respond(env.requests[1], null, false);
    await assert.rejects(expired, /HTTP 500/);
    const retry = env.details("test");
    assert.equal(env.requests.length, 3);
    respond(env.requests[2], {count: 0, triggers: []});
    await retry;
});
