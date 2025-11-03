import os
import json
from typing import Dict, List

try:
    import mysql.connector  # type: ignore
    from mysql.connector import errorcode  # type: ignore
except Exception:  # mysql may not be used in some flows
    mysql = None  # type: ignore
    errorcode = None  # type: ignore

from model import Class, Classroom, Data


def _get_db_config() -> Dict[str, str]:
    return {
        'host': os.environ.get('DB_HOST', '127.0.0.1'),
        'user': os.environ.get('DB_USER', 'root'),
        'password': os.environ.get('DB_PASS', 'Sharan@1383'),
        'database': os.environ.get('DB_NAME', 'timetable'),
        'port': int(os.environ.get('DB_PORT', '3306')),
    }


def get_connection(create_db_if_missing: bool = True):
    if mysql is None:
        raise RuntimeError('MySQL driver not available')
    cfg = _get_db_config()
    try:
        return mysql.connector.connect(
            host=cfg['host'], user=cfg['user'], password=cfg['password'], database=cfg['database'], port=cfg['port']
        )
    except mysql.connector.Error as err:  # type: ignore
        if errorcode and getattr(err, 'errno', None) == errorcode.ER_BAD_DB_ERROR and create_db_if_missing:
            tmp = mysql.connector.connect(host=cfg['host'], user=cfg['user'], password=cfg['password'], port=cfg['port'])
            cur = tmp.cursor()
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{cfg['database']}` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
            cur.close()
            tmp.close()
            return mysql.connector.connect(
                host=cfg['host'], user=cfg['user'], password=cfg['password'], database=cfg['database'], port=cfg['port']
            )
        raise


def create_schema_if_not_exists():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS teachers (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(255) UNIQUE NOT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS groups_ (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(64) UNIQUE NOT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS classrooms (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            type_code VARCHAR(8) NOT NULL,
            UNIQUE KEY uniq_room (name, type_code)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS classes (
            id INT AUTO_INCREMENT PRIMARY KEY,
            subject VARCHAR(255) NOT NULL,
            type_code VARCHAR(4) NOT NULL,
            duration INT NOT NULL,
            teacher_id INT NOT NULL,
            classroom_type_code VARCHAR(8) NOT NULL,
            CONSTRAINT fk_class_teacher FOREIGN KEY (teacher_id) REFERENCES teachers(id)
                ON DELETE RESTRICT ON UPDATE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS class_groups (
            class_id INT NOT NULL,
            group_id INT NOT NULL,
            PRIMARY KEY (class_id, group_id),
            CONSTRAINT fk_cg_class FOREIGN KEY (class_id) REFERENCES classes(id)
                ON DELETE CASCADE ON UPDATE CASCADE,
            CONSTRAINT fk_cg_group FOREIGN KEY (group_id) REFERENCES groups_(id)
                ON DELETE RESTRICT ON UPDATE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """
    )
    conn.commit()
    cur.close()
    conn.close()


def _get_count(conn, table: str) -> int:
    cur = conn.cursor()
    cur.execute(f"SELECT COUNT(*) FROM {table}")
    (count,) = cur.fetchone()
    cur.close()
    return int(count)


def seed_from_file_if_empty(input_file_path: str):
    conn = get_connection()
    try:
        if _get_count(conn, 'classes') > 0:
            return
        with open(input_file_path) as f:
            payload = json.load(f)
        cur = conn.cursor()
        for type_code, names in payload.get('Ucionice', {}).items():
            for name in names:
                cur.execute(
                    "INSERT IGNORE INTO classrooms (name, type_code) VALUES (%s, %s)",
                    (name, type_code)
                )
        name_to_teacher_id: Dict[str, int] = {}
        name_to_group_id: Dict[str, int] = {}
        for cl in payload.get('Casovi', []):
            tname = cl['Nastavnik']
            cur.execute("INSERT IGNORE INTO teachers (name) VALUES (%s)", (tname,))
            cur.execute("SELECT id FROM teachers WHERE name=%s", (tname,))
            (tid,) = cur.fetchone()
            name_to_teacher_id[tname] = tid
            gids: List[int] = []
            for gname in cl['Grupe']:
                cur.execute("INSERT IGNORE INTO groups_ (name) VALUES (%s)", (gname,))
                cur.execute("SELECT id FROM groups_ WHERE name=%s", (gname,))
                (gid,) = cur.fetchone()
                name_to_group_id[gname] = gid
                gids.append(gid)
            cur.execute(
                "INSERT INTO classes (subject, type_code, duration, teacher_id, classroom_type_code) VALUES (%s,%s,%s,%s,%s)",
                (cl['Predmet'], cl['Tip'], int(cl['Trajanje']), name_to_teacher_id[tname], cl['Ucionica'])
            )
            class_id = cur.lastrowid
            for gid in gids:
                cur.execute("INSERT IGNORE INTO class_groups (class_id, group_id) VALUES (%s,%s)", (class_id, gid))
        conn.commit()
        cur.close()
    finally:
        conn.close()


def load_data_from_db(teachers_empty_space, groups_empty_space, subjects_order) -> Data:
    conn = get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT id, name, type_code FROM classrooms ORDER BY id")
        rooms = {r['id']: Classroom(r['name'], r['type_code']) for r in cur.fetchall()}
        cur.execute("SELECT id, name FROM teachers")
        tmap = {r['id']: r['name'] for r in cur.fetchall()}
        cur.execute("SELECT id, name FROM groups_")
        gmap = {r['id']: r['name'] for r in cur.fetchall()}
        teachers = {name: tid for tid, name in tmap.items()}
        groups = {name: gid for gid, name in gmap.items()}
        for tname in teachers.keys():
            if tname not in teachers_empty_space:
                teachers_empty_space[tname] = []
        for _, gidx in groups.items():
            if gidx not in groups_empty_space:
                groups_empty_space[gidx] = []
        cur.execute("SELECT id, subject, type_code, duration, teacher_id, classroom_type_code FROM classes ORDER BY id")
        class_rows = cur.fetchall()
        cur.execute("SELECT class_id, group_id FROM class_groups")
        cg_rows = cur.fetchall()
        cid_to_gids: Dict[int, List[int]] = {}
        for r in cg_rows:
            cid_to_gids.setdefault(r['class_id'], []).append(r['group_id'])
        classes: Dict[int, Class] = {}
        for row in class_rows:
            group_ids = cid_to_gids.get(row['id'], [])
            tname = tmap[row['teacher_id']]
            for gid in group_ids:
                if (row['subject'], gid) not in subjects_order:
                    subjects_order[(row['subject'], gid)] = [-1, -1, -1]
            classes[len(classes)] = Class(group_ids, tname, row['subject'], row['type_code'], str(row['duration']), row['classroom_type_code'])
        # remap classrooms to contiguous indices by type
        idx_rooms: Dict[int, Classroom] = {}
        idx = 0
        for _, room in sorted(rooms.items(), key=lambda kv: kv[0]):
            idx_rooms[idx] = room
            idx += 1
        for i in classes:
            cl = classes[i]
            typ = cl.classrooms
            allowed = []
            for j, room in idx_rooms.items():
                if room.type == typ:
                    allowed.append(j)
            cl.classrooms = allowed
        return Data(groups, teachers, classes, idx_rooms)
    finally:
        conn.close()


