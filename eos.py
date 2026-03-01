"""
EOS (Entrepreneurial Operating System) Module
Rocks, Issues, V/TO, Scorecard, and To-Do management for Pulse Marketing.
"""

import os
import json
import sqlite3
import logging
from contextlib import contextmanager
from datetime import datetime, timedelta
from flask import Blueprint, render_template, jsonify, request, session
from auth import login_required

logger = logging.getLogger(__name__)

eos_bp = Blueprint('eos', __name__)

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def _eos_db_path():
    if os.path.isdir("/data"):
        return "/data/eos.db"
    return os.path.join(os.path.dirname(__file__), "eos.db")


def _get_db():
    conn = sqlite3.connect(_eos_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@contextmanager
def _db():
    """Context manager for safe DB access — always closes connection."""
    conn = _get_db()
    try:
        yield conn
    finally:
        conn.close()


def init_eos_db():
    """Create EOS tables if they don't exist."""
    conn = _get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS eos_rocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            owner TEXT NOT NULL,
            quarter TEXT NOT NULL,
            status TEXT DEFAULT 'on_track',
            description TEXT,
            due_date TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS eos_issues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            priority INTEGER DEFAULT 0,
            category TEXT DEFAULT 'short_term',
            status TEXT DEFAULT 'open',
            owner TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            resolved_at TIMESTAMP,
            resolution_notes TEXT
        );
        CREATE TABLE IF NOT EXISTS eos_vto (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            section TEXT NOT NULL UNIQUE,
            content TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS eos_scorecard_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            owner TEXT NOT NULL,
            goal TEXT,
            frequency TEXT DEFAULT 'weekly',
            category TEXT,
            sort_order INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS eos_scorecard_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            metric_id INTEGER REFERENCES eos_scorecard_metrics(id),
            week_start TEXT NOT NULL,
            value TEXT,
            on_track INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS eos_todos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            owner TEXT NOT NULL,
            due_date TEXT,
            status TEXT DEFAULT 'open',
            source TEXT DEFAULT 'manual',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP
        );
    """)
    # Seed V/TO sections
    vto_sections = [
        'core_values', 'core_focus', 'ten_year_target', 'marketing_strategy',
        'three_year_picture', 'one_year_plan', 'quarterly_rocks_summary'
    ]
    for s in vto_sections:
        conn.execute("INSERT OR IGNORE INTO eos_vto (section, content) VALUES (?, ?)", (s, '{}'))

    # Migration: add team column to eos_rocks if it doesn't exist
    try:
        conn.execute("ALTER TABLE eos_rocks ADD COLUMN team TEXT DEFAULT 'Admin'")
    except Exception:
        pass  # Column already exists

    conn.commit()
    conn.close()
    logger.info("EOS database initialized")


TEAMS = ["Admin", "Engineering", "Marketing"]


TEAM_MEMBERS = [
    "Jake Shumaker", "Sean Miller", "Luke Shumaker", "Bartosz Stoppel",
    "Sam Gohel", "Razvan Crisan", "Adri Andika", "Walter Miller"
]


def _row_to_dict(row):
    return dict(row) if row else None


def _rows_to_list(rows):
    return [dict(r) for r in rows]


def _current_quarter():
    now = datetime.now()
    q = (now.month - 1) // 3 + 1
    return f"Q{q} {now.year}"


# ---------------------------------------------------------------------------
# Page routes
# ---------------------------------------------------------------------------

@eos_bp.route("/eos")
@login_required
def eos_hub():
    return render_template("eos.html", page="hub", team_members=TEAM_MEMBERS, current_quarter=_current_quarter())

@eos_bp.route("/eos/rocks")
@login_required
def eos_rocks_page():
    return render_template("eos.html", page="rocks", team_members=TEAM_MEMBERS, teams=TEAMS, current_quarter=_current_quarter())

@eos_bp.route("/eos/issues")
@login_required
def eos_issues_page():
    return render_template("eos.html", page="issues", team_members=TEAM_MEMBERS, current_quarter=_current_quarter())

@eos_bp.route("/eos/vto")
@login_required
def eos_vto_page():
    return render_template("eos.html", page="vto", team_members=TEAM_MEMBERS, current_quarter=_current_quarter())

@eos_bp.route("/eos/scorecard")
@login_required
def eos_scorecard_page():
    return render_template("eos.html", page="scorecard", team_members=TEAM_MEMBERS, current_quarter=_current_quarter())

@eos_bp.route("/eos/todos")
@login_required
def eos_todos_page():
    return render_template("eos.html", page="todos", team_members=TEAM_MEMBERS, current_quarter=_current_quarter())


# ---------------------------------------------------------------------------
# API: Rocks
# ---------------------------------------------------------------------------

@eos_bp.route("/api/eos/rocks", methods=["GET"])
@login_required
def api_rocks_list():
    quarter = request.args.get("quarter", _current_quarter())
    owner = request.args.get("owner")
    status = request.args.get("status")
    with _db() as db:
        q = "SELECT * FROM eos_rocks WHERE quarter = ?"
        params = [quarter]
        if owner:
            q += " AND owner = ?"; params.append(owner)
        if status:
            q += " AND status = ?"; params.append(status)
        q += " ORDER BY created_at DESC"
        rows = db.execute(q, params).fetchall()
        return jsonify(_rows_to_list(rows))

@eos_bp.route("/api/eos/rocks", methods=["POST"])
@login_required
def api_rocks_create():
    data = request.json
    if not data or not data.get("title") or not data.get("owner"):
        return jsonify({"error": "title and owner are required"}), 400
    with _db() as db:
        cur = db.execute(
            "INSERT INTO eos_rocks (title, owner, quarter, status, description, due_date, team) VALUES (?,?,?,?,?,?,?)",
            (data["title"], data["owner"], data.get("quarter", _current_quarter()),
             data.get("status", "on_track"), data.get("description", ""), data.get("due_date"),
             data.get("team", "Admin"))
        )
        db.commit()
        row = db.execute("SELECT * FROM eos_rocks WHERE id = ?", (cur.lastrowid,)).fetchone()
        return jsonify(_row_to_dict(row)), 201

@eos_bp.route("/api/eos/rocks/<int:rock_id>", methods=["PUT"])
@login_required
def api_rocks_update(rock_id):
    data = request.json
    if not data:
        return jsonify({"error": "No data provided"}), 400
    with _db() as db:
        fields = []
        params = []
        for f in ["title", "owner", "quarter", "status", "description", "due_date", "team"]:
            if f in data:
                fields.append(f"{f} = ?"); params.append(data[f])
        if not fields:
            return jsonify({"error": "No valid fields to update"}), 400
        fields.append("updated_at = ?"); params.append(datetime.now().isoformat())
        params.append(rock_id)
        db.execute(f"UPDATE eos_rocks SET {', '.join(fields)} WHERE id = ?", params)
        db.commit()
        row = db.execute("SELECT * FROM eos_rocks WHERE id = ?", (rock_id,)).fetchone()
        if not row:
            return jsonify({"error": "Rock not found"}), 404
        return jsonify(_row_to_dict(row))

@eos_bp.route("/api/eos/rocks/<int:rock_id>", methods=["DELETE"])
@login_required
def api_rocks_delete(rock_id):
    with _db() as db:
        db.execute("DELETE FROM eos_rocks WHERE id = ?", (rock_id,))
        db.commit()
        return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# API: Issues
# ---------------------------------------------------------------------------

@eos_bp.route("/api/eos/issues", methods=["GET"])
@login_required
def api_issues_list():
    category = request.args.get("category")
    status = request.args.get("status", "open")
    with _db() as db:
        q = "SELECT * FROM eos_issues WHERE status = ?"
        params = [status]
        if category:
            q += " AND category = ?"; params.append(category)
        q += " ORDER BY priority DESC, created_at DESC"
        rows = db.execute(q, params).fetchall()
        return jsonify(_rows_to_list(rows))

@eos_bp.route("/api/eos/issues", methods=["POST"])
@login_required
def api_issues_create():
    data = request.json
    if not data or not data.get("title"):
        return jsonify({"error": "title is required"}), 400
    with _db() as db:
        cur = db.execute(
            "INSERT INTO eos_issues (title, description, priority, category, owner) VALUES (?,?,?,?,?)",
            (data["title"], data.get("description", ""), data.get("priority", 0),
             data.get("category", "short_term"), data.get("owner"))
        )
        db.commit()
        row = db.execute("SELECT * FROM eos_issues WHERE id = ?", (cur.lastrowid,)).fetchone()
        return jsonify(_row_to_dict(row)), 201

@eos_bp.route("/api/eos/issues/<int:issue_id>", methods=["PUT"])
@login_required
def api_issues_update(issue_id):
    data = request.json
    if not data:
        return jsonify({"error": "No data provided"}), 400
    with _db() as db:
        fields = []
        params = []
        for f in ["title", "description", "priority", "category", "status", "owner", "resolution_notes"]:
            if f in data:
                fields.append(f"{f} = ?"); params.append(data[f])
        if not fields:
            return jsonify({"error": "No valid fields to update"}), 400
        if data.get("status") == "resolved":
            fields.append("resolved_at = ?"); params.append(datetime.now().isoformat())
        params.append(issue_id)
        db.execute(f"UPDATE eos_issues SET {', '.join(fields)} WHERE id = ?", params)
        db.commit()
        row = db.execute("SELECT * FROM eos_issues WHERE id = ?", (issue_id,)).fetchone()
        if not row:
            return jsonify({"error": "Issue not found"}), 404
        return jsonify(_row_to_dict(row))

@eos_bp.route("/api/eos/issues/<int:issue_id>", methods=["DELETE"])
@login_required
def api_issues_delete(issue_id):
    with _db() as db:
        db.execute("DELETE FROM eos_issues WHERE id = ?", (issue_id,))
        db.commit()
        return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# API: V/TO
# ---------------------------------------------------------------------------

@eos_bp.route("/api/eos/vto", methods=["GET"])
@login_required
def api_vto_list():
    with _db() as db:
        rows = db.execute("SELECT * FROM eos_vto ORDER BY id").fetchall()
        return jsonify(_rows_to_list(rows))

@eos_bp.route("/api/eos/vto/<section>", methods=["PUT"])
@login_required
def api_vto_update(section):
    data = request.json
    if not data:
        return jsonify({"error": "No data provided"}), 400
    with _db() as db:
        # Validate section exists
        existing = db.execute("SELECT id FROM eos_vto WHERE section = ?", (section,)).fetchone()
        if not existing:
            return jsonify({"error": "Section not found"}), 404
        db.execute("UPDATE eos_vto SET content = ?, updated_at = ? WHERE section = ?",
                   (json.dumps(data.get("content", {})), datetime.now().isoformat(), section))
        db.commit()
        row = db.execute("SELECT * FROM eos_vto WHERE section = ?", (section,)).fetchone()
        return jsonify(_row_to_dict(row))


# ---------------------------------------------------------------------------
# API: Scorecard
# ---------------------------------------------------------------------------

@eos_bp.route("/api/eos/scorecard/metrics", methods=["GET"])
@login_required
def api_scorecard_metrics():
    with _db() as db:
        rows = db.execute("SELECT * FROM eos_scorecard_metrics ORDER BY sort_order, id").fetchall()
        return jsonify(_rows_to_list(rows))

@eos_bp.route("/api/eos/scorecard/metrics", methods=["POST"])
@login_required
def api_scorecard_metrics_create():
    data = request.json
    if not data or not data.get("name") or not data.get("owner"):
        return jsonify({"error": "name and owner are required"}), 400
    with _db() as db:
        cur = db.execute(
            "INSERT INTO eos_scorecard_metrics (name, owner, goal, frequency, category, sort_order) VALUES (?,?,?,?,?,?)",
            (data["name"], data["owner"], data.get("goal"), data.get("frequency", "weekly"),
             data.get("category"), data.get("sort_order", 0))
        )
        db.commit()
        row = db.execute("SELECT * FROM eos_scorecard_metrics WHERE id = ?", (cur.lastrowid,)).fetchone()
        return jsonify(_row_to_dict(row)), 201

@eos_bp.route("/api/eos/scorecard/metrics/<int:metric_id>", methods=["PUT"])
@login_required
def api_scorecard_metrics_update(metric_id):
    data = request.json
    if not data:
        return jsonify({"error": "No data provided"}), 400
    with _db() as db:
        fields = []
        params = []
        for f in ["name", "owner", "goal", "frequency", "category", "sort_order"]:
            if f in data:
                fields.append(f"{f} = ?"); params.append(data[f])
        if not fields:
            return jsonify({"error": "No valid fields to update"}), 400
        params.append(metric_id)
        db.execute(f"UPDATE eos_scorecard_metrics SET {', '.join(fields)} WHERE id = ?", params)
        db.commit()
        row = db.execute("SELECT * FROM eos_scorecard_metrics WHERE id = ?", (metric_id,)).fetchone()
        if not row:
            return jsonify({"error": "Metric not found"}), 404
        return jsonify(_row_to_dict(row))

@eos_bp.route("/api/eos/scorecard/metrics/<int:metric_id>", methods=["DELETE"])
@login_required
def api_scorecard_metrics_delete(metric_id):
    with _db() as db:
        db.execute("DELETE FROM eos_scorecard_entries WHERE metric_id = ?", (metric_id,))
        db.execute("DELETE FROM eos_scorecard_metrics WHERE id = ?", (metric_id,))
        db.commit()
        return jsonify({"ok": True})

@eos_bp.route("/api/eos/scorecard/entries", methods=["GET"])
@login_required
def api_scorecard_entries():
    metric_id = request.args.get("metric_id")
    with _db() as db:
        if metric_id:
            rows = db.execute("SELECT * FROM eos_scorecard_entries WHERE metric_id = ? ORDER BY week_start DESC", (metric_id,)).fetchall()
        else:
            rows = db.execute("SELECT * FROM eos_scorecard_entries ORDER BY week_start DESC").fetchall()
        return jsonify(_rows_to_list(rows))

@eos_bp.route("/api/eos/scorecard/entries", methods=["POST"])
@login_required
def api_scorecard_entries_create():
    data = request.json
    if not data or not data.get("metric_id") or not data.get("week_start"):
        return jsonify({"error": "metric_id and week_start are required"}), 400
    with _db() as db:
        # Upsert
        existing = db.execute("SELECT id FROM eos_scorecard_entries WHERE metric_id = ? AND week_start = ?",
                              (data["metric_id"], data["week_start"])).fetchone()
        if existing:
            db.execute("UPDATE eos_scorecard_entries SET value = ?, on_track = ? WHERE id = ?",
                       (data.get("value"), data.get("on_track", 1), existing["id"]))
        else:
            db.execute("INSERT INTO eos_scorecard_entries (metric_id, week_start, value, on_track) VALUES (?,?,?,?)",
                       (data["metric_id"], data["week_start"], data.get("value"), data.get("on_track", 1)))
        db.commit()
        return jsonify({"ok": True}), 201


# ---------------------------------------------------------------------------
# API: Todos
# ---------------------------------------------------------------------------

@eos_bp.route("/api/eos/todos", methods=["GET"])
@login_required
def api_todos_list():
    status = request.args.get("status", "open")
    with _db() as db:
        if status == "all":
            rows = db.execute("SELECT * FROM eos_todos ORDER BY status ASC, created_at DESC").fetchall()
        else:
            rows = db.execute("SELECT * FROM eos_todos WHERE status = ? ORDER BY created_at DESC", (status,)).fetchall()
        return jsonify(_rows_to_list(rows))

@eos_bp.route("/api/eos/todos", methods=["POST"])
@login_required
def api_todos_create():
    data = request.json
    if not data or not data.get("title") or not data.get("owner"):
        return jsonify({"error": "title and owner are required"}), 400
    with _db() as db:
        due = data.get("due_date") or (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
        cur = db.execute(
            "INSERT INTO eos_todos (title, owner, due_date, source) VALUES (?,?,?,?)",
            (data["title"], data["owner"], due, data.get("source", "manual"))
        )
        db.commit()
        row = db.execute("SELECT * FROM eos_todos WHERE id = ?", (cur.lastrowid,)).fetchone()
        return jsonify(_row_to_dict(row)), 201

@eos_bp.route("/api/eos/todos/<int:todo_id>", methods=["PUT"])
@login_required
def api_todos_update(todo_id):
    data = request.json
    if not data:
        return jsonify({"error": "No data provided"}), 400
    with _db() as db:
        fields = []
        params = []
        for f in ["title", "owner", "due_date", "status", "source"]:
            if f in data:
                fields.append(f"{f} = ?"); params.append(data[f])
        if not fields:
            return jsonify({"error": "No valid fields to update"}), 400
        if data.get("status") == "done":
            fields.append("completed_at = ?"); params.append(datetime.now().isoformat())
        params.append(todo_id)
        db.execute(f"UPDATE eos_todos SET {', '.join(fields)} WHERE id = ?", params)
        db.commit()
        row = db.execute("SELECT * FROM eos_todos WHERE id = ?", (todo_id,)).fetchone()
        if not row:
            return jsonify({"error": "Todo not found"}), 404
        return jsonify(_row_to_dict(row))

@eos_bp.route("/api/eos/todos/<int:todo_id>", methods=["DELETE"])
@login_required
def api_todos_delete(todo_id):
    with _db() as db:
        db.execute("DELETE FROM eos_todos WHERE id = ?", (todo_id,))
        db.commit()
        return jsonify({"ok": True})

# Auto-archive: clean up completed todos older than 2 weeks
@eos_bp.route("/api/eos/todos/archive", methods=["POST"])
@login_required
def api_todos_archive():
    cutoff = (datetime.now() - timedelta(weeks=2)).isoformat()
    with _db() as db:
        result = db.execute("DELETE FROM eos_todos WHERE status = 'done' AND completed_at < ?", (cutoff,))
        db.commit()
        return jsonify({"archived": result.rowcount})


# ---------------------------------------------------------------------------
# Seed V/TO and Rocks with Pulse Data
# ---------------------------------------------------------------------------

VTO_SEED_DATA = {
    "core_values": {
        "text": """• Unwavering Integrity
• Trailblazing Creativity
• Speed as a Superpower"""
    },

    "core_focus": {
        "text": """PURPOSE/CAUSE/PASSION:
Empowering non-Fortune 500 companies to compete and win with enterprise-level firepower, without the dysfunction, timelines, or cost that typically come with it.

"Building the Deloitte Digital for the non-Fortune 500"

NICHE:
Rapid, integrated marketing and technology implementation for mid-market companies who need results now, not strategy decks later."""
    },

    "ten_year_target": {
        "text": """$650M/yr consulting firm in Indianapolis, built with half the headcount of traditional models (averaging between 5K-10K employees as of Jan 2026)

Current market leader: BCforward"""
    },

    "marketing_strategy": {
        "text": """TARGET MARKET:
Non-Fortune 500 companies ($1M-$100M revenue) ready to leverage AI and innovative marketing to accelerate growth

3 UNIQUES:
1. We deliver in days, not months
2. AI-powered delivery means consistent results without the consulting firm chaos
3. We treat scope changes like humans, not like lawyers

PROVEN PROCESS:
• Discover - Initial consultation and scoping, typically around 1 specific, but painful problem
• Prove - Limited scope agreements to solve identified problem with speed and quality
• Scale - Full implementation and ongoing partnership into full integrated framework

GUARANTEE:
100% satisfaction guarantee on your first project. If you're not completely satisfied, we'll refund your investment in full. No more marketing or technology nightmares."""
    },

    "three_year_picture": {
        "text": """DATE: December 31, 2028

REVENUE: $10M
PROFIT: $5M (50% margin)

MEASURABLES:
• 150 AI POCs delivered annually ($2.25M at $15K each)
• >$100K+ average project size
• 90% client retention rate year-over-year

WHAT DOES IT LOOK LIKE?
Pulse is Indianapolis' lean, AI-powered consulting firm, known for implementing AI faster than traditional dev shops and taking products to market with expert care. We've become the go-to partner for companies who want enterprise-level AI development and marketing without the bloat.

Our team of under 25 includes a strong developer core and a lean marketing team of killers, all leveraging AI to deliver 10x the output of traditional firms. Retainer clients dominate our revenue base, supported by 2-3 predictable lead generation channels and a thriving partner network that consistently refers high-quality opportunities.

We've proven that world-class consulting doesn't require hundred-person teams—it requires the right people, the right process, and the right technology working in perfect sync."""
    },

    "one_year_plan": {
        "text": """DATE: December 31, 2026

REVENUE: $2M
PROFIT: $1M (50% margin)

MEASURABLES:
• 10x engineering efficiency through AI implementation
• 5 or fewer FT employees (Walter Miller, Bart Stoppel, Marketing Hire, Additional Capacity x2)
• 60 AI POCs delivered in 2026 ($600K at $10K each, $720K at $12K each)

GOALS FOR THE YEAR:
1. Hit $2M in revenue
2. Achieve 10x engineering efficiency with AI across all development work
3. Scale POC offering to 60 deliveries (10/month run rate by October)
4. Become a premier & recognized Claude Code shop"""
    },

    "quarterly_rocks_summary": {
        "text": """Q1 2026 ROCKS (Due: March 31, 2026)

1. Deliver DCC Marketing & SWG projects on time and on budget - Jake
2. Hire & onboard 2 developers and 1 marketing specialist - Sean
3. Implement Agile development processes - Jake
4. Define process to become "premier" in Claude Code - Jake
5. Implement EOS framework for leadership team across Pulse - Jake
6. Build and prove out marketing playbook for 2-Day AI POCs - Sean"""
    }
}

Q1_2026_ROCKS = [
    {
        "title": "Deliver DCC Marketing & SWG projects on time and on budget",
        "owner": "Jake Shumaker",
        "quarter": "Q1 2026",
        "due_date": "2026-03-31",
        "status": "on_track",
        "team": "Admin",
        "description": "Successfully complete DCC Marketing and SWG client projects within budget and timeline constraints."
    },
    {
        "title": "Hire & onboard 2 developers and 1 marketing specialist",
        "owner": "Sean Miller",
        "quarter": "Q1 2026",
        "due_date": "2026-03-31",
        "status": "on_track",
        "team": "Admin",
        "description": "Recruit, hire, and fully onboard two developers and one marketing specialist to expand team capacity."
    },
    {
        "title": "Implement Agile development processes",
        "owner": "Jake Shumaker",
        "quarter": "Q1 2026",
        "due_date": "2026-03-31",
        "status": "on_track",
        "team": "Engineering",
        "description": "Establish and implement Agile/Scrum development processes across all engineering work."
    },
    {
        "title": "Define process to become 'premier' in Claude Code",
        "owner": "Jake Shumaker",
        "quarter": "Q1 2026",
        "due_date": "2026-03-31",
        "status": "on_track",
        "team": "Engineering",
        "description": "Create and document the process/criteria for becoming a recognized premier Claude Code implementation shop."
    },
    {
        "title": "Implement EOS framework for leadership team across Pulse",
        "owner": "Jake Shumaker",
        "quarter": "Q1 2026",
        "due_date": "2026-03-31",
        "status": "on_track",
        "team": "Admin",
        "description": "Roll out full EOS (Entrepreneurial Operating System) implementation including L10 meetings, Rocks, Scorecard, and V/TO."
    },
    {
        "title": "Build and prove out marketing playbook for 2-Day AI POCs",
        "owner": "Sean Miller",
        "quarter": "Q1 2026",
        "due_date": "2026-03-31",
        "status": "on_track",
        "team": "Marketing",
        "description": "Develop and validate a repeatable marketing playbook for generating leads and closing 2-Day AI POC engagements."
    }
]


@eos_bp.route("/api/eos/seed-pulse-vto", methods=["POST"])
@login_required
def api_seed_pulse_vto():
    """One-time seed endpoint to populate V/TO with Pulse data. Admin only."""
    if session.get('role') != 'admin':
        return jsonify({"error": "Admin access required"}), 403

    results = {"vto": [], "rocks": []}

    with _db() as db:
        # Seed V/TO sections
        for section, content in VTO_SEED_DATA.items():
            db.execute(
                "UPDATE eos_vto SET content = ?, updated_at = ? WHERE section = ?",
                (json.dumps(content), datetime.now().isoformat(), section)
            )
            results["vto"].append(section)

        # Check if Q1 2026 rocks already exist
        existing = db.execute(
            "SELECT COUNT(*) FROM eos_rocks WHERE quarter = 'Q1 2026'"
        ).fetchone()[0]

        if existing == 0:
            # Seed Q1 2026 Rocks
            for rock in Q1_2026_ROCKS:
                db.execute(
                    """INSERT INTO eos_rocks (title, owner, quarter, status, description, due_date, team)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (rock["title"], rock["owner"], rock["quarter"],
                     rock["status"], rock["description"], rock["due_date"], rock.get("team", "Admin"))
                )
                results["rocks"].append(rock["title"][:40] + "...")
        else:
            results["rocks_skipped"] = f"Q1 2026 already has {existing} rocks"

        db.commit()

    return jsonify({
        "success": True,
        "message": "Pulse V/TO data seeded successfully",
        "results": results
    })
