"""Opérations planifiées : dates des échéances et génération des opérations (décisions 012 et 014).

La date de l'échéance de rang N se calcule depuis la première date (jamais en ajoutant les pas
un à un) : une échéance mensuelle du 31 tombe le 28 ou le 29 février, puis revient au 31 mars.
En mode échéancier, une échéance peut avoir sa propre date (trou dans l'échéancier, report).
Une échéance n'est générée qu'une fois : unicité (planification, rang) en base, plus un verrou
dans le processus pour que la tâche quotidienne et un appel d'API ne se croisent pas.
"""

import calendar
import datetime as dt
import logging
import threading
from collections.abc import Iterator

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import OccurrenceStatus, Schedule, ScheduleEnd, ScheduleOccurrence, Split, Transaction
from .operations import make_transfer

log = logging.getLogger(__name__)

MAX_RANKS = 1200  # garde-fou : 100 ans de mensualités, 3 ans d'échéances quotidiennes
MAX_INSTALLMENTS = 400  # échéancier (un montant par échéance) : plus de 30 ans de mensualités
STEP_MONTHS = {"month": 1, "quarter": 3, "year": 12}

_lock = threading.Lock()


def today() -> dt.date:
    return dt.date.today()


def add_months(start: dt.date, months: int) -> dt.date:
    year, month = divmod(start.month - 1 + months, 12)
    year += start.year
    month += 1
    return dt.date(year, month, min(start.day, calendar.monthrange(year, month)[1]))


def occurrence_date(start: dt.date, unit: str, every: int, rank: int) -> dt.date:
    steps = (rank - 1) * every
    if unit == "day":
        return start + dt.timedelta(days=steps)
    if unit == "week":
        return start + dt.timedelta(weeks=steps)
    return add_months(start, steps * STEP_MONTHS[unit])


def last_rank(schedule: Schedule) -> int | None:
    """Nombre d'échéances d'une planification finie ; None si elle n'a pas de fin."""
    if schedule.end_mode == ScheduleEnd.COUNT:
        return schedule.count
    if schedule.end_mode == ScheduleEnd.DATE:
        rank = 0
        while rank < MAX_RANKS and occurrence_date(schedule.start_date, schedule.unit, schedule.every, rank + 1) <= schedule.end_date:
            rank += 1
        return rank
    return None


def effective_date(schedule: Schedule, row: ScheduleOccurrence | None, rank: int) -> dt.date:
    """Date propre de l'échéance (échéancier) ou figée à la génération, sinon celle de la règle."""
    if row is not None and row.date is not None:
        return row.date
    return occurrence_date(schedule.start_date, schedule.unit, schedule.every, rank)


def iter_ranks(schedule: Schedule, until: dt.date | None = None, limit: int = MAX_RANKS) -> Iterator[tuple[int, dt.date]]:
    """(rang, date) par rang, jusqu'à la fin de la planification, `until` ou `limit` rangs.

    Avec des dates propres à venir (échéancier, donc fini), les rangs suivants peuvent tomber
    avant `until` même si celui-ci le dépasse : on filtre au lieu de s'arrêter.
    """
    end = last_rank(schedule)
    stop = min(end, limit) if end is not None else limit
    rows = {o.rank: o for o in schedule.occurrences}
    own_dates = end is not None and any(o.date is not None and o.status != OccurrenceStatus.GENERATED for o in rows.values())
    for rank in range(1, stop + 1):
        date = effective_date(schedule, rows.get(rank), rank)
        if until is not None and date > until:
            if own_dates:
                continue
            return
        yield rank, date


def effective_amount(schedule: Schedule, row: ScheduleOccurrence | None) -> int:
    return row.amount if row is not None and row.amount is not None else schedule.amount


def occurrence_status(schedule: Schedule, row: ScheduleOccurrence | None, date: dt.date) -> str:
    """generated, skipped, pending ; « ignored » = antérieure à `generate_from`, jamais générée d'office."""
    if row is not None and row.status != OccurrenceStatus.PENDING:
        return row.status
    return "ignored" if date < schedule.generate_from else OccurrenceStatus.PENDING


def generate_occurrence(session: Session, schedule: Schedule, rank: int, date: dt.date) -> Transaction | None:
    """Crée l'opération de l'échéance (ou les deux jambes d'un virement) et marque l'échéance générée.

    Renvoie None si l'échéance n'est plus « à venir » (déjà générée ou sautée entre-temps).
    """
    rows = {o.rank: o for o in schedule.occurrences}
    row = rows.get(rank)
    amount = effective_amount(schedule, row)
    if row is None:
        row = ScheduleOccurrence(schedule_id=schedule.id, rank=rank, status=OccurrenceStatus.PENDING)
        session.add(row)
        try:
            session.flush()
        except IntegrityError:
            session.rollback()
            return None
    # Passage pending → generated conditionnel : une seule génération même si deux appels se croisent.
    claimed = session.execute(
        update(ScheduleOccurrence)
        .where(ScheduleOccurrence.id == row.id, ScheduleOccurrence.status == OccurrenceStatus.PENDING)
        .values(status=OccurrenceStatus.GENERATED, date=date, amount=amount)
        .execution_options(synchronize_session=False)
    ).rowcount
    if claimed != 1:
        return None
    if schedule.transfer_account_id is not None:
        outgoing = amount <= 0
        source, destination = make_transfer(
            session,
            from_account_id=schedule.account_id if outgoing else schedule.transfer_account_id,
            to_account_id=schedule.transfer_account_id if outgoing else schedule.account_id,
            date=date,
            cents=abs(amount),
            payment_method_id=schedule.payment_method_id,
            label=schedule.label,
            notes=schedule.notes,
            category_id=schedule.category_id,
            class_id=schedule.class_id,
        )
        tx = source if outgoing else destination
    else:
        tx = Transaction(
            account_id=schedule.account_id,
            date=date,
            amount=amount,
            payee_id=schedule.payee_id,
            payment_method_id=schedule.payment_method_id,
            label=schedule.label,
            notes=schedule.notes,
            splits=[Split(amount=amount, category_id=schedule.category_id, class_id=schedule.class_id, position=0)],
        )
        session.add(tx)
        session.flush()
    session.execute(
        update(ScheduleOccurrence)
        .where(ScheduleOccurrence.id == row.id)
        .values(transaction_id=tx.id)
        .execution_options(synchronize_session=False)
    )
    session.expire(row)
    session.expire(schedule, ["occurrences"])
    return tx


def generate_for(session: Session, schedule: Schedule, on: dt.date | None = None) -> list[Transaction]:
    """Génère les échéances dues d'une planification active et automatique (date ≤ jour + anticipation)."""
    if not (schedule.active and schedule.auto):
        return []
    horizon = (on or today()) + dt.timedelta(days=schedule.lead_days)
    done = {o.rank for o in schedule.occurrences if o.status != OccurrenceStatus.PENDING}
    created = []
    for rank, date in iter_ranks(schedule, until=horizon):
        if rank in done or date < schedule.generate_from:
            continue
        tx = generate_occurrence(session, schedule, rank, date)
        if tx is not None:
            created.append(tx)
    return created


def generate_due(session: Session, on: dt.date | None = None) -> list[Transaction]:
    """Toutes les planifications : appelée au démarrage, chaque nuit et à la demande."""
    created = []
    with _lock:
        ids = session.scalars(select(Schedule.id).where(Schedule.active.is_(True), Schedule.auto.is_(True))).all()
        for schedule_id in ids:
            schedule = session.get(Schedule, schedule_id)
            created.extend(generate_for(session, schedule, on))
            session.commit()
    if created:
        log.info("Échéancier : %d opération(s) générée(s)", len(created))
    return created


def locked():
    """Verrou de génération, pour les routes qui génèrent ou modifient des échéances."""
    return _lock
