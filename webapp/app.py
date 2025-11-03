import os
import io
import csv
import sys
import requests
import json
from typing import Dict, Any, List
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, send_file, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
from models import Base, User, TimetableProject, TimetableData, init_db, get_db

# Ensure project root is on PYTHONPATH to import modules like utils, scheduler, etc.
PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from utils import set_up, show_statistics
from costs import hard_constraints_cost
from scheduler import initial_population, evolutionary_algorithm, simulated_hardening, schedule_labs_first
from config_adapter import build_data_from_config


app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
LAST_CFG = None

# Initialize database
init_db()


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


def _run_schedule_for_config(cfg: Dict[str, Any], year_key: str):
    filled = {}
    subjects_order = {}
    groups_empty_space = {}
    teachers_empty_space = {}

    data = build_data_from_config(cfg, year_key, teachers_empty_space, groups_empty_space, subjects_order)
    matrix, free = set_up(len(data.classrooms))
    # Restrict usable rows per day to the actual college window
    short_break_min = cfg.get('timings', {}).get('short_break_min', 15)
    lunch_break_min = cfg.get('timings', {}).get('lunch_break_min', 45)
    labels = _build_time_labels(cfg['timings']['start'], cfg['timings']['end'], 
                                short_break_min, lunch_break_min)
    slots_per_day = len(labels)
    allowed_rows = set()
    for day in range(5):
        day_start = day * 12
        for t in range(slots_per_day):
            # Exclude breaks: short break at slot index 2, lunch at slot index 4
            if t in (2, 4):
                continue
            allowed_rows.add(day_start + t)
    free[:] = [field for field in free if field[0] in allowed_rows]
    
    # Separate lab scheduling phase (runs BEFORE lecture allocation)
    schedule_labs_first(data, matrix, free, filled, groups_empty_space, teachers_empty_space, 
                        subjects_order, year_key, cfg, labels)
    
    # Lecture allocation (existing algorithm, untouched)
    initial_population(data, matrix, free, filled, groups_empty_space, teachers_empty_space, subjects_order, year_key)
    evolutionary_algorithm(matrix, data, free, filled, groups_empty_space, teachers_empty_space, subjects_order)
    # do short hardening for web responsiveness
    simulated_hardening(matrix, data, free, filled, groups_empty_space, teachers_empty_space, subjects_order, f'{year_key}.txt')
    return matrix, data, filled


def _build_time_labels(start: str, end: str, short_break_min: int = 15, lunch_break_min: int = 45):
    # start/end like '09:15'
    from datetime import datetime, timedelta
    sh, sm = [int(x) for x in start.split(':')]
    eh, em = [int(x) for x in end.split(':')]
    
    start_time = datetime(2000, 1, 1, sh, sm)
    end_time = datetime(2000, 1, 1, eh, em)
    
    labels = []
    current = start_time
    
    slot_num = 0
    while current < end_time:
        # Check if we're at a break position
        # Short break after 2 slots (slots 0, 1) - at slot index 2
        # Lunch break after 4 slots (slots 0, 1, 2-break, 3) - at slot index 4
        if slot_num == 2:
            # Short break
            next_slot = current + timedelta(minutes=short_break_min)
            current = next_slot
            slot_num += 1
            continue
        elif slot_num == 4:
            # Lunch break
            next_slot = current + timedelta(minutes=lunch_break_min)
            current = next_slot
            slot_num += 1
            continue
        
        # Regular 1-hour slot
        next_time = current + timedelta(hours=1)
        if next_time > end_time:
            break
        labels.append(f"{current.hour:02d}:{current.minute:02d}-{next_time.hour:02d}:{next_time.minute:02d}")
        current = next_time
        slot_num += 1
    
    return labels


def _matrix_to_day_grid(matrix, data, cfg: Dict[str, Any]):
    # days 6 columns like UI (Mon..Sat), rows are hourly slots
    days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
    short_break_min = cfg.get('timings', {}).get('short_break_min', 15)
    lunch_break_min = cfg.get('timings', {}).get('lunch_break_min', 45)
    labels = _build_time_labels(cfg['timings']['start'], cfg['timings']['end'],
                                short_break_min, lunch_break_min)
    # Map class index to subject name and type/duration
    idx_to_class = {}
    for i, c in data.classes.items():
        idx_to_class[i] = c

    rows = []
    slots_per_day = len(labels)
    # Build a working grid of (class_index, column) per day/time using first occupied classroom
    grid = [[None for _ in range(6)] for __ in range(slots_per_day)]
    for t in range(slots_per_day):
        for d in range(6):
            if d == 5:
                continue
            mat_row = t + d * 12
            for j in range(len(matrix[0])):
                val = matrix[mat_row][j]
                if val is not None:
                    grid[t][d] = (val, j)
                    break

    # Convert grid into render cells with vertical merge for labs
    for t in range(slots_per_day):
        cells = []
        for d in range(6):
            if d == 5:
                cells.append('FREE')
                continue
            item = grid[t][d]
            if item is None:
                cells.append('FREE')
                continue
            # If previous slot of same day has same class, mark as SKIP (covered by rowspan)
            if t > 0 and grid[t-1][d] is not None and grid[t-1][d][0] == item[0]:
                cells.append('SKIP')
                continue
            class_index, col_index = item
            cl = idx_to_class.get(class_index)
            subject_name = cl.subject if cl else ""
            # room name from data.classrooms by column index
            room_name = ''
            try:
                room_name = data.classrooms[col_index].name
            except Exception:
                room_name = ''
            rowspan = 1
            if cl and cl.type == 'L':
                span = 1
                k = t + 1
                # Count consecutive same index within the day window
                while k < slots_per_day and grid[k][d] is not None and grid[k][d][0] == class_index:
                    span += 1
                    k += 1
                rowspan = span
                # For labs, use subject name as-is (it's already the lab name from config_adapter)
                display_text = subject_name
            else:
                display_text = subject_name
            cells.append({'text': display_text, 'room': room_name, 'rowspan': rowspan})
        rows.append({'label': labels[t] if t < len(labels) else str(t), 'cells': cells})

    # Insert breaks: after 2nd slot (short), after 4th slot (lunch)
    # Use actual break durations from config
    short_break_min = cfg.get('timings', {}).get('short_break_min', 15)
    lunch_break_min = cfg.get('timings', {}).get('lunch_break_min', 45)
    
    if slots_per_day >= 3:
        # Short break row - show actual duration
        break_text = f"BREAK ({short_break_min} min)" if short_break_min else "BREAK"
        rows[2]['cells'] = [break_text] * 6
    if slots_per_day >= 5:
        # Lunch break row - show actual duration
        break_text = f"BREAK ({lunch_break_min} min)" if lunch_break_min else "BREAK"
        rows[4]['cells'] = [break_text] * 6
    return days, rows


def _audit_schedule(data, filled, cfg: Dict[str, Any]) -> list:
    # Verify subject session counts and produce warnings
    required = {}
    for idx, cl in data.classes.items():
        key = (tuple(cl.groups), cl.subject, cl.type)
        required[key] = required.get(key, 0) + 1
    assigned = {}
    for idx in filled.keys():
        cl = data.classes[idx]
        key = (tuple(cl.groups), cl.subject, cl.type)
        assigned[key] = assigned.get(key, 0) + 1
    warnings = []
    for key, req in required.items():
        got = assigned.get(key, 0)
        if got < req:
            groups, subject, typ = key
            missing = req - got
            warnings.append(f"Missing {missing} session(s) for {subject} ({'Lab' if typ=='L' else 'Lecture'})")
    # Capacity check per batch/group: total required sessions should fit into weekly available slots
    labels = _build_time_labels(cfg['timings']['start'], cfg['timings']['end'])
    slots_per_day = len(labels)
    usable_slots_per_day = slots_per_day - 2 if slots_per_day >= 5 else max(0, slots_per_day - 1)
    weekly_slots = 5 * usable_slots_per_day
    # Sum required sessions per group (each class instance is one session)
    req_per_group = {}
    for idx, cl in data.classes.items():
        for g in cl.groups:
            req_per_group[g] = req_per_group.get(g, 0) + 1
    for g, total_req in req_per_group.items():
        if total_req > weekly_slots:
            warnings.append(f"Batch capacity exceeded: needs {total_req} sessions but only {weekly_slots} slots available")
    return warnings


def _matrix_to_table(matrix) -> List[List[str]]:
    # Convert to strings for rendering
    return [[('-' if v is None else str(v)) for v in row] for row in matrix]


def _resolve_cross_year_room_conflicts(years: List[str], year_to_matrix: Dict[str, Any], year_to_data: Dict[str, Any]) -> List[str]:
    warnings: List[str] = []
    if len(years) <= 1:
        return warnings
    # assume all matrices have equal row count and column count
    ref_year = years[0]
    matrix_rows = len(year_to_matrix[ref_year])
    matrix_cols = len(year_to_matrix[ref_year][0]) if matrix_rows > 0 else 0

    for r in range(matrix_rows):
        # Track columns by room type (classroom vs lab) - they should not conflict if different types
        taken_classroom_cols = set()  # Columns with classroom type rooms
        taken_lab_cols = set()  # Columns with lab type rooms
        
        for y in years:
            # process in order; earlier years have priority
            m = year_to_matrix[y]
            d = year_to_data[y]

            # mark columns already occupied by previous years (by room type)
            if y != years[0]:
                # refresh taken from all previous years at this row
                taken_classroom_cols = set()
                taken_lab_cols = set()
                for py in years:
                    if py == y:
                        break
                    pm = year_to_matrix[py]
                    pd = year_to_data[py]
                    for c in range(matrix_cols):
                        if pm[r][c] is not None:
                            # Check room type for this column
                            room_type = None
                            if hasattr(pd, 'classrooms'):
                                for room_idx, room in pd.classrooms.items():
                                    if room_idx == c:
                                        room_type = room.type
                                        break
                            if room_type == 'r':
                                taken_lab_cols.add(c)
                            elif room_type == 'n':
                                taken_classroom_cols.add(c)
                            else:
                                # Unknown type, add to both to be safe
                                taken_classroom_cols.add(c)
                                taken_lab_cols.add(c)

            # attempt to move any starting blocks that clash with taken_cols
            for c in range(matrix_cols):
                ci = m[r][c]
                if ci is None:
                    continue
                # consider only starts of a block
                if r > 0 and m[r-1][c] == ci:
                    continue
                
                # Get room type for current column
                cl_room_type = None
                if hasattr(d, 'classrooms'):
                    for room_idx, room in d.classrooms.items():
                        if room_idx == c:
                            cl_room_type = room.type
                            break
                
                # Check if this column conflicts with earlier years (only same room type)
                has_conflict = False
                if cl_room_type == 'r' and c in taken_lab_cols:
                    has_conflict = True
                elif cl_room_type == 'n' and c in taken_classroom_cols:
                    has_conflict = True
                elif cl_room_type is None:
                    # Unknown type - check both
                    if c in taken_classroom_cols or c in taken_lab_cols:
                        has_conflict = True
                
                if not has_conflict:
                    continue
                
                # need to relocate this block to some allowed column not taken and free for full duration
                cl = d.classes[ci]
                dur = int(cl.duration)
                moved = False
                
                for new_c in cl.classrooms:
                    # Check if new column conflicts (only same room type matters)
                    new_c_room_type = None
                    if hasattr(d, 'classrooms'):
                        for room_idx, room in d.classrooms.items():
                            if room_idx == new_c:
                                new_c_room_type = room.type
                                break
                    
                    # Check if new column is taken by same room type
                    if new_c_room_type == 'r' and new_c in taken_lab_cols:
                        continue
                    elif new_c_room_type == 'n' and new_c in taken_classroom_cols:
                        continue
                    elif new_c_room_type is None:
                        if new_c in taken_classroom_cols or new_c in taken_lab_cols:
                            continue
                    
                    ok = True
                    for i in range(dur):
                        rr = r + i
                        if rr >= matrix_rows:
                            ok = False
                            break
                        if m[rr][new_c] is not None:
                            ok = False
                            break
                        # also ensure not taken by earlier years at row rr (only check same room type)
                        for py in years:
                            if py == y:
                                break
                            if year_to_matrix[py][rr][new_c] is not None:
                                py_d = year_to_data[py]
                                py_room_type = None
                                if hasattr(py_d, 'classrooms'):
                                    for room_idx, room in py_d.classrooms.items():
                                        if room_idx == new_c:
                                            py_room_type = room.type
                                            break
                                # If different room types, no conflict - allow
                                if new_c_room_type and py_room_type and new_c_room_type != py_room_type:
                                    # Different types - no conflict
                                    continue
                                else:
                                    # Same type or unknown - conflict
                                    ok = False
                                    break
                        if not ok:
                            break
                    if ok:
                        # move block
                        for i in range(dur):
                            rr = r + i
                            m[rr][new_c] = ci
                            m[rr][c] = None
                        moved = True
                        break
                
                # If still not moved, try different time slot as last resort
                if not moved and dur <= 3:  # Only for short duration classes
                    # Try shifting to next available slot in same day
                    day = r // 12
                    slot_in_day = r % 12
                    for alt_slot in range(slot_in_day + 1, min(12, slot_in_day + 4)):
                        alt_r = day * 12 + alt_slot
                        if alt_r + dur > (day + 1) * 12:
                            break
                        # Check if alt slot is free
                        alt_ok = True
                        for i in range(dur):
                            rr = alt_r + i
                            if m[rr][c] is not None:
                                alt_ok = False
                                break
                            for py in years:
                                if py == y:
                                    break
                                if year_to_matrix[py][rr][c] is not None:
                                    alt_ok = False
                                    break
                        if alt_ok:
                            # Move to alternative slot
                            for i in range(dur):
                                old_rr = r + i
                                new_rr = alt_r + i
                                m[new_rr][c] = ci
                                m[old_rr][c] = None
                            moved = True
                            break
                
                if not moved:
                    warnings.append(f"Room conflict at row {r} could not be resolved for {y}")
        # end for y
    return warnings

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if not username or not password:
            flash('Please fill all fields')
            return render_template('login.html')
        
        db = next(get_db())
        user = db.query(User).filter(User.username == username).first()
        if user and check_password_hash(user.password_hash, password):
            session['user_id'] = user.id
            session['username'] = user.username
            db.close()
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid username or password')
            db.close()
    return render_template('login.html')


@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        department = request.form.get('department', '')
        if not username or not password:
            flash('Please fill all required fields')
            return render_template('signup.html')
        
        db = next(get_db())
        if db.query(User).filter(User.username == username).first():
            flash('Username already exists')
            db.close()
            return render_template('signup.html')
        
        user = User(username=username, password_hash=generate_password_hash(password), department=department)
        db.add(user)
        db.commit()
        db.refresh(user)
        session['user_id'] = user.id
        session['username'] = user.username
        db.close()
        return redirect(url_for('dashboard'))
    return render_template('signup.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/dashboard')
@login_required
def dashboard():
    db = next(get_db())
    projects = db.query(TimetableProject).filter(TimetableProject.user_id == session['user_id']).order_by(TimetableProject.updated_at.desc()).all()
    db.close()
    return render_template('dashboard.html', projects=projects, username=session.get('username'))


@app.route('/')
def index():
    # Redirect old root to home
    return redirect(url_for('home'))


@app.route('/home')
def home():
    # Get total count of timetable projects generated
    db = next(get_db())
    total_schedules = db.query(TimetableProject).count()
    db.close()
    return render_template('home.html', username=session.get('username'), total_schedules=total_schedules)


@app.route('/about')
def about():
    return render_template('about.html')


@app.route('/configure')
@login_required
def configure():
    project_id = request.args.get('project_id')
    cfg = None
    if project_id:
        db = next(get_db())
        proj = db.query(TimetableProject).filter(TimetableProject.id == int(project_id), TimetableProject.user_id == session['user_id']).first()
        if proj:
            cfg = json.loads(proj.config_json)
        db.close()
    return render_template('index.html', project_config=cfg, project_id=project_id)


@app.route('/generate', methods=['POST'])
@login_required
def generate():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    project_name = request.form.get('project_name', '').strip()
    if not project_name:
        flash('Please enter a project name')
        return redirect(url_for('configure'))
    
    # Parse form fields into a cfg dict
    cfg = request.form.to_dict(flat=True)
    # Multi-field lists arrive as repeated keys; collect subjects per year
    def parse_subjects(prefix: str):
        names = request.form.getlist(f'{prefix}_subject_name')
        hours = request.form.getlist(f'{prefix}_subject_hours')
        labs_count = request.form.getlist(f'{prefix}_labs_count')
        lab_names = request.form.getlist(f'{prefix}_lab_name')
        lab_hours = request.form.getlist(f'{prefix}_lab_hours')
        subjects = []
        for i, name in enumerate(names):
            if not name:
                continue
            subjects.append({
                'name': name,
                'hours': int(hours[i] or '0'),
                'labs': int(labs_count[i] or '0'),
                'lab_name': (lab_names[i] if i < len(lab_names) else ''),
                'lab_hours': int(lab_hours[i] or '0') if i < len(lab_hours) else 0
            })
        return subjects

    cfg_struct = {
        'config_name': cfg.get('config_name', ''),
        'department': cfg.get('department', ''),
        'num_classrooms': int(cfg.get('num_classrooms', '0') or '0'),
        'num_labs': int(cfg.get('num_labs', '0') or '0'),
        'lab_names': [n.strip() for n in cfg.get('lab_names', '').split(',') if n.strip()],
        'batches': {
            'SY': int(cfg.get('sy_batches', '1') or '1'),
            'TY': int(cfg.get('ty_batches', '1') or '1'),
            'BTech': int(cfg.get('btech_batches', '1') or '1'),
        },
        'timings': {
            'start': cfg.get('start_time', '09:00'),
            'end': cfg.get('end_time', '20:00'),
            'short_break_min': int(cfg.get('short_break', '15') or '15'),
            'lunch_break_min': int(cfg.get('lunch_break', '45') or '45')
        },
        'SY': {
            'semester': cfg.get('sy_semester', 'odd'),
            'subjects': parse_subjects('sy')
        },
        'TY': {
            'semester': cfg.get('ty_semeseter', 'odd'),
            'subjects': parse_subjects('ty')
        },
        'BTech': {
            'semester': cfg.get('btech_semester', 'even'),
            'subjects': parse_subjects('btech')
        }
    }

    global LAST_CFG
    LAST_CFG = cfg_struct
    results = {}
    years = ['SY', 'TY', 'BTech']
    y_to_matrix = {}
    y_to_data = {}
    y_to_filled = {}
    for year in years:
        matrix, data, filled = _run_schedule_for_config(cfg_struct, year)
        y_to_matrix[year] = matrix
        y_to_data[year] = data
        y_to_filled[year] = filled

    cross_warnings = _resolve_cross_year_room_conflicts(years, y_to_matrix, y_to_data)

    # Save to database
    db = next(get_db())
    project_id = request.form.get('project_id')
    if project_id:
        proj = db.query(TimetableProject).filter(TimetableProject.id == int(project_id), TimetableProject.user_id == session['user_id']).first()
        if proj:
            # Update existing project
            proj.project_name = project_name
            proj.config_json = json.dumps(cfg_struct)
            # Delete old timetable data
            db.query(TimetableData).filter(TimetableData.project_id == proj.id).delete()
            project_id = proj.id
        else:
            project_id = None
    else:
        # Check if project name already exists for this user
        existing = db.query(TimetableProject).filter(TimetableProject.project_name == project_name, TimetableProject.user_id == session['user_id']).first()
        if existing:
            db.close()
            flash(f'Project "{project_name}" already exists. Please choose a different name.')
            return redirect(url_for('configure'))
    
    if not project_id:
        # Create new project
        proj = TimetableProject(user_id=session['user_id'], project_name=project_name, config_json=json.dumps(cfg_struct))
        db.add(proj)
        db.commit()
        db.refresh(proj)
        project_id = proj.id
    
    # Save timetable matrices
    for year in years:
        matrix = y_to_matrix[year]
        td = TimetableData(project_id=project_id, year_key=year, matrix_json=json.dumps(matrix))
        db.add(td)
    db.commit()
    db.close()

    for year in years:
        matrix = y_to_matrix[year]
        data = y_to_data[year]
        filled = y_to_filled[year]
        day_headers, day_rows = _matrix_to_day_grid(matrix, data, cfg_struct)
        warns = _audit_schedule(data, filled, cfg_struct)
        results[year] = {
            'day_headers': day_headers,
            'day_rows': day_rows,
            'semester': cfg_struct.get(year, {}).get('semester', 'even'),
            'warnings': (warns + cross_warnings)
        }

    return render_template('results.html', results=results, project_id=project_id, project_name=project_name)


def _matrix_to_simple_table(matrix, cfg: Dict[str, Any]):
    days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
    labels = _build_time_labels(cfg['timings']['start'], cfg['timings']['end'])
    slots_per_day = len(labels)
    table = []
    table.append(['Time'] + days)
    for t in range(slots_per_day):
        row = [labels[t]]
        for d in range(6):
            if d == 5:
                row.append('-')
                continue
            mat_row = t + d * 12
            cell = '-'
            for j in range(len(matrix[0])):
                val = matrix[mat_row][j]
                if val is not None:
                    cell = 'X'
                    break
            row.append(cell)
        table.append(row)
    return table


def _docx_bytes(table):
    from docx import Document
    doc = Document()
    t = doc.add_table(rows=0, cols=len(table[0]))
    for r in table:
        cells = t.add_row().cells
        for i, v in enumerate(r):
            cells[i].text = str(v)
    bio = io.BytesIO()
    doc.save(bio)
    return bio.getvalue()


def _xlsx_bytes(table):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    for r in table:
        ws.append([str(c) for c in r])
    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()


@app.route('/view/<int:project_id>')
@login_required
def view_project(project_id):
    db = next(get_db())
    proj = db.query(TimetableProject).filter(TimetableProject.id == project_id, TimetableProject.user_id == session['user_id']).first()
    if not proj:
        db.close()
        flash('Project not found')
        return redirect(url_for('dashboard'))
    
    cfg = json.loads(proj.config_json)
    timetable_rows = db.query(TimetableData).filter(TimetableData.project_id == project_id).all()
    db.close()
    
    if not timetable_rows:
        flash('No timetable data found for this project')
        return redirect(url_for('dashboard'))
    
    results = {}
    for td in timetable_rows:
        matrix = json.loads(td.matrix_json)
        # Rebuild data from config for rendering
        teachers_empty_space = {}
        groups_empty_space = {}
        subjects_order = {}
        data = build_data_from_config(cfg, td.year_key, teachers_empty_space, groups_empty_space, subjects_order)
        day_headers, day_rows = _matrix_to_day_grid(matrix, data, cfg)
        results[td.year_key] = {
            'day_headers': day_headers,
            'day_rows': day_rows,
            'semester': cfg.get(td.year_key, {}).get('semester', 'even'),
            'warnings': []
        }
    
    return render_template('results.html', results=results, project_id=project_id, project_name=proj.project_name)


@app.route('/delete/<int:project_id>', methods=['POST'])
@login_required
def delete_project(project_id):
    db = next(get_db())
    proj = db.query(TimetableProject).filter(TimetableProject.id == project_id, TimetableProject.user_id == session['user_id']).first()
    if proj:
        db.delete(proj)
        db.commit()
        flash('Project deleted successfully')
    else:
        flash('Project not found')
    db.close()
    return redirect(url_for('dashboard'))


@app.route('/download_all', methods=['GET'])
@login_required
def download_all():
    project_id = request.args.get('project_id')
    if not project_id:
        flash('No project selected')
        return redirect(url_for('dashboard'))
    
    db = next(get_db())
    proj = db.query(TimetableProject).filter(TimetableProject.id == int(project_id), TimetableProject.user_id == session['user_id']).first()
    if not proj:
        db.close()
        flash('Project not found')
        return redirect(url_for('dashboard'))
    
    cfg = json.loads(proj.config_json)
    timetable_rows = db.query(TimetableData).filter(TimetableData.project_id == project_id).all()
    db.close()
    
    if not timetable_rows:
        flash('No timetable data found')
        return redirect(url_for('dashboard'))
    
    import zipfile
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, mode='w', compression=zipfile.ZIP_DEFLATED) as zf:
        for td in timetable_rows:
            matrix = json.loads(td.matrix_json)
            table = _matrix_to_simple_table(matrix, cfg)
            zf.writestr(f'{td.year_key}.docx', _docx_bytes(table))
            zf.writestr(f'{td.year_key}.xlsx', _xlsx_bytes(table))
    mem.seek(0)
    return send_file(mem, mimetype='application/zip', as_attachment=True, download_name=f'{proj.project_name}_timetables.zip')


@app.route('/download/<year>.csv', methods=['GET'])
def download_csv(year: str):
    # This is a basic placeholder; in a production flow you'd store last matrices per session
    # For now, regenerate quickly from last known simple default form stored in environment
    # to make the route functional.
    default_cfg = {
        'config_name': 'ELE-2024-Sem1',
        'department': 'Dept',
        'num_classrooms': int(os.environ.get('DL_CLASSROOMS', '5')),
        'num_labs': int(os.environ.get('DL_LABS', '3')),
        'batches': {'SY': 1, 'TY': 1, 'BTech': 1},
        'timings': {'start': '09:00', 'end': '20:00', 'short_break_min': 15, 'lunch_break_min': 45},
        'SY': {'semester': 'odd', 'subjects': [{'name': 'Sub1', 'hours': 2, 'labs': 0, 'lab_name': '', 'lab_hours': 0}]},
        'TY': {'semester': 'odd', 'subjects': [{'name': 'Sub1', 'hours': 2, 'labs': 0, 'lab_name': '', 'lab_hours': 0}]},
        'BTech': {'semester': 'even', 'subjects': [{'name': 'Sub1', 'hours': 2, 'labs': 0, 'lab_name': '', 'lab_hours': 0}]},
    }
    matrix, data, _ = _run_schedule_for_config(default_cfg, year)

    # Build CSV in memory
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['TimeSlot'] + [f'C{c}' for c in range(len(matrix[0]))])
    for i, row in enumerate(matrix):
        writer.writerow([i] + [('-' if v is None else v) for v in row])
    output.seek(0)
    return send_file(io.BytesIO(output.read().encode('utf-8')), mimetype='text/csv', as_attachment=True,
                     download_name=f'{year}_timetable.csv')


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', '5000')), debug=True)


