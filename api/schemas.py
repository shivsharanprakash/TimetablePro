from pydantic import BaseModel
from typing import Optional, Dict, Any, List


class SignupRequest(BaseModel):
    username: str
    password: str
    department: Optional[str] = None


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = 'bearer'


class ConfigCreateRequest(BaseModel):
    config_name: str
    payload: Dict[str, Any]


class ConfigResponse(BaseModel):
    id: int
    config_name: str


class GenerateRequest(BaseModel):
    config_id: int


class TimetableResponse(BaseModel):
    id: int
    year_key: str
    matrix: List[List[Optional[int]]]




