class ElementTools {
    constructor(renderer, state, callbacks = {}) {
        this.renderer = renderer;
        this.state = state;
        this.callbacks = callbacks;
        this.dragMode = null;
        this.startPct = null;
    }

    setTool(tool) {
        this.state.currentTool = tool;
    }

    onPointerDown(event) {
        if (event.button !== 0) return;

        const pct = this.renderer.clientToPct(event.clientX, event.clientY);
        if (this.state.currentTool === "select") {
            this.callbacks.onSelect?.(this._findHit(pct));
            return;
        }

        this.dragMode = "draw";
        this.startPct = pct;
        event.preventDefault();
    }

    onPointerMove(event) {
        if (this.dragMode !== "draw") return;
        const pct = this.renderer.clientToPct(event.clientX, event.clientY);
        this.renderer.drawPreview(this.startPct, pct, this.state.currentTool);
    }

    onPointerUp(event) {
        if (this.dragMode !== "draw" || !this.startPct) return;
        const endPct = this.renderer.clientToPct(event.clientX, event.clientY);
        this.renderer.clearPreview();

        const guide = this._createGuide(this.state.currentTool, this.startPct, endPct);
        this.dragMode = null;
        this.startPct = null;
        if (!guide) return;
        this.callbacks.onGuideCreated?.(guide);
    }

    deleteSelectedGuide() {
        const selection = this.state.selection;
        if (!selection || selection.layer !== "guide") return;
        this.callbacks.onGuideDeleted?.(selection.id);
    }

    _findHit(point) {
        const slide = this.state.slide;
        if (!slide) return null;

        const layers = [
            ["final", slide.final_elements || [], "final_id"],
            ["candidate", slide.latest_revision?.candidates || [], "candidate_id"],
            ["guide", slide.guides || [], "id"],
        ];

        for (const [layer, items, idKey] of layers) {
            const hit = [...items].reverse().find((item) => this._bboxContains(item.bbox, point));
            if (hit) {
                return { layer, id: hit[idKey] };
            }
        }
        return null;
    }

    _bboxContains(bbox, point) {
        if (!bbox) return false;
        return (
            point.x_pct >= bbox.x_pct &&
            point.x_pct <= bbox.x_pct + bbox.width_pct &&
            point.y_pct >= bbox.y_pct &&
            point.y_pct <= bbox.y_pct + bbox.height_pct
        );
    }

    _createGuide(tool, start, end) {
        const bbox = normalizeBBox(start, end);
        if (bbox.width_pct < 1 && bbox.height_pct < 1) return null;

        const id = Math.random().toString(36).slice(2, 10);
        if (tool === "text_note") {
            const text = window.prompt("가이드 메모 텍스트", "") || "";
            return {
                id,
                guide_type: "text_note",
                bbox,
                text,
                note: text,
                intent_tag: "text",
                stroke_color_hex: "#ff6b00",
                fill_color_hex: "#ffd1a3",
                fill_opacity: 0.15,
                stroke_width_pt: 2,
                vision_visible: true,
            };
        }
        if (tool === "line" || tool === "arrow") {
            return {
                id,
                guide_type: tool,
                bbox,
                start_point: start,
                end_point: end,
                note: "",
                intent_tag: tool === "arrow" ? "connector" : "line",
                stroke_color_hex: "#ff6b00",
                fill_color_hex: "#ffd1a3",
                fill_opacity: 0.15,
                stroke_width_pt: 2,
                vision_visible: true,
            };
        }

        const intentMap = {
            rect: "shape",
            ellipse: "shape",
            region_box: "general",
            highlight: "keep_as_image",
        };
        return {
            id,
            guide_type: tool,
            bbox,
            note: "",
            intent_tag: intentMap[tool] || "general",
            stroke_color_hex: "#ff6b00",
            fill_color_hex: tool === "highlight" ? "#fff099" : "#ffd1a3",
            fill_opacity: tool === "highlight" ? 0.3 : 0.12,
            stroke_width_pt: 2,
            vision_visible: true,
        };
    }
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

window.ElementTools = ElementTools;
