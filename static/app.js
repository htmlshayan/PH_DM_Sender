const refreshIntervalMs = 15000;

const navButtons = document.querySelectorAll(".nav-item[data-target]");
const pages = document.querySelectorAll(".page");
const accountsList = document.getElementById("accounts-list");
const addAccountButton = document.getElementById("add-account-btn");
const targetsList = document.getElementById("targets-list");
const targetsForm = document.getElementById("targets-form");
const bulkTargetsInput = document.getElementById("bulk-targets-input");
const targetsPaginationEl = document.getElementById("targets-pagination");
const accountsPaginationEl = document.getElementById("accounts-pagination");
const dmsList = document.getElementById("dms-list");
const dmsPaginationEl = document.getElementById("dms-pagination");
const logOutput = document.getElementById("bot-log-output");
const clearLogsButton = document.getElementById("clear-logs-btn");

let activePage = "dashboard";
let lastLogText = "";

const setActivePage = (target) => {
    activePage = target;
    pages.forEach((page) => {
        page.classList.toggle("active", page.dataset.page === target);
    });
    navButtons.forEach((button) => {
        button.classList.toggle("active", button.dataset.target === target);
    });
    localStorage.setItem("activePage", target);
    if (target === "dashboard") {
        fetchBotLogs();
    }
};

navButtons.forEach((button) => {
    button.addEventListener("click", () => {
        setActivePage(button.dataset.target);
    });
});

const storedPage = localStorage.getItem("activePage") || "dashboard";
setActivePage(storedPage);

setInterval(() => {
    if (!document.hidden && activePage === "dashboard") {
        window.location.reload();
    }
}, refreshIntervalMs);

async function fetchBotLogs() {
    if (!logOutput || activePage !== "dashboard") {
        return;
    }
    try {
        const response = await fetch("/logs?lines=200", { cache: "no-store" });
        if (!response.ok) {
            return;
        }
        const text = await response.text();
        if (text !== lastLogText) {
            const shouldStickToBottom =
                logOutput.scrollHeight - logOutput.scrollTop - logOutput.clientHeight < 32;
            logOutput.textContent = text || "No logs yet.";
            if (shouldStickToBottom) {
                logOutput.scrollTop = logOutput.scrollHeight;
            }
            lastLogText = text;
        }
    } catch (error) {
        logOutput.textContent = "Unable to load logs.";
    }
}

async function clearBotLogs() {
    if (!logOutput) {
        return;
    }
    try {
        const response = await fetch("/logs/clear", { method: "POST" });
        if (!response.ok) {
            return;
        }
        lastLogText = "";
        logOutput.textContent = "No logs yet.";
    } catch (error) {
        logOutput.textContent = "Unable to clear logs.";
    }
}

const createAccountRow = () => {
    const rowId = `new_${Date.now()}`;
    const wrapper = document.createElement("div");
    wrapper.className = "account-row";
    wrapper.innerHTML = `
        <input type="hidden" name="account_id" value="">
        <div class="field">
            <label for="account_name_${rowId}">Account Name</label>
            <input type="text" id="account_name_${rowId}" name="account_name" placeholder="Account label" required>
        </div>
        <div class="field">
            <label for="account_profile_id_${rowId}">GoLogin Profile ID</label>
            <input type="text" id="account_profile_id_${rowId}" name="account_profile_id" placeholder="Profile ID">
            <div class="field-hint">Paste the GoLogin profile ID for this account.</div>
        </div>
        <div class="field">
            <label for="account_cookies_${rowId}">Account Cookies</label>
            <textarea id="account_cookies_${rowId}" name="account_cookies" rows="3" placeholder="Paste cookies JSON" required></textarea>
            <div class="field-hint">Paste the cookies array for this account.</div>
        </div>
        <div class="account-actions">
            <button type="button" class="btn btn-danger delete-account">Remove</button>
        </div>
    `;
    return wrapper;
};

if (addAccountButton && accountsList) {
    addAccountButton.addEventListener("click", () => {
        const emptyState = accountsList.querySelector(".empty-state");
        if (emptyState) {
            emptyState.remove();
        }
        accountsList.appendChild(createAccountRow());
        refreshAccountsPagination();
    });
}

if (accountsList) {
    accountsList.addEventListener("click", (event) => {
        const target = event.target;
        if (!(target instanceof HTMLElement)) {
            return;
        }
        if (target.classList.contains("delete-account")) {
            const accountId = target.dataset.accountId;
            if (!accountId) {
                const row = target.closest(".account-row");
                if (row) {
                    row.remove();
                }
                return;
            }
            const form = document.createElement("form");
            form.method = "post";
            form.action = "/accounts/delete";
            const input = document.createElement("input");
            input.type = "hidden";
            input.name = "account_id";
            input.value = accountId;
            form.appendChild(input);
            document.body.appendChild(form);
            form.submit();
        }
    });
    // Refresh pagination if client-side rows removed
    const observerAccounts = new MutationObserver(() => refreshAccountsPagination());
    observerAccounts.observe(accountsList, { childList: true, subtree: false });
}

if (targetsList) {
    targetsList.addEventListener("click", (event) => {
        const target = event.target;
        if (!(target instanceof HTMLElement)) {
            return;
        }
        if (target.classList.contains("delete-target")) {
            const row = target.closest(".target-row");
            if (row) row.remove();
        }
    });
    // Refresh pagination when targets change
    const observerTargets = new MutationObserver(() => refreshTargetsPagination());
    observerTargets.observe(targetsList, { childList: true, subtree: false });
}

if (clearLogsButton) {
    clearLogsButton.addEventListener("click", () => {
        clearBotLogs();
    });
}

// Pagination utilities
function createPaginator(container, rowSelector, paginationEl, pageSize = 5) {
    if (!container || !paginationEl) return null;
    let currentPage = 1;

    function getRows() {
        return Array.from(container.querySelectorAll(rowSelector));
    }

    function render(page = 1) {
        const rows = getRows();
        const total = rows.length;
        const totalPages = Math.max(1, Math.ceil(total / pageSize));
        currentPage = Math.min(Math.max(1, page), totalPages);

        // hide all rows
        rows.forEach((r, idx) => {
            const start = (currentPage - 1) * pageSize;
            const end = start + pageSize;
            r.style.display = idx >= start && idx < end ? "flex" : "none";
        });

        // build pagination controls
        if (totalPages <= 1) {
            paginationEl.innerHTML = "";
            return;
        }

        let html = '';
        html += `<button class="pg-btn pg-prev" ${currentPage===1? 'disabled': ''}>Prev</button>`;
        // show at most 5 page numbers
        const visible = 5;
        let startPage = Math.max(1, currentPage - Math.floor(visible/2));
        let endPage = Math.min(totalPages, startPage + visible -1);
        if (endPage - startPage +1 < visible) {
            startPage = Math.max(1, endPage - visible +1);
        }
        for (let p = startPage; p <= endPage; p++) {
            html += `<button class="pg-btn pg-page ${p===currentPage? 'active':''}" data-page="${p}">${p}</button>`;
        }
        html += `<button class="pg-btn pg-next" ${currentPage===totalPages? 'disabled': ''}>Next</button>`;
        paginationEl.innerHTML = html;

        // attach events
        paginationEl.querySelectorAll('.pg-btn').forEach(btn=>{
            btn.addEventListener('click', (ev)=>{
                if (btn.classList.contains('pg-prev')) render(currentPage-1);
                else if (btn.classList.contains('pg-next')) render(currentPage+1);
                else if (btn.classList.contains('pg-page')) render(parseInt(btn.dataset.page));
            });
        });
    }

    return { render };
}

let accountsPaginator = null;
let targetsPaginator = null;
let dmsPaginator = null;

function refreshAccountsPagination() {
    if (!accountsList || !accountsPaginationEl) return;
    if (!accountsPaginator) accountsPaginator = createPaginator(accountsList, '.account-row', accountsPaginationEl, 5);
    accountsPaginator && accountsPaginator.render(1);
}

function refreshTargetsPagination() {
    if (!targetsList || !targetsPaginationEl) return;
    if (!targetsPaginator) targetsPaginator = createPaginator(targetsList, '.target-row', targetsPaginationEl, 6);
    targetsPaginator && targetsPaginator.render(1);
}

function refreshDmsPagination() {
    if (!dmsList || !dmsPaginationEl) return;
    if (!dmsPaginator) dmsPaginator = createPaginator(dmsList, '.sent-row', dmsPaginationEl, 10);
    dmsPaginator && dmsPaginator.render(1);
}

// init paginators on load
document.addEventListener('DOMContentLoaded', ()=>{
    refreshAccountsPagination();
    refreshTargetsPagination();
    refreshDmsPagination();
    if (logOutput) {
        fetchBotLogs();
        setInterval(fetchBotLogs, 2000);
    }
    // ensure SENT_TARGETS array exists
    if (!window.SENT_TARGETS) window.SENT_TARGETS = [];
    // Intercept targets form submission to prevent adding already-sent URLs
    if (targetsForm) {
        targetsForm.addEventListener('submit', (ev) => {
            const inputs = Array.from(targetsForm.querySelectorAll('input[name="target_url"]'));
            const existingValues = inputs
                .map(inp => (inp.value || '').trim())
                .filter(v => v.length > 0);
            const bulkValues = (bulkTargetsInput && bulkTargetsInput.value)
                ? bulkTargetsInput.value.split(/\r?\n/).map(line => line.trim()).filter(line => line.length > 0)
                : [];

            const seen = new Set(existingValues.map(v => v.toLowerCase()));
            const newValues = [];
            bulkValues.forEach((value) => {
                const key = value.toLowerCase();
                if (!seen.has(key)) {
                    seen.add(key);
                    newValues.push(value);
                }
            });

            targetsForm.querySelectorAll('input.bulk-target-hidden').forEach((el) => el.remove());
            newValues.forEach((value) => {
                const hidden = document.createElement('input');
                hidden.type = 'hidden';
                hidden.name = 'target_url';
                hidden.value = value;
                hidden.className = 'bulk-target-hidden';
                targetsForm.appendChild(hidden);
            });

            const combined = existingValues.concat(newValues);
            const dupes = [];
            const sentSet = new Set((window.SENT_TARGETS || []).map(s => s.trim().toLowerCase()));
            combined.forEach((value) => {
                const key = value.trim().toLowerCase();
                if (key && sentSet.has(key)) dupes.push(value.trim());
            });
            if (dupes.length) {
                ev.preventDefault();
                alert('These profiles were already DM sent and cannot be added:\n' + dupes.join('\n'));
                return false;
            }
            if (bulkTargetsInput) {
                bulkTargetsInput.value = '';
            }
            return true;
        });
    }
});
