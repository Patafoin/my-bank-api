# My Bank API

🇬🇧 [Read in English](https://github.com/Patafoin/my-bank-api/blob/main/README.md)

Serveur auto-hébergé, sans interface, de gestion de finances personnelles, sur le modèle des logiciels de comptes sur ordinateur comme MS Comptes Bancaires. Son client principal est l'intégration Home Assistant [My Bank](https://github.com/Patafoin/ha-my-bank/blob/main/README.fr.md).

Python 3.13 · FastAPI · SQLAlchemy 2 + Alembic · SQLite (WAL). Environ 60 à 100 Mo de RAM.

## Installation

### Application Home Assistant (add-on)

Nécessite Home Assistant OS ou une installation Supervised (amd64 ou aarch64) ; testé sur Home Assistant OS 18.3 avec Home Assistant 2026.10.0. Paramètres → **Applications** → **Installer une application** → ⋮ → **Dépôts**, ajoutez `https://github.com/Patafoin/ha-addons`, puis installez **My Bank API** (les versions plus anciennes de Home Assistant appellent les applications *modules complémentaires*). L'application crée un jeton pour Home Assistant et s'annonce d'elle-même : l'intégration My Bank se configure en un clic. Voir la documentation de l'application.

### Docker

```bash
curl -O https://raw.githubusercontent.com/Patafoin/my-bank-api/main/compose.example.yaml
openssl rand -hex 32   # génère un jeton : collez-le dans MYBANK_API_TOKEN du fichier, gardez-le pour Home Assistant
docker compose -f compose.example.yaml up -d
curl http://localhost:9931/api/v1/health
```

Image multi-architecture (amd64, arm64) : `ghcr.io/patafoin/my-bank-api:<version>` ou `:latest`. Les migrations de schéma sont appliquées automatiquement au démarrage.

**Gardez l'API sur votre réseau local.** Ne l'exposez jamais sur Internet.

## Réglages

| Variable | Défaut | Rôle |
|---|---|---|
| `MYBANK_API_TOKEN` | — | Jeton accepté par l'API (créé ou mis à jour au démarrage, stocké haché). Le moyen le plus simple de donner l'accès à Home Assistant. Supprimer la variable le révoque au démarrage suivant |
| `MYBANK_DATA_DIR` | `/data` | Dossier de la base `my_bank.sqlite` |
| `MYBANK_BACKUP_DIR` | `/backups` (`/data/backups` dans l'application Home Assistant) | Dossier des sauvegardes nocturnes |
| `MYBANK_BACKUP_HOUR` | `3` | Heure locale de la sauvegarde nocturne |
| `MYBANK_BACKUP_KEEP_DAILY` / `_MONTHLY` | `14` / `12` | Rotation |
| `MYBANK_JOBS_ENABLED` | `true` | Tâches quotidiennes (génération des opérations planifiées, sauvegarde) |

Le conteneur abandonne lui-même les privilèges root (utilisateur `mybank`, uid 1000) ; `user: "1000:1000"` dans le compose fonctionne aussi.

## Ligne de commande

```bash
docker exec my-bank-api my-bank token create ha     # affiche le jeton une seule fois
docker exec my-bank-api my-bank token list
docker exec my-bank-api my-bank token revoke ha
docker exec my-bank-api my-bank backup              # instantané SQLite cohérent, immédiat
```

Sauvegardes : un instantané cohérent (API de sauvegarde SQLite) au démarrage s'il n'y en a pas encore pour la journée, puis chaque nuit ; 14 quotidiennes + 1 par mois pendant 12 mois. Copiez le dossier `backups`, pas la base en cours d'utilisation.

## Concepts

- **Montants** : chaînes décimales dans l'API (`"-14.80"`, la virgule est acceptée en entrée), centimes entiers dans la base.
- **État d'une opération** : `0` non pointée, `1` pointée, `2` rapprochée.
- **Trois soldes par compte** : `total` (toutes les opérations, y compris à date future, sauf si `date` est fourni), `pointed` (états 1 et 2), `reconciled` (état 2). Le solde initial est inclus dans les trois.
- **Les opérations rapprochées sont verrouillées** : modification du montant, de la date, du compte, de la ventilation et suppression sont refusées (409) tant que l'opération n'est pas dé-rapprochée.
- **Ventilations** : chaque opération a au moins une ligne ; leur somme est égale au montant.
- **Virements** : deux opérations jumelles (`transfer_peer_id`) ; le montant et la date se reportent d'une jambe à l'autre, l'état de pointage reste propre à chaque jambe.
- **Catégories** : arborescence illimitée, typée `income` / `expense` / `savings` (hérité de la racine). **Classes** : un second axe de classement, lui aussi arborescent.
- **Opérations planifiées** (`/schedules`) : une règle de répétition (première date, tous les N jours / semaines / mois / trimestres / ans, sans fin / jusqu'à une date / après N échéances) et un modèle d'opération (tiers, moyen de paiement, catégorie, classe, ou virement vers un autre compte). Mode `periodic` (montant fixe) ou `installments` (échéancier fini, un montant — et éventuellement une date — par échéance, `PUT /schedules/{id}/occurrences`). Les dates sont calculées à partir de la première échéance (31 janvier → 28/29 février → 31 mars). Les échéances sont générées au démarrage, chaque nuit et à la création, quand leur date ≤ aujourd'hui + `lead_days`, jamais avant `generate_from` ; chacune une seule fois. Une échéance peut être sautée, rétablie ou saisie en avance à la main.
- **Budgets** (`/budgets`) : un budget par compte et par plage de mois (sans chevauchement), une grille catégories × mois (`PUT /budgets/{id}/lines`), copie vers une nouvelle période, rapport prévu / réalisé / écart. Les montants prévus sont positifs dans le sens naturel de la catégorie ; les chiffres d'une catégorie incluent ses sous-catégories ; un écart positif est favorable.
- **Déplacement en masse** (`POST /{accounts|categories|classes|payees}/{id}/move`, `{"target_id": …}`) : toutes les opérations et planifications d'un élément passent sur un autre. Les comptes et les tiers exposent un `transaction_count`.
- **Dépenses partagées** (`/shared-accounts`, indépendantes des comptes bancaires) : participants (au moins deux), dépenses (qui a payé, montant positif, date, libellé). Part de chacun = total ÷ participants, les centimes restants allant aux plus gros payeurs ; prochain payeur = celui qui a le moins payé ; équilibrage = au plus n − 1 remboursements. La clôture (`PATCH {"closed": true}`) fige le compte ; il peut être rouvert.

## API

Préfixe `/api/v1`, en-tête `Authorization: Bearer <jeton>` partout sauf sur `/health`. Documentation interactive : `/docs` (descriptions en français).

Les messages d'erreur suivent l'en-tête `Accept-Language` : `en`, `fr` ou `de` (anglais pour toute autre langue, français en l'absence d'en-tête). L'intégration Home Assistant envoie la langue de Home Assistant. Les traductions se trouvent dans `src/my_bank_api/i18n_messages.py`, indexées par le texte français passé à `tr()` ; un test vérifie que chaque message a une version anglaise et une version allemande.

## Import depuis MS Comptes Bancaires

`POST /api/v1/import/ms` avec les exports QIF et le CSV des tiers, encodés en base64 ; `dry_run` vaut `true` par défaut (le rapport est calculé, rien n'est enregistré). Uniquement dans des comptes vides. Équivalent en ligne de commande :

```bash
docker cp tiers.csv my-bank-api:/tmp/ && docker cp courant.qif my-bank-api:/tmp/
docker exec my-bank-api my-bank import-ms --payees /tmp/tiers.csv "Courant=/tmp/courant.qif"            # essai à blanc
docker exec my-bank-api my-bank import-ms --apply --payees /tmp/tiers.csv "Courant=/tmp/courant.qif"    # pour de vrai
```

## Données de démonstration

`scripts/demo_data.py` remplit une instance **vide** avec des données fictives (comptes, quatre mois d'opérations, planifications, un budget, des dépenses partagées), en anglais, français ou allemand. Bibliothèque standard uniquement :

```bash
python3 scripts/demo_data.py --url http://localhost:9931 --token <jeton> --lang fr
```

## Développement

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest -q             # tests rapides
.venv/bin/python -m pytest -q -m slow -s  # test de charge, 100 000 opérations
.venv/bin/ruff check src tests scripts
```

Nouvelle migration de schéma (après modification de `models.py`) :

```bash
.venv/bin/python -c "from alembic import command; from my_bank_api.migrate import alembic_config; \
command.upgrade(c := alembic_config('sqlite:////tmp/gen.sqlite'), 'head'); \
command.revision(c, message='description', autogenerate=True, rev_id='0004')"
```

Publication : augmentez la version dans `pyproject.toml` et `src/my_bank_api/__init__.py`, puis posez le tag `vX.Y.Z` : GitHub Actions publie l'image ; reportez ensuite la même version dans le `config.yaml` de l'application ([ha-addons](https://github.com/Patafoin/ha-addons)).

## Licence

MIT
