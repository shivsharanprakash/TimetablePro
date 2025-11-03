# User-Specific Data Flow Explanation

## 🔐 How User-Specific Data Works

This document explains how the system ensures each user can only access and manipulate their own data.

---

## 1. **Authentication & Session Management**

### Login Flow:
```
User submits login form
    ↓
System checks username/password
    ↓
If valid → Creates Flask session with user_id
    ↓
session['user_id'] = 123  (stores in server-side session/cookie)
```

**Code Location: `app.py` lines 268-287**
```python
session['user_id'] = user.id  # Stores logged-in user's ID
session['username'] = user.username
```

### Session Protection:
All protected routes use `@login_required` decorator:
```python
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function
```

---

## 2. **Database Schema - User Isolation**

### Database Structure:

```
users table
├── id (PK) ← Unique user identifier
├── username
├── password_hash
└── department

timetable_projects table
├── id (PK)
├── user_id (FK → users.id) ← CRITICAL: Links project to user
├── project_name
├── config_json
└── timestamps

timetable_data table
├── id (PK)
├── project_id (FK → timetable_projects.id) ← Inherits user ownership
├── year_key
└── matrix_json
```

**Key Point**: Every project has a `user_id` foreign key. This is the foundation of user isolation.

---

## 3. **Data Manipulation Operations**

### ✅ **CREATE - Saving a New Project**

**Route**: `POST /generate` (line 353)

```python
# 1. Get current user from session
user_id = session['user_id']  # e.g., 123

# 2. Create project with user_id
proj = TimetableProject(
    user_id=session['user_id'],  # ← Links to current user
    project_name=project_name,
    config_json=json.dumps(cfg_struct)
)

# 3. Save to database
db.add(proj)
db.commit()
```

**Result**: Project is automatically associated with the logged-in user.

---

### 📖 **READ - Viewing Projects**

**Route**: `GET /dashboard` (line 323)

```python
# Only query projects belonging to current user
projects = db.query(TimetableProject).filter(
    TimetableProject.user_id == session['user_id']  # ← Filters by current user
).all()
```

**What happens**:
- User A (id=1) logs in → sees only projects where `user_id = 1`
- User B (id=2) logs in → sees only projects where `user_id = 2`
- Even if User B knows project_id=5 (belonging to User A), they can't see it

**Example Query**:
```sql
SELECT * FROM timetable_projects 
WHERE user_id = 123;  -- Only current user's projects
```

---

### 👁️ **VIEW - Viewing a Specific Project**

**Route**: `GET /view/<project_id>` (line 534)

```python
# Double check: project must belong to current user
proj = db.query(TimetableProject).filter(
    TimetableProject.id == project_id,
    TimetableProject.user_id == session['user_id']  # ← Security check
).first()

if not proj:
    flash('Project not found')  # User can't access other's projects
    return redirect(url_for('dashboard'))
```

**Security**: Even if user manually types `/view/999` (another user's project), they get "Project not found" because the query filters by `user_id`.

---

### ✏️ **UPDATE - Editing a Project**

**Route**: `POST /generate` with `project_id` (line 436)

```python
# 1. Verify project belongs to user
proj = db.query(TimetableProject).filter(
    TimetableProject.id == int(project_id),
    TimetableProject.user_id == session['user_id']  # ← Must match
).first()

if proj:
    # 2. Update only if user owns it
    proj.project_name = project_name
    proj.config_json = json.dumps(cfg_struct)
    db.commit()
```

**Security**: If `project_id` doesn't belong to current user, `proj` is `None`, so update is skipped.

---

### 🗑️ **DELETE - Deleting a Project**

**Route**: `POST /delete/<project_id>` (line 571)

```python
# Only delete if project belongs to current user
proj = db.query(TimetableProject).filter(
    TimetableProject.id == project_id,
    TimetableProject.user_id == session['user_id']  # ← Ownership check
).first()

if proj:
    db.delete(proj)  # Only deletes if user owns it
    db.commit()
```

**Cascade Delete**: When a project is deleted, all related `timetable_data` records are automatically deleted (via `cascade='all, delete-orphan'` in models.py).

---

### 📥 **DOWNLOAD - Downloading Projects**

**Route**: `GET /download_all?project_id=X` (line 586)

```python
# Verify ownership before allowing download
proj = db.query(TimetableProject).filter(
    TimetableProject.id == int(project_id),
    TimetableProject.user_id == session['user_id']  # ← Security check
).first()

if not proj:
    flash('Project not found')
    return redirect(url_for('dashboard'))
```

---

## 4. **Security Measures**

### ✅ **All Queries Filter by `user_id`**

Every database query that accesses projects includes:
```python
.filter(TimetableProject.user_id == session['user_id'])
```

This ensures users can ONLY see/modify their own data.

### ✅ **Session-Based Authentication**

- User must log in to access any functionality
- `user_id` is stored in Flask session (server-side)
- If session expires or user logs out, access is denied

### ✅ **Route Protection**

All sensitive routes use `@login_required`:
- `/dashboard` - View projects
- `/configure` - Create/edit projects
- `/generate` - Generate timetables
- `/view/<id>` - View project
- `/delete/<id>` - Delete project
- `/download_all` - Download files

### ✅ **Ownership Verification**

Before any operation (view/edit/delete), the system verifies:
```python
# Project must exist AND belong to current user
proj = db.query(...).filter(
    TimetableProject.id == project_id,
    TimetableProject.user_id == session['user_id']
).first()
```

---

## 5. **Data Flow Example**

### Scenario: User "alice" (id=1) creates a project

```
1. Alice logs in
   → session['user_id'] = 1

2. Alice fills form and clicks "Generate Timetable"
   → POST /generate
   → project_name = "CSE-2026"

3. System saves to database:
   INSERT INTO timetable_projects (user_id, project_name, ...)
   VALUES (1, 'CSE-2026', ...);  ← user_id=1 links to Alice

4. Alice goes to dashboard
   → GET /dashboard
   → Query: SELECT * FROM timetable_projects WHERE user_id = 1
   → Returns: Only Alice's projects

5. Bob (id=2) tries to access Alice's project
   → GET /view/5  (project 5 belongs to Alice)
   → Query: SELECT * FROM timetable_projects 
            WHERE id = 5 AND user_id = 2
   → Returns: None (no match)
   → Bob sees: "Project not found"
```

---

## 6. **Key Database Constraints**

```sql
-- Foreign key ensures referential integrity
ALTER TABLE timetable_projects 
ADD FOREIGN KEY (user_id) REFERENCES users(id);

-- Project name uniqueness is per-user (handled in application)
-- Two users CAN have projects with same name
```

---

## 7. **Visual Data Isolation**

```
User Table:
┌────┬─────────┐
│ id │ username│
├────┼─────────┤
│ 1  │ alice   │
│ 2  │ bob     │
│ 3  │ charlie │
└────┴─────────┘

Projects Table:
┌────┬─────────┬─────────────────────┐
│ id │ user_id │ project_name        │
├────┼─────────┼─────────────────────┤
│ 1  │ 1       │ CSE-2026            │ ← Alice's project
│ 2  │ 1       │ CSE-2027            │ ← Alice's project
│ 3  │ 2       │ CSE-2026            │ ← Bob's project (same name OK)
│ 4  │ 3       │ ECE-2026            │ ← Charlie's project
└────┴─────────┴─────────────────────┘

When Alice (user_id=1) logs in:
→ SELECT * FROM timetable_projects WHERE user_id = 1
→ Returns: rows 1, 2 only

When Bob (user_id=2) logs in:
→ SELECT * FROM timetable_projects WHERE user_id = 2
→ Returns: row 3 only
```

---

## 8. **Summary**

✅ **User Isolation is Achieved Through**:
1. Session stores `user_id` after login
2. Every project has `user_id` foreign key
3. All queries filter by `session['user_id']`
4. Routes are protected with `@login_required`
5. Ownership is verified before any operation

✅ **Users CANNOT**:
- See other users' projects
- Edit other users' projects
- Delete other users' projects
- Access data they don't own

✅ **Users CAN**:
- Create unlimited projects
- Name projects whatever they want (duplicates allowed per user)
- Edit/delete only their own projects
- View/download only their own timetables

---

## 9. **Testing User Isolation**

To verify it works:

1. Create two test accounts (user1, user2)
2. User1 creates a project "Test-Project"
3. User1 logs out, User2 logs in
4. User2 tries to access `/view/<project_id>` of User1's project
5. Result: "Project not found" ✅ (correct behavior)

This proves the system is user-specific and secure!


