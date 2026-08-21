from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import rules as rules_mod
from app.db import get_db
from app.models import Account, Rule
from app.schemas import RuleCreate, RuleOut

router = APIRouter(prefix="/rules", tags=["rules"])


@router.get("", response_model=list[RuleOut])
def list_rules(db: Session = Depends(get_db)):
    return db.scalars(select(Rule).order_by(Rule.priority, Rule.id)).all()


@router.post("", response_model=RuleOut, status_code=201)
def create_rule(payload: RuleCreate, db: Session = Depends(get_db)):
    if db.get(Account, payload.account_id) is None:
        raise HTTPException(422, f"account {payload.account_id} not found")
    try:
        rules_mod.compile_pattern(payload.pattern, payload.match_type)
    except rules_mod.RuleError as exc:
        raise HTTPException(422, str(exc)) from exc

    rule = Rule(**payload.model_dump())
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


@router.delete("/{rule_id}", status_code=204)
def delete_rule(rule_id: int, db: Session = Depends(get_db)):
    rule = db.get(Rule, rule_id)
    if rule is None:
        raise HTTPException(404, f"rule {rule_id} not found")
    db.delete(rule)
    db.commit()
