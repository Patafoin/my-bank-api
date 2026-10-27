import datetime as dt
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints, model_validator

from .i18n import tr
from .models import AccountKind, AttachmentKind, CategoryKind, ScheduleEnd, ScheduleMode, ScheduleUnit, TxState
from .money import Money

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Currency = Annotated[
    str,
    BeforeValidator(lambda v: v.strip().upper() if isinstance(v, str) else v),
    StringConstraints(pattern=r"^[A-Z]{3}$"),
]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- Soldes --------------------------------------------------------------------------------------


class Balances(BaseModel):
    total: Money = Field(description="Toutes les opérations (y compris datées dans le futur, sauf si `date` est donnée)")
    pointed: Money = Field(description="Opérations pointées ou rapprochées")
    reconciled: Money = Field(description="Opérations rapprochées")


# --- Comptes -------------------------------------------------------------------------------------


class AccountBase(Strict):
    kind: AccountKind = AccountKind.CHECKING
    number: str | None = None
    iban: str | None = None
    currency: Currency = "EUR"
    opening_balance: Money = Field(default=Decimal("0.00"))
    opening_date: dt.date | None = None
    closed: bool = False
    position: int = 0
    notes: str | None = None
    is_default: bool = Field(default=False, description="Préselectionné partout où un compte se choisit ; un seul compte à la fois")


class AccountCreate(AccountBase):
    name: Name


class AccountUpdate(Strict):
    name: Name | None = None
    kind: AccountKind | None = None
    number: str | None = None
    iban: str | None = None
    currency: Currency | None = None
    opening_balance: Money | None = None
    opening_date: dt.date | None = None
    closed: bool | None = None
    position: int | None = None
    notes: str | None = None
    is_default: bool | None = None


class AccountOut(Out):
    id: int
    name: str
    kind: AccountKind
    number: str | None
    iban: str | None
    currency: Currency
    opening_balance: Money
    opening_date: dt.date | None
    closed: bool
    position: int
    notes: str | None
    is_default: bool
    balances: Balances
    transaction_count: int = Field(default=0, description="Opérations du compte (chaque jambe d'un virement compte sur son compte)")


class AccountBalancesOut(Balances):
    account_id: int
    date: dt.date | None


# --- Arborescences (rubriques, classes) ----------------------------------------------------------


class CategoryCreate(Strict):
    name: Name
    parent_id: int | None = None
    kind: CategoryKind | None = Field(default=None, description="Obligatoire pour une rubrique racine, hérité sinon")
    active: bool = True
    position: int = 0


class CategoryUpdate(Strict):
    name: Name | None = None
    parent_id: int | None = None
    kind: CategoryKind | None = None
    active: bool | None = None
    position: int | None = None


class CategoryOut(Out):
    id: int
    parent_id: int | None
    name: str
    kind: CategoryKind
    active: bool
    position: int
    path: str
    depth: int
    transaction_count: int = Field(description="Opérations rattachées directement (sous-éléments non cumulés)")


class ClassCreate(Strict):
    name: Name
    parent_id: int | None = None
    active: bool = True
    position: int = 0


class ClassUpdate(Strict):
    name: Name | None = None
    parent_id: int | None = None
    active: bool | None = None
    position: int | None = None


class ClassOut(Out):
    id: int
    parent_id: int | None
    name: str
    active: bool
    position: int
    path: str
    depth: int
    transaction_count: int = Field(description="Opérations rattachées directement (sous-éléments non cumulés)")


# --- Modes de règlement --------------------------------------------------------------------------


class PaymentMethodCreate(Strict):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
    active: bool = True
    position: int = 0


class PaymentMethodUpdate(Strict):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)] | None = None
    active: bool | None = None
    position: int | None = None


class PaymentMethodOut(Out):
    id: int
    name: str
    active: bool
    position: int


# --- Tiers ---------------------------------------------------------------------------------------


class PayeeFields(Strict):
    short_name: str | None = None
    default_category_id: int | None = None
    payee_category: str | None = None
    address1: str | None = None
    address2: str | None = None
    address3: str | None = None
    postal_code: str | None = None
    city: str | None = None
    region: str | None = None
    country: str | None = None
    phone: str | None = None
    phone2: str | None = None
    mobile: str | None = None
    fax: str | None = None
    fax2: str | None = None
    email: str | None = None
    url: str | None = None
    bank_name: str | None = None
    bank_code: str | None = None
    branch_code: str | None = None
    account_number: str | None = None
    rib_key: str | None = None
    iban: str | None = None
    swift: str | None = None
    free1: str | None = None
    free2: str | None = None
    free3: str | None = None
    notes: str | None = None
    external_code: str | None = None


class PayeeCreate(PayeeFields):
    name: Name
    active: bool = True


class PayeeUpdate(PayeeFields):
    name: Name | None = None
    active: bool | None = None


class PayeeOut(PayeeFields, Out):
    model_config = ConfigDict(from_attributes=True, extra="ignore")
    id: int
    name: str
    active: bool
    transaction_count: int = Field(default=0, description="Opérations rattachées à ce tiers")


# --- Justificatifs (décision 023) -----------------------------------------------------------------

# Taille décodée maximale d'un fichier (photo ou scan d'un justificatif, pas un coffre-fort documentaire) ;
# le base64 envoyé par l'intégration pèse environ un tiers de plus.
MAX_ATTACHMENT_SIZE = 10 * 1024 * 1024


def _web_url(value: str) -> str:
    # Lien ouvert tel quel par le navigateur : http(s) seulement (pas de javascript:, data:…).
    scheme, _, rest = value.partition(":")
    if scheme.lower() not in {"http", "https"} or not rest.startswith("//") or len(rest) <= 2:
        raise ValueError(tr("le lien doit commencer par http:// ou https://"))
    return value


class AttachmentLink(Strict):
    kind: Literal["link"] = "link"
    url: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000), AfterValidator(_web_url)]


class AttachmentFile(Strict):
    kind: Literal["file"] = "file"
    filename: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
    content_base64: str = Field(description="Contenu brut du fichier, encodé en base64")
    mime_type: str | None = Field(default=None, max_length=100)


AttachmentIn = Annotated[AttachmentLink | AttachmentFile, Field(discriminator="kind")]


class AttachmentOut(Out):
    kind: AttachmentKind
    url: str | None
    filename: str | None
    mime_type: str | None
    size: int | None = Field(description="Octets ; fichier seulement")


# --- Opérations ----------------------------------------------------------------------------------


class SplitIn(Strict):
    amount: Money
    category_id: int | None = None
    class_id: int | None = None
    label: str | None = None


class SplitOut(Out):
    id: int
    amount: Money
    category_id: int | None
    class_id: int | None
    label: str | None


class TransactionCreate(Strict):
    account_id: int
    date: dt.date
    amount: Money = Field(description="Signé : négatif pour un débit, positif pour un crédit")
    payee_id: int | None = None
    payee_name: Name | None = Field(default=None, description="Tiers par son nom, créé s'il n'existe pas")
    payment_method_id: int | None = None
    number: str | None = Field(default=None, max_length=30)
    label: str | None = Field(default=None, max_length=500)
    notes: str | None = None
    state: TxState = TxState.NONE
    category_id: int | None = Field(default=None, description="Raccourci quand il n'y a pas de ventilation")
    class_id: int | None = Field(default=None, description="Raccourci quand il n'y a pas de ventilation")
    splits: list[SplitIn] | None = Field(default=None, description="Ventilation ; la somme doit égaler `amount`")

    @model_validator(mode="after")
    def _shortcut_or_splits(self):
        if self.splits is not None and (self.category_id is not None or self.class_id is not None):
            raise ValueError(tr("`category_id`/`class_id` et `splits` sont exclusifs"))
        if self.splits is not None and not self.splits:
            raise ValueError(tr("`splits` ne peut pas être vide"))
        if self.payee_id is not None and self.payee_name is not None:
            raise ValueError(tr("`payee_id` et `payee_name` sont exclusifs"))
        return self


class TransactionUpdate(Strict):
    account_id: int | None = None
    date: dt.date | None = None
    amount: Money | None = None
    payee_id: int | None = None
    payee_name: Name | None = Field(default=None, description="Tiers par son nom, créé s'il n'existe pas")
    payment_method_id: int | None = None
    number: str | None = Field(default=None, max_length=30)
    label: str | None = Field(default=None, max_length=500)
    notes: str | None = None
    state: TxState | None = None
    category_id: int | None = None
    class_id: int | None = None
    splits: list[SplitIn] | None = None

    @model_validator(mode="after")
    def _shortcut_or_splits(self):
        if "splits" in self.model_fields_set and (
            "category_id" in self.model_fields_set or "class_id" in self.model_fields_set
        ):
            raise ValueError(tr("`category_id`/`class_id` et `splits` sont exclusifs"))
        if self.splits is not None and not self.splits:
            raise ValueError(tr("`splits` ne peut pas être vide"))
        if "payee_id" in self.model_fields_set and "payee_name" in self.model_fields_set:
            raise ValueError(tr("`payee_id` et `payee_name` sont exclusifs"))
        return self


class TransactionOut(Out):
    id: int
    account_id: int
    date: dt.date
    amount: Money
    payee_id: int | None
    payment_method_id: int | None
    number: str | None
    label: str | None
    notes: str | None
    state: TxState
    transfer_peer_id: int | None
    transfer_account_id: int | None = Field(description="Compte de l'autre opération d'un virement")
    splits: list[SplitOut]
    attachment: AttachmentOut | None = Field(description="Justificatif (lien ou fichier) ; null si aucun")


class TransactionWithBalance(TransactionOut):
    balance_after: Money = Field(description="Solde du compte (toutes opérations) après cette opération")


class TransactionPage(BaseModel):
    total: int
    items: list[TransactionOut]


class AccountTransactions(BaseModel):
    account_id: int
    start: dt.date | None
    end: dt.date | None
    opening_balance: Money = Field(description="Solde du compte (toutes opérations) avant `start`")
    items: list[TransactionWithBalance]


class StateChange(Strict):
    state: TxState


class BulkStateChange(Strict):
    ids: list[int] = Field(min_length=1, max_length=5000)
    state: TxState


class TransferCreate(Strict):
    from_account_id: int
    to_account_id: int
    date: dt.date
    amount: Money = Field(gt=0, description="Montant positif, débité du compte source et crédité au compte cible")
    payment_method_id: int | None = None
    label: str | None = Field(default=None, max_length=500)
    notes: str | None = None
    category_id: int | None = Field(default=None, description="Rubrique des deux jambes (ex. Épargne)")
    class_id: int | None = None
    from_state: TxState = TxState.NONE
    to_state: TxState = TxState.NONE


class TransferOut(BaseModel):
    source: TransactionOut
    destination: TransactionOut


# --- Opérations planifiées -----------------------------------------------------------------------


class ScheduleFields(Strict):
    label: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)] | None = None
    mode: ScheduleMode | None = Field(
        default=None, description="`periodic` : montant fixe ; `installments` : échéancier, un montant par échéance (durée finie)"
    )
    start_date: dt.date | None = Field(default=None, description="Date de la première échéance")
    unit: ScheduleUnit | None = None
    every: int | None = Field(default=None, ge=1, le=366, description="Tous les `every` `unit`")
    end_mode: ScheduleEnd | None = None
    end_date: dt.date | None = None
    count: int | None = Field(default=None, ge=1, le=1200, description="Nombre d'échéances si `end_mode` = count")
    amount: Money | None = Field(default=None, description="Montant de base, signé (négatif = débit du compte)")
    payee_id: int | None = None
    payee_name: Name | None = Field(default=None, description="Tiers par son nom, créé s'il n'existe pas")
    payment_method_id: int | None = None
    category_id: int | None = None
    class_id: int | None = None
    transfer_account_id: int | None = Field(
        default=None, description="Virement : autre compte. Montant négatif = du compte vers l'autre compte"
    )
    notes: str | None = None
    auto: bool | None = Field(default=None, description="Génération automatique des opérations à échéance")
    lead_days: int | None = Field(default=None, ge=0, le=90, description="Jours d'anticipation de la génération")
    generate_from: dt.date | None = Field(
        default=None, description="Les échéances antérieures ne sont jamais générées d'office (défaut : jour de création)"
    )
    active: bool | None = None

    @model_validator(mode="after")
    def _payee(self):
        if "payee_id" in self.model_fields_set and "payee_name" in self.model_fields_set:
            raise ValueError(tr("`payee_id` et `payee_name` sont exclusifs"))
        return self


class ScheduleCreate(ScheduleFields):
    account_id: int
    label: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    start_date: dt.date
    amount: Money
    mode: ScheduleMode = ScheduleMode.PERIODIC
    unit: ScheduleUnit = ScheduleUnit.MONTH
    every: int = Field(default=1, ge=1, le=366)
    end_mode: ScheduleEnd = ScheduleEnd.NEVER
    auto: bool = True
    lead_days: int = Field(default=0, ge=0, le=90)
    active: bool = True


class ScheduleUpdate(ScheduleFields):
    account_id: int | None = None


class ScheduleOut(Out):
    id: int
    account_id: int
    label: str
    mode: ScheduleMode
    start_date: dt.date
    unit: ScheduleUnit
    every: int
    end_mode: ScheduleEnd
    end_date: dt.date | None
    count: int | None
    amount: Money
    payee_id: int | None
    payment_method_id: int | None
    category_id: int | None
    class_id: int | None
    transfer_account_id: int | None
    notes: str | None
    auto: bool
    lead_days: int
    generate_from: dt.date
    active: bool
    total_occurrences: int | None = Field(description="Nombre d'échéances ; null si sans fin")
    generated: int = Field(description="Échéances déjà générées")
    next_rank: int | None = Field(description="Prochaine échéance à venir (ni générée, ni sautée, ni antérieure)")
    next_date: dt.date | None
    next_amount: Money | None
    remaining: int | None = Field(description="Échéances encore à venir ; null si sans fin")


class OccurrenceOut(BaseModel):
    rank: int
    date: dt.date
    amount: Money
    own_amount: bool = Field(description="Montant propre à cette échéance (différent du montant de base)")
    own_date: bool = Field(description="Date propre à cette échéance (différente de celle de la règle)")
    status: Literal["pending", "generated", "skipped", "ignored"] = Field(
        description="ignored = antérieure à `generate_from`, jamais générée d'office"
    )
    transaction_id: int | None


class OccurrenceList(BaseModel):
    schedule_id: int
    total_occurrences: int | None
    items: list[OccurrenceOut]


class OccurrenceAmount(Strict):
    rank: int = Field(ge=1)
    amount: Money
    date: dt.date | None = Field(default=None, description="Date propre ; absente = date de la règle")


class OccurrenceGrid(Strict):
    items: list[OccurrenceAmount] = Field(max_length=1200, description="Grille complète : un montant (et une date) par échéance")


class UpcomingOccurrence(BaseModel):
    schedule_id: int
    account_id: int
    label: str
    transfer_account_id: int | None
    auto: bool
    rank: int
    date: dt.date
    amount: Money
    overdue: bool = Field(description="Date passée et pas encore générée")


class GenerateResult(BaseModel):
    created: list[TransactionOut]


# --- Budgets -------------------------------------------------------------------------------------


def _first_of_month(value):
    if isinstance(value, str) and len(value) == 7:
        value = f"{value}-01"
    return value


Month = Annotated[dt.date, BeforeValidator(_first_of_month), AfterValidator(lambda d: d.replace(day=1))]


class BudgetCreate(Strict):
    account_id: int
    name: Name
    start_month: Month = Field(description="Premier mois (`2026-01` ou une date, ramenée au 1er du mois)")
    months: int = Field(default=12, ge=1, le=36)
    archived: bool = False
    notes: str | None = None


class BudgetUpdate(Strict):
    account_id: int | None = None
    name: Name | None = None
    start_month: Month | None = None
    months: int | None = Field(default=None, ge=1, le=36)
    archived: bool | None = None
    notes: str | None = None


class BudgetCopy(Strict):
    name: Name
    start_month: Month | None = Field(default=None, description="Défaut : le mois qui suit la fin du budget copié")
    months: int | None = Field(default=None, ge=1, le=36)
    account_id: int | None = None


class BudgetOut(Out):
    id: int
    account_id: int
    name: str
    start_month: dt.date
    end_month: dt.date
    months: int
    source_budget_id: int | None
    archived: bool
    notes: str | None


class BudgetLineIn(Strict):
    category_id: int
    month: Month
    amount: Money = Field(description="Montant prévu dans le sens naturel de la rubrique (positif)")


class BudgetLines(Strict):
    lines: list[BudgetLineIn] = Field(max_length=20000)


class BudgetLineOut(BaseModel):
    category_id: int
    month: str = Field(examples=["2026-01"])
    amount: Money


class BudgetLinesOut(BaseModel):
    budget_id: int
    months: list[str]
    lines: list[BudgetLineOut]


class BudgetReportRow(BaseModel):
    category_id: int
    parent_id: int | None
    name: str
    path: str
    depth: int
    kind: CategoryKind
    active: bool
    has_children: bool
    direction: int = Field(description="+1 si la rubrique fait entrer de l'argent sur ce compte, -1 si elle en fait sortir")
    own_planned: list[Money] = Field(description="Montants saisis sur la rubrique elle-même")
    planned: list[Money] = Field(description="Prévu, sous-rubriques comprises")
    actual: list[Money] = Field(description="Réalisé, sous-rubriques comprises, dans le sens naturel")
    variance: list[Money] = Field(description="Écart ; positif = favorable (moins dépensé, plus reçu ou épargné)")
    planned_total: Money
    actual_total: Money
    variance_total: Money


class BudgetTotals(BaseModel):
    planned: list[Money]
    actual: list[Money]
    planned_total: Money
    actual_total: Money


class BudgetReport(BaseModel):
    budget: BudgetOut
    months: list[str]
    rows: list[BudgetReportRow]
    totals: dict[str, BudgetTotals] = Field(
        description="Par type de rubrique (income, expense, savings), dans le sens naturel ; "
        "`net` = effet signé sur le compte, opérations sans rubrique comprises"
    )
    uncategorized: list[Money] = Field(description="Réalisé signé des lignes sans rubrique")


# --- Comptes partagés (décision 019) ---------------------------------------------------------------

Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class SharedAccountCreate(Strict):
    name: Name
    participants: list[Name] = Field(min_length=2, max_length=50, description="Noms des participants, dans l'ordre (au moins deux)")
    notes: str | None = None


class SharedAccountUpdate(Strict):
    name: Name | None = None
    notes: str | None = None
    closed: bool | None = Field(default=None, description="true : clôture (date du jour) ; false : réouverture")


class SharedParticipantIn(Strict):
    name: Name


class SharedExpenseCreate(Strict):
    participant_id: int = Field(description="Qui a payé")
    amount: Money = Field(description="Montant payé, positif")
    date: dt.date | None = Field(default=None, description="Défaut : aujourd'hui")
    label: Label | None = None


class SharedExpenseUpdate(Strict):
    participant_id: int | None = None
    amount: Money | None = None
    date: dt.date | None = None
    label: Label | None = None


class SharedParticipantOut(BaseModel):
    id: int
    name: str
    paid: Money = Field(description="Total payé")
    share: Money = Field(description="Part égale du total")
    balance: Money = Field(description="Payé − part : positif = doit recevoir, négatif = doit verser")
    expense_count: int


class SharedExpenseOut(BaseModel):
    id: int
    participant_id: int
    date: dt.date
    amount: Money
    label: str | None


class SharedTransfer(BaseModel):
    from_participant_id: int
    from_name: str
    to_participant_id: int
    to_name: str
    amount: Money


class SharedAccountOut(BaseModel):
    id: int
    name: str
    notes: str | None
    closed: bool
    closed_on: dt.date | None
    created_on: dt.date
    total: Money
    expense_count: int
    participants: list[SharedParticipantOut]
    next_participant_id: int | None = Field(description="Qui paie la prochaine dépense : celui qui a le moins payé ; null si clôturé")
    settlement: list[SharedTransfer] = Field(
        description="Remboursements pour que chacun ait payé exactement sa part (au plus n − 1 virements) ; "
        "définitifs une fois le compte clôturé, sinon « si l'on clôturait maintenant »"
    )


class SharedAccountDetail(SharedAccountOut):
    expenses: list[SharedExpenseOut] = Field(description="Du plus récent au plus ancien")


# --- Déplacements en masse (décision 020) ----------------------------------------------------------


class MoveIn(Strict):
    target_id: int = Field(description="Élément qui reçoit les opérations (même nature que la source)")
    include_reconciled: bool = Field(default=False, description="Comptes seulement : déplacer aussi les opérations rapprochées")


class MoveOut(BaseModel):
    transactions: int = Field(description="Opérations déplacées")
    schedules: int = Field(description="Planifications modifiées")
