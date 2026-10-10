from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from .errors import conflict, invalid, not_found
from .i18n import tr
from .models import AnalysisClass, Category

TreeModel = type[Category] | type[AnalysisClass]
LABELS = {Category: "Rubrique", AnalysisClass: "Classe"}


def load(session: Session, model: TreeModel) -> dict[int, Category | AnalysisClass]:
    return {node.id: node for node in session.scalars(select(model))}


def ordered(nodes: dict[int, Category | AnalysisClass]) -> list[tuple[Category | AnalysisClass, str, int]]:
    """Parcours en profondeur : (nœud, chemin « Parent > Enfant », profondeur)."""
    children = defaultdict(list)
    for node in nodes.values():
        children[node.parent_id].append(node)
    for siblings in children.values():
        siblings.sort(key=lambda n: (n.position, n.name.casefold(), n.id))
    out = []

    def walk(parent_id, prefix, depth):
        for node in children.get(parent_id, []):
            path = f"{prefix} > {node.name}" if prefix else node.name
            out.append((node, path, depth))
            walk(node.id, path, depth + 1)

    walk(None, "", 0)
    return out


def subtree_ids(nodes: dict[int, Category | AnalysisClass], root_id: int) -> set[int]:
    children = defaultdict(list)
    for node in nodes.values():
        children[node.parent_id].append(node.id)
    result, stack = set(), [root_id]
    while stack:
        current = stack.pop()
        result.add(current)
        stack.extend(children.get(current, []))
    return result


def check_parent(nodes, model: TreeModel, node_id: int | None, parent_id: int | None) -> None:
    if parent_id is None:
        return
    if parent_id not in nodes:
        raise not_found(LABELS[model], parent_id)
    if node_id is not None and parent_id in subtree_ids(nodes, node_id):
        raise invalid(tr("{what} : un élément ne peut pas être rangé sous lui-même ou sous un de ses descendants", what=tr(LABELS[model])))


def check_unique_name(nodes, model: TreeModel, node_id: int | None, parent_id: int | None, name: str) -> None:
    for other in nodes.values():
        if other.id != node_id and other.parent_id == parent_id and other.name.casefold() == name.casefold():
            raise conflict(tr("{what} « {name} » existe déjà à cet endroit de l'arborescence", what=tr(LABELS[model]), name=name))


def propagate_kind(nodes: dict[int, Category], root_id: int, kind: str) -> None:
    for node_id in subtree_ids(nodes, root_id):
        nodes[node_id].kind = kind
