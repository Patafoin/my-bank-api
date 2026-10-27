import base64
import binascii
import datetime as dt
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .. import trees
from ..db import get_session
from ..errors import conflict, invalid, not_found
from ..i18n import tr
from ..models import (
    Account,
    AnalysisClass,
    Attachment,
    AttachmentKind,
    Category,
    Payee,
    PaymentMethod,
    Split,
    Transaction,
    TxState,
)
from ..money import from_cents, to_cents
from ..operations import make_transfer
from ..schemas import (
    MAX_ATTACHMENT_SIZE,
    AttachmentFile,
    AttachmentIn,
    AttachmentOut,
    BulkStateChange,
    SplitIn,
    StateChange,
    TransactionCreate,
    TransactionOut,
    TransactionPage,
    TransactionUpdate,
    TransferCreate,
    TransferOut,
)
from ..serialize import attachment_out, tx_out

router = APIRouter(prefix="/transactions", tags=["opérations"])
transfers = APIRouter(prefix="/transfers", tags=["virements"])

# Textes français ; traduits au moment de lever l'erreur (langue de la requête).
LOCKED = (
    "Opération rapprochée : la dé-rapprocher avant de modifier son montant, sa date, son compte "
    "ou sa ventilation, ou avant de la supprimer"
)
PEER_LOCKED = "L'opération jumelle de ce virement est rapprochée : la dé-rapprocher d'abord"


def _get(session: Session, tx_id: int) -> Transaction:
    tx = session.get(Transaction, tx_id)
    if tx is None:
        raise not_found("Opération", tx_id)
    return tx


def _check_refs(
    session: Session,
    *,
    account_id: int | None = None,
    payee_id: int | None = None,
    payment_method_id: int | None = None,
    splits: list[Split] = (),
) -> None:
    for model, label, ident in (
        (Account, "Compte", account_id),
        (Payee, "Tiers", payee_id),
        (PaymentMethod, "Mode de règlement", payment_method_id),
    ):
        if ident is not None and session.get(model, ident) is None:
            raise not_found(label, ident)
    for split in splits:
        if split.category_id is not None and session.get(Category, split.category_id) is None:
            raise not_found("Rubrique", split.category_id)
        if split.class_id is not None and session.get(AnalysisClass, split.class_id) is None:
            raise not_found("Classe", split.class_id)


def _payee_by_name(session: Session, name: str | None) -> int | None:
    if name is None:
        return None
    wanted = name.strip().casefold()
    for payee in session.scalars(select(Payee)):
        if payee.name.casefold() == wanted:
            return payee.id
    payee = Payee(name=name.strip())
    session.add(payee)
    session.flush()
    return payee.id


def _build_splits(amount: int, lines: list[SplitIn] | None, category_id=None, class_id=None) -> list[Split]:
    if lines is None:
        return [Split(amount=amount, category_id=category_id, class_id=class_id, position=0)]
    splits = [
        Split(amount=to_cents(s.amount), category_id=s.category_id, class_id=s.class_id, label=s.label, position=i)
        for i, s in enumerate(lines)
    ]
    total = sum(s.amount for s in splits)
    if total != amount:
        raise invalid(
            tr("La ventilation totalise {total} au lieu du montant de l'opération ({amount})", total=from_cents(total), amount=from_cents(amount))
        )
    return splits


def _signature(splits: list[Split]) -> list[tuple]:
    return [(s.amount, s.category_id, s.class_id) for s in splits]


# --- Lecture -------------------------------------------------------------------------------------


@router.get("", response_model=TransactionPage, summary="Recherche d'opérations (tous comptes)")
def list_transactions(
    account_id: int | None = None,
    start: dt.date | None = None,
    end: dt.date | None = None,
    state: list[int] | None = Query(None, description="0 non pointée, 1 pointée, 2 rapprochée"),
    payee_id: int | None = None,
    category_id: int | None = None,
    include_subcategories: bool = True,
    class_id: int | None = None,
    q: str | None = Query(None, description="Texte cherché dans le libellé, les notes, le numéro et le tiers"),
    order: Literal["asc", "desc"] = "desc",
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
):
    tx = Transaction
    filters = []
    if account_id is not None:
        filters.append(tx.account_id == account_id)
    if start is not None:
        filters.append(tx.date >= start)
    if end is not None:
        filters.append(tx.date <= end)
    if state:
        filters.append(tx.state.in_(state))
    if payee_id is not None:
        filters.append(tx.payee_id == payee_id)
    if category_id is not None:
        ids = trees.subtree_ids(trees.load(session, Category), category_id) if include_subcategories else {category_id}
        filters.append(tx.id.in_(select(Split.transaction_id).where(Split.category_id.in_(ids))))
    if class_id is not None:
        filters.append(tx.id.in_(select(Split.transaction_id).where(Split.class_id == class_id)))
    if q:
        pattern = f"%{q.strip()}%"
        filters.append(
            or_(
                tx.label.ilike(pattern),
                tx.notes.ilike(pattern),
                tx.number.ilike(pattern),
                tx.payee_id.in_(select(Payee.id).where(Payee.name.ilike(pattern))),
            )
        )
    total = session.scalar(select(func.count()).select_from(tx).where(*filters))
    ordering = (tx.date.asc(), tx.id.asc()) if order == "asc" else (tx.date.desc(), tx.id.desc())
    items = session.scalars(select(tx).where(*filters).order_by(*ordering).limit(limit).offset(offset)).all()
    return {"total": total, "items": [tx_out(t) for t in items]}


@router.get("/{tx_id}", response_model=TransactionOut)
def get_transaction(tx_id: int, session: Session = Depends(get_session)):
    return tx_out(_get(session, tx_id))


# --- Écriture ------------------------------------------------------------------------------------


@router.post("", response_model=TransactionOut, status_code=201)
def create_transaction(data: TransactionCreate, session: Session = Depends(get_session)):
    amount = to_cents(data.amount)
    splits = _build_splits(amount, data.splits, data.category_id, data.class_id)
    _check_refs(
        session,
        account_id=data.account_id,
        payee_id=data.payee_id,
        payment_method_id=data.payment_method_id,
        splits=splits,
    )
    tx = Transaction(
        account_id=data.account_id,
        date=data.date,
        amount=amount,
        payee_id=data.payee_id if data.payee_name is None else _payee_by_name(session, data.payee_name),
        payment_method_id=data.payment_method_id,
        number=data.number,
        label=data.label,
        notes=data.notes,
        state=int(data.state),
        splits=splits,
    )
    session.add(tx)
    session.commit()
    return tx_out(tx)


@router.patch(
    "/{tx_id}",
    response_model=TransactionOut,
    summary="Modification partielle ; `splits` remplace toute la ventilation",
)
def update_transaction(tx_id: int, data: TransactionUpdate, session: Session = Depends(get_session)):
    tx = _get(session, tx_id)
    values = data.model_dump(exclude_unset=True)
    if "payee_name" in values:
        values["payee_id"] = _payee_by_name(session, values.pop("payee_name"))
    for required in ("account_id", "date", "amount", "state"):
        if required in values and values[required] is None:
            raise invalid(tr("`{key}` ne peut pas être vide", key=required))

    new_account = values.get("account_id", tx.account_id)
    new_date = values.get("date", tx.date)
    new_amount = to_cents(data.amount) if "amount" in values else tx.amount

    new_splits = None
    if "splits" in values and data.splits is None:
        raise invalid(tr("`splits` ne peut pas être vide"))
    if "splits" in values:
        new_splits = _build_splits(new_amount, data.splits)
    elif "category_id" in values or "class_id" in values or new_amount != tx.amount:
        if len(tx.splits) != 1:
            raise invalid(tr("Opération ventilée : fournir la ventilation complète (`splits`)"))
        current = tx.splits[0]
        new_splits = [
            Split(
                amount=new_amount,
                category_id=values["category_id"] if "category_id" in values else current.category_id,
                class_id=values["class_id"] if "class_id" in values else current.class_id,
                label=current.label,
                position=0,
            )
        ]

    affects_balances = (
        new_account != tx.account_id
        or new_date != tx.date
        or new_amount != tx.amount
        or (new_splits is not None and _signature(new_splits) != _signature(tx.splits))
    )
    if tx.state == TxState.RECONCILED and affects_balances:
        raise conflict(tr(LOCKED))

    _check_refs(
        session,
        account_id=values.get("account_id"),
        payee_id=values.get("payee_id"),
        payment_method_id=values.get("payment_method_id"),
        splits=new_splits or [],
    )

    if tx.transfer_peer_id is not None:
        peer = _get(session, tx.transfer_peer_id)
        if new_account == peer.account_id:
            raise invalid(tr("Les deux opérations d'un virement ne peuvent pas être sur le même compte"))
        if new_amount != tx.amount or new_date != tx.date:
            if peer.state == TxState.RECONCILED:
                raise conflict(tr(PEER_LOCKED))
            if new_amount != tx.amount:
                if len(peer.splits) != 1:
                    raise conflict(tr("L'opération jumelle de ce virement est ventilée : la modifier d'abord"))
                peer.amount = -new_amount
                peer.splits[0].amount = -new_amount
            peer.date = new_date

    tx.account_id, tx.date, tx.amount = new_account, new_date, new_amount
    for key in ("payee_id", "payment_method_id", "number", "label", "notes"):
        if key in values:
            setattr(tx, key, values[key])
    if "state" in values:
        tx.state = int(values["state"])
    if new_splits is not None:
        tx.splits = new_splits
    session.commit()
    return tx_out(tx)


@router.patch(
    "/{tx_id}/state",
    response_model=TransactionOut,
    summary="Pointer / rapprocher / dé-pointer (rapprocher une opération non pointée la pointe d'office)",
)
def set_state(tx_id: int, data: StateChange, session: Session = Depends(get_session)):
    tx = _get(session, tx_id)
    tx.state = int(data.state)
    session.commit()
    return tx_out(tx)


@router.post("/state", response_model=list[TransactionOut], summary="Changer l'état de plusieurs opérations")
def set_state_bulk(data: BulkStateChange, session: Session = Depends(get_session)):
    ids = set(data.ids)
    found = session.scalars(select(Transaction).where(Transaction.id.in_(ids))).all()
    missing = ids - {t.id for t in found}
    if missing:
        raise not_found("Opération", min(missing))
    for tx in found:
        tx.state = int(data.state)
    session.commit()
    return [tx_out(t) for t in sorted(found, key=lambda t: (t.date, t.id))]


@router.delete("/{tx_id}", status_code=204, summary="Supprime l'opération (et sa jumelle s'il s'agit d'un virement)")
def delete_transaction(tx_id: int, session: Session = Depends(get_session)):
    tx = _get(session, tx_id)
    if tx.state == TxState.RECONCILED:
        raise conflict(tr(LOCKED))
    doomed = [tx]
    if tx.transfer_peer_id is not None:
        peer = _get(session, tx.transfer_peer_id)
        if peer.state == TxState.RECONCILED:
            raise conflict(tr(PEER_LOCKED))
        doomed.append(peer)
    for t in doomed:
        t.transfer_peer_id = None
    session.flush()
    for t in doomed:
        session.delete(t)
    session.commit()
    return Response(status_code=204)


# --- Justificatifs (décision 023) -----------------------------------------------------------------


def _decode_attachment(value: str) -> bytes:
    try:
        content = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as err:
        raise invalid(tr("Justificatif : base64 invalide")) from err
    if len(content) > MAX_ATTACHMENT_SIZE:
        raise invalid(tr("Le justificatif dépasse {size} Mio", size=MAX_ATTACHMENT_SIZE // (1024 * 1024)))
    return content


@router.put("/{tx_id}/attachment", response_model=AttachmentOut, summary="Ajoute ou remplace le justificatif de l'opération")
def set_attachment(tx_id: int, data: AttachmentIn, session: Session = Depends(get_session)):
    tx = _get(session, tx_id)
    if tx.attachment is not None:
        session.delete(tx.attachment)
        session.flush()
    if isinstance(data, AttachmentFile):
        content = _decode_attachment(data.content_base64)
        attachment = Attachment(
            transaction_id=tx.id,
            kind=AttachmentKind.FILE,
            filename=data.filename,
            mime_type=data.mime_type,
            content=content,
            size=len(content),
        )
    else:
        attachment = Attachment(transaction_id=tx.id, kind=AttachmentKind.LINK, url=data.url)
    session.add(attachment)
    session.commit()
    return attachment_out(attachment)


@router.delete("/{tx_id}/attachment", status_code=204, summary="Retire le justificatif de l'opération")
def delete_attachment(tx_id: int, session: Session = Depends(get_session)):
    tx = _get(session, tx_id)
    if tx.attachment is not None:
        session.delete(tx.attachment)
        session.commit()
    return Response(status_code=204)


# Types affichés dans le navigateur ; tout autre type (HTML, SVG…) pourrait exécuter du script sur
# l'origine qui le sert (Home Assistant, via la vue de l'intégration) : il est servi en téléchargement.
INLINE_TYPES = {"application/pdf", "image/png", "image/jpeg", "image/gif", "image/webp", "text/plain"}


def _served_type(mime_type: str | None) -> str:
    base = (mime_type or "").split(";")[0].strip().lower()
    return base if base in INLINE_TYPES else "application/octet-stream"


def _content_headers(att: Attachment) -> dict[str, str]:
    served = _served_type(att.mime_type)
    name = "".join(c for c in att.filename if c.isprintable())
    # filename= : repli ASCII sans guillemet (les en-têtes HTTP sont en latin-1) ; filename* : nom exact.
    fallback = "".join(c if c.isascii() and c not in '"\\' else "_" for c in name)
    headers = {
        "Content-Disposition": f"{'attachment' if served == 'application/octet-stream' else 'inline'}; filename=\"{fallback}\"; "
        f"filename*=UTF-8''{quote(name, safe='')}",
        "X-Content-Type-Options": "nosniff",
    }
    # Défense en plus ; pas pour le PDF, que la visionneuse de Chrome refuse d'afficher sous `sandbox`.
    if served != "application/pdf":
        headers["Content-Security-Policy"] = "sandbox; default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'"
    return headers


@router.get("/{tx_id}/attachment/content", summary="Contenu brut du justificatif (fichier uniquement)")
def get_attachment_content(tx_id: int, session: Session = Depends(get_session)):
    tx = _get(session, tx_id)
    if tx.attachment is None or tx.attachment.kind != AttachmentKind.FILE:
        raise not_found("Justificatif", tx_id)
    att = tx.attachment
    return Response(content=att.content, media_type=_served_type(att.mime_type), headers=_content_headers(att))


# --- Virements -----------------------------------------------------------------------------------


@transfers.post(
    "",
    response_model=TransferOut,
    status_code=201,
    summary="Virement : deux opérations jumelles, chacune avec son propre état de pointage",
)
def create_transfer(data: TransferCreate, session: Session = Depends(get_session)):
    if data.from_account_id == data.to_account_id:
        raise invalid(tr("Les comptes source et cible doivent être différents"))
    _check_refs(session, account_id=data.from_account_id, payment_method_id=data.payment_method_id)
    _check_refs(session, account_id=data.to_account_id, splits=[Split(category_id=data.category_id, class_id=data.class_id)])
    from_account = session.get(Account, data.from_account_id)
    to_account = session.get(Account, data.to_account_id)
    if from_account.currency != to_account.currency:
        # Aucune conversion de change dans l'application : un virement garde le même montant sur les deux jambes.
        raise invalid(
            tr(
                "Le compte source est en {from_currency}, le compte cible en {to_currency} : aucune conversion n'est faite",
                from_currency=from_account.currency,
                to_currency=to_account.currency,
            )
        )
    source, destination = make_transfer(
        session,
        from_account_id=data.from_account_id,
        to_account_id=data.to_account_id,
        date=data.date,
        cents=to_cents(data.amount),
        payment_method_id=data.payment_method_id,
        label=data.label,
        notes=data.notes,
        category_id=data.category_id,
        class_id=data.class_id,
        from_state=data.from_state,
        to_state=data.to_state,
    )
    session.commit()
    return {"source": tx_out(source), "destination": tx_out(destination)}
