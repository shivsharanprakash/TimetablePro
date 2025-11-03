import io
import csv
import json
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import FinalTimetable
from ..routers.configs import get_current_user_id


router = APIRouter()


@router.get('/csv/{timetable_id}')
def download_csv(timetable_id: int, db: Session = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    row = db.query(FinalTimetable).filter(FinalTimetable.id == timetable_id, FinalTimetable.user_id == user_id).first()
    if not row:
        raise HTTPException(status_code=404, detail='Not found')
    matrix = json.loads(row.matrix_json)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['TimeSlot'] + [f'C{i}' for i in range(len(matrix[0]))])
    for i, r in enumerate(matrix):
        writer.writerow([i] + [('-' if v is None else v) for v in r])
    output.seek(0)
    return StreamingResponse(iter([output.getvalue()]), media_type='text/csv', headers={
        'Content-Disposition': f'attachment; filename="timetable_{row.year_key}.csv"'
    })




