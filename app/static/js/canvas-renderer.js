const SVG_NS = "http://www.w3.org/2000/svg";

class CanvasRenderer {
    constructor({ container, imageEl, guideSvg, candidateSvg, finalSvg, previewSvg }) {
        this.container = container;
        this.imageEl = imageEl;
        this.guideSvg = guideSvg;
        this.candidateSvg = candidateSvg;
        this.finalSvg = finalSvg;
        this.previewSvg = previewSvg;

        [guideSvg, candidateSvg, finalSvg, previewSvg].forEach((svg) => {
            svg.setAttribute("viewBox", "0 0 100 100");
            svg.setAttribute("preserveAspectRatio", "none");
            this._ensureDefs(svg);
        });
    }

    async loadSourceImage(url) {
        return new Promise((resolve) => {
            const done = () => {
                this.imageEl.removeEventListener("load", done);
                this.imageEl.removeEventListener("error", done);
                resolve();
            };
            this.imageEl.addEventListener("load", done);
            this.imageEl.addEventListener("error", done);
            this.imageEl.src = url;
        });
    }

    render({ guides = [], candidates = [], finals = [], selection = null }) {
        this._renderGuides(guides, selection);
        this._renderElements(this.candidateSvg, candidates, selection, "candidate");
        this._renderElements(this.finalSvg, finals, selection, "final");
    }

    clearPreview() {
        this.previewSvg.replaceChildren(this.previewSvg.querySelector("defs"));
    }

    drawPreview(startPct, currentPct, tool) {
        this.clearPreview();
        if (!startPct || !currentPct || tool === "select") return;

        const group = this._createNode("g");
        group.classList.add("preview-shape");
        const color = "#ff6b00";

        if (tool === "line" || tool === "arrow") {
            const line = this._createNode("line");
            line.setAttribute("x1", startPct.x_pct);
            line.setAttribute("y1", startPct.y_pct);
            line.setAttribute("x2", currentPct.x_pct);
            line.setAttribute("y2", currentPct.y_pct);
            line.setAttribute("stroke", color);
            line.setAttribute("stroke-width", "0.7");
            line.setAttribute("stroke-dasharray", "2 1");
            if (tool === "arrow") {
                line.setAttribute("marker-end", "url(#preview-arrow)");
            }
            group.appendChild(line);
        } else {
            const bbox = normalizeBBox(startPct, currentPct);
            const shape = this._createNode(tool === "ellipse" ? "ellipse" : "rect");
            if (tool === "ellipse") {
                shape.setAttribute("cx", bbox.x_pct + bbox.width_pct / 2);
                shape.setAttribute("cy", bbox.y_pct + bbox.height_pct / 2);
                shape.setAttribute("rx", bbox.width_pct / 2);
                shape.setAttribute("ry", bbox.height_pct / 2);
            } else {
                shape.setAttribute("x", bbox.x_pct);
                shape.setAttribute("y", bbox.y_pct);
                shape.setAttribute("width", bbox.width_pct);
                shape.setAttribute("height", bbox.height_pct);
            }
            shape.setAttribute("stroke", color);
            shape.setAttribute("fill", "rgba(255,107,0,0.12)");
            shape.setAttribute("stroke-width", "0.5");
            shape.setAttribute("stroke-dasharray", "2 1");
            group.appendChild(shape);
        }
        this.previewSvg.appendChild(group);
    }

    clientToPct(clientX, clientY) {
        const rect = this.container.getBoundingClientRect();
        const xPct = ((clientX - rect.left) / (rect.width || 1)) * 100;
        const yPct = ((clientY - rect.top) / (rect.height || 1)) * 100;
        return {
            x_pct: clampPct(xPct),
            y_pct: clampPct(yPct),
        };
    }

    hitFromEvent(event) {
        const target = event.target.closest?.("[data-item-id]");
        if (!target) return null;
        return {
            layer: target.dataset.layer,
            id: target.dataset.itemId,
        };
    }

    _renderGuides(guides, selection) {
        this.guideSvg.replaceChildren(this.guideSvg.querySelector("defs"));
        guides.forEach((guide) => {
            const group = this._createGuideNode(guide, selection);
            this.guideSvg.appendChild(group);
        });
    }

    _renderElements(svg, elements, selection, layerName) {
        svg.replaceChildren(svg.querySelector("defs"));
        elements.forEach((element) => {
            const group = this._createElementNode(element, selection, layerName);
            svg.appendChild(group);
        });
    }

    _createGuideNode(guide, selection) {
        const group = this._createNode("g");
        group.dataset.itemId = guide.id;
        group.dataset.layer = "guide";
        group.classList.add("svg-item", "guide-item");
        if (selection?.layer === "guide" && selection.id === guide.id) {
            group.classList.add("selected");
        }

        const color = guide.stroke_color_hex || "#ff6b00";
        if (guide.guide_type === "line" || guide.guide_type === "arrow") {
            const line = this._createNode("line");
            const start = guide.start_point || { x_pct: guide.bbox.x_pct, y_pct: guide.bbox.y_pct };
            const end = guide.end_point || {
                x_pct: guide.bbox.x_pct + guide.bbox.width_pct,
                y_pct: guide.bbox.y_pct + guide.bbox.height_pct,
            };
            line.setAttribute("x1", start.x_pct);
            line.setAttribute("y1", start.y_pct);
            line.setAttribute("x2", end.x_pct);
            line.setAttribute("y2", end.y_pct);
            line.setAttribute("stroke", color);
            line.setAttribute("stroke-width", "0.6");
            if (guide.guide_type === "arrow") {
                line.setAttribute("marker-end", "url(#guide-arrow)");
            }
            group.appendChild(line);
        } else if (guide.guide_type === "ellipse") {
            const ellipse = this._createNode("ellipse");
            ellipse.setAttribute("cx", guide.bbox.x_pct + guide.bbox.width_pct / 2);
            ellipse.setAttribute("cy", guide.bbox.y_pct + guide.bbox.height_pct / 2);
            ellipse.setAttribute("rx", guide.bbox.width_pct / 2);
            ellipse.setAttribute("ry", guide.bbox.height_pct / 2);
            ellipse.setAttribute("fill", withAlpha(guide.fill_color_hex || color, guide.fill_opacity ?? 0.12));
            ellipse.setAttribute("stroke", color);
            ellipse.setAttribute("stroke-width", "0.4");
            group.appendChild(ellipse);
        } else {
            const rect = this._createNode("rect");
            rect.setAttribute("x", guide.bbox.x_pct);
            rect.setAttribute("y", guide.bbox.y_pct);
            rect.setAttribute("width", guide.bbox.width_pct);
            rect.setAttribute("height", guide.bbox.height_pct);
            rect.setAttribute("fill", withAlpha(guide.fill_color_hex || color, guide.fill_opacity ?? 0.12));
            rect.setAttribute("stroke", color);
            rect.setAttribute("stroke-width", "0.4");
            if (guide.guide_type === "highlight") {
                rect.setAttribute("stroke-dasharray", "1.2 0.8");
            }
            group.appendChild(rect);
        }

        const label = this._createLabel(
            guide.bbox.x_pct,
            Math.max(2.5, guide.bbox.y_pct - 1.5),
            `${guide.intent_tag || "general"}`
        );
        group.appendChild(label);
        return group;
    }

    _createElementNode(element, selection, layerName) {
        const group = this._createNode("g");
        const itemId = layerName === "candidate" ? element.candidate_id : element.final_id;
        group.dataset.itemId = itemId;
        group.dataset.layer = layerName;
        group.classList.add("svg-item", `${layerName}-item`);
        if (selection?.layer === layerName && selection.id === itemId) {
            group.classList.add("selected");
        }

        const color = layerName === "candidate" ? "#1177cc" : "#178f57";
        const rect = this._createNode("rect");
        rect.setAttribute("x", element.bbox.x_pct);
        rect.setAttribute("y", element.bbox.y_pct);
        rect.setAttribute("width", element.bbox.width_pct);
        rect.setAttribute("height", element.bbox.height_pct);
        rect.setAttribute("fill", withAlpha(color, layerName === "candidate" ? 0.12 : 0.08));
        rect.setAttribute("stroke", color);
        rect.setAttribute("stroke-width", selection?.layer === layerName && selection.id === itemId ? "0.7" : "0.35");
        if (layerName === "final" && element.locked) {
            rect.setAttribute("stroke-dasharray", "1 0.7");
        }
        group.appendChild(rect);

        const label = this._createLabel(
            element.bbox.x_pct,
            Math.max(2.5, element.bbox.y_pct - 1.5),
            `${layerName}:${element.element_type}${element.locked ? " [locked]" : ""}`
        );
        group.appendChild(label);
        return group;
    }

    _createLabel(x, y, text) {
        const label = this._createNode("text");
        label.setAttribute("x", x);
        label.setAttribute("y", y);
        label.setAttribute("class", "svg-label");
        label.textContent = text;
        return label;
    }

    _createNode(name) {
        return document.createElementNS(SVG_NS, name);
    }

    _ensureDefs(svg) {
        if (svg.querySelector("defs")) return;
        const defs = this._createNode("defs");
        defs.appendChild(createArrowMarker("guide-arrow", "#ff6b00"));
        defs.appendChild(createArrowMarker("preview-arrow", "#ff6b00"));
        svg.appendChild(defs);
    }
}

function createArrowMarker(id, color) {
    const marker = document.createElementNS(SVG_NS, "marker");
    marker.setAttribute("id", id);
    marker.setAttribute("viewBox", "0 0 10 10");
    marker.setAttribute("refX", "9");
    marker.setAttribute("refY", "5");
    marker.setAttribute("markerWidth", "5");
    marker.setAttribute("markerHeight", "5");
    marker.setAttribute("orient", "auto-start-reverse");

    const path = document.createElementNS(SVG_NS, "path");
    path.setAttribute("d", "M 0 0 L 10 5 L 0 10 z");
    path.setAttribute("fill", color);
    marker.appendChild(path);
    return marker;
}

function normalizeBBox(start, end) {
    const x1 = Math.min(start.x_pct, end.x_pct);
    const y1 = Math.min(start.y_pct, end.y_pct);
    const x2 = Math.max(start.x_pct, end.x_pct);
    const y2 = Math.max(start.y_pct, end.y_pct);
    return {
        x_pct: x1,
        y_pct: y1,
        width_pct: Math.max(1, x2 - x1),
        height_pct: Math.max(1, y2 - y1),
    };
}

function clampPct(value) {
    return Math.max(0, Math.min(100, value));
}

function withAlpha(hex, alpha) {
    const value = (hex || "#ff6b00").replace("#", "");
    const a = Math.round(Math.max(0, Math.min(1, alpha)) * 255)
        .toString(16)
        .padStart(2, "0");
    return `#${value}${a}`;
}

window.CanvasRenderer = CanvasRenderer;
window.__canvasHelpers = { normalizeBBox, clampPct };
