"""Auditoría y alertas (helpers compartidos)."""
import json
from datetime import datetime, timezone


def audit(db, actor, event, entity=None, entity_id=None, detail=None):
    db.exec("INSERT INTO audit(ts,abs_day,period,actor,event,entity,entity_id,detail) VALUES(?,?,?,?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(timespec="milliseconds"), db.abs_day, db.period, actor, event,
             entity, None if entity_id is None else str(entity_id),
             json.dumps(detail, ensure_ascii=False) if detail is not None else None))


def raise_alert(db, type_, severity, subject_type, subject_id, message):
    """Crea la alerta salvo que ya exista una activa del mismo tipo/sujeto en el periodo. Devuelve id o None."""
    dup = db.scalar("SELECT id FROM alerts WHERE type=? AND subject_id=? AND period=? AND state IN ('open','acknowledged')",
                    (type_, str(subject_id), db.period))
    if dup:
        return None
    cur = db.exec("INSERT INTO alerts(type,severity,state,subject_type,subject_id,message,period,abs_day,updated_day) "
                  "VALUES(?,?,?,?,?,?,?,?,?)",
                  (type_, severity, "open", subject_type, str(subject_id), message, db.period, db.abs_day, db.abs_day))
    audit(db, "orchestrator", "alert.raised", "alert", cur.lastrowid,
          {"type": type_, "subject": subject_id, "severity": severity})
    return cur.lastrowid
