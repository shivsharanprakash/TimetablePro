from typing import Dict, Any, List

from model import Class, Classroom, Data


def _build_classrooms(num_classrooms: int, num_labs: int, lab_names: List[str] | None = None) -> Dict[int, Classroom]:
    classrooms: Dict[int, Classroom] = {}
    idx = 0
    for i in range(num_classrooms):
        classrooms[idx] = Classroom(f'CR-{i+1}', 'n')
        idx += 1
    for i in range(num_labs):
        name = None
        if lab_names and i < len(lab_names):
            name = lab_names[i]
        classrooms[idx] = Classroom(name or f'Lab-{i+1}', 'r')
        idx += 1
    return classrooms


def build_data_from_config(cfg: Dict[str, Any], year_key: str,
                           teachers_empty_space: Dict[str, List[int]],
                           groups_empty_space: Dict[int, List[int]],
                           subjects_order: Dict) -> Data:
    # Treat each batch as a group for the given year
    batch_count = cfg['batches'][year_key]
    groups: Dict[str, int] = {}
    teachers: Dict[str, int] = {}
    classes: Dict[int, Class] = {}
    classrooms = _build_classrooms(cfg['num_classrooms'], cfg['num_labs'], cfg.get('lab_names'))

    # Helper to ensure dict init
    def ensure_teacher(name: str):
        if name not in teachers:
            teachers[name] = len(teachers)
            if name not in teachers_empty_space:
                teachers_empty_space[name] = []

    # Create group IDs for batches
    for b in range(batch_count):
        gname = f'{year_key}-B{b+1}'
        groups[gname] = len(groups)
        groups_empty_space[groups[gname]] = []

    # For each subject, create a class per batch with duration = hours; map labs to lab classroom type 'r'
    subj_list = cfg[year_key]['subjects']
    class_index = 0
    for subj in subj_list:
        # Expand lectures: interpret 'hours' as number of 1-hour sessions per week
        lec_sessions = max(0, int(subj.get('hours', 0) or 0))
        if lec_sessions > 0:
            ensure_teacher(f'Teacher-{subj["name"]}')
            for gname, gidx in groups.items():
                for s in range(lec_sessions):
                    cl = Class([gidx], f'Teacher-{subj["name"]}', subj['name'], 'P', '1', 'n')
                    classes[class_index] = cl
                    subjects_order[(subj['name'], gidx)] = [-1, -1, -1]
                    class_index += 1

        # Expand labs: interpret 'labs' as number of sessions per week, each of duration 'lab_hours'
        lab_sessions = max(0, int(subj.get('labs', 0) or 0))
        lab_dur = max(1, int(subj.get('lab_hours', 0) or 1))
        if lab_sessions > 0:
            ensure_teacher(f'Lab-{subj["lab_name"] or subj["name"]}')
            for gname, gidx in groups.items():
                for s in range(lab_sessions):
                    cl = Class([gidx], f'Lab-{subj["lab_name"] or subj["name"]}', subj['lab_name'] or subj['name'],
                               'L', str(lab_dur), 'r')
                    classes[class_index] = cl
                    subjects_order[(subj['lab_name'] or subj['name'], gidx)] = [-1, -1, -1]
                    class_index += 1

    # Convert classrooms field to indices expected by the rest of the code
    for i in classes:
        cl = classes[i]
        classroom_type = cl.classrooms
        index_classrooms: List[int] = []
        for idx, room in classrooms.items():
            if room.type == classroom_type:
                index_classrooms.append(idx)
        cl.classrooms = index_classrooms

    return Data(groups, teachers, classes, classrooms)


