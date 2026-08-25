from pydantic import BaseModel, Field


class SessionRequest(BaseModel):
    bootstrap_token: str = Field(min_length=1, max_length=256)


class SessionResponse(BaseModel):
    session_token: str


class CaseResponse(BaseModel):
    case_id: str


class JobResponse(BaseModel):
    job_id: str
