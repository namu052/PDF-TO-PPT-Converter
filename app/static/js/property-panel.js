const INTENT_OPTIONS = [
    ["general", "일반"],
    ["title", "제목"],
    ["body", "본문"],
    ["text", "텍스트"],
    ["shape", "도형"],
    ["line", "직선"],
    ["connector", "연결선"],
    ["image", "이미지"],
    ["table", "표"],
    ["keep_as_image", "이미지로 유지"],
];

const RENDER_STRATEGY_OPTIONS = [
    ["primitive", "프리미티브"],
    ["hybrid", "하이브리드"],
    ["raster", "래스터"],
];

class PropertyPanel {
    constructor(root, callbacks = {}) {
        this.root = root;
        this.callbacks = callbacks;
    }

    render({ selection }) {
        this.root.replaceChildren();

        if (!selection) {
            this.root.appendChild(this._empty("가이드, 후보, 또는 확정 요소를 선택하세요."));
            return;
        }

        if (selection.layer === "guide") {
            this.root.appendChild(this._guideEditor(selection.item));
            return;
        }
        if (selection.layer === "candidate") {
            this.root.appendChild(this._candidatePanel(selection.item));
            return;
        }
        if (selection.layer === "final") {
            this.root.appendChild(this._finalPanel(selection.item));
        }
    }

    _empty(text) {
        const p = document.createElement("p");
        p.className = "panel-empty";
        p.textContent = text;
        return p;
    }

    _section(title) {
        const section = document.createElement("section");
        section.className = "panel-section";
        const h3 = document.createElement("h3");
        h3.textContent = title;
        section.appendChild(h3);
        return section;
    }

    _field(labelText, input) {
        const wrap = document.createElement("label");
        wrap.className = "field";
        const span = document.createElement("span");
        span.textContent = labelText;
        wrap.appendChild(span);
        wrap.appendChild(input);
        return wrap;
    }

    _guideEditor(guide) {
        const section = this._section("가이드");

        const type = document.createElement("div");
        type.className = "info-chip";
        type.textContent = `${guide.guide_type} / ${guide.intent_tag}`;
        section.appendChild(type);

        const intent = document.createElement("select");
        INTENT_OPTIONS.forEach(([value, label]) => {
            const option = document.createElement("option");
            option.value = value;
            option.textContent = label;
            if (guide.intent_tag === value) option.selected = true;
            intent.appendChild(option);
        });
        intent.addEventListener("change", () => {
            guide.intent_tag = intent.value;
            this.callbacks.onGuideChange?.(guide);
        });
        section.appendChild(this._field("용도", intent));

        const textInput = document.createElement("textarea");
        textInput.rows = 3;
        textInput.value = guide.text || guide.note || "";
        textInput.addEventListener("input", () => {
            guide.text = textInput.value;
            guide.note = textInput.value;
            this.callbacks.onGuideChange?.(guide);
        });
        section.appendChild(this._field("메모", textInput));

        const visible = document.createElement("input");
        visible.type = "checkbox";
        visible.checked = !!guide.vision_visible;
        visible.addEventListener("change", () => {
            guide.vision_visible = visible.checked;
            this.callbacks.onGuideChange?.(guide);
        });
        section.appendChild(this._field("Vision에 표시", visible));

        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "btn-danger";
        remove.textContent = "가이드 삭제";
        remove.addEventListener("click", () => this.callbacks.onGuideDelete?.(guide.id));
        section.appendChild(remove);
        return section;
    }

    _candidatePanel(candidate) {
        const section = this._section("후보");
        section.appendChild(this._infoRow("유형", candidate.element_type));
        section.appendChild(this._infoRow("렌더 전략", candidate.render_strategy));
        section.appendChild(this._infoRow("신뢰도", `${Math.round((candidate.style_confidence || 0) * 100)}%`));
        section.appendChild(this._infoRow("비고", candidate.fidelity_note || "-"));

        const accept = document.createElement("button");
        accept.type = "button";
        accept.className = "btn-primary";
        accept.textContent = "후보 수락";
        accept.addEventListener("click", () => this.callbacks.onCandidateAccept?.(candidate));
        section.appendChild(accept);
        return section;
    }

    _finalPanel(finalElement) {
        const section = this._section("확정 요소");
        section.appendChild(this._infoRow("유형", finalElement.element_type));

        const strategy = document.createElement("select");
        RENDER_STRATEGY_OPTIONS.forEach(([value, label]) => {
            const option = document.createElement("option");
            option.value = value;
            option.textContent = label;
            if (finalElement.render_strategy === value) option.selected = true;
            strategy.appendChild(option);
        });
        strategy.addEventListener("change", () => {
            finalElement.render_strategy = strategy.value;
            this.callbacks.onFinalChange?.(finalElement);
        });
        section.appendChild(this._field("렌더 전략", strategy));

        const note = document.createElement("textarea");
        note.rows = 3;
        note.value = finalElement.fidelity_note || "";
        note.addEventListener("input", () => {
            finalElement.fidelity_note = note.value;
            this.callbacks.onFinalChange?.(finalElement);
        });
        section.appendChild(this._field("충실도 비고", note));

        const locked = document.createElement("input");
        locked.type = "checkbox";
        locked.checked = !!finalElement.locked;
        locked.addEventListener("change", () => this.callbacks.onToggleLock?.(finalElement, locked.checked));
        section.appendChild(this._field("잠금", locked));

        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "btn-danger";
        remove.textContent = "확정 요소 삭제";
        remove.addEventListener("click", () => this.callbacks.onFinalDelete?.(finalElement));
        section.appendChild(remove);
        return section;
    }

    _infoRow(label, value) {
        const row = document.createElement("div");
        row.className = "info-row";
        const k = document.createElement("span");
        const v = document.createElement("strong");
        k.textContent = label;
        v.textContent = value;
        row.appendChild(k);
        row.appendChild(v);
        return row;
    }
}

window.PropertyPanel = PropertyPanel;
