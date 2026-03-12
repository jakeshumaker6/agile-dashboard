(function() {
    const DAY_THEMES = {
        1: { title: 'Day 1', subtitle: 'Welcome to Pulse', theme: 'Culture & Identity' },
        2: { title: 'Day 2', subtitle: 'Your Toolkit', theme: 'Tools & Process' },
        3: { title: 'Day 3', subtitle: 'Your Impact', theme: 'Clients & Contribution' }
    };
    const NUDGES = [
        "Unwavering Integrity — we do what we say we'll do.",
        "Trailblazing Creativity — you don't settle for good enough.",
        "Speed is your superpower. Let's keep moving."
    ];
    const FORMAT_ICONS = {
        text: '\uD83D\uDCC4', loom: '\uD83C\uDFA5', checklist: '\u2611',
        form: '\u270F', exercise: '\uD83D\uDCA1', mixed: '\uD83D\uDD2C'
    };

    let state = null;

    // Sidebar
    document.getElementById('hamburger-btn').addEventListener('click', function() {
        document.getElementById('sidebar').classList.toggle('open');
        document.getElementById('sidebar-overlay').classList.toggle('visible');
    });
    document.getElementById('sidebar-overlay').addEventListener('click', function() {
        document.getElementById('sidebar').classList.remove('open');
        this.classList.remove('visible');
    });

    async function loadData() {
        try {
            const res = await fetch('/api/onboarding/my-progress');
            const data = await res.json();
            document.getElementById('loading').style.display = 'none';

            if (!data.active) {
                document.getElementById('no-onboarding').style.display = 'block';
                return;
            }
            state = data;
            render();
        } catch (err) {
            document.getElementById('loading').textContent = 'Error loading onboarding data.';
            throw err;
        }
    }

    function render() {
        const app = document.getElementById('onboarding-app');
        app.style.display = 'block';
        const p = state.participant;
        const prog = state.progress;

        if (p.status === 'completed') {
            app.querySelector('#welcome-banner').style.display = 'none';
            app.querySelector('.overall-progress').style.display = 'none';
            app.querySelector('#progress-stats').style.display = 'none';
            app.querySelector('#day-tabs').style.display = 'none';
            app.querySelector('#day-panels').style.display = 'none';
            document.getElementById('completion-screen').style.display = 'block';
            return;
        }

        document.getElementById('welcome-heading').textContent =
            'Welcome to Pulse, ' + p.name.split(' ')[0] + '!';
        if (p.welcome_message) {
            document.getElementById('welcome-text').innerHTML = p.welcome_message;
        }

        document.getElementById('progress-label').textContent = prog.overall_pct + '% Complete';
        document.getElementById('progress-detail').textContent =
            prog.completed + ' of ' + prog.total + ' modules';
        document.getElementById('progress-bar').style.width = prog.overall_pct + '%';
        document.getElementById('nudge-text').textContent =
            NUDGES[Math.floor(Math.random() * NUDGES.length)];

        document.getElementById('progress-stats').innerHTML =
            '<div class="progress-stat"><div class="number">' + prog.overall_pct + '%</div><div class="label">Overall</div></div>' +
            '<div class="progress-stat"><div class="number">Day ' + p.current_day + '</div><div class="label">Current Day</div></div>' +
            '<div class="progress-stat"><div class="number">' + prog.completed + '/' + prog.total + '</div><div class="label">Modules Done</div></div>' +
            '<div class="progress-stat"><div class="number">' + prog.remaining_minutes + 'm</div><div class="label">Est. Remaining</div></div>';

        renderDayTabs(p.current_day, prog.days);
        renderDayPanels(p.current_day);
    }

    function renderDayTabs(currentDay, dayStats) {
        const container = document.getElementById('day-tabs');
        container.innerHTML = '';
        for (let d = 1; d <= 3; d++) {
            const info = DAY_THEMES[d];
            const ds = dayStats[d] || { total: 0, completed: 0, pct: 0 };
            const locked = d > currentDay;
            const tab = document.createElement('div');
            tab.className = 'day-tab' + (d === currentDay ? ' active' : '') + (locked ? ' locked' : '');
            tab.innerHTML =
                '<div class="tab-title">' + info.title + '</div>' +
                '<div class="tab-sub">' + info.subtitle + '</div>' +
                (locked
                    ? '<div class="tab-progress" style="color:var(--text-muted);">\uD83D\uDD12 Locked</div>'
                    : '<div class="tab-progress">' + ds.completed + '/' + ds.total + ' complete</div>');
            if (!locked) {
                tab.addEventListener('click', (function(day) {
                    return function() { switchDay(day); };
                })(d));
            }
            container.appendChild(tab);
        }
    }

    function switchDay(day) {
        document.querySelectorAll('.day-tab').forEach(function(t, i) {
            t.classList.toggle('active', i + 1 === day);
        });
        document.querySelectorAll('.day-content').forEach(function(panel) {
            panel.classList.toggle('active', panel.dataset.day === String(day));
        });
    }

    function renderDayPanels(currentDay) {
        const container = document.getElementById('day-panels');
        container.innerHTML = '';
        for (let d = 1; d <= 3; d++) {
            const panel = document.createElement('div');
            panel.className = 'day-content' + (d === currentDay ? ' active' : '');
            panel.dataset.day = d;

            if (d > currentDay) {
                panel.innerHTML = '<div class="day-locked"><div class="lock-icon">\uD83D\uDD12</div>' +
                    '<p>Complete Day ' + (d - 1) + ' to unlock Day ' + d + '.</p></div>';
                container.appendChild(panel);
                continue;
            }

            const modules = state.days[String(d)] || [];
            panel.innerHTML += renderTouchpoints(d);
            modules.forEach(function(mod) { panel.innerHTML += renderModuleCard(mod); });
            if (d === 3) {
                panel.innerHTML += '<div class="projects-section" id="your-projects-section">' +
                    '<h3>Your Projects</h3>' +
                    '<div class="projects-placeholder">Loading your assigned projects\u2026</div>' +
                    '</div>';
            }
            container.appendChild(panel);
        }
        bindModuleEvents();
        if (currentDay >= 3) {
            loadProjects();
        }
    }

    async function loadProjects() {
        var section = document.getElementById('your-projects-section');
        if (!section) return;
        try {
            const res = await fetch('/api/onboarding/my-projects');
            const data = await res.json();
            var projects = data.projects || [];
            if (projects.length === 0) {
                section.querySelector('.projects-placeholder').textContent =
                    'Your ClickUp projects will appear here once you\u2019re assigned to them.';
                return;
            }
            var html = '<h3>Your Projects</h3>' +
                projects.map(function(g) {
                    return '<div class="project-group">' +
                        '<div class="project-group-header">' +
                        '<span class="project-client">' + escHtml(g.client) + '</span>' +
                        '<span class="project-name">\u2014 ' + escHtml(g.project) + '</span>' +
                        '</div>' +
                        g.tasks.map(function(t) {
                            return '<div class="project-task">' +
                                '<a href="' + escHtml(t.url) + '" target="_blank" rel="noopener">' +
                                escHtml(t.name) + '</a>' +
                                (t.status ? '<span class="task-status">' + escHtml(t.status) + '</span>' : '') +
                                '</div>';
                        }).join('') +
                        '</div>';
                }).join('');
            section.innerHTML = html;
        } catch (err) {
            if (section) {
                section.querySelector('.projects-placeholder').textContent =
                    'Could not load projects right now.';
            }
            throw err;
        }
    }

    function renderTouchpoints(day) {
        let schedule = state.participant.touchpoint_schedule;
        if (!schedule) return '';
        if (typeof schedule === 'string') {
            try { schedule = JSON.parse(schedule); } catch (e) { return ''; }
        }
        if (!Array.isArray(schedule)) return '';
        return schedule
            .filter(function(tp) { return tp.day === day; })
            .map(function(tp) {
                return '<div class="touchpoint-card">' +
                    '<div class="touchpoint-icon">\uD83D\uDCDE</div>' +
                    '<div class="touchpoint-info"><h4>' + (tp.title || 'Live Call') + '</h4>' +
                    '<p>' + (tp.time || '') + (tp.participants ? ' \u2014 ' + tp.participants : '') + '</p>' +
                    '<p>' + (tp.description || '') + '</p></div></div>';
            }).join('');
    }

    function renderModuleCard(mod) {
        const isComplete = mod.progress_status === 'completed';
        const icon = FORMAT_ICONS[mod.content_type] || FORMAT_ICONS.text;
        return '<div class="module-card' + (isComplete ? ' completed' : '') +
            '" data-module-id="' + mod.id + '">' +
            '<div class="module-header">' +
            '<div class="module-status">' + (isComplete ? '\u2713' : '') + '</div>' +
            '<div class="module-meta"><h4>' + escHtml(mod.title) + '</h4>' +
            '<div class="meta-row">' +
            '<span class="meta-tag">' + icon + ' ' + mod.content_type + '</span>' +
            '<span class="meta-tag">\u23F1 ' + mod.estimated_minutes + ' min</span>' +
            (mod.track !== 'all' ? '<span class="meta-tag" style="color:var(--blue);">' + mod.track + ' track</span>' : '') +
            '</div></div>' +
            '<div class="module-chevron">\u25B6</div></div>' +
            '<div class="module-body">' + renderModuleBody(mod) + '</div></div>';
    }

    function renderModuleBody(mod) {
        let html = '';

        if (mod.loom_url) {
            html += '<div class="loom-embed"><iframe src="' + escHtml(mod.loom_url) +
                '" allowfullscreen></iframe></div>';
        } else if (mod.content_type === 'loom') {
            html += '<div class="loom-embed"><div class="loom-placeholder">Video coming soon</div></div>';
        }

        if (mod.content_html) {
            html += '<div class="module-content">' + mod.content_html + '</div>';
        }

        if (mod.content_type === 'checklist') {
            html = renderToolChecklist(html);
        }

        if (mod.content_type === 'form' || mod.content_type === 'exercise') {
            html += '<div class="form-area"><textarea placeholder="Type your response here..." ' +
                'data-module-id="' + mod.id + '">' + escHtml(mod.response_text || '') + '</textarea></div>';
        }

        if (mod.slug === 'd3-checkpoint') {
            html += renderSatisfactionSurvey();
        }

        if (mod.progress_status !== 'completed') {
            html += '<div class="module-actions"><button class="btn btn-primary complete-btn" ' +
                'data-module-id="' + mod.id + '">Mark Complete</button></div>';
        } else {
            html += '<div class="module-actions"><span style="color:var(--green);font-size:13px;' +
                'font-weight:500;">\u2713 Completed</span></div>';
        }

        return html;
    }

    function renderToolChecklist(bodyHtml) {
        const toolMap = {};
        (state.tools || []).forEach(function(t) { toolMap[t.tool_name] = t.confirmed; });
        return bodyHtml.replace(/<div class="tool-card" data-tool="([^"]+)">/g, function(match, key) {
            const confirmed = toolMap[key];
            return '<div class="tool-card" data-tool="' + key + '">' +
                '<div class="tool-check' + (confirmed ? ' confirmed' : '') +
                '" data-tool-key="' + key + '">' + (confirmed ? '\u2713' : '') + '</div>';
        });
    }

    function renderSatisfactionSurvey() {
        const current = state.participant.satisfaction_rating || 0;
        let stars = '<div class="rating-stars">';
        for (let i = 1; i <= 5; i++) {
            stars += '<div class="star' + (i <= current ? ' active' : '') +
                '" data-rating="' + i + '">\u2605</div>';
        }
        return stars + '</div>';
    }

    function bindModuleEvents() {
        document.querySelectorAll('.module-header').forEach(function(header) {
            header.addEventListener('click', function() {
                this.parentElement.classList.toggle('expanded');
            });
        });

        document.querySelectorAll('.complete-btn').forEach(function(btn) {
            btn.addEventListener('click', function(e) {
                e.stopPropagation();
                const moduleId = this.dataset.moduleId;
                const textarea = document.querySelector('textarea[data-module-id="' + moduleId + '"]');
                completeModule(moduleId, textarea ? textarea.value : null);
            });
        });

        document.querySelectorAll('.tool-check:not(.confirmed)').forEach(function(check) {
            check.addEventListener('click', function(e) {
                e.stopPropagation();
                confirmToolSetup(this.dataset.toolKey, this);
            });
        });

        document.querySelectorAll('.star').forEach(function(star) {
            star.addEventListener('click', function(e) {
                e.stopPropagation();
                submitRating(parseInt(this.dataset.rating));
            });
        });
    }

    async function completeModule(moduleId, responseText) {
        const body = { status: 'completed' };
        if (responseText) body.response_text = responseText;
        try {
            const res = await fetch('/api/onboarding/progress/' + moduleId, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            });
            if (!res.ok) throw new Error('Failed to update progress');
            await loadData();
        } catch (err) {
            alert('Error saving progress. Please try again.');
            throw err;
        }
    }

    async function confirmToolSetup(toolKey, el) {
        try {
            const res = await fetch('/api/onboarding/tool-setup/' + toolKey, { method: 'POST' });
            if (!res.ok) throw new Error('Failed to confirm tool');
            el.classList.add('confirmed');
            el.innerHTML = '\u2713';
        } catch (err) {
            alert('Error confirming tool setup.');
            throw err;
        }
    }

    async function submitRating(rating) {
        try {
            const res = await fetch('/api/onboarding/satisfaction', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ rating: rating })
            });
            if (!res.ok) throw new Error('Failed to submit rating');
            document.querySelectorAll('.star').forEach(function(s, i) {
                s.classList.toggle('active', i < rating);
            });
        } catch (err) {
            alert('Error submitting rating.');
            throw err;
        }
    }

    function escHtml(str) {
        if (!str) return '';
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    loadData();
})();
