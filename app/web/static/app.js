const API_BASE = "/api";
const JOBS_List = document.getElementById("job-list");
const STATUS_EL = document.getElementById("connection-status");
let jobsMap = {};

// Debug Logger - Disabled
function log(msg, type='info') {
    // console.log(msg); 
}

// Utils
function formatStatus(status) {
    const map = {
        queued:    'في الانتظار',
        running:   'جارٍ التحميل',
        completed: 'اكتمل',
        failed:    'فشل',
        cancelled: 'ملغى',
    };
    return status ? (map[status] || status.toUpperCase()) : 'غير معروف';
}

function updateConnection(connected) {
    if (connected) {
        STATUS_EL.textContent = "متصل";
        STATUS_EL.style.color = "var(--color-success)";
        log("Connected to Real-time Events");
    } else {
        STATUS_EL.textContent = "غير متصل";
        STATUS_EL.style.color = "var(--color-error)";
        log("Disconnected from Events", "error");
    }
}

// Job Rendering
function createJobCard(job) {
    try {
        const tmpl = document.getElementById("job-template");
        if (!tmpl) throw new Error("Job template not found!");
        
        const clone = tmpl.content.cloneNode(true);
        const card = clone.querySelector(".job-card");
        if (!card) throw new Error("Job card element not found in template!");
        
        card.id = `job-${job.id}`;
        
        updateJobElement(card, job);
        
        // Bind Actions
        card.querySelector(".btn-cancel").onclick = () => cancelJob(job.id);
        card.querySelector(".btn-delete").onclick = () => deleteJob(job.id);
        
        return card;
    } catch (e) {
        log(`Error creating card for job ${job.id}: ${e.message}`, "error");
        console.error(e);
        return null;
    }
}

function updateJobElement(card, job) {
    try {
        card.className = `card job-card status-${job.status}`;
        // card.querySelector(".job-id").textContent = `#${job.id.slice(0,8)}`;
        
        const statusEl = card.querySelector(".job-status");
        if(statusEl) statusEl.textContent = formatStatus(job.status);
        
        // Stats
        const stats = job.stats || {};
        const found = stats.found || 0;
        const dl = stats.downloaded || 0;
        const failed = stats.failed || 0;
        const skipped = stats.skipped || 0;
        
        // Progress Calculation
        let pct = 0;
        if (found > 0) {
            const done = dl + skipped + failed;
            pct = (done / found) * 100;
        } else if (job.status === 'completed') {
            pct = 100;
        } else if (job.status === 'running') {
            const bar = card.querySelector(".progress-bar");
            if (bar) bar.classList.add("indeterminate");
        }
        
        const bar = card.querySelector(".progress-bar");
        if (bar) {
            if (pct > 0 || found > 0) bar.classList.remove("indeterminate");
            bar.style.width = `${pct}%`;
        }
        
        // Header Info
        const titleEl = card.querySelector(".job-title");
        if (titleEl) titleEl.textContent = job.title || "جارٍ التحميل...";
        
        const urlLink = card.querySelector(".job-url");
        if (urlLink) {
            urlLink.textContent = job.url;
            urlLink.href = job.url;
        }

        // Details
        let trackText = job.current_track ? `${job.current_track}` :
            (job.status === 'running' ? (found ? 'جارٍ التحميل...' : 'جارٍ الاستكشاف...') : 'في الانتظار');

        if (job.status === 'completed') trackText = 'اكتمل بنجاح';
        if (job.status === 'failed') trackText = 'فشل';
        if (job.status === 'cancelled') trackText = 'ملغى';
        
        const trackInfo = card.querySelector(".track-info");
        if (trackInfo) trackInfo.textContent = trackText;
        
        const statsInfo = card.querySelector(".stats-info");
        if (statsInfo) statsInfo.textContent = `العثور عليها: ${found} | محمّلة: ${dl}/${found} | أخطاء: ${failed}`;
        
        // Error
        const errDiv = card.querySelector(".job-last-error");
        if (errDiv) {
            if (job.error || (job.status === 'failed' && !job.error)) {
                errDiv.style.display = 'block';
                errDiv.textContent = job.error || "Unknown error";
            } else {
                errDiv.style.display = 'none';
            }
        }

        // Buttons
        const btnCancel = card.querySelector(".btn-cancel");
        if (btnCancel) {
            btnCancel.style.display = ['queued', 'running'].includes(job.status) ? 'inline-block' : 'none';
        }
    } catch (e) {
        log(`Error updating job element ${job.id}: ${e.message}`, "error");
        console.error(e);
    }
}

function renderJobs(jobs) {
    JOBS_List.innerHTML = "";
    log(`Rendering ${jobs.length} jobs`);
    jobs.forEach(job => {
        const el = createJobCard(job);
        if (el) {
            JOBS_List.appendChild(el);
            jobsMap[job.id] = job;
        }
    });
}

function updateOrAddJob(job) {
    if (!job || !job.id) return;
    jobsMap[job.id] = job;
    let card = document.getElementById(`job-${job.id}`);
    if (card) {
        updateJobElement(card, job);
    } else {
        card = createJobCard(job);
        if (card) JOBS_List.insertBefore(card, JOBS_List.firstChild);
    }
}

// API Calls
async function fetchJobs() {
    try {
        log("Fetching jobs...");
        const res = await fetch(`${API_BASE}/jobs`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        renderJobs(data.jobs);
    } catch (e) {
        log(`Failed to fetch jobs: ${e.message}`, "error");
    }
}

async function addJob(e) {
    e.preventDefault();
    const fd = new FormData(e.target);
    const data = {
        url: document.getElementById("url-input").value,
        output_dir: fd.get("output_dir"),
        genre: fd.get("genre") || null,
        resume: !!fd.get("resume"),
        tag: !!fd.get("tag"),
        cover: !!fd.get("cover"),
        dry_run: !!fd.get("dry_run"),
        max_items: null 
    };
    
    try {
        log(`Adding job: ${data.url}`);
        await fetch(`${API_BASE}/jobs`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify(data)
        });
        e.target.reset();
    } catch (err) {
        log(`Failed to add job: ${err.message}`, "error");
        alert("فشل إضافة التحميل. يرجى المحاولة مرة أخرى.");
    }
}

async function cancelJob(id) {
    log(`Cancelling job ${id}`);
    await fetch(`${API_BASE}/jobs/${id}/cancel`, {method: "POST"});
}

async function deleteJob(id) {
    if(!confirm("هل تريد حذف هذا التحميل من السجل؟")) return;
    log(`Deleting job ${id}`);
    await fetch(`${API_BASE}/jobs/${id}`, {method: "DELETE"});
}

// SSE
let eventSource = null;
function setupSSE() {
    if (eventSource) eventSource.close();
    
    log("Connecting to SSE...");
    eventSource = new EventSource(`${API_BASE}/events`);
    
    eventSource.onopen = () => updateConnection(true);
    
    eventSource.onerror = () => {
        updateConnection(false);
    };
    
    eventSource.onmessage = (e) => {
        try {
            const msg = JSON.parse(e.data);
            const { event, data } = msg;

            if (event === 'job_created' || event === 'job_updated') {
                updateOrAddJob(data);
            } 
            else if (event === 'job_deleted') {
                const id = data.id;
                const el = document.getElementById(`job-${id}`);
                if (el) el.remove();
                delete jobsMap[id];
            }
        } catch (err) {
            log(`SSE Parse Error: ${err.message}`, "error");
        }
    };
}

// Init
document.getElementById("add-job-form").addEventListener("submit", addJob);
fetchJobs();
setupSSE();

// Removed polling setInterval

