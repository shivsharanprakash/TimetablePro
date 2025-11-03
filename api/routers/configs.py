import json
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import TimetableConfig, User
from ..schemas import ConfigCreateRequest, ConfigResponse
from ..auth_utils import decode_token
from fastapi import Header


router = APIRouter()


def get_current_user_id(authorization: str = Header(default="")) -> int:
    if not authorization.startswith('Bearer '):
        raise HTTPException(status_code=401, detail='Missing token')
    token = authorization.split(' ', 1)[1]
    sub = decode_token(token)
    if not sub:
        raise HTTPException(status_code=401, detail='Invalid token')
    return int(sub)


@router.post('', response_model=ConfigResponse)
def create_config(payload: ConfigCreateRequest, db: Session = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    cfg = TimetableConfig(user_id=user_id, config_name=payload.config_name, payload_json=json.dumps(payload.payload))
    db.add(cfg)
    db.commit()
    db.refresh(cfg)
    return ConfigResponse(id=cfg.id, config_name=cfg.config_name)


@router.get('', response_model=list[ConfigResponse])
def list_configs(db: Session = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    rows = db.query(TimetableConfig).filter(TimetableConfig.user_id == user_id).order_by(TimetableConfig.id.desc()).all()
    return [ConfigResponse(id=r.id, config_name=r.config_name) for r in rows]


@router.put('/{config_id}', response_model=ConfigResponse)
def update_config(config_id: int, payload: ConfigCreateRequest, db: Session = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    cfg = db.query(TimetableConfig).filter(TimetableConfig.id == config_id, TimetableConfig.user_id == user_id).first()
    if not cfg:
        raise HTTPException(status_code=404, detail='Configuration not found')
    cfg.config_name = payload.config_name
    cfg.payload_json = json.dumps(payload.payload)
    db.commit()
    return ConfigResponse(id=cfg.id, config_name=cfg.config_name)




