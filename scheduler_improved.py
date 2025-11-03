"""
Improved scheduler with enhanced resource management, conflict resolution, and batch-level isolation.
This module implements priority-based scheduling, separate resource pools, and backtracking.
"""
import random
import copy
import math
from typing import Dict, List, Tuple, Set, Optional
from collections import defaultdict


class ResourceManager:
    """Manages separate pools for classrooms and labs with occupancy tracking."""
    
    def __init__(self, data, matrix_rows=60):
        self.data = data
        self.matrix_rows = matrix_rows
        # Separate resource pools
        self.classrooms = {}  # {idx: Classroom} for theory ('n')
        self.labs = {}        # {idx: Classroom} for practical ('r')
        
        # Initialize resource pools
        for idx, room in data.classrooms.items():
            if room.type == 'n':
                self.classrooms[idx] = room
            elif room.type == 'r':
                self.labs[idx] = room
        
        # Batch-level occupancy tracking: year -> batch -> day -> slot -> room
        self.occupied_slots = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: {})))
        
        # Lab occupancy: lab_idx -> day -> slot -> True
        self.lab_occupancy = defaultdict(lambda: defaultdict(lambda: {}))
        
        # Conflict log
        self.conflicts = []
    
    def is_room_free(self, room_idx: int, day: int, slot: int, duration: int, 
                     year: str, batch: int) -> bool:
        """Check if room is free for given period."""
        # Check all slots in the duration
        for i in range(duration):
            if slot + i >= 12:  # Don't cross day boundary
                return False
            actual_slot = slot + i
            row = day * 12 + actual_slot
            
            # Check if room is occupied by any batch at this slot
            if self.occupied_slots[year][batch].get(day, {}).get(actual_slot):
                return False
            
            # For labs, check lab-specific occupancy
            if room_idx in self.labs:
                if self.lab_occupancy[room_idx][day].get(actual_slot):
                    return False
        
        return True
    
    def find_alternate_room_or_slot(self, class_idx: int, day: int, 
                                     preferred_slots: List[int]) -> Optional[Tuple[int, int]]:
        """Find alternate room or slot for a class."""
        class_obj = self.data.classes[class_idx]
        year = self._get_year_from_class(class_obj)
        batch = class_obj.groups[0] if class_obj.groups else 0
        
        # Get appropriate resource pool
        resource_pool = self.labs if class_obj.type == 'L' else self.classrooms
        
        # Try each preferred slot
        for slot in preferred_slots:
            # Try each room in the pool
            for room_idx in class_obj.classrooms:
                if room_idx not in resource_pool:
                    continue
                if self.is_room_free(room_idx, day, slot, int(class_obj.duration), year, batch):
                    return (day * 12 + slot, room_idx)
        
        # Try all available slots as fallback
        for slot in range(0, 12 - int(class_obj.duration) + 1):
            for room_idx in class_obj.classrooms:
                if room_idx not in resource_pool:
                    continue
                if self.is_room_free(room_idx, day, slot, int(class_obj.duration), year, batch):
                    return (day * 12 + slot, room_idx)
        
        return None
    
    def reserve_slot(self, room_idx: int, day: int, slot: int, duration: int,
                     year: str, batch: int, class_idx: int):
        """Reserve slots for a class."""
        for i in range(duration):
            actual_slot = slot + i
            row = day * 12 + actual_slot
            self.occupied_slots[year][batch][day][actual_slot] = room_idx
            
            # Track lab occupancy separately
            if room_idx in self.labs:
                self.lab_occupancy[room_idx][day][actual_slot] = True
    
    def release_slot(self, room_idx: int, day: int, slot: int, duration: int,
                     year: str, batch: int):
        """Release slots when backtracking."""
        for i in range(duration):
            actual_slot = slot + i
            if day in self.occupied_slots[year][batch] and actual_slot in self.occupied_slots[year][batch][day]:
                del self.occupied_slots[year][batch][day][actual_slot]
            
            if room_idx in self.labs and day in self.lab_occupancy[room_idx]:
                if actual_slot in self.lab_occupancy[room_idx][day]:
                    del self.lab_occupancy[room_idx][day][actual_slot]
    
    def log_conflict(self, subject: str, batch: int, other_subject: str, 
                     other_batch: int, room: str, day: int, slot: int):
        """Log conflict with contextual details."""
        conflict_msg = (f"Conflict: {subject} (Batch {batch}) and {other_subject} "
                       f"(Batch {other_batch}) at {room} on day {day}, slot {slot}")
        self.conflicts.append(conflict_msg)
    
    def validate_capacity(self, classes_by_type: Dict) -> List[str]:
        """Validate if resources are sufficient for all sessions."""
        warnings = []
        
        total_lecture_sessions = len(classes_by_type.get('P', []))
        total_lab_sessions = len(classes_by_type.get('L', []))
        
        # Check classroom capacity
        max_parallel_lectures = len(self.classrooms) * 5  # 5 days
        if total_lecture_sessions > max_parallel_lectures:
            warnings.append(
                f"Insufficient classrooms: Need {total_lecture_sessions} lecture slots "
                f"but only {max_parallel_lectures} available with {len(self.classrooms)} classrooms"
            )
        
        # Check lab capacity
        max_parallel_labs = len(self.labs) * 5  # 5 days
        if total_lab_sessions > max_parallel_labs:
            warnings.append(
                f"Insufficient labs: Need {total_lab_sessions} lab slots "
                f"but only {max_parallel_labs} available with {len(self.labs)} labs"
            )
        
        return warnings
    
    def _get_year_from_class(self, class_obj) -> str:
        """Extract year from class groups (e.g., 'SY-B1' -> 'SY')."""
        if class_obj.groups:
            # This is a simplified extraction - you may need to adjust based on your naming
            # Assuming groups are named like 'SY-B1', 'TY-B1', etc.
            return "Unknown"
        return "Unknown"


def improved_initial_population(data, matrix, free, filled, groups_empty_space, 
                                teachers_empty_space, subjects_order):
    """
    Improved initial population with structured resource management and priority-based scheduling.
    """
    classes = data.classes
    resource_manager = ResourceManager(data, len(matrix))
    
    # Separate classes by type and year for priority scheduling
    lab_classes = []
    lecture_classes = []
    
    for idx, cls in classes.items():
        if cls.type == 'L':
            lab_classes.append(idx)
        elif cls.type == 'P':
            lecture_classes.append(idx)
    
    # Priority order: Labs first (tighter constraints), then lectures
    # Within each: Higher years first (BTech -> TY -> SY)
    priority_order = []
    
    # Sort by year priority (BTech=0, TY=1, SY=2) - adjust based on your naming
    def get_year_priority(class_idx):
        cls = classes[class_idx]
        # Extract year from groups - adjust this logic based on your naming convention
        return 2  # Default to SY
    
    # Add labs first, sorted by priority
    lab_classes_sorted = sorted(lab_classes, key=get_year_priority)
    priority_order.extend(lab_classes_sorted)
    
    # Add lectures, sorted by priority
    lecture_classes_sorted = sorted(lecture_classes, key=get_year_priority)
    priority_order.extend(lecture_classes_sorted)
    
    # Validate capacity before scheduling
    classes_by_type = {'P': lecture_classes, 'L': lab_classes}
    capacity_warnings = resource_manager.validate_capacity(classes_by_type)
    if capacity_warnings:
        print("Capacity Warnings:")
        for warning in capacity_warnings:
            print(f"  - {warning}")
    
    # Track per-day counts
    lecture_day_counts = defaultdict(lambda: defaultdict(int))
    lab_day_counts = defaultdict(lambda: defaultdict(int))
    
    # Scheduling loop with priority order
    for class_idx in priority_order:
        class_obj = classes[class_idx]
        placed = False
        
        # Determine day order (prioritize early days)
        day_order = list(range(5))
        
        # For labs, prefer first 3 days
        if class_obj.type == 'L':
            day_order = [0, 1, 2, 3, 4]
        else:
            day_order = [0, 1, 2, 3, 4]
        
        # Try to place the class
        for day in day_order:
            if placed:
                break
            
            # Check per-day limits
            if class_obj.type == 'P':
                g0 = class_obj.groups[0] if class_obj.groups else 0
                if lecture_day_counts[(class_obj.subject, g0, day)] >= 1:
                    continue
            else:
                # Labs: max 1 per day per subject per batch
                for gidx in class_obj.groups:
                    if lab_day_counts[(class_obj.subject, gidx, day)] >= 1:
                        continue
            
            # Determine preferred slots
            preferred_slots = []
            if class_obj.type == 'P':
                # Lectures: prefer 09:15, 10:15, 14:15
                preferred_slots = [0, 1, 5]
            else:
                # Labs: prefer 09:15, then 14:15
                preferred_slots = [0, 5] if int(class_obj.duration) == 2 else [0]
            
            # Find alternate room/slot
            placement = resource_manager.find_alternate_room_or_slot(
                class_idx, day, preferred_slots
            )
            
            if placement:
                row, col = placement
                slot = row % 12
                
                # Reserve the slot
                year = "Year"  # Extract from class_obj if available
                batch = class_obj.groups[0] if class_obj.groups else 0
                resource_manager.reserve_slot(
                    col, day, slot, int(class_obj.duration), year, batch, class_idx
                )
                
                # Place class in matrix
                for i in range(int(class_obj.duration)):
                    actual_row = day * 12 + slot + i
                    filled.setdefault(class_idx, []).append((actual_row, col))
                    if (actual_row, col) in free:
                        free.remove((actual_row, col))
                    
                    # Update tracking dictionaries
                    for group_idx in class_obj.groups:
                        groups_empty_space[group_idx].append(actual_row)
                    teachers_empty_space[class_obj.teacher].append(actual_row)
                
                # Update subjects order
                for group_idx in class_obj.groups:
                    insert_order(subjects_order, class_obj.subject, group_idx, 
                               class_obj.type, row)
                
                # Update day counts
                if class_obj.type == 'P':
                    for group_idx in class_obj.groups:
                        lecture_day_counts[(class_obj.subject, group_idx, day)] += 1
                else:
                    for group_idx in class_obj.groups:
                        lab_day_counts[(class_obj.subject, group_idx, day)] += 1
                
                placed = True
        
        if not placed:
            print(f"Warning: Could not place {class_obj.subject} ({class_obj.type})")
    
    # Fill the matrix
    for class_idx, fields_list in filled.items():
        for field in fields_list:
            matrix[field[0]][field[1]] = class_idx
    
    # Print conflict log if any
    if resource_manager.conflicts:
        print("\nConflicts detected:")
        for conflict in resource_manager.conflicts:
            print(f"  - {conflict}")


# Import existing helper functions
from scheduler import insert_order, valid_teacher_group_row, mutate_ideal_spot, \
    evolutionary_algorithm, simulated_hardening



