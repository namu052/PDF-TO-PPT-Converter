class ApiClient {
    static async convertPdf(file) {
        const formData = new FormData();
        formData.append("file", file);
        return this._fetch("/api/convert", { method: "POST", body: formData });
    }

    static async getStatus(jobId) {
        return this._fetch(`/api/status/${jobId}`);
    }

    static async getSession(jobId) {
        return this._fetch(`/api/session/${jobId}`);
    }

    static async getSlide(jobId, slideNum) {
        return this._fetch(`/api/slide/${jobId}/${slideNum}`);
    }

    static async saveGuides(jobId, slideNum, guides) {
        return this._fetch(`/api/guides/${jobId}/${slideNum}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ guides }),
        });
    }

    static async analyzeSlide(jobId, slideNum, payload = {}) {
        return this._fetch(`/api/analyze/${jobId}/${slideNum}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
    }

    static async getRevision(jobId, slideNum, revisionId) {
        return this._fetch(`/api/analysis/${jobId}/${slideNum}/${revisionId}`);
    }

    static async applyRevision(jobId, slideNum, revisionId, payload = {}) {
        return this._fetch(`/api/review/${jobId}/${slideNum}/${revisionId}/apply`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
    }

    static async lockElements(jobId, slideNum, elementIds, locked = true) {
        return this._fetch(`/api/lock/${jobId}/${slideNum}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ element_ids: elementIds, locked }),
        });
    }

    static async saveFinalElements(jobId, slideNum, finalElements) {
        return this._fetch(`/api/final-elements/${jobId}/${slideNum}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ final_elements: finalElements }),
        });
    }

    static async generatePptx(jobId) {
        return this._fetch(`/api/generate/${jobId}`, { method: "POST" });
    }

    static async ocrRegion(jobId, slideNum, region) {
        return this._fetch("/api/ocr-region", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ job_id: jobId, slide_num: slideNum, region }),
        });
    }

    static async analyzeRegion(jobId, slideNum, region) {
        return this._fetch("/api/analyze-region", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ job_id: jobId, slide_num: slideNum, region }),
        });
    }

    static getSourceImageUrl(jobId, slideNum) {
        return `/api/source-image/${jobId}/${slideNum}`;
    }

    static getDownloadUrl(jobId) {
        return `/api/download/${jobId}`;
    }

    static async _fetch(url, options = {}) {
        let response;
        try {
            response = await fetch(url, options);
        } catch (error) {
            throw new Error(`Network error: ${error.message}`);
        }

        const contentType = response.headers.get("content-type") || "";
        let body = null;
        if (contentType.includes("application/json")) {
            body = await response.json();
        } else {
            const text = await response.text();
            body = text ? { detail: text } : {};
        }

        if (!response.ok) {
            if (typeof body.detail === "object" && body.detail !== null) {
                throw new Error(body.detail.message || `HTTP ${response.status}`);
            }
            throw new Error(body.detail || body.error || `HTTP ${response.status}`);
        }
        return body;
    }
}

window.ApiClient = ApiClient;
