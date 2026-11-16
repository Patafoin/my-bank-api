from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from .. import profiles as prof
from .. import trees
from ..db import get_session
from ..errors import conflict, invalid, not_found
from ..i18n import tr
from ..models import AnalysisClass, BudgetLine, Category, Schedule, Split
from ..schemas import CategoryCreate, CategoryOut, CategoryUpdate, ClassCreate, ClassOut, ClassUpdate

categories = APIRouter(prefix="/categories", tags=["rubriques"])
classes = APIRouter(prefix="/classes", tags=["classes"])

KIND_LABELS = {"income": "recette", "expense": "dépense", "savings": "épargne"}


def _counts(session: Session, model) -> dict[int, int]:
    """Nombre d'opérations rattachées directement à chaque nœud (au moins une ligne de ventilation
    sur ce nœud ; les sous-éléments ne sont pas cumulés)."""
    column = Split.category_id if model is Category else Split.class_id
    rows = session.execute(select(column, func.count(distinct(Split.transaction_id))).where(column.is_not(None)).group_by(column))
    return dict(rows.all())


def _list(session: Session, model, profile_id: int | None = None) -> list[dict]:
    counts = _counts(session, model)
    return [_row(n, p, d, counts) for n, p, d in trees.ordered(trees.load(session, model, profile_id))]


def _new_node_profile(session: Session, model, parent_id: int | None, profile_id: int | None) -> int:
    """Profil d'un nouveau nœud : celui de son parent, sinon celui demandé, sinon le profil par défaut."""
    if parent_id is None:
        return prof.resolve(session, profile_id)
    parent = session.get(model, parent_id)
    if parent is None:
        raise not_found(trees.LABELS[model], parent_id)
    if profile_id is not None:
        prof.check_refs(session, profile_id, [(model, parent_id)])
    return parent.profile_id


def _existing(session: Session, model, node_id: int, parent_id: int | None) -> tuple[dict, object]:
    """Nœuds du profil de `node_id`, et ce nœud ; un nouveau parent doit être du même profil."""
    node = session.get(model, node_id)
    if node is None:
        raise not_found(trees.LABELS[model], node_id)
    prof.check_refs(session, node.profile_id, [(model, parent_id)])
    nodes = trees.load(session, model, node.profile_id)
    return nodes, nodes[node_id]


def _out(session: Session, model, node_id: int) -> dict:
    for row in _list(session, model):
        if row["id"] == node_id:
            return row
    raise not_found(trees.LABELS[model], node_id)


def _row(node, path: str, depth: int, counts: dict[int, int]) -> dict:
    row = {
        "id": node.id,
        "profile_id": node.profile_id,
        "parent_id": node.parent_id,
        "name": node.name,
        "active": node.active,
        "position": node.position,
        "path": path,
        "depth": depth,
        "transaction_count": counts.get(node.id, 0),
    }
    if isinstance(node, Category):
        row["kind"] = node.kind
    return row


def _delete(session: Session, model, node_id: int, split_column) -> Response:
    nodes = trees.load(session, model)
    node = nodes.get(node_id)
    if node is None:
        raise not_found(trees.LABELS[model], node_id)
    if any(other.parent_id == node_id for other in nodes.values()):
        raise conflict(tr("{what} « {name} » a des sous-éléments : les déplacer ou les supprimer d'abord", what=tr(trees.LABELS[model]), name=node.name))
    used = session.scalar(select(func.count()).where(split_column == node_id))
    if used:
        raise conflict(tr("{what} « {name} » est utilisée par {used} ligne(s) d'opération : la désactiver plutôt", what=tr(trees.LABELS[model]), name=node.name, used=used))
    schedule_column = Schedule.category_id if model is Category else Schedule.class_id
    if session.scalar(select(func.count()).where(schedule_column == node_id)):
        raise conflict(tr("{what} « {name} » est utilisée par une opération planifiée : la désactiver plutôt", what=tr(trees.LABELS[model]), name=node.name))
    if model is Category and session.scalar(select(func.count()).where(BudgetLine.category_id == node_id)):
        raise conflict(tr("Rubrique « {name} » a des montants dans un budget : les retirer ou la désactiver", name=node.name))
    session.delete(node)
    session.commit()
    return Response(status_code=204)


# --- Rubriques -----------------------------------------------------------------------------------


@categories.get("", response_model=list[CategoryOut], summary="Rubriques, à plat, dans l'ordre de l'arborescence")
def list_categories(
    profile_id: int | None = Query(None, description="Seulement les rubriques de ce profil"),
    session: Session = Depends(get_session),
):
    return _list(session, Category, profile_id)


@categories.get("/{category_id}", response_model=CategoryOut)
def get_category(category_id: int, session: Session = Depends(get_session)):
    return _out(session, Category, category_id)


@categories.post("", response_model=CategoryOut, status_code=201)
def create_category(data: CategoryCreate, session: Session = Depends(get_session)):
    profile_id = _new_node_profile(session, Category, data.parent_id, data.profile_id)
    nodes = trees.load(session, Category, profile_id)
    trees.check_parent(nodes, Category, None, data.parent_id)
    trees.check_unique_name(nodes, Category, None, data.parent_id, data.name)
    if data.parent_id is None:
        if data.kind is None:
            raise invalid(tr("Une rubrique racine doit avoir un type : income, expense ou savings"))
        kind = data.kind
    else:
        kind = nodes[data.parent_id].kind
        if data.kind is not None and data.kind != kind:
            raise invalid(tr("Une sous-rubrique hérite du type de son parent ({kind})", kind=kind))
    node = Category(
        profile_id=profile_id, name=data.name, parent_id=data.parent_id, kind=kind, active=data.active, position=data.position
    )
    session.add(node)
    session.commit()
    return _out(session, Category, node.id)


@categories.patch("/{category_id}", response_model=CategoryOut)
def update_category(category_id: int, data: CategoryUpdate, session: Session = Depends(get_session)):
    nodes, node = _existing(session, Category, category_id, data.parent_id)
    fields = data.model_fields_set
    parent_id = data.parent_id if "parent_id" in fields else node.parent_id
    name = data.name if data.name is not None else node.name
    trees.check_parent(nodes, Category, node.id, parent_id)
    trees.check_unique_name(nodes, Category, node.id, parent_id, name)
    if parent_id is None:
        kind = data.kind or node.kind
    else:
        parent = nodes[parent_id]
        kind = parent.kind
        if data.kind is not None and data.kind != kind:
            raise invalid(tr("Une sous-rubrique hérite du type de son parent ({kind})", kind=kind))
        # Un déplacement ne change jamais le type d'une rubrique (ni celui de ses opérations passées)
        if kind != node.kind:
            raise invalid(
                tr(
                    "« {name} » est une rubrique de type {kind} : elle ne peut pas aller sous « {parent} » (type {parent_kind})",
                    name=node.name, kind=tr(KIND_LABELS[node.kind]), parent=parent.name, parent_kind=tr(KIND_LABELS[kind]),
                )
            )
    node.parent_id, node.name = parent_id, name
    if data.active is not None:
        node.active = data.active
    if data.position is not None:
        node.position = data.position
    trees.propagate_kind(nodes, node.id, kind)
    session.commit()
    return _out(session, Category, node.id)


@categories.delete("/{category_id}", status_code=204)
def delete_category(category_id: int, session: Session = Depends(get_session)):
    return _delete(session, Category, category_id, Split.category_id)


# --- Classes -------------------------------------------------------------------------------------


@classes.get("", response_model=list[ClassOut], summary="Classes, à plat, dans l'ordre de l'arborescence")
def list_classes(
    profile_id: int | None = Query(None, description="Seulement les classes de ce profil"),
    session: Session = Depends(get_session),
):
    return _list(session, AnalysisClass, profile_id)


@classes.get("/{class_id}", response_model=ClassOut)
def get_class(class_id: int, session: Session = Depends(get_session)):
    return _out(session, AnalysisClass, class_id)


@classes.post("", response_model=ClassOut, status_code=201)
def create_class(data: ClassCreate, session: Session = Depends(get_session)):
    profile_id = _new_node_profile(session, AnalysisClass, data.parent_id, data.profile_id)
    nodes = trees.load(session, AnalysisClass, profile_id)
    trees.check_parent(nodes, AnalysisClass, None, data.parent_id)
    trees.check_unique_name(nodes, AnalysisClass, None, data.parent_id, data.name)
    node = AnalysisClass(**{**data.model_dump(), "profile_id": profile_id})
    session.add(node)
    session.commit()
    return _out(session, AnalysisClass, node.id)


@classes.patch("/{class_id}", response_model=ClassOut)
def update_class(class_id: int, data: ClassUpdate, session: Session = Depends(get_session)):
    nodes, node = _existing(session, AnalysisClass, class_id, data.parent_id)
    fields = data.model_fields_set
    parent_id = data.parent_id if "parent_id" in fields else node.parent_id
    name = data.name if data.name is not None else node.name
    trees.check_parent(nodes, AnalysisClass, node.id, parent_id)
    trees.check_unique_name(nodes, AnalysisClass, node.id, parent_id, name)
    node.parent_id, node.name = parent_id, name
    if data.active is not None:
        node.active = data.active
    if data.position is not None:
        node.position = data.position
    session.commit()
    return _out(session, AnalysisClass, node.id)


@classes.delete("/{class_id}", status_code=204)
def delete_class(class_id: int, session: Session = Depends(get_session)):
    return _delete(session, AnalysisClass, class_id, Split.class_id)
