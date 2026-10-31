# Eloverblik MCP — consommation électrique locale

Extension du projet [eloverblik-utilities de Morten Helmstedt](https://github.com/helmstedt/eloverblik-utilities), sous licence MIT. Les scripts historiques sont conservés ; les nouvelles commandes ci-dessous n'utilisent pas leur stockage de tokens avec pickle.

## Ce qui fonctionne

- Client Customer API Eloverblik : token temporaire en mémoire, compteurs, consommation et snapshot des charges.
- Synchronisation explicite, par tranches de 30 jours, avec remplacement des relevés corrigés dans SQLite.
- Dates de requête danoises ; intervalles stockés en UTC ; heures locales avec décalage UTC pour les changements d'heure.
- Résolutions de 15/30/60 minutes reconnues ; valeurs manquantes exclues et couverture affichée.
- Quatre outils MCP en lecture seule sur la base : état, consommation, comparaison et profil horaire.
- Importable depuis Streamlit : `from energy_mcp.core import Store`, puis `Store().query(meter, start, end)`.

Cette version ne calcule pas la facture, le CO₂ ou la rentabilité d'une batterie. Les charges récupérées sont un snapshot brut, pas un historique complet des tarifs. La fraîcheur dépend des publications Eloverblik et de votre dernière synchronisation ; ce n'est pas du temps réel.

## Installation sur Mac — Python 3.11 à 3.13

Dans Terminal, dans le dossier où vous souhaitez conserver le projet :

```bash
git clone https://github.com/supersimonr/eloverblik-mcp.git
cd eloverblik-mcp
git checkout feature/local-mcp
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
chmod 600 .env
open -e .env
```

Après fusion de la pull request, utiliser `main` au lieu de `feature/local-mcp`.

Dans .env, renseigner **un nouveau token** généré dans Eloverblik. Révoquer tout token précédemment partagé. Ne le coller ni dans ChatGPT, ni dans GitHub, ni dans une commande shell. Le fichier .env, les tokens, bases et exports sont exclus de Git. Ne pas utiliser les anciens scripts pour configurer la nouvelle connexion.

```dotenv
ELOVERBLIK_REFRESH_TOKEN=COLLER_LE_NOUVEAU_TOKEN_ICI_LOCALEMENT
```

Le client utilise le Customer API à https://api.eloverblik.dk/customerapi/api/. Aucun appel avec un vrai compte n'a été effectué pendant le développement : la validation de votre token et de la disponibilité actuelle de l'API est la première étape locale. Les erreurs d'autorisation sont bornées et ne montrent pas le token.

## Récupérer les données

```bash
.venv/bin/python -m energy_mcp.cli meters
```

Copier le numéro de compteur renvoyé, puis remplacer COMPTEUR dans :

```bash
.venv/bin/python -m energy_mcp.cli sync --meter COMPTEUR --start 2025-01-01 --end 2026-01-01
.venv/bin/python -m energy_mcp.cli status
.venv/bin/python -m energy_mcp.cli charges --meter COMPTEUR
```

**Toutes les bornes de fin sont exclusives** : du 1 janvier au 1 février couvre janvier. Maximum 366 jours par commande. La borne de fin ne peut pas dépasser aujourd'hui pour la synchronisation. Les appels sont espacés et peuvent prendre plusieurs minutes pour un an ; ne pas lancer plusieurs synchronisations en parallèle.

La base se trouve dans `data/energy.sqlite3`. Le chemin peut être changé dans .env avec ELOVERBLIK_DB (chemin absolu recommandé). Elle contient des données personnelles et doit rester locale. Le token temporaire n'est jamais écrit sur disque.

Pour actualiser l'historique, relancer la commande sync sur les 7 à 14 derniers jours terminés. Les corrections remplacent les valeurs précédentes. Aucune automatisation n'est installée par défaut.

## Ajouter le MCP dans un client desktop local compatible

Le serveur fonctionne uniquement par **STDIO**, sans port HTTP public et sans authentification distante à configurer. Le client desktop lance lui-même le programme.

Dans les réglages MCP du client, si disponibles :

- Nom : Eloverblik local
- Transport : STDIO
- Commande : **chemin absolu** vers `.venv/bin/python`
- Argument : **chemin absolu** vers `run_mcp.py`

Exemple de configuration (adapter les deux chemins) :

```json
{
  "mcpServers": {
    "eloverblik": {
      "command": "/Users/simon/Projects/eloverblik-mcp/.venv/bin/python",
      "args": ["/Users/simon/Projects/eloverblik-mcp/run_mcp.py"]
    }
  }
}
```

Pour obtenir le dossier absolu : `pwd` dans Terminal. Le script fonctionne même si le client démarre depuis un autre dossier. Le Mac doit être allumé. Le MCP n'effectue pas de synchronisation automatique : après un nouvel import, les outils liront les nouvelles données.

Outils :

| Outil | Usage |
|---|---|
| get_data_status | Compteurs stockés, limites de l'historique, dernière synchronisation |
| get_consumption | kWh par heure/jour/mois et couverture |
| compare_periods | Comparaison de deux périodes et moyennes horaires |
| get_hourly_profile | Profil par heure de la journée, heures incomplètes exclues |

Exemples : « Utilise Eloverblik local : quelle est ma consommation en octobre 2025 ? » ; « Compare les deux dernières semaines complètes et indique la couverture. »

### ChatGPT web / Work hébergé

**Ce dépôt ne fournit pas encore de serveur distant ni de plugin installé dans ChatGPT web.** Ajouter les chemins locaux ci-dessus dans le navigateur ne rend pas le Mac accessible. Pour une conversation hébergée, il faut une connexion distante authentifiée ou un tunnel pris en charge, ainsi que les permissions du compte. Ne pas exposer ce serveur ou la base publiquement sans authentification.

Documentation officielle :
- https://learn.chatgpt.com/docs/extend/mcp
- https://developers.openai.com/api/docs/guides/custom-mcp-server

## Hypothèses et contrôles

- Données de consommation A04/A64, unité kWh ; autres séries refusées.
- Qualité A03 incluse comme estimée ; A01 ajustée et A04 mesurée incluses ; A02/A05 manquantes/incomplètes exclues.
- Les sommes portent sur les données disponibles. Les lacunes ne sont jamais remplacées par zéro.
- Le profil horaire inclut les estimations ; ce n'est pas une détection d'appareil ni une correction météo.
- Les deux occurrences d'une heure au passage à l'heure d'hiver sont conservées.
- Le serveur MCP ne renvoie pas les tokens, noms ou adresses. Les identifiants de compteurs restent présents dans les outils : ils sont nécessaires pour choisir le compteur.
- Les périodes de durées différentes doivent être comparées aussi en kWh par heure couverte.
- Ne pas conclure sur une hausse/baisse avec une couverture incomplète.
- Tarifs : vérifier dates d'effet, TVA, fournisseur et complétude avant tout calcul de coût.

## Tests sans secrets

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Les tests utilisent des données synthétiques et des réponses HTTP simulées. Ils ne prouvent pas l'accès à un vrai compte Eloverblik. Le test MCP lance un vrai sous-processus STDIO et vérifie la découverte et l'appel d'outils.

## Code d'origine et licence

Les fichiers `eloverblik.py`, `eloverblik_thirdparty.py` et `energidataservice_DeclarationEmissionHour.py` sont les utilitaires historiques, conservés pour référence et non utilisés par le nouveau serveur. Leurs procédures et limites diffèrent. Licence MIT et copyright d'origine conservés dans LICENSE.

