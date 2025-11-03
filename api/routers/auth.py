from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..db import get_db, Base, engine
from ..models import User
from ..schemas import SignupRequest, LoginRequest, TokenResponse
from ..auth_utils import hash_password, verify_password, create_access_token


# Ensure tables exist
Base.metadata.create_all(bind=engine)

router = APIRouter()


@router.post('/signup', response_model=TokenResponse)
def signup(payload: SignupRequest, db: Session = Depends(get_db)):
    if db.query(User).filter(User.username == payload.username).first():
        raise HTTPException(status_code=400, detail='Username already exists')
    user = User(username=payload.username, password_hash=hash_password(payload.password), department=payload.department)
    db.add(user)
    db.commit()
    db.refresh(user)
    token = create_access_token(sub=str(user.id))
    return TokenResponse(access_token=token)


@router.post('/login', response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == payload.username).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Invalid credentials')
    token = create_access_token(sub=str(user.id))
    return TokenResponse(access_token=token)




