const state = {
    jobId: null,
    session: null,
    slide: null,
    currentSlideIndex: 0,
    currentTool: "select",
    selection: null,
    saveTimer: null,
};

let renderer;
let tools;
let panel;
let navigatorView;


async function initEditor() {
    const params = new URLSearchParams(window.location.search);
    state.jobId = params.get("job_id");
    if (!state.jobId) {
        window.location.href = "/";
        return;
    }

    renderer = new CanvasRenderer({
        container: document.getElementById("canvas-container"),
        imageEl: document.getElementById("slide-image"),
        guideSvg: document.getElementById("guide-layer"),
        candidateSvg: document.getElementById("candidate-layer"),
        finalSvg: document.getElementById("final-layer"),
        previewSvg: document.getElementById("preview-layer"),
    });

    tools = new ElementTools(renderer, state, {
        onSelect: handleSelection,
        onGuideCreated: handleGuideCreated,
        onGuideDeleted: handleGuideDeleted,
    });

    panel = new PropertyPanel(document.getElementById("property-panel"), {
        onGuideChange: handleGuideUpdated,
        onGuideDelete: handleGuideDeleted,
        onCandidateAccept: handleCandidateAccept,
        onFinalChange: handleFinalUpdated,
        onFinalDelete: handleFinalDeleted,
        onToggleLock: handleToggleLock,
    });

    navigatorView = new SlideNavigator(document.getElementById("slide-nav"), handleSlideSelect);

    bindToolbar();
    bindCanvas();
    bindShortcuts();

    try {
        await reloadSession();
        await loadCurrentSlide();
    } catch (error) {
        hideLoading();
        throw error;
    }
}


function bindToolbar() {
    document.querySelectorAll("[data-tool]").forEach((button) => {
        button.addEventListener("click", () => {
            document.querySelectorAll("[data-tool]").forEach((node) => node.classList.remove("active"));
            button.classList.add("active");
            tools.setTool(button.dataset.tool);
            updateToolbarState();
        });
    });
    document.getElementById("btn-analyze-slide").addEventListener("click", () => analyzeCurrentSlide());
    document.getElementById("btn-analyze-selection").addEventListener("click", () => analyzeSelectedRegion());
    document.getElementById("btn-accept-all").addEventListener("click", () => acceptAllCandidates());
    document.getElementById("btn-delete-guide").addEventListener("click", () => tools.deleteSelectedGuide());
    document.getElementById("btn-generate").addEventListener("click", () => generatePpt());
}


function bindCanvas() {
    const layer = document.getElementById("interaction-layer");
    layer.addEventListener("pointerdown", (event) => tools.onPointerDown(event));
    window.addEventListener("pointermove", (event) => tools.onPointerMove(event));
    window.addEventListener("pointerup", (event) => tools.onPointerUp(event));
}


function bindShortcuts() {
    window.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
            state.selection = null;
            render();
        }
        if (event.key === "Delete" && state.selection?.layer === "guide") {
            tools.deleteSelectedGuide();
        }
    });
}


async function reloadSession() {
    state.session = await ApiClient.getSession(state.jobId);
    navigatorView.render(state.session, state.currentSlideIndex, state.jobId);
}


async function loadCurrentSlide() {
    const slideNum = state.currentSlideIndex + 1;
    showLoading(`${slideNum}번 슬라이드를 불러오는 중...`);
    try {
        state.slide = await ApiClient.getSlide(state.jobId, slideNum);
        await renderer.loadSourceImage(state.slide.source_image_url);
        state.selection = null;
        render();
    } finally {
        hideLoading();
    }
}


function render() {
    const selection = resolveSelection();
    renderer.render({
        guides: state.slide?.guides || [],
        candidates: state.slide?.latest_revision?.candidates || [],
        finals: state.slide?.final_elements || [],
        selection,
    });
    panel.render({ selection, slide: state.slide });
    updateStatusBar();
    updateToolbarState();
    navigatorView.render(state.session, state.currentSlideIndex, state.jobId);
}


function resolveSelection() {
    if (!state.selection || !state.slide) return null;
    const { layer, id } = state.selection;
    const collections = {
        guide: state.slide.guides || [],
        candidate: state.slide.latest_revision?.candidates || [],
        final: state.slide.final_elements || [],
    };
    const item = collections[layer]?.find((entry) => getSelectionId(layer, entry) === id);
    if (!item) return null;
    return { layer, id, item };
}


function handleSelection(hit) {
    state.selection = hit || null;
    render();
}


function handleGuideCreated(guide) {
    state.slide.guides = [...(state.slide.guides || []), guide];
    state.selection = { layer: "guide", id: guide.id };
    scheduleGuideSave();
    render();
}


function handleGuideUpdated(guide) {
    state.slide.guides = (state.slide.guides || []).map((item) => item.id === guide.id ? guide : item);
    scheduleGuideSave();
    render();
}


function handleGuideDeleted(guideId) {
    state.slide.guides = (state.slide.guides || []).filter((item) => item.id !== guideId);
    state.selection = null;
    scheduleGuideSave(true);
    render();
}


async function handleCandidateAccept(candidate) {
    if (!state.slide?.latest_revision?.revision_id) return;
    showLoading("후보를 수락하는 중...");
    try {
        await ApiClient.applyRevision(
            state.jobId,
            state.slide.slide_number,
            state.slide.latest_revision.revision_id,
            { candidate_ids: [candidate.candidate_id] }
        );
        await reloadSession();
        await loadCurrentSlide();
    } finally {
        hideLoading();
    }
}


async function acceptAllCandidates() {
    if (!state.slide?.latest_revision?.revision_id) return;
    showLoading("모든 후보를 수락하는 중...");
    try {
        await ApiClient.applyRevision(
            state.jobId,
            state.slide.slide_number,
            state.slide.latest_revision.revision_id,
            { accept_all: true }
        );
        await reloadSession();
        await loadCurrentSlide();
    } finally {
        hideLoading();
    }
}


async function handleFinalUpdated(finalElement) {
    state.slide.final_elements = (state.slide.final_elements || []).map((item) =>
        item.final_id === finalElement.final_id ? finalElement : item
    );
    await ApiClient.saveFinalElements(state.jobId, state.slide.slide_number, state.slide.final_elements);
    await reloadSession();
    render();
}


async function handleFinalDeleted(finalElement) {
    state.slide.final_elements = (state.slide.final_elements || []).filter(
        (item) => item.final_id !== finalElement.final_id
    );
    state.selection = null;
    await ApiClient.saveFinalElements(state.jobId, state.slide.slide_number, state.slide.final_elements);
    await reloadSession();
    render();
}


async function handleToggleLock(finalElement, locked) {
    await ApiClient.lockElements(state.jobId, state.slide.slide_number, [finalElement.final_id], locked);
    await reloadSession();
    await loadCurrentSlide();
}


function scheduleGuideSave(immediate = false) {
    if (state.saveTimer) clearTimeout(state.saveTimer);
    if (immediate) {
        void saveGuidesNow();
        return;
    }
    state.saveTimer = setTimeout(() => {
        void saveGuidesNow();
    }, 350);
}


async function saveGuidesNow() {
    if (!state.slide) return;
    await ApiClient.saveGuides(state.jobId, state.slide.slide_number, state.slide.guides || []);
    await reloadSession();
}


async function analyzeCurrentSlide() {
    showLoading("가이드 기반 분석을 실행하는 중...");
    try {
        await saveGuidesNow();
        await ApiClient.analyzeSlide(state.jobId, state.slide.slide_number, {
            guide_ids: (state.slide.guides || []).map((guide) => guide.id),
            scope: "slide",
        });
        await reloadSession();
        await loadCurrentSlide();
    } finally {
        hideLoading();
    }
}


async function analyzeSelectedRegion() {
    const selection = resolveSelection();
    if (!selection || selection.layer !== "guide" || !["region_box", "highlight"].includes(selection.item.guide_type)) {
        showToast("먼저 '영역' 또는 '강조' 가이드를 선택하세요.", "error");
        return;
    }
    showLoading("영역 분석을 실행하는 중...");
    try {
        await saveGuidesNow();
        await ApiClient.analyzeSlide(state.jobId, state.slide.slide_number, {
            guide_ids: [selection.item.id],
            scope: "region",
            region: selection.item.bbox,
        });
        await reloadSession();
        await loadCurrentSlide();
    } finally {
        hideLoading();
    }
}


async function generatePpt() {
    showLoading("PPTX를 생성하는 중...");
    try {
        await ApiClient.generatePptx(state.jobId);
        const anchor = document.createElement("a");
        anchor.href = ApiClient.getDownloadUrl(state.jobId);
        anchor.download = "ManualFirst_Slides.pptx";
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
    } catch (error) {
        showToast(error.message, "error");
    } finally {
        hideLoading();
        await reloadSession();
        await loadCurrentSlide();
    }
}


async function handleSlideSelect(index) {
    state.currentSlideIndex = index;
    await loadCurrentSlide();
}


const JOB_STATUS_LABEL = {
    uploading: "업로드 중",
    rasterizing: "이미지 변환 중",
    editing_guidance: "가이드 편집",
    analyzing: "분석 중",
    editing_review: "검토 편집",
    ready_for_generate: "생성 준비됨",
    generating: "PPTX 생성 중",
    complete: "완료",
    failed: "실패",
};

const SLIDE_STATUS_LABEL = {
    guidance: "가이드",
    review: "검토",
    ready: "준비됨",
};

function localizeJobStatus(value) {
    if (!value) return "-";
    return JOB_STATUS_LABEL[value] || value;
}

function localizeSlideStatus(value) {
    if (!value) return "-";
    return SLIDE_STATUS_LABEL[value] || value;
}

function updateStatusBar() {
    document.getElementById("status-left").textContent =
        `슬라이드 ${state.currentSlideIndex + 1}/${state.session?.slides?.length || 0}`;
    document.getElementById("status-mid").textContent =
        `작업 ${localizeJobStatus(state.session?.status)} / 슬라이드 ${localizeSlideStatus(state.slide?.status)}`;
    document.getElementById("status-right").textContent =
        `가이드 ${state.slide?.guides?.length || 0}개 / 확정 ${state.slide?.final_elements?.length || 0}개`;
    document.getElementById("mode-badge").textContent = localizeJobStatus(state.session?.status) || "가이드 편집";
}


function updateToolbarState() {
    const selection = resolveSelection();
    const canAnalyzeSelection = selection &&
        selection.layer === "guide" &&
        ["region_box", "highlight"].includes(selection.item.guide_type);
    document.getElementById("btn-analyze-selection").disabled = !canAnalyzeSelection;
    document.getElementById("btn-delete-guide").disabled = !(selection && selection.layer === "guide");
    document.getElementById("btn-accept-all").disabled = !state.slide?.latest_revision?.candidates?.length;
    document.getElementById("btn-generate").disabled = state.session?.ready_slides !== state.session?.total_slides;
}


function getSelectionId(layer, item) {
    if (layer === "candidate") return item.candidate_id;
    if (layer === "final") return item.final_id;
    return item.id;
}


function showLoading(message) {
    const overlay = document.getElementById("canvas-loading");
    if (!overlay) return;
    overlay.classList.remove("hidden");
    document.getElementById("loading-message").textContent = message;
}


function hideLoading() {
    const overlay = document.getElementById("canvas-loading");
    if (!overlay) return;
    overlay.classList.add("hidden");
}


function showToast(message, kind = "success") {
    const toast = document.createElement("div");
    toast.className = `toast ${kind}`;
    toast.textContent = message;
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 3200);
}


function reportFatalError(error) {
    console.error(error);
    const message = error?.message || String(error);
    hideLoading();
    const badge = document.getElementById("mode-badge");
    if (badge) {
        badge.textContent = `오류: ${message}`;
        badge.style.background = "#fde2e2";
        badge.style.color = "#b42318";
    }
    const statusMid = document.getElementById("status-mid");
    if (statusMid) {
        statusMid.textContent = `초기화 오류: ${message}`;
        statusMid.style.color = "#b42318";
    }
    try {
        showToast(message, "error");
    } catch (_) {
        // toast DOM not ready
    }
}

window.addEventListener("error", (event) => {
    if (event?.error) reportFatalError(event.error);
});
window.addEventListener("unhandledrejection", (event) => {
    if (event?.reason) reportFatalError(event.reason);
});

document.addEventListener("DOMContentLoaded", () => {
    initEditor().catch(reportFatalError);
});
