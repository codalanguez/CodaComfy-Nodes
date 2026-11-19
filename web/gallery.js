const PAGE_SIZE = 48;
let popupCount = 0;

function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
}

// Batch selection stays on the canvas; thumbnail clicks open an image lightbox.
export class ChooserGallery {
    constructor({ imageUrl, onRespond }) {
        this.imageUrl = imageUrl;
        this.onRespond = onRespond;
        this.root = element("section", "lc-node");
        this.root.setAttribute("aria-label", "Latent image chooser");
        for (const name of ["pointerdown", "mousedown", "dblclick", "wheel", "keydown"]) {
            this.root.addEventListener(name, (event) => event.stopPropagation());
        }
        this.description = element("p", "lc-description", "Queue the workflow to preview its latent batch here.");
        this.grid = element("div", "lc-grid");
        this.grid.setAttribute("aria-label", "Batch previews");
        this.preview = element("img", "lc-large-image");
        this.preview.alt = "Selected image preview";
        this.popup = element("dialog", "lc-image-popup");
        this.popupTitle = element("h2", "lc-popup-title", "Image preview");
        this.preview.addEventListener("load", () => {
            this.popupTitle.textContent = `Image ${this.selected + 1} · batch index ${this.selected} · ${this.preview.naturalWidth} × ${this.preview.naturalHeight}`;
        });
        this.popupTitle.id = `lc-popup-title-${++popupCount}`;
        this.popup.setAttribute("aria-labelledby", this.popupTitle.id);
        this.popupClose = this.button("Close image preview", () => this.hidePreview());
        const popupHeader = element("div", "lc-popup-header");
        popupHeader.append(this.popupTitle, this.popupClose);
        this.popup.append(popupHeader, this.preview,
            element("p", "lc-popup-hint", "Close this preview, then Continue with selection in the workflow node."));
        this.popup.addEventListener("cancel", (event) => {
            event.preventDefault();
            this.hidePreview();
        });
        this.popup.addEventListener("click", (event) => {
            if (event.target === this.popup) this.hidePreview();
        });
        this.popup.addEventListener("keydown", (event) => event.stopPropagation());
        document.body.append(this.popup);
        this.message = element("p", "lc-message", "Waiting for a latent batch.");
        this.message.setAttribute("role", "status");
        this.previous = this.button("Previous page", () => this.changePage(-1));
        this.next = this.button("Next page", () => this.changePage(1));
        this.pageLabel = element("span", "lc-page", "No batch");
        const navigation = element("div", "lc-navigation");
        navigation.append(this.previous, this.pageLabel, this.next);
        this.cancel = this.button("Cancel this run", () => this.respond(true));
        this.confirm = this.button("Continue with selection", () => this.respond(false));
        this.confirm.classList.add("lc-primary");
        const footer = element("div", "lc-footer");
        footer.append(this.cancel, this.confirm);
        this.root.append(this.description, this.grid, navigation, this.message, footer);
        this.updateControls();
    }

    button(label, action) {
        const button = element("button", "lc-button", label);
        button.type = "button";
        button.addEventListener("click", (event) => {
            event.stopPropagation();
            action();
        });
        return button;
    }

    open(request) {
        if (this.request?.token === request.token) return;
        this.request = request;
        this.selected = null;
        this.page = 0;
        this.busy = false;
        this.cancelling = false;
        this.hidePreview();
        this.preview.removeAttribute("src");
        this.description.textContent = `${request.batch_size} ${request.batch_size === 1 ? "image" : "images"} · Workflow paused`;
        this.message.textContent = "Click an image to inspect it, then continue with your selection.";
        this.render();
    }

    close(token) {
        if (this.request?.token !== token) return;
        this.request = null;
        this.hidePreview();
        this.description.textContent = "Run ended · Queue again for a new selection";
        this.message.textContent = this.busy && !this.cancelling
            ? `Image ${this.selected + 1} selected (batch index ${this.selected}). Selection sent.`
            : "This run ended. No further selection can be submitted.";
        this.busy = false;
        this.updateControls();
    }

    hidePreview() {
        this.popup.close();
    }

    changePage(delta) {
        if (this.busy || !this.request) return;
        this.page = Math.max(0, Math.min(Math.ceil(this.request.batch_size / PAGE_SIZE) - 1, this.page + delta));
        this.render();
    }

    render() {
        const start = this.page * PAGE_SIZE;
        const end = Math.min(start + PAGE_SIZE, this.request.batch_size);
        this.grid.replaceChildren();
        this.request.images.slice(start, end).forEach((descriptor, offset) => {
            const index = start + offset;
            const card = this.button("", () => {
                if (this.busy || !this.request) return;
                this.selected = index;
                this.preview.src = this.imageUrl(descriptor.full_size ?? descriptor);
                this.preview.alt = `Large preview of image ${index + 1}, batch index ${index}`;
                this.popupTitle.textContent = `Image ${index + 1} · batch index ${index}`;
                if (!this.popup.open) this.popup.showModal();
                this.message.textContent = `Image ${index + 1} selected (batch index ${index}). Workflow still paused.`;
                for (const item of this.grid.children) {
                    item.setAttribute("aria-pressed", String(Number(item.dataset.index) === index));
                }
                this.updateControls();
            });
            card.className = "lc-card";
            card.dataset.index = String(index);
            card.setAttribute("aria-label", `Select image ${index + 1}, batch index ${index}`);
            card.setAttribute("aria-pressed", String(this.selected === index));
            const image = element("img");
            image.src = this.imageUrl(descriptor);
            image.alt = `Preview of image ${index + 1}`;
            image.loading = "lazy";
            card.append(image, element("span", "lc-card-label", `${index + 1} · index ${index}`));
            this.grid.append(card);
        });
        this.pageLabel.textContent = `${start + 1}–${end} of ${this.request.batch_size}`;
        this.updateControls();
    }

    updateControls() {
        const inactive = !this.request || this.busy;
        this.confirm.disabled = inactive || this.selected === null;
        this.cancel.disabled = inactive;
        this.previous.disabled = inactive || this.page === 0;
        this.next.disabled = inactive || (this.page + 1) * PAGE_SIZE >= this.request.batch_size;
        for (const card of this.grid.children) card.disabled = inactive;
    }

    async respond(cancel) {
        if (this.busy || !this.request || (!cancel && this.selected === null)) return;
        const request = this.request;
        this.busy = true;
        this.cancelling = cancel;
        this.updateControls();
        this.message.textContent = cancel ? "Cancelling this run…" : "Resuming workflow…";
        try {
            await this.onRespond(request, this.selected, cancel);
            this.close(request.token);
        } catch (error) {
            if (this.request?.token === request.token) {
                this.message.textContent = error.message || "Could not send the choice. Please try again.";
            }
        } finally {
            if (this.request?.token === request.token) {
                this.busy = false;
                this.updateControls();
            }
        }
    }

    dispose() {
        this.hidePreview();
        this.preview.removeAttribute("src");
        this.popup.remove();
        this.root.remove();
    }
}
