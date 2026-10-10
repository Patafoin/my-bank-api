"""Comptes partagés : parts égales, prochain payeur et remboursements (décision 019).

Tout est calculé en centimes. La part de chacun est le total divisé par le nombre de participants ;
les centimes qui ne se divisent pas sont ajoutés à la part des plus gros payeurs (ordre des participants
en cas d'égalité), pour que la somme des parts égale exactement le total.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Person:
    id: int
    name: str
    paid: int  # centimes


@dataclass(frozen=True)
class Transfer:
    from_id: int
    to_id: int
    amount: int  # centimes


def shares(people: list[Person]) -> dict[int, int]:
    """Part de chacun (centimes) ; la somme des parts égale le total payé."""
    if not people:
        return {}
    total = sum(p.paid for p in people)
    base, rest = divmod(total, len(people))
    by_paid = sorted(range(len(people)), key=lambda i: (-people[i].paid, i))
    extra = set(by_paid[:rest])
    return {p.id: base + (1 if i in extra else 0) for i, p in enumerate(people)}


def next_payer(people: list[Person]) -> int | None:
    """Celui qui a le moins payé (le premier dans l'ordre des participants en cas d'égalité)."""
    if not people:
        return None
    return people[min(range(len(people)), key=lambda i: (people[i].paid, i))].id


def settlement(people: list[Person]) -> list[Transfer]:
    """Remboursements pour que chacun ait payé exactement sa part : au plus n − 1 virements.

    Le plus gros débiteur rembourse le plus gros créancier, et ainsi de suite.
    """
    share = shares(people)
    order = {p.id: i for i, p in enumerate(people)}
    debtors = [[p.id, share[p.id] - p.paid] for p in people if p.paid < share[p.id]]
    creditors = [[p.id, p.paid - share[p.id]] for p in people if p.paid > share[p.id]]
    debtors.sort(key=lambda d: (-d[1], order[d[0]]))
    creditors.sort(key=lambda c: (-c[1], order[c[0]]))
    result = []
    i = j = 0
    while i < len(debtors) and j < len(creditors):
        amount = min(debtors[i][1], creditors[j][1])
        result.append(Transfer(debtors[i][0], creditors[j][0], amount))
        debtors[i][1] -= amount
        creditors[j][1] -= amount
        if debtors[i][1] == 0:
            i += 1
        if creditors[j][1] == 0:
            j += 1
    return result
