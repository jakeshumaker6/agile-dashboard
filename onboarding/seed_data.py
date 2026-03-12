"""
Static content data for the 3-day onboarding curriculum.

Imported by seed_content.py — contains all module HTML and tool definitions.
"""

# ---------------------------------------------------------------------------
# Tool list — used by the Day 2 checklist module
# ---------------------------------------------------------------------------

TOOLS = [
    {"name": "Gmail & Google Workspace", "key": "google_workspace", "instructions": (
        "Log in with your @pulsemarketing.co credentials (sent to your personal "
        "email). Typically firstName@pulsemarketing.co or "
        "firstName.lastName@pulsemarketing.co. Configure Google Calendar settings "
        "and working hours. Familiarize yourself with the Google Drive folder "
        "structure: client folders, internal docs, SOPs, and templates.")},
    {"name": "Slack", "key": "slack", "instructions": (
        "Accept the Slack invite sent to your work email. Complete your profile "
        "(photo, title, time zone). Configure notification preferences. Message "
        "Jake Shumaker to confirm you are set up. Key channels: #general "
        "(announcements), #random (team bonding), #testimonials (wins). Use "
        "threads, @mention for attention, DMs for urgent, emoji reactions to "
        "signal acknowledgment.")},
    {"name": "ClickUp", "key": "clickup", "instructions": (
        "Accept the ClickUp invite sent to your work email. Complete your profile "
        "and set your time zone. Familiarize yourself with the workspace structure "
        "(Spaces = departments, Folders = clients, Lists = projects, Tasks = "
        "deliverables). Install the desktop app. Daily habits: check 'My Tasks' "
        "each morning, update statuses, log time, add comments.")},
    {"name": "1Password", "key": "1password", "instructions": (
        "Accept the 1Password invite. Create your master password (strong, "
        "memorable). Install the browser extension (Chrome recommended) and "
        "desktop app. Vault structure: Private (your personal work passwords), "
        "Pulse - Shared (team-wide credentials), Pulse - Client [Name] "
        "(client-specific logins). Never share passwords outside of 1Password.")},
    {"name": "Loom", "key": "loom", "instructions": (
        "Accept the Loom invite. Install the desktop app and Chrome extension. "
        "Test camera and microphone. Use Loom when explaining something easier "
        "to show than write, for client walkthroughs, design feedback, and "
        "quick updates.")},
    {"name": "Grain", "key": "grain", "instructions": (
        "Accept the Grain invite. Connect your Google Calendar. Configure "
        "recording preferences (Grain can auto-join meetings). Review AI summary "
        "settings. Always inform clients calls are recorded. Review AI summaries "
        "for accuracy before sharing.")},
    {"name": "Figma", "key": "figma", "instructions": (
        "Accept the Figma invite. Familiarize yourself with view mode vs. edit "
        "mode. Learn how to leave comments on designs. For non-designers: your "
        "role is reviewing designs via comments, accessing brand assets and "
        "style guides, and viewing mockups before development.")},
    {"name": "GitHub", "key": "github", "instructions": (
        "Accept the GitHub org invite. Configure SSH keys or personal access "
        "token. Clone relevant repos per your project assignments. Engineer "
        "track gets active repo access; General track gets read access.")},
    {"name": "Claude Team", "key": "claude_team", "instructions": (
        "Accept the Claude Team invite. Log in and explore the interface. "
        "Review any team-shared prompts or projects. Reference: AI Operations "
        "Guide in ClickUp. Tips: be specific about context, provide output "
        "examples, iterate prompts, always review before sending to clients.")},
    {"name": "Superhuman", "key": "superhuman", "instructions": (
        "Accept the Superhuman invite. Complete the onboarding tutorial within "
        "Superhuman. Configure keyboard shortcuts and workflows.")},
]


def _tool_checklist_html():
    items = [
        f'<div class="tool-card" data-tool="{t["key"]}">'
        f'<h4>{t["name"]}</h4><p>{t["instructions"]}</p></div>'
        for t in TOOLS
    ]
    note = (
        "<p><strong>Note:</strong> All invitations are sent to your work email "
        "during Day 1 or Day 2. If you haven't received access to any tool by "
        "the end of Day 2, reach out to Jake Shumaker.</p>"
    )
    return "\n".join(items) + note


# ---------------------------------------------------------------------------
# Day 1 modules — Welcome to Pulse
# ---------------------------------------------------------------------------

DAY_1_MODULES = [
    {"slug": "d1-welcome-video", "title": "Welcome Video", "content_type": "loom",
     "loom_url": "", "estimated_minutes": 5, "track": "all", "content_html": (
         "<p>Watch Sean and Jake's welcome message reinforcing what was discussed "
         "in the live Welcome Call. You'll hear the Pulse origin story and what "
         "makes this place different.</p>"
         "<p><em>Loom video coming soon — your admin will add the URL once recorded.</em></p>")},
    {"slug": "d1-mission-vision-values", "title": "Mission, Vision & Values",
     "content_type": "text", "estimated_minutes": 15, "track": "all", "content_html": (
         "<h3>Our Mission</h3>"
         "<p><strong>\"With you, delivering tomorrow's technology today at the "
         "speed your organization demands.\"</strong></p>"
         "<h3>Our Vision</h3>"
         "<p>To be the Deloitte Digital for the non-Fortune 500 in the Midwest.</p>"
         "<h3>Our Values</h3>"
         "<h4>Unwavering Integrity</h4>"
         "<p>\"We do what we say we'll do. Our word is our bond with clients, "
         "with each other, and in every decision we make.\"</p>"
         "<p><em>In practice:</em> When we told Strategic Wealth Group we'd deliver "
         "their AI roadmap in two weeks, we worked nights to hit that date. When we "
         "found a bug in GAAPP's chatbot the day before launch, we disclosed it "
         "immediately and fixed it before go-live.</p>"
         "<h4>Trailblazing Creativity</h4>"
         "<p>\"We don't settle for 'good enough' or follow the same playbook "
         "everyone else uses.\"</p>"
         "<p><em>In practice:</em> For DCC Marketing, instead of a standard CRM, we "
         "built an AI-powered RFP management platform. For National Concerts, we "
         "designed a digital sales room that replaced a manual PDF-based process.</p>"
         "<h4>Speed Is Our Superpower</h4>"
         "<p>\"In a world where most agencies move slowly, we move fast. "
         "We ship, iterate, and improve.\"</p>"
         "<p><em>In practice:</em> We shipped GAAPP's Asthma Care Map MVP in under "
         "3 weeks. We launched South Forty's e-commerce migration in a single sprint. "
         "Our 1-week sprint cadence means clients see progress every single week.</p>")},
    {"slug": "d1-meet-the-team", "title": "Meet the Team",
     "content_type": "text", "estimated_minutes": 10, "track": "all", "content_html": (
         "<h3>The Pulse Team</h3>"
         "<div class='team-grid'>"
         "<div class='team-member'><strong>Sean Miller</strong> — CEO<br>"
         "Vision, strategy, business development, HR.</div>"
         "<div class='team-member'><strong>Jake Shumaker</strong> — COO<br>"
         "Operations, project delivery, technology.</div>"
         "<div class='team-member'><strong>Walter Miller</strong> — AI Architect<br>"
         "Designs and implements AI solutions across client projects.</div>"
         "<div class='team-member'><strong>Bart Stoppel</strong> — Engineer<br>"
         "Full-stack development across client projects.</div>"
         "<div class='team-member'><strong>Sam Gohel</strong> — Jr. Engineer<br>"
         "Frontend and backend development.</div>"
         "<div class='team-member'><strong>Adri Andika</strong> — Designer<br>"
         "UI/UX design, brand assets, and visual identity.</div>"
         "<div class='team-member'><strong>Luke Shumaker</strong> — PM Intern<br>"
         "Project coordination and client communication support.</div>"
         "<div class='team-member'><strong>Raz Crisan</strong> — Salesforce Contractor<br>"
         "Salesforce CRM implementation and optimization.</div>"
         "</div>"
         "<p><strong>Who to go to:</strong> Jake for operations, delivery, or "
         "technical questions. Sean for strategy, client relationships, or HR. "
         "Your project lead for day-to-day task questions.</p>")},
    {"slug": "d1-how-we-win", "title": "How We Win",
     "content_type": "text", "estimated_minutes": 10, "track": "all", "content_html": (
         "<h3>Vision / Traction Organizer</h3>"
         "<h4>10-Year Target</h4>"
         "<p>~$660M revenue, lowest headcount in the industry, #1 consulting "
         "agency in Indianapolis.</p>"
         "<h4>3-Year Picture</h4><p>$10M ARR. Best AI-focused integrated agency in the Midwest.</p>"
         "<h4>2026 Goals</h4>"
         "<ul><li><strong>$2M revenue</strong></li>"
         "<li><strong>10x operational efficiency</strong> — turning 10hr tasks into "
         "1hr tasks through AI and process</li></ul>"
         "<h3>Our Proven Process</h3>"
         "<p><strong>Discover &rarr; Prove &rarr; Scale</strong></p>"
         "<ol>"
         "<li><strong>Discover:</strong> Deep-dive into the client's business, pain "
         "points, and opportunities. Audit current state.</li>"
         "<li><strong>Prove:</strong> Build a focused MVP that demonstrates value fast.</li>"
         "<li><strong>Scale:</strong> Expand based on proven results. Add features, "
         "integrations, and optimization.</li></ol>")},
    {"slug": "d1-what-we-do", "title": "What We Do",
     "content_type": "text", "estimated_minutes": 10, "track": "all", "content_html": (
         "<h3>Technology Services</h3>"
         "<ul><li>Website development</li><li>Custom application development</li>"
         "<li>AI consulting and implementation</li>"
         "<li>CRM implementation and optimization</li>"
         "<li>Platform integrations and data pipelines</li></ul>"
         "<h3>Growth Services</h3>"
         "<ul><li>AEO / SEO</li><li>Paid advertising (Google Ads, Meta, LinkedIn)</li>"
         "<li>Content strategy</li><li>Marketing automation</li>"
         "<li>Analytics and reporting</li><li>Brand strategy and positioning</li></ul>"
         "<p>These two areas are interconnected — our technology powers our growth "
         "services, and our growth insights inform what we build.</p>")},
    {"slug": "d1-checkpoint", "title": "Day 1 Checkpoint",
     "content_type": "form", "estimated_minutes": 5, "track": "all", "content_html": (
         "<h3>Reflection</h3>"
         "<p>Which Pulse core value resonates most with you and why? "
         "This isn't graded — it's a conversation starter for your next "
         "touchpoint with Jake or Sean.</p>")},
]

# ---------------------------------------------------------------------------
# Day 2 modules — Your Toolkit
# ---------------------------------------------------------------------------

DAY_2_MODULES = [
    {"slug": "d2-tool-setup", "title": "Tool Setup Checklist",
     "content_type": "checklist", "estimated_minutes": 45, "track": "all",
     "content_html": _tool_checklist_html()},
    {"slug": "d2-how-we-ship", "title": "How We Ship",
     "content_type": "loom", "loom_url": "", "estimated_minutes": 15, "track": "all",
     "content_html": (
         "<h3>Agile at Pulse</h3>"
         "<p>We run <strong>1-week sprints</strong> with sprint planning, daily standups, and retros.</p>"
         "<h4>ClickUp Workspace Structure</h4>"
         "<ul><li><strong>Spaces</strong> = Internal departments</li>"
         "<li><strong>Folders</strong> = Clients</li>"
         "<li><strong>Lists</strong> = Projects</li>"
         "<li><strong>Tasks</strong> = Individual deliverables</li></ul>"
         "<h4>Task Statuses</h4>"
         "<p><code>Backlog &rarr; In Progress &rarr; Ready for Review &rarr; "
         "Awaiting Response &rarr; Complete</code></p>"
         "<h4>Daily Habits</h4>"
         "<ol><li>Check \"My Tasks\" each morning</li>"
         "<li>Update task statuses as you work</li>"
         "<li>Log time on tasks</li><li>Add comments for context</li></ol>"
         "<h4>Definition of Done</h4>"
         "<p>A task is complete when it meets acceptance criteria, has been reviewed, "
         "and the client or internal stakeholder has approved the deliverable.</p>"
         "<p><em>Loom walkthrough coming soon.</em></p>")},
    {"slug": "d2-claude-home-base", "title": "Claude as Home Base",
     "content_type": "exercise", "loom_url": "", "estimated_minutes": 20, "track": "all",
     "content_html": (
         "<h3>Claude: Your AI Co-Pilot at Pulse</h3>"
         "<p>Claude is Pulse's core productivity tool. Here's how we use it:</p>"
         "<h4>MCP Integrations</h4>"
         "<p>Claude connects to: ClickUp, Slack, HubSpot, Figma, Grain, Google Drive, "
         "and Gmail — letting you work across tools from one interface.</p>"
         "<h4>Common Use Cases</h4>"
         "<ul><li>Drafting client emails, proposals, and reports</li>"
         "<li>Research and competitive analysis</li>"
         "<li>Brainstorming campaign ideas</li>"
         "<li>Summarizing documents and meeting notes</li>"
         "<li>Technical troubleshooting and documentation</li></ul>"
         "<h4>Tips for Better Results</h4>"
         "<ul><li>Be specific about context</li>"
         "<li>Provide examples of desired output style</li>"
         "<li>Iterate and refine prompts</li>"
         "<li>Always review AI-generated content before sending to clients</li></ul>"
         "<h4>Hands-On Exercise</h4>"
         "<p>Complete a simple task using Claude relevant to your role. "
         "Submit the output below.</p>"
         "<p><em>Loom overview coming soon.</em></p>")},
    {"slug": "d2-engineering", "title": "Engineering at Pulse",
     "content_type": "loom", "loom_url": "", "estimated_minutes": 15, "track": "engineer",
     "content_html": (
         "<h3>AI-Native Development</h3>"
         "<p>At Pulse, we embrace AI-native development. Claude Code, Cursor, and "
         "Lovable are core parts of our workflow.</p>"
         "<h4>Key Principles</h4>"
         "<ul><li><strong>Ship quality, not vibe code.</strong> AI tools accelerate "
         "us, but we review everything before it ships.</li>"
         "<li><strong>Code review is mandatory.</strong> Every PR gets reviewed before merge.</li>"
         "<li><strong>Multi-project context switching</strong> is normal — you'll work "
         "across 2-3 client projects in any given week.</li></ul>"
         "<h4>GitHub Repo Structure</h4>"
         "<p>Each client project has its own repo. Branching: "
         "<code>main</code> (production) &rarr; <code>staging</code> (QA) &rarr; "
         "<code>feature/*</code> or <code>fix/*</code> (development).</p>"
         "<h4>Production Quality Standards</h4>"
         "<ul><li>No suppressed warnings or ignored errors</li>"
         "<li>Meaningful error handling</li>"
         "<li>Clean, readable code with clear naming</li>"
         "<li>Tests for critical paths</li></ul>"
         "<p><em>Loom deep-dive coming soon.</em></p>")},
    {"slug": "d2-communication", "title": "Communication Norms",
     "content_type": "text", "estimated_minutes": 10, "track": "all", "content_html": (
         "<h3>When to Use What</h3>"
         "<ul><li><strong>Slack:</strong> Quick questions and project updates</li>"
         "<li><strong>Email:</strong> Client correspondence and formal internal updates</li>"
         "<li><strong>Loom:</strong> Anything easier to show than write</li>"
         "<li><strong>Meetings:</strong> Real-time back-and-forth discussions</li></ul>"
         "<h4>Slack Norms</h4>"
         "<ul><li>Use threads to keep conversations organized</li>"
         "<li>@mention people when you need their attention</li>"
         "<li>DMs for urgent matters</li>"
         "<li>Emoji reactions signal you've seen something and boost culture</li></ul>"
         "<h4>Key Slack Channels</h4>"
         "<ul><li><strong>#general</strong> — company-wide announcements</li>"
         "<li><strong>#random</strong> — non-work chat and team bonding</li>"
         "<li><strong>#testimonials</strong> — celebrate client and team wins</li>"
         "<li>Project channels named by client (e.g., #proj-strategicwealthgroup-ai)</li>"
         "<li>Service channels named by service (e.g., #serv-crm-consulting)</li></ul>"
         "<h4>Response Time Expectations</h4>"
         "<ul><li><strong>Slack:</strong> Same business day</li>"
         "<li><strong>Email:</strong> Within 24 hours</li>"
         "<li><strong>Client-facing:</strong> As fast as possible — escalate if unsure</li></ul>")},
    {"slug": "d2-checkpoint", "title": "Day 2 Checkpoint",
     "content_type": "form", "estimated_minutes": 5, "track": "all", "content_html": (
         "<h3>Confirm Your Setup</h3>"
         "<p>Verify that all tools from the checklist are set up. "
         "If you completed the Claude hands-on exercise, submit your output below.</p>")},
]

# ---------------------------------------------------------------------------
# Day 3 modules — Your Impact
# ---------------------------------------------------------------------------

DAY_3_MODULES = [
    {"slug": "d3-client-portfolio", "title": "Client Portfolio Overview",
     "content_type": "text", "estimated_minutes": 20, "track": "all", "content_html": (
         "<h3>Active Pulse Clients</h3>"
         "<div class='client-card'><h4>GAAPP / Sanofi</h4>"
         "<p><strong>Industry:</strong> Healthcare advocacy</p>"
         "<p><strong>What we're building:</strong> Asthma Care Map, Speak Up for COPD, AI chatbots</p>"
         "<p><strong>Status:</strong> Active — ongoing development and feature expansion</p></div>"
         "<div class='client-card'><h4>Strategic Wealth Group</h4>"
         "<p><strong>Industry:</strong> Wealth management</p>"
         "<p><strong>What we're building:</strong> AI roadmap, CRM implementation</p>"
         "<p><strong>Status:</strong> Active</p></div>"
         "<div class='client-card'><h4>DCC Marketing</h4>"
         "<p><strong>Industry:</strong> Government contracting</p>"
         "<p><strong>What we're building:</strong> AI-powered RFP management platform</p>"
         "<p><strong>Status:</strong> Active</p></div>"
         "<div class='client-card'><h4>South Forty Specialties (S40S)</h4>"
         "<p><strong>Industry:</strong> E-commerce / specialty food</p>"
         "<p><strong>What we're building:</strong> E-commerce platform, ERP implementation</p>"
         "<p><strong>Status:</strong> Active</p></div>"
         "<div class='client-card'><h4>National Concerts</h4>"
         "<p><strong>Industry:</strong> Entertainment</p>"
         "<p><strong>What we're building:</strong> Digital sales room</p>"
         "<p><strong>Status:</strong> Active</p></div>"
         "<div class='client-card'><h4>BRE Law</h4>"
         "<p><strong>Industry:</strong> Legal services</p>"
         "<p><strong>What we're building:</strong> AI strategy, SEO</p>"
         "<p><strong>Status:</strong> Active</p></div>"
         "<div class='client-card'><h4>Premier Fund Solutions</h4>"
         "<p><strong>Industry:</strong> Financial services</p>"
         "<p><strong>What we're building:</strong> Marketing, WordPress</p>"
         "<p><strong>Status:</strong> Active</p></div>"
         "<div class='client-card'><h4>Hungerford</h4>"
         "<p><strong>Industry:</strong> Financial services</p>"
         "<p><strong>What we're building:</strong> SEO / PPC</p>"
         "<p><strong>Status:</strong> Active</p></div>")},
    {"slug": "d3-your-projects", "title": "Your Projects",
     "content_type": "text", "estimated_minutes": 10, "track": "all", "content_html": (
         "<h3>Your Assigned Projects</h3>"
         "<p>This section will be dynamically populated from ClickUp with your "
         "assigned tasks, lists, and folders once integrations are configured.</p>"
         "<p><em>Projects TBD — your team lead will assign projects shortly.</em></p>")},
    {"slug": "d3-first-ticket", "title": "First Ticket",
     "content_type": "exercise", "estimated_minutes": 120, "track": "all", "content_html": (
         "<h3>Your First Contribution</h3>"
         "<p>Time to ship something real. Your first ticket is a scoped starter task "
         "designed to be completable in 2-4 hours.</p>"
         "<p><strong>Purpose:</strong> Build confidence, familiarize yourself with "
         "the workflow, and ship something real on Day 3.</p>"
         "<p>Your task link will appear here once your admin creates it.</p>")},
    {"slug": "d3-working-with-clients", "title": "Working with Clients",
     "content_type": "loom", "loom_url": "", "estimated_minutes": 15, "track": "all",
     "content_html": (
         "<h3>Client Communication Standards</h3>"
         "<h4>Discovery Calls</h4>"
         "<p>We start every engagement with discovery. Listen more than you talk. "
         "Ask clarifying questions. Document everything.</p>"
         "<h4>How We Scope &amp; Deliver</h4>"
         "<ol><li>Discover the client's pain points and goals</li>"
         "<li>Scope the work with clear acceptance criteria</li>"
         "<li>Estimate effort using Fibonacci points</li>"
         "<li>Deliver in weekly sprint increments with client check-ins</li></ol>"
         "<h4>Meeting Etiquette &amp; Grain</h4>"
         "<ul><li>Always inform clients that calls are recorded</li>"
         "<li>Review AI summaries for accuracy before sharing</li>"
         "<li>Use Grain search to prep for follow-up calls</li></ul>"
         "<p><em>Loom deep-dive coming soon.</em></p>")},
    {"slug": "d3-checkpoint", "title": "Day 3 Checkpoint",
     "content_type": "form", "estimated_minutes": 10, "track": "all", "content_html": (
         "<h3>Final Checkpoint</h3>"
         "<p>Confirm your first ticket is submitted or in review.</p>"
         "<p>Rate your onboarding experience (1-5) and share any feedback:</p>"
         "<ul><li>What was most helpful?</li><li>What was confusing or missing?</li></ul>")},
]
