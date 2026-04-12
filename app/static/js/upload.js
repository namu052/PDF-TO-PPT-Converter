const uploadState = {
    jobId: null,
    nextUrl: null,
    pollTimer: null,
};

function initUploadPage() {
    const uploadArea = document.getElementById("upload-area");
    const browseButton = document.getElementById("btn-browse");
    const fileInput = document.getElementById("file-input");

    browseButton.addEventListener("click", () => fileInput.click());
    fileInput.addEventListener("change", async (event) => {
        const [file] = event.target.files || [];
        if (file) {
            await startUpload(file);
        }
    });

    ["dragenter", "dragover"].forEach((eventName) => {
        uploadArea.addEventListener(eventName, (event) => {
            event.preventDefault();
            uploadArea.classList.add("drag-over");
        });
    });

    ["dragleave", "drop"].forEach((eventName) => {
        uploadArea.addEventListener(eventName, (event) => {
            event.preventDefault();
            uploadArea.classList.remove("drag-over");
        });
    });

    uploadArea.addEventListener("drop", async (event) => {
        const file = [...(event.dataTransfer?.files || [])][0];
        if (file) {
            await startUpload(file);
        }
    });
}

async function startUpload(file) {
    if (!file.name.toLowerCase().endsWith(".pdf")) {
        showUploadMessage("PDF 파일만 업로드할 수 있습니다.", true);
        return;
    }

    setProgressMode(true);
    updateProgress({
        title: "PDF 업로드 중...",
        message: "수동 우선 세션을 생성하는 중입니다.",
        detail: file.name,
        percent: 15,
    });

    try {
        const result = await ApiClient.convertPdf(file);
        uploadState.jobId = result.job_id;
        uploadState.nextUrl = result.next_url;
        updateProgress({
            title: "슬라이드 이미지를 준비하는 중...",
            message: "가이드 편집 전에 PDF 페이지를 이미지로 변환합니다.",
            detail: `작업 ${result.job_id} / ${result.total_slides}개 슬라이드`,
            percent: 35,
        });
        await pollStatus(result.job_id, result.next_url);
    } catch (error) {
        setProgressMode(false);
        showUploadMessage(error.message, true);
    }
}

async function pollStatus(jobId, nextUrl) {
    clearTimeout(uploadState.pollTimer);

    try {
        const status = await ApiClient.getStatus(jobId);
        const totalSlides = Math.max(status.total_slides || 0, 1);
        const currentSlide = Math.max(status.current_slide || 0, 0);
        const readySlides = Math.max(status.ready_slides || 0, 0);

        if (status.status === "failed") {
            throw new Error(status.error || status.message || "이미지 변환에 실패했습니다.");
        }

        if (["editing_guidance", "editing_review", "ready_for_generate", "complete"].includes(status.status)) {
            updateProgress({
                title: "편집기를 여는 중...",
                message: status.message || "슬라이드가 가이드 편집 준비 상태입니다.",
                detail: `${totalSlides}개 슬라이드를 준비했습니다.`,
                percent: 100,
            });
            renderEditorFallback(nextUrl);
            window.location.assign(nextUrl);
            return;
        }

        const rasterPercent = totalSlides > 0
            ? Math.min(90, 25 + Math.round((currentSlide / totalSlides) * 55))
            : 25;
        updateProgress({
            title: "슬라이드 이미지를 준비하는 중...",
            message: status.message || "PDF 페이지를 이미지로 변환하는 중입니다.",
            detail: `진행 ${currentSlide}/${totalSlides} / 완료 ${readySlides}/${totalSlides}`,
            percent: rasterPercent,
        });

        uploadState.pollTimer = setTimeout(() => {
            void pollStatus(jobId, nextUrl);
        }, 1200);
    } catch (error) {
        setProgressMode(false);
        showUploadMessage(error.message, true);
    }
}

function setProgressMode(active) {
    document.getElementById("upload-area").classList.toggle("hidden", active);
    document.getElementById("progress-area").classList.toggle("hidden", !active);
}

function updateProgress({ title, message, detail, percent }) {
    document.getElementById("progress-title").textContent = title;
    document.getElementById("progress-message").textContent = message;
    const detailNode = document.getElementById("progress-detail");
    detailNode.textContent = detail;
    document.getElementById("progress-fill").style.width = `${Math.max(0, Math.min(100, percent))}%`;
}

function showUploadMessage(message, isError = false) {
    const detail = document.getElementById("progress-detail");
    if (!detail) return;
    detail.textContent = message;
    detail.style.color = isError ? "#b42318" : "";
}

function renderEditorFallback(nextUrl) {
    const detail = document.getElementById("progress-detail");
    if (!detail || document.getElementById("open-editor-link")) return;

    detail.replaceChildren();
    detail.append(document.createTextNode("편집기가 자동으로 열리지 않으면 아래 링크를 사용하세요."));
    detail.append(document.createElement("br"));

    const link = document.createElement("a");
    link.id = "open-editor-link";
    link.href = nextUrl;
    link.textContent = "편집기 열기";
    link.className = "fallback-link";
    detail.append(link);
}

document.addEventListener("DOMContentLoaded", initUploadPage);
