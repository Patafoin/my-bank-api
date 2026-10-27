import datetime as dt

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import schedules as sched
from ..db import get_session
from ..errors import conflict, invalid, not_found
from ..i18n import tr
from ..models import (
    Account,
    AnalysisClass,
    Category,
    OccurrenceStatus,
    Payee,
    PaymentMethod,
    Schedule,
    ScheduleEnd,
    ScheduleMode,
    ScheduleOccurrence,
)
from ..money import from_cents, to_cents
from ..schemas import (
    GenerateResult,
    OccurrenceGrid,
    OccurrenceList,
    ScheduleCreate,
    ScheduleOut,
    ScheduleUpdate,
    UpcomingOccurrence,
)
from ..serialize import tx_out
from .transactions import _payee_by_name

router = APIRouter(prefix="/schedules", tags=["échéancier"])

FIELDS = (
    "account_id label mode start_date unit every end_mode end_date count payee_id payment_method_id "
    "category_id class_id transfer_account_id notes auto lead_days generate_from active"
).split()


def _get(session: Session, schedule_id: int) -> Schedule:
    schedule = session.get(Schedule, schedule_id)
    if schedule is None:
        raise not_found("Planification", schedule_id)
    return schedule


def _rows(schedule: Schedule) -> dict[int, ScheduleOccurrence]:
    return {o.rank: o for o in schedule.occurrences}


def _out(schedule: Schedule) -> dict:
    rows = _rows(schedule)
    total = sched.last_rank(schedule)
    generated = sum(1 for o in rows.values() if o.status == OccurrenceStatus.GENERATED)
    next_rank = next_date = next_amount = None
    remaining = 0 if total is not None else None
    for rank, date in sched.iter_ranks(schedule):
        if sched.occurrence_status(schedule, rows.get(rank), date) != OccurrenceStatus.PENDING:
            continue
        if next_rank is None:
            next_rank, next_date = rank, date
            next_amount = from_cents(sched.effective_amount(schedule, rows.get(rank)))
            if total is None:
                break
        remaining += 1
    out = {key: getattr(schedule, key) for key in FIELDS}
    out.update(
        id=schedule.id,
        amount=from_cents(schedule.amount),
        total_occurrences=total,
        generated=generated,
        next_rank=next_rank,
        next_date=next_date,
        next_amount=next_amount,
        remaining=remaining,
    )
    return out


def _check(session: Session, s: Schedule) -> None:
    """Règles de cohérence, sur l'état final (création comme modification)."""
    for model, label, ident in (
        (Account, "Compte", s.account_id),
        (Account, "Compte", s.transfer_account_id),
        (Payee, "Tiers", s.payee_id),
        (PaymentMethod, "Mode de règlement", s.payment_method_id),
        (Category, "Rubrique", s.category_id),
        (AnalysisClass, "Classe", s.class_id),
    ):
        if ident is not None and session.get(model, ident) is None:
            raise not_found(label, ident)
    if s.transfer_account_id is not None and s.transfer_account_id == s.account_id:
        raise invalid(tr("Le compte d'un virement planifié doit être différent du compte de la planification"))
    if s.transfer_account_id is not None:
        account = session.get(Account, s.account_id)
        transfer_account = session.get(Account, s.transfer_account_id)
        if account.currency != transfer_account.currency:
            raise invalid(
                tr(
                    "Le compte source est en {from_currency}, le compte cible en {to_currency} : aucune conversion n'est faite",
                    from_currency=account.currency,
                    to_currency=transfer_account.currency,
                )
            )
    if s.end_mode == ScheduleEnd.DATE:
        if s.end_date is None:
            raise invalid(tr("`end_date` est obligatoire quand `end_mode` = date"))
        if s.end_date < s.start_date:
            raise invalid(tr("`end_date` doit suivre la première échéance"))
    if s.end_mode == ScheduleEnd.COUNT and s.count is None:
        raise invalid(tr("`count` est obligatoire quand `end_mode` = count"))
    if s.mode == ScheduleMode.INSTALLMENTS and s.end_mode == ScheduleEnd.NEVER:
        raise invalid(tr("Un échéancier a une durée finie : `end_mode` = count ou date"))
    total = sched.last_rank(s)
    if total is not None:
        if total > sched.MAX_RANKS:
            raise invalid(tr("{n} échéances au maximum", n=sched.MAX_RANKS))
        if s.mode == ScheduleMode.INSTALLMENTS and total > sched.MAX_INSTALLMENTS:
            raise invalid(tr("Un échéancier compte au plus {n} échéances ({total} demandées)", n=sched.MAX_INSTALLMENTS, total=total))
        generated = [o.rank for o in s.occurrences if o.status == OccurrenceStatus.GENERATED and o.rank > total]
        if generated:
            raise conflict(tr("L'échéance {rank} a déjà été générée : la durée ne peut pas s'arrêter avant", rank=max(generated)))


def _tidy(session: Session, s: Schedule) -> None:
    """Après un changement de règle : retire ce qui n'a plus de sens (rangs hors durée, montants et dates propres en mode périodique)."""
    total = sched.last_rank(s)
    for o in list(s.occurrences):
        if o.status == OccurrenceStatus.GENERATED:
            continue
        if total is not None and o.rank > total:
            s.occurrences.remove(o)
            continue
        if s.mode == ScheduleMode.PERIODIC:
            o.amount = o.date = None
        elif o.amount == s.amount:
            o.amount = None
        if o.status == OccurrenceStatus.PENDING and o.amount is None and o.date is None:
            s.occurrences.remove(o)


def _apply(session: Session, schedule: Schedule, values: dict, data) -> None:
    if "payee_name" in values:
        values["payee_id"] = _payee_by_name(session, values.pop("payee_name"))
    if "amount" in values:
        values["amount"] = to_cents(data.amount)
    for key, value in values.items():
        setattr(schedule, key, value)
    if schedule.end_mode != ScheduleEnd.DATE:
        schedule.end_date = None
    if schedule.end_mode != ScheduleEnd.COUNT:
        schedule.count = None


# --- Lecture -------------------------------------------------------------------------------------


@router.get("", response_model=list[ScheduleOut], summary="Opérations planifiées, avec leur prochaine échéance")
def list_schedules(
    account_id: int | None = None,
    active: bool | None = None,
    session: Session = Depends(get_session),
):
    stmt = select(Schedule).order_by(Schedule.label, Schedule.id)
    if account_id is not None:
        stmt = stmt.where((Schedule.account_id == account_id) | (Schedule.transfer_account_id == account_id))
    if active is not None:
        stmt = stmt.where(Schedule.active.is_(active))
    items = [_out(s) for s in session.scalars(stmt)]
    items.sort(key=lambda s: (s["next_date"] is None, s["next_date"] or dt.date.max, s["label"].casefold()))
    return items


@router.get(
    "/upcoming",
    response_model=list[UpcomingOccurrence],
    summary="Échéances à venir (et en retard) de toutes les planifications actives",
)
def upcoming(
    days: int = Query(30, ge=0, le=366, description="Horizon, en jours à partir d'aujourd'hui"),
    account_id: int | None = None,
    session: Session = Depends(get_session),
):
    today = sched.today()
    horizon = today + dt.timedelta(days=days)
    stmt = select(Schedule).where(Schedule.active.is_(True))
    if account_id is not None:
        stmt = stmt.where((Schedule.account_id == account_id) | (Schedule.transfer_account_id == account_id))
    items = []
    for s in session.scalars(stmt):
        rows = _rows(s)
        for rank, date in sched.iter_ranks(s, until=horizon):
            row = rows.get(rank)
            if sched.occurrence_status(s, row, date) != OccurrenceStatus.PENDING:
                continue
            items.append(
                {
                    "schedule_id": s.id,
                    "account_id": s.account_id,
                    "label": s.label,
                    "transfer_account_id": s.transfer_account_id,
                    "auto": s.auto,
                    "rank": rank,
                    "date": date,
                    "amount": from_cents(sched.effective_amount(s, row)),
                    "overdue": date < today,
                }
            )
    items.sort(key=lambda o: (o["date"], o["label"].casefold(), o["rank"]))
    return items


@router.get("/{schedule_id}", response_model=ScheduleOut)
def get_schedule(schedule_id: int, session: Session = Depends(get_session)):
    return _out(_get(session, schedule_id))


@router.get(
    "/{schedule_id}/occurrences",
    response_model=OccurrenceList,
    summary="Grille des échéances (toutes si la planification est finie, sinon les générées + `upcoming` à venir)",
)
def list_occurrences(
    schedule_id: int,
    upcoming: int = Query(12, ge=1, le=240, description="Sans fin : nombre d'échéances à venir affichées"),
    session: Session = Depends(get_session),
):
    s = _get(session, schedule_id)
    rows = _rows(s)
    total = sched.last_rank(s)
    items, pending_seen = [], 0
    for rank, date in sched.iter_ranks(s):
        row = rows.get(rank)
        status = sched.occurrence_status(s, row, date)
        if total is None:
            if status == OccurrenceStatus.PENDING:
                pending_seen += 1
                if pending_seen > upcoming:
                    break
            elif status == "ignored":
                continue
        amount = sched.effective_amount(s, row)
        items.append(
            {
                "rank": rank,
                "date": date,
                "amount": from_cents(amount),
                "own_amount": amount != s.amount,
                "own_date": date != sched.occurrence_date(s.start_date, s.unit, s.every, rank),
                "status": status,
                "transaction_id": row.transaction_id if row is not None else None,
            }
        )
    return {"schedule_id": s.id, "total_occurrences": total, "items": items}


# --- Écriture ------------------------------------------------------------------------------------


@router.post("", response_model=ScheduleOut, status_code=201, summary="Crée une planification (et génère ce qui est déjà dû)")
def create_schedule(data: ScheduleCreate, session: Session = Depends(get_session)):
    values = data.model_dump(exclude_unset=True)
    schedule = Schedule(
        mode=data.mode,
        unit=data.unit,
        every=data.every,
        end_mode=data.end_mode,
        auto=data.auto,
        lead_days=data.lead_days,
        active=data.active,
        generate_from=data.generate_from or sched.today(),
        occurrences=[],
    )
    _apply(session, schedule, values, data)
    if schedule.generate_from is None:
        schedule.generate_from = sched.today()
    _check(session, schedule)
    session.add(schedule)
    with sched.locked():
        session.flush()
        sched.generate_for(session, schedule)
        session.commit()
    return _out(schedule)


@router.patch(
    "/{schedule_id}",
    response_model=ScheduleOut,
    summary="Modification partielle ; les échéances non générées suivent la nouvelle règle, montants propres conservés",
)
def update_schedule(schedule_id: int, data: ScheduleUpdate, session: Session = Depends(get_session)):
    values = data.model_dump(exclude_unset=True)
    nullable = {"end_date", "count", "payee_id", "payee_name", "payment_method_id", "category_id", "class_id", "transfer_account_id", "notes"}
    for key, value in values.items():
        if value is None and key not in nullable:
            raise invalid(tr("`{key}` ne peut pas être vide", key=key))
    with sched.locked():
        schedule = _get(session, schedule_id)
        _apply(session, schedule, values, data)
        _check(session, schedule)
        _tidy(session, schedule)
        session.flush()
        sched.generate_for(session, schedule)
        session.commit()
    return _out(schedule)


@router.delete(
    "/{schedule_id}",
    status_code=204,
    summary="Supprime la planification ; les opérations déjà générées restent",
)
def delete_schedule(schedule_id: int, session: Session = Depends(get_session)):
    with sched.locked():
        session.delete(_get(session, schedule_id))
        session.commit()
    return Response(status_code=204)


@router.put(
    "/{schedule_id}/occurrences",
    response_model=OccurrenceList,
    summary="Échéancier : enregistre la grille complète des montants et des dates (le « copier vers le bas » est fait par le client)",
)
def set_occurrences(schedule_id: int, data: OccurrenceGrid, session: Session = Depends(get_session)):
    with sched.locked():
        s = _get(session, schedule_id)
        if s.mode != ScheduleMode.INSTALLMENTS:
            raise conflict(tr("Montants par échéance : passer d'abord la planification en mode échéancier"))
        total = sched.last_rank(s)
        amounts, dates = {}, {}
        for item in data.items:
            if item.rank > total:
                raise invalid(tr("Échéance {rank} : la planification n'en compte que {total}", rank=item.rank, total=total))
            if item.rank in amounts:
                raise invalid(tr("Échéance {rank} en double", rank=item.rank))
            amounts[item.rank] = to_cents(item.amount)
            if item.date is not None:
                dates[item.rank] = item.date
        rows = _rows(s)
        previous = None
        for rank in range(1, total + 1):
            row = rows.get(rank)
            rule_date = sched.occurrence_date(s.start_date, s.unit, s.every, rank)
            wanted = amounts.get(rank, s.amount)
            wanted_date = dates.get(rank, rule_date)
            if row is not None and row.status == OccurrenceStatus.GENERATED:
                if rank in amounts and wanted != row.amount:
                    raise conflict(tr("L'échéance {rank} a déjà été générée : modifier son opération à la place", rank=rank))
                if rank in dates and wanted_date != row.date:
                    raise conflict(tr("L'échéance {rank} a déjà été générée : modifier la date de son opération à la place", rank=rank))
                wanted_date = row.date or rule_date
            if previous is not None and wanted_date < previous:
                raise invalid(tr("Échéance {rank} : sa date ({date}) précède celle de l'échéance {previous}", rank=rank, date=wanted_date.isoformat(), previous=rank - 1))
            previous = wanted_date
            if row is not None and row.status == OccurrenceStatus.GENERATED:
                continue
            own = None if wanted == s.amount else wanted
            own_date = None if wanted_date == rule_date else wanted_date
            if row is None:
                if own is not None or own_date is not None:
                    s.occurrences.append(ScheduleOccurrence(rank=rank, amount=own, date=own_date, status=OccurrenceStatus.PENDING))
            elif own is None and own_date is None and row.status == OccurrenceStatus.PENDING:
                s.occurrences.remove(row)
            else:
                row.amount, row.date = own, own_date
        session.flush()
        sched.generate_for(session, s)
        session.commit()
    return list_occurrences(schedule_id, upcoming=12, session=session)


def _pending_occurrence(s: Schedule, rank: int) -> tuple[ScheduleOccurrence | None, dt.date]:
    total = sched.last_rank(s)
    if rank < 1 or (total is not None and rank > total) or rank > sched.MAX_RANKS:
        raise not_found("Échéance", rank)
    row = _rows(s).get(rank)
    if row is not None and row.status == OccurrenceStatus.GENERATED:
        raise conflict(tr("L'échéance {rank} a déjà été générée", rank=rank))
    return row, sched.effective_date(s, row, rank)


@router.post(
    "/{schedule_id}/occurrences/{rank}/skip",
    response_model=OccurrenceList,
    summary="Saute une échéance : elle ne sera pas générée",
)
def skip_occurrence(schedule_id: int, rank: int, session: Session = Depends(get_session)):
    with sched.locked():
        s = _get(session, schedule_id)
        row, _date = _pending_occurrence(s, rank)
        if row is None:
            s.occurrences.append(ScheduleOccurrence(rank=rank, status=OccurrenceStatus.SKIPPED))
        else:
            row.status = OccurrenceStatus.SKIPPED
        session.commit()
    return list_occurrences(schedule_id, upcoming=12, session=session)


@router.delete(
    "/{schedule_id}/occurrences/{rank}/skip",
    response_model=OccurrenceList,
    summary="Rétablit une échéance sautée",
)
def unskip_occurrence(schedule_id: int, rank: int, session: Session = Depends(get_session)):
    with sched.locked():
        s = _get(session, schedule_id)
        row, _date = _pending_occurrence(s, rank)
        if row is None or row.status != OccurrenceStatus.SKIPPED:
            raise conflict(tr("L'échéance {rank} n'est pas sautée", rank=rank))
        if row.amount is None and row.date is None:
            s.occurrences.remove(row)
        else:
            row.status = OccurrenceStatus.PENDING
        session.flush()
        sched.generate_for(session, s)
        session.commit()
    return list_occurrences(schedule_id, upcoming=12, session=session)


@router.post(
    "/{schedule_id}/occurrences/{rank}/generate",
    response_model=GenerateResult,
    status_code=201,
    summary="Saisit l'opération d'une échéance maintenant (à sa date prévue), même en avance ou en mode manuel",
)
def generate_occurrence(schedule_id: int, rank: int, session: Session = Depends(get_session)):
    with sched.locked():
        s = _get(session, schedule_id)
        row, date = _pending_occurrence(s, rank)
        if row is not None and row.status == OccurrenceStatus.SKIPPED:
            row.status = OccurrenceStatus.PENDING
            session.flush()
        tx = sched.generate_occurrence(session, s, rank, date)
        if tx is None:
            raise conflict(tr("L'échéance {rank} a déjà été générée", rank=rank))
        session.commit()
    return {"created": [tx_out(tx)]}


@router.post(
    "/generate",
    response_model=GenerateResult,
    summary="Génère tout ce qui est dû (fait aussi au démarrage et chaque nuit)",
)
def generate_due(session: Session = Depends(get_session)):
    return {"created": [tx_out(t) for t in sched.generate_due(session)]}
