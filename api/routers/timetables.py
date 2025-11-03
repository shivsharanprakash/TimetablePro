import json
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import TimetableConfig, FinalTimetable
from ..schemas import GenerateRequest, TimetableResponse
from ..routers.configs import get_current_user_id

# Import algorithm adapters from project root
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if ROOT not in sys.path: sys.path.insert(0, ROOT)
from utils import set_up
from scheduler import initial_population, evolutionary_algorithm
from config_adapter import build_data_from_config


router = APIRouter()


@router.post('/generate')
def generate(req: GenerateRequest, db: Session = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    cfg = db.query(TimetableConfig).filter(TimetableConfig.id == req.config_id, TimetableConfig.user_id == user_id).first()
    if not cfg:
        raise HTTPException(status_code=404, detail='Configuration not found')
    cfg_payload = json.loads(cfg.payload_json)

    results = []
    for year in ['SY', 'TY', 'BTech']:
        filled = {}
        subjects_order = {}
        groups_empty_space = {}
        teachers_empty_space = {}

        data = build_data_from_config(cfg_payload, year, teachers_empty_space, groups_empty_space, subjects_order)
        matrix, free = set_up(len(data.classrooms))
        initial_population(data, matrix, free, filled, groups_empty_space, teachers_empty_space, subjects_order)
        evolutionary_algorithm(matrix, data, free, filled, groups_empty_space, teachers_empty_space, subjects_order)

        matrix_json = json.dumps(matrix)
        row = FinalTimetable(user_id=user_id, config_id=cfg.id, year_key=year, matrix_json=matrix_json)
        db.add(row)
        db.commit()
        db.refresh(row)
        results.append({'id': row.id, 'year_key': year})
    return {'status': 'ok', 'generated': results}


@router.get('/my-timetables')
def my_timetables(config_id: int, db: Session = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    rows = db.query(FinalTimetable).filter(FinalTimetable.user_id == user_id, FinalTimetable.config_id == config_id).all()
    return [{'id': r.id, 'year_key': r.year_key, 'matrix': json.loads(r.matrix_json)} for r in rows]


