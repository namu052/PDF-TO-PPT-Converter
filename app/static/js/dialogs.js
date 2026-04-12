/**
 * dialogs.js — 편집기 모달 다이얼로그 (XSS 안전, DOM API 기반)
 * PLAN.md §11.5
 *
 * Promise 기반:
 *  - showDialog({title, message, buttons}) → 클릭한 버튼 인덱스
 *  - showOcrResultDialog(paragraphs, confidence) → {action, paragraphs?}
 *
 * 모든 다이얼로그는 #dialog-root 에 추가된다.
 */

function _dialogRoot() {
    return document.getElementById('dialog-root') || document.body;
}

function showDialog({ title, message, buttons }) {
    return new Promise((resolve) => {
        const overlay = document.createElement('div');
        overlay.className = 'dialog-overlay';

        const dlg = document.createElement('div');
        dlg.className = 'dialog';

        const h3 = document.createElement('h3');
        h3.className = 'dialog-title';
        h3.textContent = title || '';
        dlg.appendChild(h3);

        if (message) {
            const p = document.createElement('p');
            p.className = 'dialog-message';
            p.textContent = message;
            dlg.appendChild(p);
        }

        const btnRow = document.createElement('div');
        btnRow.className = 'dialog-buttons';
        (buttons || ['확인']).forEach((label, i) => {
            const b = document.createElement('button');
            b.type = 'button';
            b.className = i === 0 ? 'btn-primary' : 'btn-secondary';
            b.textContent = label;
            b.addEventListener('click', () => {
                overlay.remove();
                resolve(i);
            });
            btnRow.appendChild(b);
        });
        dlg.appendChild(btnRow);
        overlay.appendChild(dlg);
        _dialogRoot().appendChild(overlay);
    });
}

function showOcrResultDialog(paragraphs, confidence) {
    return new Promise((resolve) => {
        const text = (paragraphs || [])
            .map((p) => (p.runs || []).map((r) => r.text || '').join(''))
            .join('\n');

        const overlay = document.createElement('div');
        overlay.className = 'dialog-overlay';
        const dlg = document.createElement('div');
        dlg.className = 'dialog dialog-wide';

        const h3 = document.createElement('h3');
        h3.className = 'dialog-title';
        h3.textContent = `OCR 결과 (신뢰도: ${confidence ?? '-'})`;
        dlg.appendChild(h3);

        const ta = document.createElement('textarea');
        ta.className = 'dialog-textarea';
        ta.value = text;
        dlg.appendChild(ta);

        const hint = document.createElement('p');
        hint.className = 'dialog-message';
        hint.textContent = '텍스트를 수정하고 적용하거나, 직접 입력으로 전환할 수 있습니다.';
        dlg.appendChild(hint);

        const btnRow = document.createElement('div');
        btnRow.className = 'dialog-buttons';

        const acceptBtn = document.createElement('button');
        acceptBtn.type = 'button';
        acceptBtn.className = 'btn-primary';
        acceptBtn.textContent = '적용';
        acceptBtn.addEventListener('click', () => {
            const baseFont = paragraphs?.[0]?.runs?.[0]?.font || {
                family: 'Calibri', size_pt: 18, bold: false, italic: false,
                underline: false, color_hex: '#000000',
            };
            const edited = ta.value.split('\n').map((line) => ({
                runs: [{ text: line, font: { ...baseFont } }],
                alignment: 'left',
                bullet: false,
                indent_level: 0,
                line_spacing: 1.2,
            }));
            overlay.remove();
            resolve({ action: 'accept', paragraphs: edited });
        });
        btnRow.appendChild(acceptBtn);

        const manualBtn = document.createElement('button');
        manualBtn.type = 'button';
        manualBtn.className = 'btn-secondary';
        manualBtn.textContent = '직접 입력';
        manualBtn.addEventListener('click', () => {
            overlay.remove();
            resolve({ action: 'manual' });
        });
        btnRow.appendChild(manualBtn);

        const cancelBtn = document.createElement('button');
        cancelBtn.type = 'button';
        cancelBtn.className = 'btn-secondary';
        cancelBtn.textContent = '취소';
        cancelBtn.addEventListener('click', () => {
            overlay.remove();
            resolve({ action: 'cancel' });
        });
        btnRow.appendChild(cancelBtn);

        dlg.appendChild(btnRow);
        overlay.appendChild(dlg);
        _dialogRoot().appendChild(overlay);
    });
}

window.showDialog = showDialog;
window.showOcrResultDialog = showOcrResultDialog;
