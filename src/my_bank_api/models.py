import datetime as dt
import enum

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


class Timestamps:
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.current_timestamp())
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp(), onupdate=func.current_timestamp()
    )


class AccountKind(enum.StrEnum):
    CHECKING = "checking"
    SAVINGS = "savings"
    CARD = "card"
    CASH = "cash"
    OTHER = "other"


class CategoryKind(enum.StrEnum):
    INCOME = "income"
    EXPENSE = "expense"
    SAVINGS = "savings"


class ScheduleMode(enum.StrEnum):
    PERIODIC = "periodic"
    INSTALLMENTS = "installments"


class ScheduleUnit(enum.StrEnum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"


class ScheduleEnd(enum.StrEnum):
    NEVER = "never"
    DATE = "date"
    COUNT = "count"


class OccurrenceStatus(enum.StrEnum):
    PENDING = "pending"
    GENERATED = "generated"
    SKIPPED = "skipped"


class AttachmentKind(enum.StrEnum):
    LINK = "link"
    FILE = "file"


class TxState(enum.IntEnum):
    NONE = 0
    POINTED = 1
    RECONCILED = 2


class Account(Timestamps, Base):
    __tablename__ = "accounts"
    __table_args__ = (CheckConstraint("kind IN ('checking','savings','card','cash','other')", name="kind"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    kind: Mapped[str] = mapped_column(String(20), default=AccountKind.CHECKING)
    number: Mapped[str | None] = mapped_column(String(50))
    iban: Mapped[str | None] = mapped_column(String(50))
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    opening_balance: Mapped[int] = mapped_column(Integer, default=0)
    opening_date: Mapped[dt.date | None] = mapped_column(Date)
    closed: Mapped[bool] = mapped_column(default=False)
    position: Mapped[int] = mapped_column(default=0)
    notes: Mapped[str | None] = mapped_column(Text)


class Category(Timestamps, Base):
    __tablename__ = "categories"
    __table_args__ = (CheckConstraint("kind IN ('income','expense','savings')", name="kind"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id", ondelete="RESTRICT"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(10))
    active: Mapped[bool] = mapped_column(default=True)
    position: Mapped[int] = mapped_column(default=0)


class AnalysisClass(Timestamps, Base):
    __tablename__ = "classes"

    id: Mapped[int] = mapped_column(primary_key=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("classes.id", ondelete="RESTRICT"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    active: Mapped[bool] = mapped_column(default=True)
    position: Mapped[int] = mapped_column(default=0)


class PaymentMethod(Timestamps, Base):
    __tablename__ = "payment_methods"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)
    active: Mapped[bool] = mapped_column(default=True)
    position: Mapped[int] = mapped_column(default=0)


class Payee(Timestamps, Base):
    __tablename__ = "payees"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    short_name: Mapped[str | None] = mapped_column(String(50))
    default_category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id", ondelete="SET NULL"))
    payee_category: Mapped[str | None] = mapped_column(String(50))
    address1: Mapped[str | None] = mapped_column(String(200))
    address2: Mapped[str | None] = mapped_column(String(200))
    address3: Mapped[str | None] = mapped_column(String(200))
    postal_code: Mapped[str | None] = mapped_column(String(20))
    city: Mapped[str | None] = mapped_column(String(100))
    region: Mapped[str | None] = mapped_column(String(100))
    country: Mapped[str | None] = mapped_column(String(100))
    phone: Mapped[str | None] = mapped_column(String(50))
    phone2: Mapped[str | None] = mapped_column(String(50))
    mobile: Mapped[str | None] = mapped_column(String(50))
    fax: Mapped[str | None] = mapped_column(String(50))
    fax2: Mapped[str | None] = mapped_column(String(50))
    email: Mapped[str | None] = mapped_column(String(200))
    url: Mapped[str | None] = mapped_column(String(300))
    bank_name: Mapped[str | None] = mapped_column(String(100))
    bank_code: Mapped[str | None] = mapped_column(String(20))
    branch_code: Mapped[str | None] = mapped_column(String(20))
    account_number: Mapped[str | None] = mapped_column(String(50))
    rib_key: Mapped[str | None] = mapped_column(String(5))
    iban: Mapped[str | None] = mapped_column(String(50))
    swift: Mapped[str | None] = mapped_column(String(20))
    free1: Mapped[str | None] = mapped_column(String(200))
    free2: Mapped[str | None] = mapped_column(String(200))
    free3: Mapped[str | None] = mapped_column(String(200))
    notes: Mapped[str | None] = mapped_column(Text)
    external_code: Mapped[str | None] = mapped_column(String(50))
    active: Mapped[bool] = mapped_column(default=True)


class Transaction(Timestamps, Base):
    __tablename__ = "transactions"
    __table_args__ = (
        CheckConstraint("state IN (0, 1, 2)", name="state"),
        # Index couvrant : les trois soldes et le solde cumulé se calculent sans lire la table (décision 013).
        Index("ix_transactions_balance", "account_id", "date", "id", "state", "amount"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"))
    date: Mapped[dt.date] = mapped_column(Date)
    amount: Mapped[int] = mapped_column(Integer)
    payee_id: Mapped[int | None] = mapped_column(ForeignKey("payees.id", ondelete="RESTRICT"), index=True)
    payment_method_id: Mapped[int | None] = mapped_column(ForeignKey("payment_methods.id", ondelete="RESTRICT"))
    number: Mapped[str | None] = mapped_column(String(30))
    label: Mapped[str | None] = mapped_column(String(500))
    notes: Mapped[str | None] = mapped_column(Text)
    state: Mapped[int] = mapped_column(Integer, default=TxState.NONE)
    transfer_peer_id: Mapped[int | None] = mapped_column(
        ForeignKey("transactions.id", ondelete="SET NULL"), unique=True
    )

    splits: Mapped[list["Split"]] = relationship(
        back_populates="transaction",
        cascade="all, delete-orphan",
        order_by="Split.position",
        lazy="selectin",
    )
    # Lecture seule : le lien est écrit via transfer_peer_id.
    transfer_peer: Mapped["Transaction | None"] = relationship(
        remote_side=[id], foreign_keys=[transfer_peer_id], viewonly=True, lazy="selectin"
    )
    attachment: Mapped["Attachment | None"] = relationship(
        back_populates="transaction", cascade="all, delete-orphan", uselist=False, lazy="selectin"
    )


class Split(Base):
    __tablename__ = "splits"

    id: Mapped[int] = mapped_column(primary_key=True)
    transaction_id: Mapped[int] = mapped_column(ForeignKey("transactions.id", ondelete="CASCADE"), index=True)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id", ondelete="RESTRICT"), index=True)
    class_id: Mapped[int | None] = mapped_column(ForeignKey("classes.id", ondelete="RESTRICT"), index=True)
    amount: Mapped[int] = mapped_column(Integer)
    label: Mapped[str | None] = mapped_column(String(500))
    position: Mapped[int] = mapped_column(default=0)

    transaction: Mapped[Transaction] = relationship(back_populates="splits")


class Attachment(Timestamps, Base):
    """Justificatif d'une opération (décision 023) : un lien, ou un fichier stocké en base."""

    __tablename__ = "attachments"
    __table_args__ = (
        UniqueConstraint("transaction_id", name="uq_attachments_transaction"),
        CheckConstraint("kind IN ('link','file')", name="kind"),
        CheckConstraint("kind != 'link' OR url IS NOT NULL", name="link_has_url"),
        CheckConstraint("kind != 'file' OR (filename IS NOT NULL AND content IS NOT NULL)", name="file_has_content"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    transaction_id: Mapped[int] = mapped_column(ForeignKey("transactions.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(10))
    url: Mapped[str | None] = mapped_column(String(2000))
    filename: Mapped[str | None] = mapped_column(String(255))
    mime_type: Mapped[str | None] = mapped_column(String(100))
    size: Mapped[int | None] = mapped_column(Integer)
    content: Mapped[bytes | None] = mapped_column(LargeBinary)

    transaction: Mapped[Transaction] = relationship(back_populates="attachment")


class Schedule(Timestamps, Base):
    """Opération planifiée (décision 012) : règle de répétition + gabarit d'opération.

    Les dates des échéances se calculent depuis la règle ; seules les échéances qui ont un état
    (générée, sautée), un montant propre ou une date propre ont une ligne dans `schedule_occurrences`.
    """

    __tablename__ = "schedules"
    __table_args__ = (
        CheckConstraint("mode IN ('periodic','installments')", name="mode"),
        CheckConstraint("unit IN ('day','week','month','quarter','year')", name="unit"),
        CheckConstraint("end_mode IN ('never','date','count')", name="end_mode"),
        CheckConstraint("every >= 1", name="every"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"), index=True)
    label: Mapped[str] = mapped_column(String(500))
    mode: Mapped[str] = mapped_column(String(15), default=ScheduleMode.PERIODIC)
    start_date: Mapped[dt.date] = mapped_column(Date)
    unit: Mapped[str] = mapped_column(String(10), default=ScheduleUnit.MONTH)
    every: Mapped[int] = mapped_column(default=1)
    end_mode: Mapped[str] = mapped_column(String(10), default=ScheduleEnd.NEVER)
    end_date: Mapped[dt.date | None] = mapped_column(Date)
    count: Mapped[int | None] = mapped_column(Integer)
    amount: Mapped[int] = mapped_column(Integer)
    payee_id: Mapped[int | None] = mapped_column(ForeignKey("payees.id", ondelete="RESTRICT"))
    payment_method_id: Mapped[int | None] = mapped_column(ForeignKey("payment_methods.id", ondelete="RESTRICT"))
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id", ondelete="RESTRICT"))
    class_id: Mapped[int | None] = mapped_column(ForeignKey("classes.id", ondelete="RESTRICT"))
    transfer_account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"))
    notes: Mapped[str | None] = mapped_column(Text)
    auto: Mapped[bool] = mapped_column(default=True)
    lead_days: Mapped[int] = mapped_column(default=0)
    generate_from: Mapped[dt.date] = mapped_column(Date)
    active: Mapped[bool] = mapped_column(default=True)

    occurrences: Mapped[list["ScheduleOccurrence"]] = relationship(
        back_populates="schedule",
        cascade="all, delete-orphan",
        order_by="ScheduleOccurrence.rank",
        lazy="selectin",
    )


class ScheduleOccurrence(Base):
    __tablename__ = "schedule_occurrences"
    __table_args__ = (
        UniqueConstraint("schedule_id", "rank", name="uq_schedule_occurrences_rank"),
        CheckConstraint("status IN ('pending','generated','skipped')", name="status"),
        CheckConstraint("rank >= 1", name="rank"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    schedule_id: Mapped[int] = mapped_column(ForeignKey("schedules.id", ondelete="CASCADE"))
    rank: Mapped[int] = mapped_column(Integer)
    # Montant propre (mode échéancier) ; None = montant de base de la planification.
    amount: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(10), default=OccurrenceStatus.PENDING)
    # Date propre (mode échéancier) tant que l'échéance est à venir ; None = date de la règle.
    # Date et montant figés au moment de la génération.
    date: Mapped[dt.date | None] = mapped_column(Date)
    transaction_id: Mapped[int | None] = mapped_column(ForeignKey("transactions.id", ondelete="SET NULL"))

    schedule: Mapped[Schedule] = relationship(back_populates="occurrences")


class Budget(Timestamps, Base):
    """Budget de suivi d'un compte (décision 010) : montants prévus par rubrique et par mois."""

    __tablename__ = "budgets"
    __table_args__ = (CheckConstraint("months BETWEEN 1 AND 36", name="months"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    start_month: Mapped[dt.date] = mapped_column(Date)  # toujours le 1er du mois
    months: Mapped[int] = mapped_column(default=12)
    source_budget_id: Mapped[int | None] = mapped_column(ForeignKey("budgets.id", ondelete="SET NULL"))
    archived: Mapped[bool] = mapped_column(default=False)
    notes: Mapped[str | None] = mapped_column(Text)

    lines: Mapped[list["BudgetLine"]] = relationship(
        back_populates="budget", cascade="all, delete-orphan", lazy="selectin"
    )


class BudgetLine(Base):
    __tablename__ = "budget_lines"
    __table_args__ = (UniqueConstraint("budget_id", "category_id", "month", name="uq_budget_lines_cell"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    budget_id: Mapped[int] = mapped_column(ForeignKey("budgets.id", ondelete="CASCADE"))
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id", ondelete="RESTRICT"), index=True)
    month: Mapped[dt.date] = mapped_column(Date)  # 1er du mois
    # Montant prévu dans le sens naturel de la rubrique (positif = dépense prévue pour une rubrique de dépense).
    amount: Mapped[int] = mapped_column(Integer)

    budget: Mapped[Budget] = relationship(back_populates="lines")


class SharedAccount(Timestamps, Base):
    """Compte partagé (décision 019) : dépenses communes d'un groupe, à équilibrer. Sans lien avec les comptes bancaires."""

    __tablename__ = "shared_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    notes: Mapped[str | None] = mapped_column(Text)
    closed_on: Mapped[dt.date | None] = mapped_column(Date)  # clôturé ce jour-là ; null = en cours

    participants: Mapped[list["SharedParticipant"]] = relationship(
        back_populates="account", cascade="all, delete-orphan", lazy="selectin",
        order_by="(SharedParticipant.position, SharedParticipant.id)",
    )
    expenses: Mapped[list["SharedExpense"]] = relationship(
        back_populates="account", cascade="all, delete-orphan", lazy="selectin",
        order_by="(SharedExpense.date.desc(), SharedExpense.id.desc())",
    )


class SharedParticipant(Base):
    __tablename__ = "shared_participants"
    __table_args__ = (UniqueConstraint("shared_account_id", "name", name="uq_shared_participants_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    shared_account_id: Mapped[int] = mapped_column(ForeignKey("shared_accounts.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    position: Mapped[int] = mapped_column(default=0)

    account: Mapped[SharedAccount] = relationship(back_populates="participants")


class SharedExpense(Timestamps, Base):
    __tablename__ = "shared_expenses"
    __table_args__ = (CheckConstraint("amount > 0", name="amount"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    shared_account_id: Mapped[int] = mapped_column(ForeignKey("shared_accounts.id", ondelete="CASCADE"), index=True)
    participant_id: Mapped[int] = mapped_column(ForeignKey("shared_participants.id", ondelete="RESTRICT"), index=True)
    date: Mapped[dt.date] = mapped_column(Date)
    amount: Mapped[int] = mapped_column(Integer)  # centimes, toujours positif
    label: Mapped[str | None] = mapped_column(String(500))

    account: Mapped[SharedAccount] = relationship(back_populates="expenses")


class ApiToken(Base):
    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.current_timestamp())
    last_used_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
