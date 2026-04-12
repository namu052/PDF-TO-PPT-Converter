const SLIDE_NAV_STATUS_LABEL = {
    guidance: "가이드",
    review: "검토",
    ready: "준비됨",
};

class SlideNavigator {
    constructor(navElement, onSelect) {
        this.nav = navElement;
        this.onSelect = onSelect;
    }

    render(session, currentIndex, jobId) {
        this.nav.replaceChildren();
        const slides = session?.slides || [];
        slides.forEach((slide, index) => {
            const card = document.createElement("button");
            card.type = "button";
            card.className = "thumb";
            if (index === currentIndex) card.classList.add("active");
            card.addEventListener("click", () => this.onSelect(index));

            const num = document.createElement("span");
            num.className = "thumb-num";
            num.textContent = String(slide.slide_number);
            card.appendChild(num);

            const img = document.createElement("img");
            img.className = "thumb-image";
            img.src = ApiClient.getSourceImageUrl(jobId, slide.slide_number);
            img.alt = `슬라이드 ${slide.slide_number}`;
            img.loading = "lazy";
            img.draggable = false;
            card.appendChild(img);

            const status = document.createElement("span");
            status.className = `thumb-status status-${slide.status}`;
            status.textContent = SLIDE_NAV_STATUS_LABEL[slide.status] || slide.status;
            card.appendChild(status);

            const meta = document.createElement("span");
            meta.className = "thumb-info";
            meta.textContent = `가이드 ${slide.guides_count}개 / 확정 ${slide.final_elements_count}개`;
            card.appendChild(meta);

            this.nav.appendChild(card);
        });
    }
}

window.SlideNavigator = SlideNavigator;
