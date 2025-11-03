import random
import os
from operator import itemgetter
from collections import defaultdict
from typing import Dict, Any
from utils import load_data, show_timetable, set_up, show_statistics, write_solution_to_file
from costs import check_hard_constraints, hard_constraints_cost, empty_space_groups_cost, empty_space_teachers_cost, \
    free_hour
import copy
import math
from db_utils import create_schema_if_not_exists, seed_from_file_if_empty, load_data_from_db


def build_lab_map_from_config(cfg: Dict[str, Any], year_key: str, data=None) -> Dict[str, str]:
    """
    Build subject-to-lab mapping from configuration.
    Returns: {lab_class_subject: lab_room_name}
    """
    lab_map = {}
    subj_list = cfg.get(year_key, {}).get('subjects', [])
    lab_names_list = cfg.get('lab_names', [])
    
    # Build mapping of lab_index -> lab_room_name from available labs
    lab_room_names = {}
    if data:
        lab_idx = 0
        for idx, room in data.classrooms.items():
            if room.type == 'r':
                lab_room_names[lab_idx] = room.name
                lab_idx += 1
    
    subject_lab_index = 0
    
    for subj in subj_list:
        subject_name = subj.get('name', '')
        lab_name = subj.get('lab_name', '').strip()
        num_labs_per_subject = int(subj.get('labs', 0) or 0)
        
        if num_labs_per_subject > 0 and subject_name:
            lab_class_subject = lab_name if lab_name else subject_name
            
            if lab_name:
                actual_lab_room_name = lab_name
            elif subject_lab_index < len(lab_names_list) and lab_names_list[subject_lab_index]:
                actual_lab_room_name = lab_names_list[subject_lab_index]
            elif data and subject_lab_index < len(lab_room_names):
                actual_lab_room_name = lab_room_names[subject_lab_index]
            else:
                actual_lab_room_name = f"Lab-{subject_lab_index + 1}"
            
            lab_map[lab_class_subject] = actual_lab_room_name
            subject_lab_index += 1
    
    return lab_map


def schedule_labs_first(data, matrix, free, filled, groups_empty_space, teachers_empty_space, 
                        subjects_order, year_key, cfg, labels):
    """
    Separate lab scheduling phase that runs BEFORE lecture allocation.
    Enhanced with proper resource occupancy matrices and batch-level quota tracking.
    Ensures labs are scheduled in 2-hour continuous blocks for each batch subgroup.
    """
    # Build reverse map: original subject -> lab name for matching
    subject_to_lab_name = {}
    subj_list = cfg.get(year_key, {}).get('subjects', [])
    for subj in subj_list:
        subject_name = subj.get('name', '')
        lab_name = subj.get('lab_name', '').strip()
        num_labs = int(subj.get('labs', 0) or 0)
        if num_labs > 0 and subject_name:
            # Map original subject to lab name (lab classes use lab_name as subject)
            if lab_name:
                subject_to_lab_name[subject_name] = lab_name
            else:
                subject_to_lab_name[subject_name] = f"{subject_name} Lab"
    
    lab_map = build_lab_map_from_config(cfg, year_key, data)
    
    if not lab_map and not subject_to_lab_name:
        print("No labs to schedule.")
        return
    
    print(f"Subject to Lab mapping: {subject_to_lab_name}")
    print(f"Lab map (for matching): {lab_map}")
    
    # Enhanced resource occupancy matrices
    lab_occupancy = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: None)))
    batch_schedule = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: None)))
    subject_quota = defaultdict(lambda: {'labs_required': 0, 'labs_scheduled': 0})
    
    # Get lab pool
    lab_pool = {}
    lab_name_to_room_idx = {}
    lab_names_list = []
    for idx, room in data.classrooms.items():
        if room.type == 'r':
            lab_pool[idx] = room
            lab_name_to_room_idx[room.name] = idx
            lab_names_list.append(room.name)
    
    print(f"Lab Resource Pool: {lab_names_list}")
    
    # Initialize quota tracking - use original subject names
    subj_list = cfg.get(year_key, {}).get('subjects', [])
    batch_count = cfg.get('batches', {}).get(year_key, 1)
    for subj in subj_list:
        subject_name = subj.get('name', '')
        num_labs = int(subj.get('labs', 0) or 0)
        if num_labs > 0:
            for batch_num in range(1, batch_count + 1):
                batch_name = f'{year_key}-B{batch_num}'
                batch_idx = data.groups.get(batch_name, None)
                if batch_idx is not None:
                    # Track by original subject name
                    subject_quota[(subject_name, batch_idx)]['labs_required'] = num_labs
                    # Also track by lab name if different
                    lab_name = subj.get('lab_name', '').strip() or f"{subject_name} Lab"
                    if lab_name != subject_name:
                        subject_quota[(lab_name, batch_idx)]['labs_required'] = num_labs
    
    # Lab scheduling must use consecutive 2-hour blocks (lab_duration = 2)
    # Based on user requirement: labs can only be placed in these 3 windows:
    # 1. Starting two slots: [0, 1] = 09:15-11:15 (before short break)
    # 2. After short break two slots: [3, 4] = 12:15-14:15 (after short break, before lunch)
    # 3. After long break two slots: [5, 6] = 14:15-16:15 (after lunch break)
    # 
    # Note: Break slots are at index 2 (short break) and index 4 (lunch break)
    # So valid consecutive 2-slot windows that don't include breaks are: [0,1], [3,4?], [5,6]
    # But if slot 4 is the lunch break itself, then [3,4] is invalid
    # Actually, if lunch break is at slot 4, it means slot 4 IS the break, so [3,4] crosses the break
    # Let's check: if break structure is 0,1,2(break),3,4(break),5,6
    # Then valid windows are [0,1] and [5,6], and maybe [3] alone? No, labs need 2 consecutive slots.
    # 
    # User says "after short break two slots" - this likely means slots 3-4 (12:15-14:15)
    # But if slot 4 is lunch break, then we need to skip it
    # Let's assume the structure is: 0,1,2(break),3,4,5(break?),6
    # Or: 0,1,2(break),3,4(break),5,6
    
    # Standard structure: breaks at slots 2 (short) and 4 (lunch)
    # Valid consecutive 2-slot windows: [0,1] and [5,6]
    # But user wants "after short break two slots" = [3,4] if slot 4 is not break
    # If slot 4 is lunch break, then we can only use [0,1] and [5,6]
    
    # For 2-hour labs, only allow placement in these specific windows:
    valid_lab_windows = [
        [0, 1],  # First 2 slots: 09:15-11:15 (before short break)
        [3, 4],  # After short break: 12:15-14:15 (assuming slot 4 is not break - will filter if needed)
        [5, 6]   # After lunch: 14:15-16:15
    ]
    
    # Break slots are at index 2 (short break after slots 0-1) and index 4 (lunch break after slots 0-1-2-3)
    # Filter out windows that include break slots
    break_slots = {2, 4}
    valid_lab_windows = [win for win in valid_lab_windows if not any(s in break_slots for s in win)]
    
    # Since slot 4 is lunch break, [3,4] is invalid
    # So valid windows are: [0,1] and [5,6]
    # But user explicitly wants "after short break two slots" - this might mean slots 3 + next slot after lunch
    # However, they must be consecutive, so we can't do [3,5] as that skips slot 4 (break)
    # Solution: Only use [0,1] and [5,6], skip [3,4] if slot 4 is break
    
    # For now, use only confirmed valid windows
    valid_lab_windows = [[0, 1], [5, 6]]  # [3,4] excluded because slot 4 is lunch break
    
    # Create flat list of start slots for preferred ordering
    preferred_lab_start_slots = []
    for win in valid_lab_windows:
        preferred_lab_start_slots.extend(win[:1])  # Add first slot of each window
    
    preferred_morning_slots = [0]  # Start of first window: 09:15-11:15
    preferred_afternoon_slots = [5]  # Start of third window: 14:15-16:15
    preferred_lab_slots = preferred_lab_start_slots
    day_order = [0, 1, 2, 3, 4]  # Prioritize first three days
    lab_day_counts = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    
    # Get batch subgroup names for better reporting
    def get_batch_subgroup_name(year_key, batch_num):
        """Get batch subgroup name (S1, S2, S3 for SY; T1, T2... for TY; B1, B2... for BTech)"""
        if year_key == 'SY':
            return f'S{batch_num}'
        elif year_key == 'TY':
            return f'T{batch_num}'
        elif year_key == 'BTech':
            return f'B{batch_num}'
        else:
            return f'{year_key}-B{batch_num}'
    
    # Get ALL lab classes (type 'L') - they should all be scheduled
    lab_classes = []
    total_classes = len(data.classes)
    lab_type_classes = []
    
    for idx, cls in data.classes.items():
        if cls.type == 'L':
            lab_type_classes.append((idx, cls))
    
    # Match lab classes to subjects that have labs
    for idx, cls in lab_type_classes:
        subject = cls.subject  # This is lab_name from config (e.g., "DS Lab" or just "DS")
        
        # Check if this lab class matches any subject that requires labs
        matched = False
        for orig_subject, lab_name in subject_to_lab_name.items():
            # Match if: subject == lab_name, or subject contains lab_name, or lab_name contains subject
            if (subject == lab_name or 
                subject == orig_subject or 
                subject in lab_name or 
                lab_name in subject or
                subject.replace(' Lab', '') == orig_subject):
                lab_classes.append((idx, cls))
                matched = True
                break
        
        # If no match found, still include it (might be valid lab class)
        if not matched:
            # Check if it's a valid lab name pattern
            if 'Lab' in subject or any(lab_name in subject for lab_name in subject_to_lab_name.values()):
                lab_classes.append((idx, cls))
            else:
                # Include all type 'L' classes anyway - better to try scheduling them
                lab_classes.append((idx, cls))
    
    print(f"\n=== LAB SCHEDULING PHASE ===")
    print(f"Total classes in data: {total_classes}")
    print(f"Classes with type 'L': {len(lab_type_classes)}")
    print(f"Lab classes to schedule: {len(lab_classes)}")
    print(f"Available lab rooms: {len(lab_pool)}")
    print(f"Lab room names: {[r.name for r in lab_pool.values()]}")
    if lab_classes:
        print(f"Sample lab classes:")
        for i, (idx, cls) in enumerate(lab_classes[:3]):  # Show first 3
            print(f"  [{idx}] subject='{cls.subject}', groups={cls.groups}, duration={cls.duration}, classrooms={cls.classrooms}")
    else:
        print("WARNING: No lab classes found! Check config_adapter.py lab creation logic.")
    
    # Sort labs by duration (longer first) and then by subject for consistent ordering
    lab_classes.sort(key=lambda x: (int(x[1].duration), x[1].subject), reverse=True)
    
    conflicts = []
    unplaced_labs = []
    reassignments = []
    
    for class_idx, class_obj in lab_classes:
        subject = class_obj.subject  # This is the lab_name from config (e.g., "DS Lab")
        batch_idx = class_obj.groups[0] if class_obj.groups else 0
        lab_duration = int(class_obj.duration)
        
        # Find expected lab name - check lab_map first, then reverse lookup
        expected_lab_name = lab_map.get(subject)
        if not expected_lab_name:
            # Try reverse lookup: find original subject that maps to this lab name
            for orig_subject, lab_name in subject_to_lab_name.items():
                if lab_name == subject:
                    expected_lab_name = lab_map.get(lab_name) or lab_name
                    break
        
        # If still not found, use subject itself (it might be the lab name)
        if not expected_lab_name:
            expected_lab_name = subject
        
        # Find lab room by name match
        lab_room_idx = None
        for room_idx, room in lab_pool.items():
            # Match by exact name or partial match
            if room.name == expected_lab_name or expected_lab_name in room.name or room.name in expected_lab_name:
                lab_room_idx = room_idx
                expected_lab_name = room.name  # Use actual room name
                break
        
        # If still not found, use any available lab from pool
        if lab_room_idx is None:
            if lab_pool:
                # Use first available lab as fallback
                lab_room_idx = list(lab_pool.keys())[0]
                expected_lab_name = lab_pool[lab_room_idx].name
                reassignments.append(f"{subject}: Using fallback lab {expected_lab_name}")
            else:
                conflicts.append(f"Error: No lab rooms available for {subject}")
                continue
        
        # Ensure lab_room_idx is in allowed classrooms (should be since it's type 'r')
        if lab_room_idx not in class_obj.classrooms:
            # Add it to allowed classrooms since it's a lab
            class_obj.classrooms.append(lab_room_idx)
        
        if not class_obj.classrooms:
            conflicts.append(f"Error: {subject} has no allowed lab classrooms")
            continue
        
        placed = False
        retry_with_alternate_lab = False
        
        # Get batch number for subgroup naming
        batch_num = None
        for gname, gidx in data.groups.items():
            if gidx == batch_idx:
                # Extract batch number from group name (e.g., 'SY-B1' -> 1)
                if '-B' in gname:
                    try:
                        batch_num = int(gname.split('-B')[1])
                    except:
                        batch_num = batch_idx + 1
                break
        if batch_num is None:
            batch_num = batch_idx + 1
        
        batch_subgroup = get_batch_subgroup_name(year_key, batch_num)
        
        # First pass: Try preferred lab and preferred slots
        for day in day_order:
            if placed:
                break
            
            # Check if this batch already has 2 labs on this day (max 2 labs per day per batch)
            total_labs_today = sum(lab_day_counts[s][batch_idx][day] for s in lab_day_counts)
            if total_labs_today >= 2:
                continue
            
            # Also check if this specific subject already has a lab on this day
            if lab_day_counts[subject][batch_idx][day] >= 1:
                continue
            
            # Try only valid 2-hour windows for labs (lab_duration must be 2)
            # Only attempt placement in valid_lab_windows: [0,1] and [5,6]
            # Order: prefer morning window [0,1] first, then afternoon [5,6]
            for win in valid_lab_windows:
                if placed:
                    break
                
                # Check if this window is valid for lab_duration
                if len(win) < lab_duration:
                    continue
                
                slot = win[0]  # Start slot of the window
                
                # Verify all slots in window are consecutive and available
                if slot + lab_duration > 12:
                    continue
                
                all_slots_valid = True
                for d in range(lab_duration):
                    check_slot = slot + d
                    # Ensure check_slot is within the window and not a break slot
                    if check_slot not in win or check_slot in {2, 4}:  # Break slots
                        all_slots_valid = False
                        break
                    row = day * 12 + check_slot
                    if row not in [f[0] for f in free]:
                        all_slots_valid = False
                        break
                if not all_slots_valid:
                    continue
                
                lab_occupied = False
                for d in range(lab_duration):
                    if lab_occupancy[expected_lab_name][day][slot + d] is not None:
                        lab_occupied = True
                        break
                if lab_occupied:
                    continue
                
                batch_conflict = False
                for d in range(lab_duration):
                    if batch_schedule[batch_idx][day][slot + d] is not None:
                        batch_conflict = True
                        break
                if batch_conflict:
                    continue
                
                row_conflict = False
                for d in range(lab_duration):
                    row = day * 12 + slot + d
                    if not valid_teacher_group_row(matrix, data, class_idx, row):
                        row_conflict = True
                        break
                if row_conflict:
                    continue
                
                # Place the lab
                batch_name = f"{year_key}-B{batch_num}" if batch_num else f"Batch-{batch_idx}"
                
                for d in range(lab_duration):
                    row = day * 12 + slot + d
                    actual_slot = slot + d
                    
                    lab_occupancy[expected_lab_name][day][actual_slot] = batch_subgroup
                    batch_schedule[batch_idx][day][actual_slot] = (expected_lab_name, subject, 'L')
                    
                    filled.setdefault(class_idx, []).append((row, lab_room_idx))
                    if (row, lab_room_idx) in free:
                        free.remove((row, lab_room_idx))
                    
                    for group_idx in class_obj.groups:
                        groups_empty_space[group_idx].append(row)
                    teachers_empty_space[class_obj.teacher].append(row)
                
                start_row = day * 12 + slot
                for group_idx in class_obj.groups:
                    insert_order(subjects_order, class_obj.subject, group_idx, class_obj.type, start_row)
                
                lab_day_counts[subject][batch_idx][day] += 1
                # Update quota based on original subject name if available
                quota_updated = False
                for orig_subject, lab_name in subject_to_lab_name.items():
                    if lab_name == subject or subject == orig_subject:
                        subject_quota[(orig_subject, batch_idx)]['labs_scheduled'] += 1
                        quota_updated = True
                        break
                if not quota_updated:
                    # Fallback: use subject directly
                    subject_quota[(subject, batch_idx)]['labs_scheduled'] += 1
                
                day_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']
                slot_time = ['09:15-10:15', '10:15-11:15', '11:15-12:15', '12:15-13:15', 
                            '13:15-14:15', '14:15-15:15', '15:15-16:15', '16:15-17:15']
                time_str = slot_time[slot] if slot < len(slot_time) else f"Slot {slot}"
                print(f"  ✓ Placed {subject} lab ({lab_duration}h) -> {expected_lab_name} on {day_names[day]}, {time_str} (Batch {batch_subgroup})")
                placed = True
                break
        
        # Second pass: Try alternate labs if preferred lab failed
        # Still only use valid 2-hour windows
        if not placed and expected_lab_name:
            for alt_lab_name in lab_names_list:
                if alt_lab_name == expected_lab_name:
                    continue
                
                alt_lab_room_idx = lab_name_to_room_idx.get(alt_lab_name)
                if alt_lab_room_idx is None or alt_lab_room_idx not in class_obj.classrooms:
                    continue
                
                for day in day_order:
                    if placed:
                        break
                    # Check max 2 labs per day per batch
                    total_labs_today = sum(lab_day_counts[s][batch_idx][day] for s in lab_day_counts)
                    if total_labs_today >= 2:
                        continue
                    # Also check if this subject already has a lab today
                    if lab_day_counts[subject][batch_idx][day] >= 1:
                        continue
                    
                    # Try only valid 2-hour windows
                    for win in valid_lab_windows:
                        if placed:
                            break
                        
                        if len(win) < lab_duration:
                            continue
                        
                        slot = win[0]
                        if placed:
                            break
                        
                        # Verify all slots in window are consecutive and available
                        if slot + lab_duration > 12:
                            continue
                        
                        all_slots_valid = True
                        for d in range(lab_duration):
                            check_slot = slot + d
                            # Ensure check_slot is within the window and not a break slot
                            if check_slot not in win or check_slot in {2, 4}:  # Break slots
                                all_slots_valid = False
                                break
                            row = day * 12 + check_slot
                            if row not in [f[0] for f in free]:
                                all_slots_valid = False
                                break
                        if not all_slots_valid:
                            continue
                        
                        alt_lab_occupied = False
                        for d in range(lab_duration):
                            if lab_occupancy[alt_lab_name][day][slot + d] is not None:
                                alt_lab_occupied = True
                                break
                        if alt_lab_occupied:
                            continue
                        
                        batch_conflict = False
                        for d in range(lab_duration):
                            if batch_schedule[batch_idx][day][slot + d] is not None:
                                batch_conflict = True
                                break
                        if batch_conflict:
                            continue
                        
                        row_conflict = False
                        for d in range(lab_duration):
                            row = day * 12 + slot + d
                            if not valid_teacher_group_row(matrix, data, class_idx, row):
                                row_conflict = True
                                break
                        if row_conflict:
                            continue
                        
                        # Place in alternate lab
                        batch_name = f"{year_key}-B{batch_num}" if batch_num else f"Batch-{batch_idx}"
                        
                        for d in range(lab_duration):
                            row = day * 12 + slot + d
                            actual_slot = slot + d
                            lab_occupancy[alt_lab_name][day][actual_slot] = batch_subgroup
                            batch_schedule[batch_idx][day][actual_slot] = (alt_lab_name, subject, 'L')
                            filled.setdefault(class_idx, []).append((row, alt_lab_room_idx))
                            if (row, alt_lab_room_idx) in free:
                                free.remove((row, alt_lab_room_idx))
                            for group_idx in class_obj.groups:
                                groups_empty_space[group_idx].append(row)
                            teachers_empty_space[class_obj.teacher].append(row)
                        
                        start_row = day * 12 + slot
                        for group_idx in class_obj.groups:
                            insert_order(subjects_order, class_obj.subject, group_idx, class_obj.type, start_row)
                        
                        lab_day_counts[subject][batch_idx][day] += 1
                        # Update quota
                        quota_updated = False
                        for orig_subject, lab_name in subject_to_lab_name.items():
                            if lab_name == subject or subject == orig_subject:
                                subject_quota[(orig_subject, batch_idx)]['labs_scheduled'] += 1
                                quota_updated = True
                                break
                        if not quota_updated:
                            subject_quota[(subject, batch_idx)]['labs_scheduled'] += 1
                        
                        reassignments.append(f"{subject} (Batch {batch_subgroup}): Reassigned from {expected_lab_name} to {alt_lab_name}")
                        day_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']
                        slot_time = ['09:15-10:15', '10:15-11:15', '11:15-12:15', '12:15-13:15', 
                                    '13:15-14:15', '14:15-15:15', '15:15-16:15', '16:15-17:15']
                        time_str = slot_time[slot] if slot < len(slot_time) else f"Slot {slot}"
                        print(f"  ✓ Placed {subject} lab ({lab_duration}h) -> {alt_lab_name} (fallback) on {day_names[day]}, {time_str} (Batch {batch_subgroup})")
                        placed = True
                        break
                if placed:
                    break
        
        if not placed:
            unplaced_labs.append(f"{subject} (Batch {batch_subgroup}, {lab_duration}h)")
            conflicts.append(f"Could not place {subject} lab for Batch {batch_subgroup} - no available slots")
    
    # Fill matrix with lab assignments - ensure we update it
    for class_idx, fields_list in filled.items():
        for field in fields_list:
            if field[0] < len(matrix) and field[1] < len(matrix[field[0]]):
                matrix[field[0]][field[1]] = class_idx
    
    # Final retry for unplaced labs - try any available slot
    unplaced_retry = []
    for class_idx, class_obj in lab_classes:
        if class_idx in filled:
            continue
        
        subject = class_obj.subject
        batch_idx = class_obj.groups[0] if class_obj.groups else 0
        lab_duration = int(class_obj.duration)
        
        # Get batch number for subgroup naming
        batch_num = None
        for gname, gidx in data.groups.items():
            if gidx == batch_idx:
                if '-B' in gname:
                    try:
                        batch_num = int(gname.split('-B')[1])
                    except:
                        batch_num = batch_idx + 1
                break
        if batch_num is None:
            batch_num = batch_idx + 1
        batch_subgroup = get_batch_subgroup_name(year_key, batch_num)
        
        placed = False
        for day in range(5):
            if placed:
                break
            # Final retry: Still only use valid 2-hour windows
            for win in valid_lab_windows:
                if placed:
                    break
                
                if len(win) < lab_duration:
                    continue
                
                slot = win[0]
                
                # Try any available lab
                for lab_name, lab_room_idx in lab_name_to_room_idx.items():
                    if lab_room_idx not in class_obj.classrooms:
                        continue
                    
                    # Verify all slots in window are consecutive and available
                    if slot + lab_duration > 12:
                        continue
                    
                    all_valid = True
                    for d in range(lab_duration):
                        check_slot = slot + d
                        # Ensure check_slot is within the window and not a break slot
                        if check_slot not in win or check_slot in {2, 4}:  # Break slots
                            all_valid = False
                            break
                        row = day * 12 + check_slot
                        if row not in [f[0] for f in free]:
                            all_valid = False
                            break
                        if not valid_teacher_group_row(matrix, data, class_idx, row):
                            all_valid = False
                            break
                    if not all_valid:
                        continue
                    
                    if lab_occupancy[lab_name][day][slot] is not None:
                        continue
                    
                    if batch_schedule[batch_idx][day][slot] is not None:
                        continue
                    
                    # Place it
                    for d in range(lab_duration):
                        row = day * 12 + slot + d
                        actual_slot = slot + d
                        lab_occupancy[lab_name][day][actual_slot] = batch_subgroup
                        batch_schedule[batch_idx][day][actual_slot] = (lab_name, subject, 'L')
                        filled.setdefault(class_idx, []).append((row, lab_room_idx))
                        if (row, lab_room_idx) in free:
                            free.remove((row, lab_room_idx))
                        for group_idx in class_obj.groups:
                            groups_empty_space[group_idx].append(row)
                        teachers_empty_space[class_obj.teacher].append(row)
                    
                    start_row = day * 12 + slot
                    for group_idx in class_obj.groups:
                        insert_order(subjects_order, class_obj.subject, group_idx, class_obj.type, start_row)
                    
                    lab_day_counts[subject][batch_idx][day] += 1
                    # Update quota
                    quota_updated = False
                    for orig_subject, lab_name in subject_to_lab_name.items():
                        if lab_name == subject or subject == orig_subject:
                            subject_quota[(orig_subject, batch_idx)]['labs_scheduled'] += 1
                            quota_updated = True
                            break
                    if not quota_updated:
                        subject_quota[(subject, batch_idx)]['labs_scheduled'] += 1
                    
                    day_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']
                    slot_time = ['09:15-10:15', '10:15-11:15', '11:15-12:15', '12:15-13:15', 
                                '13:15-14:15', '14:15-15:15', '15:15-16:15', '16:15-17:15']
                    time_str = slot_time[slot] if slot < len(slot_time) else f"Slot {slot}"
                    print(f"  ✓ Retry placed {subject} lab -> {lab_name} on {day_names[day]}, {time_str} (Batch {batch_subgroup})")
                    placed = True
                    break
                if placed:
                    break
        
        if not placed:
            unplaced_retry.append(f"{subject} (Batch {batch_subgroup})")
    
    # Update matrix after retry
    for class_idx, fields_list in filled.items():
        for field in fields_list:
            matrix[field[0]][field[1]] = class_idx
    
    # Validation and reporting
    print(f"\n=== LAB SCHEDULING SUMMARY ===")
    final_unplaced = [lab for lab in unplaced_labs if lab.split('(')[0].strip() not in [r.split('(')[0].strip() for r in unplaced_retry]]
    placed_count = len(lab_classes) - len(final_unplaced)
    print(f"Placed: {placed_count}/{len(lab_classes)} lab sessions")
    
    if reassignments:
        print(f"\n✓ Lab Reassignments (conflict resolution):")
        for reassign in reassignments:
            print(f"  - {reassign}")
    
    if final_unplaced:
        print(f"\n⚠️  Unplaced labs after retry: {final_unplaced}")
    
    if conflicts and final_unplaced:
        print(f"\n⚠️  Lab Conflicts/Warnings:")
        for conflict in conflicts:
            print(f"  - {conflict}")
    
    # Weekly quota verification with enhanced reporting
    print(f"\n=== LAB QUOTA VERIFICATION ===")
    missing_sessions = []
    for (subject, batch_idx), quota_info in subject_quota.items():
        required = quota_info['labs_required']
        scheduled = quota_info['labs_scheduled']
        
        # Get batch subgroup name
        batch_num = None
        for gname, gidx in data.groups.items():
            if gidx == batch_idx:
                if '-B' in gname:
                    try:
                        batch_num = int(gname.split('-B')[1])
                    except:
                        batch_num = batch_idx + 1
                break
        if batch_num is None:
            batch_num = batch_idx + 1
        batch_subgroup = get_batch_subgroup_name(year_key, batch_num)
        
        if scheduled < required:
            missing = required - scheduled
            missing_sessions.append(f"Missing {missing} lab session(s) for {subject} (Batch {batch_subgroup}) - required {required}, scheduled {scheduled}")
            print(f"  ⚠️ Missing {missing} lab session(s) for {subject} (Batch {batch_subgroup})")
        else:
            print(f"  ✅ {subject} (Batch {batch_subgroup}): {scheduled}/{required} sessions completed")
    
    if missing_sessions:
        print(f"\n⚠️  MISSING SESSIONS DETECTED:")
        for missing in missing_sessions:
            print(f"  {missing}")


def initial_population(data, matrix, free, filled, groups_empty_space, teachers_empty_space, subjects_order, year_key=None):
    """
    Improved initial population with structured resource management, priority-based scheduling,
    batch-level isolation, and enhanced conflict resolution.
    Enhanced with proper resource occupancy matrices and weekly quota tracking.
    """
    classes = data.classes

    # Get batch subgroup naming function
    def get_batch_subgroup_name(year_key, batch_num):
        """Get batch subgroup name (S1, S2, S3 for SY; T1, T2... for TY; B1, B2... for BTech)"""
        if year_key == 'SY':
            return f'S{batch_num}'
        elif year_key == 'TY':
            return f'T{batch_num}'
        elif year_key == 'BTech':
            return f'B{batch_num}'
        else:
            return f'{year_key}-B{batch_num}' if year_key else f'Batch-{batch_num}'

    # Separate resource pools
    classroom_pool = []
    lab_pool = []
    classroom_names = []
    for idx, room in data.classrooms.items():
        if room.type == 'n':
            classroom_pool.append(idx)
            classroom_names.append(room.name)
        elif room.type == 'r':
            lab_pool.append(idx)
    
    print(f"Classroom Resource Pool: {classroom_names}")
    
    # Enhanced resource occupancy matrices
    classroom_occupancy = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: None)))
    batch_schedule = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: None)))
    subject_quota = defaultdict(lambda: {'lectures_required': 0, 'lectures_scheduled': 0})
    
    batch_occupancy = defaultdict(lambda: defaultdict(lambda: {}))
    lab_specific_occupancy = defaultdict(lambda: defaultdict(lambda: {}))
    conflict_log = []
    
    # Validate capacity
    def validate_capacity():
        warnings = []
        total_lectures = sum(1 for _, c in classes.items() if c.type == 'P')
        total_labs = sum(1 for _, c in classes.items() if c.type == 'L')
        max_lecture_slots = len(classroom_pool) * 5 * 6
        max_lab_slots = len(lab_pool) * 5 * 6
        if total_lectures > max_lecture_slots:
            warnings.append(
                f"Capacity warning: {total_lectures} lecture sessions but only "
                f"{max_lecture_slots} slots available ({len(classroom_pool)} classrooms)"
            )
        if total_labs > max_lab_slots:
            warnings.append(
                f"Capacity warning: {total_labs} lab sessions but only "
                f"{max_lab_slots} slots available ({len(lab_pool)} labs)"
            )
        return warnings
    
    capacity_warnings = validate_capacity()
    if capacity_warnings:
        print("Resource Capacity Warnings:")
        for w in capacity_warnings:
            print(f"  - {w}")
    
    # Only process lectures - labs are handled separately
    lecture_classes = []
    lecture_quota_map = {}
    
    for idx, cls in classes.items():
        if cls.type == 'P' and idx not in filled:
            lecture_classes.append(idx)
            for batch_idx in cls.groups:
                subject_key = (cls.subject, batch_idx)
                if subject_key not in lecture_quota_map:
                    lecture_quota_map[subject_key] = 0
                lecture_quota_map[subject_key] += 1
    
    # Initialize quota tracking
    for (subject, batch_idx), count in lecture_quota_map.items():
        subject_quota[(subject, batch_idx)]['lectures_required'] = count
    
    priority_order = lecture_classes
    
    # Track per-day lecture counts
    lecture_day_counts = {}
    lab_day_counts = {}
    total_lectures = sum(1 for _, c in classes.items() if c.type == 'P')
    total_labs = sum(1 for _, c in classes.items() if c.type == 'L')
    per_day_lecture_target = max(1, math.ceil(total_lectures / 5)) if total_lectures > 0 else 0
    per_day_lab_target = max(1, math.ceil(total_labs / 5)) if total_labs > 0 else 0
    placed_lectures_per_day = {d: 0 for d in range(5)}
    placed_labs_per_day = {d: 0 for d in range(5)}
    day_order = [0, 1, 2, 3, 4]
    
    # Helper functions
    def is_slot_free_for_batch(batch_idx, day, slot, duration, room_idx, class_type):
        for i in range(duration):
            if slot + i >= 12:
                return False
            actual_slot = slot + i
            
            if batch_idx in batch_occupancy and day in batch_occupancy[batch_idx]:
                if actual_slot in batch_occupancy[batch_idx][day]:
                    occupied_room = batch_occupancy[batch_idx][day][actual_slot]
                    if occupied_room != room_idx:
                        return False
            
            if class_type == 'L' and room_idx in lab_specific_occupancy:
                if day in lab_specific_occupancy[room_idx]:
                    if actual_slot in lab_specific_occupancy[room_idx][day]:
                        return False
        return True
    
    def reserve_slot_for_batch(batch_idx, day, slot, duration, room_idx, class_type):
        for i in range(duration):
            actual_slot = slot + i
            batch_occupancy[batch_idx][day][actual_slot] = room_idx
            if class_type == 'L':
                lab_specific_occupancy[room_idx][day][actual_slot] = True

    # Main scheduling loop
    for index in priority_order:
        classs = classes[index]
        ind = 0
        placed = False
        
        while ind < len(free) and not placed:
            start_field = free[ind]

            start_time = start_field[0]
            end_time = start_time + int(classs.duration) - 1
            if start_time % 12 > end_time % 12:
                ind += 1
                continue

            found = True
            for i in range(1, int(classs.duration)):
                field = (i + start_time, start_field[1])
                if field not in free:
                    found = False
                    ind += 1
                    break

            room_idx = start_field[1]
            if classs.type == 'P':
                if room_idx not in classroom_pool:
                    ind += 1
                    continue
            elif classs.type == 'L':
                if room_idx not in lab_pool:
                    ind += 1
                    continue
            
            batch_idx = classs.groups[0] if classs.groups else 0
            day = start_time // 12
            slot = start_time % 12
            if not is_slot_free_for_batch(batch_idx, day, slot, int(classs.duration), room_idx, classs.type):
                ind += 1
                continue
            
            if room_idx not in classs.classrooms:
                ind += 1
                continue

            # Try preferred placements
            placed_preferred = False
            preferred_starts = []
            
            if classs.type == 'P':
                for day_pref in day_order:
                    if placed_lectures_per_day[day_pref] >= per_day_lecture_target:
                        continue
                    g0 = classs.groups[0] if len(classs.groups) > 0 else 0
                    cnt = lecture_day_counts.get((classs.subject, g0, day_pref), 0)
                    if cnt >= 1:
                        continue
                    for slot in [0, 1, 5]:
                        preferred_starts.append(day_pref * 12 + slot)
            
            for start_time_pref in preferred_starts:
                end_time_pref = start_time_pref + int(classs.duration) - 1
                if start_time_pref % 12 > end_time_pref % 12:
                    continue
                
                if classs.type == 'P':
                    dayp = start_time_pref // 12
                    over_cap = False
                    for group_index in classs.groups:
                        if lecture_day_counts.get((classs.subject, group_index, dayp), 0) >= 1:
                            over_cap = True
                            break
                    if over_cap:
                        continue
                    if placed_lectures_per_day[dayp] >= per_day_lecture_target:
                        continue
                
                available_rooms = []
                if classs.type == 'P':
                    available_rooms = [c for c in classs.classrooms if c in classroom_pool]
                elif classs.type == 'L':
                    available_rooms = [c for c in classs.classrooms if c in lab_pool]
                else:
                    available_rooms = classs.classrooms
                
                for col in available_rooms:
                    ok = True
                    for i2 in range(int(classs.duration)):
                        field = (i2 + start_time_pref, col)
                        if field not in free or not valid_teacher_group_row(matrix, data, index, field[0]):
                            ok = False
                            break
                    
                    dayp = start_time_pref // 12
                    slotp = start_time_pref % 12
                    batch_idx = classs.groups[0] if classs.groups else 0
                    if not is_slot_free_for_batch(batch_idx, dayp, slotp, int(classs.duration), col, classs.type):
                        ok = False
                    
                    if ok:
                        reserve_slot_for_batch(batch_idx, dayp, slotp, int(classs.duration), col, classs.type)
                        
                        classroom_name = data.classrooms[col].name
                        batch_name = f"{year_key}-B{batch_idx+1}" if year_key and batch_idx < len(data.groups) else f"Batch-{batch_idx}"
                        
                        for i in range(int(classs.duration)):
                            actual_slot = slotp + i
                            classroom_occupancy[classroom_name][dayp][actual_slot] = batch_name
                            batch_schedule[batch_idx][dayp][actual_slot] = (classroom_name, classs.subject, 'P')
                        
                        for group_index in classs.groups:
                            insert_order(subjects_order, classs.subject, group_index, classs.type, start_time_pref)
                        
                        for i2 in range(int(classs.duration)):
                            for group_index in classs.groups:
                                groups_empty_space[group_index].append(i2 + start_time_pref)
                            filled.setdefault(index, []).append((i2 + start_time_pref, col))
                            free.remove((i2 + start_time_pref, col))
                            teachers_empty_space[classs.teacher].append(i2 + start_time_pref)
                        
                        if classs.type == 'P':
                            for group_index in classs.groups:
                                key = (classs.subject, group_index, start_time_pref // 12)
                                lecture_day_counts[key] = lecture_day_counts.get(key, 0) + 1
                                subject_quota[(classs.subject, group_index)]['lectures_scheduled'] += 1
                            placed_lectures_per_day[start_time_pref // 12] += 1
                        
                        placed_preferred = True
                        placed = True
                        break
                if placed_preferred:
                    break

            if placed_preferred:
                placed = True
                break

            if found:
                day = start_time // 12
                slot = start_time % 12
                batch_idx = classs.groups[0] if classs.groups else 0
                
                if not is_slot_free_for_batch(batch_idx, day, slot, int(classs.duration), start_field[1], classs.type):
                    ind += 1
                    continue
                
                if classs.type == 'P':
                    over_cap = False
                    for group_index in classs.groups:
                        key = (classs.subject, group_index, day)
                        cnt = lecture_day_counts.get(key, 0)
                        if cnt >= 1:
                            over_cap = True
                            break
                    if over_cap:
                        ind += 1
                        continue
                
                reserve_slot_for_batch(batch_idx, day, slot, int(classs.duration), start_field[1], classs.type)
                
                classroom_name = data.classrooms[start_field[1]].name
                
                # Get batch subgroup name
                batch_num = None
                for gname, gidx in data.groups.items():
                    if gidx == batch_idx:
                        if '-B' in gname:
                            try:
                                batch_num = int(gname.split('-B')[1])
                            except:
                                batch_num = batch_idx + 1
                        break
                if batch_num is None:
                    batch_num = batch_idx + 1
                batch_subgroup = get_batch_subgroup_name(year_key, batch_num)
                
                for i in range(int(classs.duration)):
                    actual_slot = slot + i
                    classroom_occupancy[classroom_name][day][actual_slot] = batch_subgroup
                    batch_schedule[batch_idx][day][actual_slot] = (classroom_name, classs.subject, 'P')

                for group_index in classs.groups:
                    insert_order(subjects_order, classs.subject, group_index, classs.type, start_time)
                    for i in range(int(classs.duration)):
                        groups_empty_space[group_index].append(i + start_time)

                for i in range(int(classs.duration)):
                    filled.setdefault(index, []).append((i + start_time, start_field[1]))
                    free.remove((i + start_time, start_field[1]))
                    teachers_empty_space[classs.teacher].append(i + start_time)
                
                if classs.type == 'P':
                    for group_index in classs.groups:
                        key = (classs.subject, group_index, day)
                        lecture_day_counts[key] = lecture_day_counts.get(key, 0) + 1
                        subject_quota[(classs.subject, group_index)]['lectures_scheduled'] += 1
                
                placed = True
                break

            ind += 1
        
        if not placed:
            conflict_log.append(
                f"Could not place {classs.subject} ({classs.type}) for groups {classs.groups}"
            )

    # Fill the matrix
    for index, fields_list in filled.items():
        for field in fields_list:
            matrix[field[0]][field[1]] = index
    
    # Print conflict log
    if conflict_log:
        print("\nDetailed Conflict Log:")
        for conflict in conflict_log:
            print(f"  - {conflict}")
    else:
        print("\nNo conflicts detected during initial scheduling.")
    
    # Weekly quota verification for lectures
    print(f"\n=== LECTURE QUOTA VERIFICATION ===")
    quota_warnings = []
    missing_lecture_sessions = []
    for (subject, batch_idx), quota_info in subject_quota.items():
        required = quota_info['lectures_required']
        scheduled = quota_info['lectures_scheduled']
        
        # Get batch subgroup name
        batch_num = None
        for gname, gidx in data.groups.items():
            if gidx == batch_idx:
                if '-B' in gname:
                    try:
                        batch_num = int(gname.split('-B')[1])
                    except:
                        batch_num = batch_idx + 1
                break
        if batch_num is None:
            batch_num = batch_idx + 1
        batch_subgroup = get_batch_subgroup_name(year_key, batch_num)
        
        if scheduled < required:
            missing = required - scheduled
            missing_lecture_sessions.append(f"Missing {missing} lecture session(s) for {subject} (Batch {batch_subgroup}) - required {required}, scheduled {scheduled}")
            quota_warnings.append(
                f"⚠️ Missing {missing} lecture session(s) for {subject} (Batch {batch_subgroup}) - "
                f"required {required}, scheduled {scheduled}"
            )
        else:
            print(f"  ✅ {subject} (Batch {batch_subgroup}): {scheduled}/{required} sessions completed")
    
    if quota_warnings:
        print("\n⚠️  LECTURE QUOTA WARNINGS:")
        for warning in quota_warnings:
            print(f"  {warning}")
    
    if missing_lecture_sessions:
        print("\n⚠️  MISSING LECTURE SESSIONS DETECTED:")
        for missing in missing_lecture_sessions:
            print(f"  {missing}")


def insert_order(subjects_order, subject, group, type, start_time):
    """Inserts start time of the class for given subject, group and type of class."""
    times = subjects_order[(subject, group)]
    if type == 'P':
        times[0] = start_time
    elif type == 'V':
        times[1] = start_time
    else:
        times[2] = start_time
    subjects_order[(subject, group)] = times


def exchange_two(matrix, filled, ind1, ind2):
    """Changes places of two classes with the same duration in timetable matrix."""
    fields1 = filled[ind1]
    filled.pop(ind1, None)
    fields2 = filled[ind2]
    filled.pop(ind2, None)

    for i in range(len(fields1)):
        t = matrix[fields1[i][0]][fields1[i][1]]
        matrix[fields1[i][0]][fields1[i][1]] = matrix[fields2[i][0]][fields2[i][1]]
        matrix[fields2[i][0]][fields2[i][1]] = t

    filled[ind1] = fields2
    filled[ind2] = fields1

    return matrix


def valid_teacher_group_row(matrix, data, index_class, row):
    """Returns if the class can be in that row because of possible teacher or groups overlaps."""
    c1 = data.classes[index_class]
    for j in range(len(matrix[row])):
        if matrix[row][j] is not None:
            c2 = data.classes[matrix[row][j]]
            if c1.teacher == c2.teacher:
                return False
            for g in c2.groups:
                if g in c1.groups:
                    return False
    return True


def mutate_ideal_spot(matrix, data, ind_class, free, filled, groups_empty_space, teachers_empty_space, subjects_order):
    """Function that tries to find new fields in matrix for class index where the cost of the class is 0."""
    if ind_class not in filled:
        return

    rows = []
    fields = filled[ind_class]
    for f in fields:
        rows.append(f[0])

    classs = data.classes[ind_class]
    ind = 0
    while True:
        if ind >= len(free):
            return
        start_field = free[ind]

        start_time = start_field[0]
        end_time = start_time + int(classs.duration) - 1
        if start_time % 12 > end_time % 12:
            ind += 1
            continue

        if start_field[1] not in classs.classrooms:
            ind += 1
            continue

        found = True
        for i in range(int(classs.duration)):
            field = (i + start_time, start_field[1])
            if field not in free or not valid_teacher_group_row(matrix, data, ind_class, field[0]):
                found = False
                ind += 1
                break

        if found:
            filled.pop(ind_class, None)
            for f in fields:
                free.append((f[0], f[1]))
                matrix[f[0]][f[1]] = None
                for group_index in classs.groups:
                    groups_empty_space[group_index].remove(f[0])
                teachers_empty_space[classs.teacher].remove(f[0])

            for group_index in classs.groups:
                insert_order(subjects_order, classs.subject, group_index, classs.type, start_time)
                for i in range(int(classs.duration)):
                    groups_empty_space[group_index].append(i + start_time)

            for i in range(int(classs.duration)):
                filled.setdefault(ind_class, []).append((i + start_time, start_field[1]))
                free.remove((i + start_time, start_field[1]))
                matrix[i + start_time][start_field[1]] = ind_class
                teachers_empty_space[classs.teacher].append(i+start_time)
            break


def evolutionary_algorithm(matrix, data, free, filled, groups_empty_space, teachers_empty_space, subjects_order):
    """Evolutionary algorithm that tries to find schedule such that hard constraints are satisfied."""
    n = 3
    sigma = 2
    run_times = 5
    max_stagnation = 200

    for run in range(run_times):
        print('Run {} | sigma = {}'.format(run + 1, sigma))

        t = 0
        stagnation = 0
        cost_stats = 0
        loss_after, _, cost_teachers, cost_classrooms, cost_groups = hard_constraints_cost(matrix, data)
        
        while stagnation < max_stagnation:
            loss_before, cost_classes, cost_teachers, cost_classrooms, cost_groups = hard_constraints_cost(matrix, data)
            if loss_before == 0 and check_hard_constraints(matrix, data) == 0:
                print('Found optimal solution: \n')
                show_timetable(matrix)
                break

            costs_list = sorted(cost_classes.items(), key=itemgetter(1), reverse=True)

            for i in range(len(costs_list) // 4):
                if random.uniform(0, 1) < sigma and costs_list[i][1] != 0:
                    mutate_ideal_spot(matrix, data, costs_list[i][0], free, filled, groups_empty_space,
                                      teachers_empty_space, subjects_order)

            loss_after, _, _, _, _ = hard_constraints_cost(matrix, data)
            if loss_after < loss_before:
                stagnation = 0
                cost_stats += 1
            else:
                stagnation += 1

            t += 1
            if t >= 10*n and t % n == 0:
                s = cost_stats
                if s < 2*n:
                    sigma *= 0.85
                else:
                    sigma /= 0.85
                cost_stats = 0

        loss_after, _, cost_teachers, cost_classrooms, cost_groups = hard_constraints_cost(matrix, data)
        print('Number of iterations: {} \nCost: {} \nTeachers cost: {} | Groups cost: {} | Classrooms cost:'
              ' {}'.format(t, loss_after, cost_teachers, cost_groups, cost_classrooms))


def simulated_hardening(matrix, data, free, filled, groups_empty_space, teachers_empty_space, subjects_order, file):
    """Algorithm that uses simulated hardening with geometric decrease of temperature."""
    iter_count = 2500
    t = 0.5
    _, _, curr_cost_group = empty_space_groups_cost(groups_empty_space)
    _, _, curr_cost_teachers = empty_space_teachers_cost(teachers_empty_space)
    curr_cost = curr_cost_group
    if free_hour(matrix) == -1:
        curr_cost += 1

    for i in range(iter_count):
        rt = random.uniform(0, 1)
        t *= 0.99

        old_matrix = copy.deepcopy(matrix)
        old_free = copy.deepcopy(free)
        old_filled = copy.deepcopy(filled)
        old_groups_empty_space = copy.deepcopy(groups_empty_space)
        old_teachers_empty_space = copy.deepcopy(teachers_empty_space)
        old_subjects_order = copy.deepcopy(subjects_order)

        placed_class_indices = list(filled.keys())
        if placed_class_indices:
            num_to_mutate = min(len(data.classes) // 4, len(placed_class_indices))
            for j in range(num_to_mutate):
                index_class = random.choice(placed_class_indices)
            mutate_ideal_spot(matrix, data, index_class, free, filled, groups_empty_space, teachers_empty_space,
                              subjects_order)
        _, _, new_cost_groups = empty_space_groups_cost(groups_empty_space)
        _, _, new_cost_teachers = empty_space_teachers_cost(teachers_empty_space)
        new_cost = new_cost_groups
        if free_hour(matrix) == -1:
            new_cost += 1

        if new_cost < curr_cost or rt <= math.exp((curr_cost - new_cost) / t):
            curr_cost = new_cost
        else:
            matrix = copy.deepcopy(old_matrix)
            free = copy.deepcopy(old_free)
            filled = copy.deepcopy(old_filled)
            groups_empty_space = copy.deepcopy(old_groups_empty_space)
            teachers_empty_space = copy.deepcopy(old_teachers_empty_space)
            subjects_order = copy.deepcopy(old_subjects_order)
        if i % 100 == 0:
            print('Iteration: {:4d} | Average cost: {:0.8f}'.format(i, curr_cost))

    print('TIMETABLE AFTER HARDENING')
    show_timetable(matrix)
    print('STATISTICS AFTER HARDENING')
    show_statistics(matrix, data, subjects_order, groups_empty_space, teachers_empty_space)
    write_solution_to_file(matrix, data, filled, file, groups_empty_space, teachers_empty_space, subjects_order)


def main():
    """Main function for running scheduler from command line."""
    filled = {}
    subjects_order = {}
    groups_empty_space = {}
    teachers_empty_space = {}
    file = 'ulaz1.txt'

    use_db = os.environ.get('USE_DB', '0') == '1'
    if use_db:
        create_schema_if_not_exists()
        seed_from_file_if_empty('test_files/' + file)
        data = load_data_from_db(teachers_empty_space, groups_empty_space, subjects_order)
    else:
        data = load_data('test_files/' + file, teachers_empty_space, groups_empty_space, subjects_order)
    matrix, free = set_up(len(data.classrooms))
    initial_population(data, matrix, free, filled, groups_empty_space, teachers_empty_space, subjects_order)
    evolutionary_algorithm(matrix, data, free, filled, groups_empty_space, teachers_empty_space, subjects_order)
    simulated_hardening(matrix, data, free, filled, groups_empty_space, teachers_empty_space, subjects_order, file)

    total, _, _, _, _ = hard_constraints_cost(matrix, data)
    print('Initial cost of hard constraints: {}'.format(total))


if __name__ == '__main__':
    main()
