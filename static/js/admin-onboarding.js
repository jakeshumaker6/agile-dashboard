(function() {
    var participants = [];
    var modules = [];
    var activeDay = 1;
    var quill = null;

    // Sidebar
    document.getElementById('hamburger-btn').addEventListener('click', function() {
        document.getElementById('sidebar').classList.toggle('open');
        document.getElementById('sidebar-overlay').classList.toggle('visible');
    });
    document.getElementById('sidebar-overlay').addEventListener('click', function() {
        document.getElementById('sidebar').classList.remove('open');
        this.classList.remove('visible');
    });

    // Tabs
    document.querySelectorAll('.admin-tab').forEach(function(tab) {
        tab.addEventListener('click', function() {
            document.querySelectorAll('.admin-tab').forEach(function(t) { t.classList.remove('active'); });
            document.querySelectorAll('.tab-panel').forEach(function(p) { p.classList.remove('active'); });
            this.classList.add('active');
            document.getElementById('panel-' + this.dataset.tab).classList.add('active');
        });
    });

    // Day filter tabs
    document.querySelectorAll('.day-filter-tab').forEach(function(tab) {
        tab.addEventListener('click', function() {
            document.querySelectorAll('.day-filter-tab').forEach(function(t) { t.classList.remove('active'); });
            this.classList.add('active');
            activeDay = parseInt(this.dataset.day);
            renderModuleList();
        });
    });

    // Modal helpers
    function openModal(id) { document.getElementById(id).classList.add('visible'); }
    function closeModal(id) { document.getElementById(id).classList.remove('visible'); }

    function showAlert(msg, type) {
        var el = document.getElementById('alert-container');
        el.innerHTML = '<div class="alert alert-' + type + '">' + msg + '</div>';
        setTimeout(function() { el.innerHTML = ''; }, 5000);
    }

    function esc(str) {
        if (!str) return '';
        var div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    // --- Quill init (lazy) ---

    function getQuill() {
        if (!quill) {
            quill = new Quill('#quill-editor', {
                theme: 'snow',
                placeholder: 'Module content (HTML)...',
                modules: {
                    toolbar: [
                        ['bold', 'italic'], ['link'],
                        [{ header: [2, 3, false] }],
                        [{ list: 'ordered' }, { list: 'bullet' }],
                        ['clean']
                    ]
                }
            });
        }
        return quill;
    }

    // --- Data loading ---

    async function loadParticipants() {
        try {
            const res = await fetch('/api/admin/onboarding/participants');
            const data = await res.json();
            participants = data.participants || [];
            renderParticipants();
        } catch (err) {
            document.getElementById('participants-tbody').innerHTML =
                '<tr><td colspan="7">Error loading data</td></tr>';
            throw err;
        }
    }

    async function loadModules() {
        try {
            const res = await fetch('/api/admin/onboarding/modules');
            const data = await res.json();
            modules = data.modules || [];
            renderModuleList();
        } catch (err) {
            document.getElementById('modules-list').innerHTML =
                '<div class="loading">Error loading modules</div>';
            throw err;
        }
    }

    async function loadAnalytics() {
        const res = await fetch('/api/admin/onboarding/analytics');
        const data = await res.json();
        document.getElementById('stat-total').textContent = data.total_participants;
        document.getElementById('stat-active').textContent = data.active;
        document.getElementById('stat-completed').textContent = data.completed;
        document.getElementById('stat-satisfaction').textContent =
            data.avg_satisfaction !== null ? data.avg_satisfaction + '/5' : '—';
    }

    // --- Participants ---

    function renderParticipants() {
        var tbody = document.getElementById('participants-tbody');
        if (participants.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--text-muted);padding:32px;">No participants yet. Click "+ New Hire" to get started.</td></tr>';
            return;
        }
        tbody.innerHTML = participants.map(function(p) {
            var pct = p.progress ? p.progress.overall_pct : 0;
            return '<tr>' +
                '<td><strong>' + esc(p.name) + '</strong><br><span style="font-size:11px;color:var(--text-muted);">' + esc(p.email) + '</span></td>' +
                '<td><span class="badge badge-' + p.track + '">' + p.track + '</span></td>' +
                '<td>Day ' + p.current_day + '</td>' +
                '<td><div class="mini-bar"><div class="mini-bar-fill" style="width:' + pct + '%"></div></div>' + pct + '%</td>' +
                '<td><span class="badge badge-' + p.status + '">' + p.status + '</span></td>' +
                '<td>' + p.start_date + '</td>' +
                '<td><button class="btn btn-sm btn-ghost view-btn" data-id="' + p.id + '">View</button></td>' +
                '</tr>';
        }).join('');

        tbody.querySelectorAll('.view-btn').forEach(function(btn) {
            btn.addEventListener('click', function() { showDetail(parseInt(this.dataset.id)); });
        });
    }

    async function showDetail(id) {
        try {
            const res = await fetch('/api/admin/onboarding/participants/' + id);
            const data = await res.json();
            var p = data.participant;
            var prog = data.progress;
            var tools = data.tools || [];
            var modProg = data.module_progress || [];

            document.getElementById('detail-name').textContent = p.name;

            var ticketHtml = '';
            if (p.first_ticket_url) {
                ticketHtml = '<div class="detail-row"><span class="dl">First Ticket</span>' +
                    '<span><a href="' + esc(p.first_ticket_url) + '" target="_blank" style="color:var(--pulse-primary);">View in ClickUp</a>' +
                    ' <button class="btn btn-sm btn-ghost override-ticket-btn" data-id="' + p.id + '" style="margin-left:8px;">Override</button></span></div>';
            } else {
                ticketHtml = '<div class="detail-row"><span class="dl">First Ticket</span>' +
                    '<span><button class="btn btn-sm btn-primary create-ticket-btn" data-id="' + p.id + '">Create First Ticket</button></span></div>';
            }

            var html = '<div class="detail-panel"><h4>Overview</h4>' +
                '<div class="detail-row"><span class="dl">Email</span><span>' + esc(p.email) + '</span></div>' +
                '<div class="detail-row"><span class="dl">Track</span><span class="badge badge-' + p.track + '">' + p.track + '</span></div>' +
                '<div class="detail-row"><span class="dl">Status</span><span class="badge badge-' + p.status + '">' + p.status + '</span></div>' +
                '<div class="detail-row"><span class="dl">Current Day</span><span>Day ' + p.current_day + '</span></div>' +
                '<div class="detail-row"><span class="dl">Progress</span><span>' + prog.overall_pct + '% (' + prog.completed + '/' + prog.total + ')</span></div>' +
                '<div class="detail-row"><span class="dl">Start Date</span><span>' + p.start_date + '</span></div>' +
                ticketHtml +
                (p.satisfaction_rating ? '<div class="detail-row"><span class="dl">Rating</span><span>' + p.satisfaction_rating + '/5</span></div>' : '') +
                '</div>';

            if (tools.length > 0) {
                html += '<div class="detail-panel"><h4>Tool Setup</h4>';
                tools.forEach(function(t) {
                    html += '<div class="detail-row"><span class="dl">' + esc(t.tool_name) + '</span>' +
                        '<span style="color:' + (t.confirmed ? 'var(--green)' : 'var(--red)') + ';">' +
                        (t.confirmed ? '✓ Confirmed' : '○ Pending') + '</span></div>';
                });
                html += '</div>';
            }

            var submissions = modProg.filter(function(mp) { return mp.response_text; });
            if (submissions.length > 0) {
                html += '<div class="detail-panel"><h4>Submissions</h4>';
                submissions.forEach(function(mp) {
                    html += '<div style="margin-bottom:12px;">' +
                        '<div style="font-size:12px;color:var(--text-muted);margin-bottom:4px;">Module #' + mp.module_id + '</div>' +
                        '<div style="font-size:13px;background:var(--card-bg);padding:10px;border-radius:6px;">' + esc(mp.response_text) + '</div></div>';
                });
                html += '</div>';
            }

            // Touchpoint scheduling section
            var schedule = [];
            if (p.touchpoint_schedule) {
                try { schedule = JSON.parse(p.touchpoint_schedule); } catch (e) {}
            }
            html += '<div class="detail-panel"><h4>Touchpoint Schedule</h4>' +
                '<div style="font-size:12px;color:var(--text-muted);margin-bottom:12px;">Set live call times shown to the new hire on each day.</div>';
            [1, 2, 3].forEach(function(day) {
                var tp = schedule.find(function(t) { return t.day === day; }) || {};
                html += '<div style="margin-bottom:12px;">' +
                    '<div style="font-size:11px;font-weight:600;color:var(--text-muted);text-transform:uppercase;margin-bottom:6px;">Day ' + day + '</div>' +
                    '<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:6px;">' +
                    '<input type="text" class="form-control tp-title" data-day="' + day + '" placeholder="Title" value="' + esc(tp.title || '') + '" style="font-size:12px;padding:6px 8px;">' +
                    '<input type="text" class="form-control tp-time" data-day="' + day + '" placeholder="Time (e.g. 10:00 AM)" value="' + esc(tp.time || '') + '" style="font-size:12px;padding:6px 8px;">' +
                    '<input type="text" class="form-control tp-participants" data-day="' + day + '" placeholder="Participants" value="' + esc(tp.participants || '') + '" style="font-size:12px;padding:6px 8px;">' +
                    '</div></div>';
            });
            html += '<div style="margin-top:8px;">' +
                '<button class="btn btn-primary btn-sm save-touchpoints-btn" data-id="' + p.id + '">Save Touchpoints</button>' +
                '</div></div>';

            document.getElementById('detail-content').innerHTML = html;
            openModal('modal-detail');

            // Bind Create Ticket
            var createBtn = document.querySelector('.create-ticket-btn');
            if (createBtn) {
                createBtn.addEventListener('click', function() {
                    createTicket(parseInt(this.dataset.id), this);
                });
            }

            // Bind Override Ticket
            var overrideBtn = document.querySelector('.override-ticket-btn');
            if (overrideBtn) {
                overrideBtn.addEventListener('click', function() {
                    var url = prompt('Paste the ClickUp task URL:');
                    if (url) overrideTicket(parseInt(this.dataset.id), url.trim());
                });
            }

            // Bind Save Touchpoints
            var saveTPBtn = document.querySelector('.save-touchpoints-btn');
            if (saveTPBtn) {
                saveTPBtn.addEventListener('click', function() {
                    var pid = parseInt(this.dataset.id);
                    var newSchedule = [];
                    [1, 2, 3].forEach(function(day) {
                        var title = (document.querySelector('.tp-title[data-day="' + day + '"]').value || '').trim();
                        var time = (document.querySelector('.tp-time[data-day="' + day + '"]').value || '').trim();
                        var participants = (document.querySelector('.tp-participants[data-day="' + day + '"]').value || '').trim();
                        if (title) newSchedule.push({ day: day, title: title, time: time, participants: participants });
                    });
                    saveTouchpoints(pid, newSchedule, saveTPBtn);
                });
            }
        } catch (err) {
            showAlert('Error loading participant details.', 'error');
            throw err;
        }
    }

    async function createTicket(participantId, btn) {
        btn.disabled = true;
        btn.textContent = 'Creating…';
        try {
            const res = await fetch(
                '/api/admin/onboarding/participants/' + participantId + '/create-ticket',
                { method: 'POST' }
            );
            const data = await res.json();
            if (data.first_ticket_url) {
                btn.parentElement.innerHTML = '<a href="' + esc(data.first_ticket_url) +
                    '" target="_blank" style="color:var(--pulse-primary);">View in ClickUp</a>';
                showAlert('ClickUp task created.', 'success');
            } else {
                btn.disabled = false;
                btn.textContent = 'Create First Ticket';
                showAlert('Could not create ClickUp task. Check server logs.', 'error');
            }
        } catch (err) {
            btn.disabled = false;
            btn.textContent = 'Create First Ticket';
            showAlert('Network error creating ticket.', 'error');
            throw err;
        }
    }

    async function saveTouchpoints(participantId, schedule, btn) {
        btn.disabled = true;
        btn.textContent = 'Saving…';
        try {
            const res = await fetch('/api/admin/onboarding/participants/' + participantId, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ touchpoint_schedule: JSON.stringify(schedule) })
            });
            if (!res.ok) throw new Error('Failed to save');
            showAlert('Touchpoint schedule saved.', 'success');
            btn.textContent = 'Saved ✓';
        } catch (err) {
            showAlert('Error saving touchpoints.', 'error');
            btn.disabled = false;
            btn.textContent = 'Save Touchpoints';
            throw err;
        }
    }

    async function overrideTicket(participantId, url) {
        try {
            const res = await fetch('/api/admin/onboarding/participants/' + participantId,
                { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ first_ticket_url: url }) });
            if (!res.ok) throw new Error('Failed to update');
            showAlert('Ticket URL updated.', 'success');
            showDetail(participantId);
        } catch (err) {
            showAlert('Error updating ticket URL.', 'error');
            throw err;
        }
    }

    // --- Modules ---

    function renderModuleList() {
        var container = document.getElementById('modules-list');
        var dayMods = modules.filter(function(m) { return m.day === activeDay; });

        if (modules.length === 0) {
            container.innerHTML = '<div class="card" style="text-align:center;color:var(--text-muted);">No modules yet. Click "Seed Content" to populate default modules.</div>';
            return;
        }
        if (dayMods.length === 0) {
            container.innerHTML = '<div class="card" style="text-align:center;color:var(--text-muted);">No modules for Day ' + activeDay + '.</div>';
            return;
        }

        container.innerHTML = dayMods.map(function(m) {
            return '<div class="module-row" data-module-id="' + m.id + '">' +
                '<div class="module-row-info">' +
                '<strong>' + esc(m.title) + '</strong>' +
                '<div class="module-row-meta">' +
                '<span class="badge badge-' + m.track + '" style="margin-right:4px;">' + m.track + '</span>' +
                m.content_type + ' — ' + m.estimated_minutes + ' min' +
                '</div></div>' +
                '<div class="module-row-actions">' +
                '<button class="btn btn-sm btn-ghost edit-mod-btn" data-id="' + m.id + '">Edit</button>' +
                '<button class="btn btn-sm btn-danger del-mod-btn" data-id="' + m.id + '">Delete</button>' +
                '</div></div>';
        }).join('');

        container.querySelectorAll('.edit-mod-btn').forEach(function(btn) {
            btn.addEventListener('click', function() { openModuleEditor(parseInt(this.dataset.id)); });
        });
        container.querySelectorAll('.del-mod-btn').forEach(function(btn) {
            btn.addEventListener('click', function() { deleteModule(parseInt(this.dataset.id)); });
        });
    }

    function openModuleEditor(moduleId) {
        var q = getQuill();
        if (moduleId) {
            var mod = modules.find(function(m) { return m.id === moduleId; });
            if (!mod) return;
            document.getElementById('module-modal-title').textContent = 'Edit Module';
            document.getElementById('mod-id').value = mod.id;
            document.getElementById('mod-title').value = mod.title || '';
            document.getElementById('mod-day').value = mod.day;
            document.getElementById('mod-slug').value = mod.slug || '';
            document.getElementById('mod-type').value = mod.content_type || 'text';
            document.getElementById('mod-track').value = mod.track || 'all';
            document.getElementById('mod-minutes').value = mod.estimated_minutes || 15;
            document.getElementById('mod-loom').value = mod.loom_url || '';
            q.root.innerHTML = mod.content_html || '';
        } else {
            document.getElementById('module-modal-title').textContent = 'Add Module';
            document.getElementById('mod-id').value = '';
            document.getElementById('mod-title').value = '';
            document.getElementById('mod-day').value = activeDay;
            document.getElementById('mod-slug').value = '';
            document.getElementById('mod-type').value = 'text';
            document.getElementById('mod-track').value = 'all';
            document.getElementById('mod-minutes').value = 15;
            document.getElementById('mod-loom').value = '';
            q.root.innerHTML = '';
        }
        openModal('modal-module');
    }

    document.getElementById('btn-add-module').addEventListener('click', function() { openModuleEditor(null); });
    document.getElementById('btn-cancel-module').addEventListener('click', function() { closeModal('modal-module'); });

    document.getElementById('btn-save-module').addEventListener('click', async function() {
        var modId = document.getElementById('mod-id').value;
        var q = getQuill();

        var payload = {
            title: document.getElementById('mod-title').value.trim(),
            day: parseInt(document.getElementById('mod-day').value),
            slug: document.getElementById('mod-slug').value.trim(),
            content_type: document.getElementById('mod-type').value,
            track: document.getElementById('mod-track').value,
            estimated_minutes: parseInt(document.getElementById('mod-minutes').value) || 15,
            loom_url: document.getElementById('mod-loom').value.trim() || null,
            content_html: q.root.innerHTML,
        };

        if (!payload.title || !payload.slug) {
            showAlert('Title and slug are required.', 'error');
            return;
        }

        try {
            var url = modId
                ? '/api/admin/onboarding/modules/' + modId
                : '/api/admin/onboarding/modules';
            var method = modId ? 'PUT' : 'POST';
            const res = await fetch(url, {
                method: method,
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const data = await res.json();
            if (!res.ok) {
                showAlert(data.error || 'Failed to save module.', 'error');
                return;
            }
            closeModal('modal-module');
            showAlert('Module saved.', 'success');
            loadModules();
        } catch (err) {
            showAlert('Network error saving module.', 'error');
            throw err;
        }
    });

    async function deleteModule(moduleId) {
        if (!confirm('Delete this module? This cannot be undone.')) return;
        try {
            const res = await fetch('/api/admin/onboarding/modules/' + moduleId, { method: 'DELETE' });
            if (!res.ok) throw new Error('Delete failed');
            showAlert('Module deleted.', 'success');
            loadModules();
        } catch (err) {
            showAlert('Error deleting module.', 'error');
            throw err;
        }
    }

    // --- New participant ---

    document.getElementById('btn-new-participant').addEventListener('click', function() { openModal('modal-new'); });
    document.getElementById('btn-cancel-new').addEventListener('click', function() { closeModal('modal-new'); });
    document.getElementById('btn-close-detail').addEventListener('click', function() { closeModal('modal-detail'); });

    document.getElementById('btn-save-new').addEventListener('click', async function() {
        var name = document.getElementById('inp-name').value.trim();
        var email = document.getElementById('inp-email').value.trim();
        var track = document.getElementById('inp-track').value;
        var startDate = document.getElementById('inp-start-date').value;

        if (!name || !email || !startDate) {
            showAlert('Please fill in all required fields.', 'error');
            return;
        }

        try {
            const res = await fetch('/api/admin/onboarding/participants', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: name, email: email, track: track, start_date: startDate })
            });
            const data = await res.json();
            if (!res.ok) {
                showAlert(data.error || 'Failed to create participant.', 'error');
                return;
            }
            closeModal('modal-new');
            document.getElementById('inp-name').value = '';
            document.getElementById('inp-email').value = '';
            document.getElementById('inp-start-date').value = '';
            showAlert('Onboarding initiated for ' + name + '.', 'success');
            loadParticipants();
            loadAnalytics();
        } catch (err) {
            showAlert('Network error. Please try again.', 'error');
            throw err;
        }
    });

    // --- Seed + Initial load ---

    document.getElementById('btn-seed').addEventListener('click', async function() {
        try {
            const res = await fetch('/api/onboarding/seed', { method: 'POST' });
            const data = await res.json();
            showAlert(data.message, 'success');
            loadModules();
        } catch (err) {
            showAlert('Error seeding content.', 'error');
            throw err;
        }
    });

    loadParticipants();
    loadModules();
    loadAnalytics();
})();
